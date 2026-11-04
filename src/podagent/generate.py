"""Image generation through the OpenAI Images API, for approved concepts only.

Money rules, enforced here:
- Only concepts approved at Gate 1 are generated.
- A batch never spends more than images.max_batch_usd (estimated from config prices).
- The request is recorded before it is sent and the image is saved the moment it arrives.
  If a run died in between, the image may have been paid for, so it is not requested
  again unless you pass retry_unsaved.
- A held design is regenerated at most images.auto_regenerate times (default 1), with the
  check's fix added to the prompt. After that it stays held.
"""
from __future__ import annotations

import base64
import json
import time

from PIL import Image

from podagent import concepts as C
from podagent import costs, rules
from podagent.config import variant_suffix_len
from podagent.http import request, urllib_transport
from podagent.state import Batch, now

API = "https://api.openai.com/v1/images/generations"
# hold reason (as written by checks.verdict) -> what to add to the prompt
REGENERABLE = {("border",): "The artwork reaches every edge of the canvas: no border, margin, frame or paper edge.",
               ("visible defect",): "Clean, simple shapes with nothing distorted, duplicated or melted. No watermark or signature.",
               ("possible ip", "banned term"): "No brands, logos, characters, celebrities or recognisable people.",
               ("landscape",): "Portrait orientation."}
TEXT_REASONS = ("spelling", "lettering reads")


class ImageClient:
    def __init__(self, api_key: str, cfg: dict, transport=None, sleep=time.sleep):
        self.key, self.img = api_key, cfg.get("images", {})
        self.transport, self.sleep = transport or urllib_transport, sleep

    def generate(self, prompt: str) -> tuple[bytes, dict]:
        body = {"model": self.img["model"], "prompt": prompt, "size": self.img.get("size", "1024x1536"),
                "quality": self.img.get("quality", "medium"), "n": 1, "output_format": "png"}
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        r = request(self.transport, "POST", API, headers, json.dumps(body).encode(), idempotent=False, sleep=self.sleep).json()
        item = (r.get("data") or [{}])[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"]), r.get("usage", {})
        if item.get("url"):
            return request(self.transport, "GET", item["url"], sleep=self.sleep).body, r.get("usage", {})
        raise RuntimeError("image API returned no image")


def _spent(batch: Batch) -> float:
    return sum(c["usd"] for c in batch.data.get("costs", []) if c["kind"] == "image_generation")


def _fixes(item: dict, concept: dict) -> list[str]:
    """Prompt additions that answer the reasons a design was held."""
    out = []
    for reason in item.get("held", {}).get("reasons", []):
        r = reason.lower()
        if r.startswith(TEXT_REASONS):
            out.append(f'The only text is "{concept["lettering"]}", spelled exactly like that.' if concept.get("lettering")
                       else "Do not include any text, letters or numbers.")
        for prefixes, fix in REGENERABLE.items():
            if r.startswith(prefixes):
                out.append(fix)
    return list(dict.fromkeys(out))


def _save(batch: Batch, item_id: str, png: bytes) -> None:
    d = batch.item_dir(item_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "original.png").write_bytes(png)
    Image.open(d / "original.png").convert("RGB").save(d / "source.png")


def run(batch: Batch, cfg: dict, client: ImageClient, taken_skus: set[str], retry_unsaved: bool = False,
        cost_note: str = "") -> dict:
    """Generate every approved concept not yet generated, then regenerate held designs once."""
    img = cfg.get("images", {})
    price = costs.image_price(cfg)
    if price is None:
        raise ValueError("no price for this model, quality and size in [images.price_usd]; add one so spend can be capped")
    budget = img.get("max_batch_usd", 2.0)
    made, regenerated, skipped = [], [], []

    def can_spend() -> bool:
        return _spent(batch) + price <= budget + 1e-9

    def pay(item_id: str, usage: dict) -> None:
        batch.add_cost("image_generation", 0.0 if cost_note else price, item_id,
                       cost_note or f"{img['model']} {img.get('quality')} {img.get('size')}; estimated from config; tokens {usage.get('total_tokens', '?')}")

    for c in batch.data.get("concepts", []):
        if c.get("decision") != "approved" or c.get("item"):
            continue
        if c.get("requested_at") and not retry_unsaved:
            skipped.append(f"{c['id']}: a paid request was sent at {c['requested_at']} but no image was saved. "
                           "Check your OpenAI usage, then rerun with --retry-unsaved to request it again.")
            continue
        if not can_spend():
            skipped.append(f"{c['id']}: would pass the ${budget:.2f} batch budget (images.max_batch_usd)")
            continue
        c["requested_at"] = now()
        batch.save()
        png, usage = client.generate(C.full_prompt(c, cfg))
        sku = rules.make_sku(cfg["naming"]["sku_prefix"], c["title"], taken_skus, variant_suffix_len(cfg))
        taken_skus.add(sku)
        batch.add_item(c["id"], sku, f"generated:{c['id']}", concept=c["id"], regenerations=0,
                       lettering_expected=c.get("lettering", ""))
        _save(batch, c["id"], png)
        c["item"] = c["id"]
        pay(c["id"], usage)
        made.append(c["id"])

    limit = int(img.get("auto_regenerate", 1))
    by_concept = {c["id"]: c for c in batch.data.get("concepts", [])}
    for item_id, it in list(batch.items.items()):
        concept = by_concept.get(it.get("concept"))
        if it["status"] != "held" or not concept or it.get("regenerations", 0) >= limit:
            continue
        fixes = _fixes(it, concept)
        if not fixes:
            continue                                   # e.g. a near-duplicate: a new image won't fix it
        if not can_spend():
            skipped.append(f"{item_id}: regeneration would pass the ${budget:.2f} batch budget")
            continue
        prompt = f"{C.full_prompt(concept, cfg)} {' '.join(fixes)}"
        png, usage = client.generate(prompt)
        d = batch.item_dir(item_id)
        n = it.get("regenerations", 0) + 1
        (d / "source.png").replace(d / f"source_attempt{n}.png")
        for f in ("vision.json", "preview.jpg", "checks.json", "master.png", "copy_errors.json"):
            (d / f).unlink(missing_ok=True)
        _save(batch, item_id, png)
        it["regenerations"], it["last_prompt"] = n, prompt
        batch.release(item_id, ["checks", "images", "mockups", "copy", "prices", "provider"])
        pay(item_id, usage)
        regenerated.append(item_id)
    return {"generated": made, "regenerated": regenerated, "skipped": skipped,
            "spent_usd": round(_spent(batch), 4), "budget_usd": budget}
