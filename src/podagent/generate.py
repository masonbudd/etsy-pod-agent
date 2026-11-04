"""Image generation through the OpenAI Images API, for approved concepts only.

Money rules, enforced here:
- Only concepts approved at Gate 1 are generated.
- A batch never spends more than images.max_batch_usd (estimated from config prices).
- Every request, first or regeneration, is recorded and its cost counted before it is sent,
  and the image is written to disk the moment it arrives. A rerun picks up an image that
  arrived before a crash without paying again. If no image arrived, the request may still
  have been charged, so it is not sent again unless you pass retry_unsaved.
- A request the API refuses (a 4xx such as a moderation block) is recorded at $0 with the
  reason, and the run carries on with the other concepts.
- A held design is regenerated at most images.auto_regenerate times (default 1), with the
  check's fix added to the prompt. After that it stays held.
"""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path

from PIL import Image

from podagent import concepts as C
from podagent import costs, rules
from podagent.config import variant_suffix_len
from podagent.http import HttpError, request, urllib_transport
from podagent.state import Batch, now

API = "https://api.openai.com/v1/images/generations"
# hold reason (as written by checks.verdict) -> what to add to the prompt
REGENERABLE = {
    ("border",): "The artwork reaches every edge of the canvas: no border, margin, frame or paper edge.",
    (
        "visible defect",
    ): "Clean, simple shapes with nothing distorted, duplicated or melted. No watermark or signature.",
    ("possible ip", "banned term"): "No brands, logos, characters, celebrities or recognisable people.",
    ("landscape",): "Portrait orientation.",
}
TEXT_REASONS = ("spelling", "lettering reads")
CLEAR_ON_REGENERATE = ("vision.json", "preview.jpg", "checks.json", "master.png", "copy_errors.json")


class Refused(Exception):
    """The API turned the request down (e.g. a moderation block). Nothing was generated."""


class RateLimited(Exception):
    """The API kept answering 429. The request was not processed; try again later."""


class ImageClient:
    def __init__(self, api_key: str, cfg: dict, transport=None, sleep=time.sleep):
        self.key, self.img = api_key, cfg.get("images", {})
        self.transport, self.sleep = transport or urllib_transport, sleep

    def generate(self, prompt: str) -> tuple[bytes, dict]:
        body = {
            "model": self.img["model"],
            "prompt": prompt,
            "size": self.img.get("size", "1024x1536"),
            "quality": self.img.get("quality", "medium"),
            "n": 1,
            "output_format": "png",
        }
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        r = request(
            self.transport, "POST", API, headers, json.dumps(body).encode(), idempotent=False, sleep=self.sleep
        ).json()
        item = (r.get("data") or [{}])[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"]), r.get("usage", {})
        if item.get("url"):
            return request(self.transport, "GET", item["url"], sleep=self.sleep).body, r.get("usage", {})
        raise RuntimeError("image API returned no image")


def _spent(batch: Batch) -> float:
    return sum(c["usd"] for c in batch.data.get("costs", []) if c["kind"] == "image_generation")


def api_reason(e: HttpError) -> str:
    """The API's own short explanation, e.g. 'moderation_blocked: Your request was rejected...'."""
    try:
        err = json.loads(e.body).get("error", {})
        text, code = err.get("message") or e.body, err.get("code")
    except (ValueError, AttributeError):
        text, code = e.body, None
    return ((f"{code}: " if code else "") + str(text))[:200]


def _request(batch: Batch, client: ImageClient, prompt: str, item: str, usd: float, note: str):
    """Send one paid request. The ledger entry is written first, so the cost counts against the
    budget even if the run dies before the image is saved."""
    entry = batch.add_cost("image_generation", usd, item, note, status="requested")
    try:
        png, usage = client.generate(prompt)
    except HttpError as e:
        if e.status == 429:
            entry.update(usd=0.0, status="rate_limited", note="429 from the image API: not processed")
            batch.save()
            raise RateLimited(api_reason(e)) from e
        if 400 <= e.status < 500:
            entry.update(usd=0.0, status="refused", note=f"refused by the image API: {api_reason(e)}")
            batch.save()
            raise Refused(api_reason(e)) from e
        raise  # a 5xx or network failure may still have been charged: the entry stays "requested"
    return png, usage, entry


def _write(path: Path, png: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(png)
    os.replace(tmp, path)


def _settle(batch: Batch, item: str, entry: dict | None, usage: dict | None) -> None:
    """Mark the request's ledger entry as delivered (found by item if the run was interrupted)."""
    if entry is None:
        entry = next(
            (e for e in reversed(batch.data["costs"]) if e.get("item") == item and e.get("status") == "requested"), None
        )
    if entry is not None:
        entry.update(status="saved", tokens=(usage or {}).get("total_tokens"))


def _fixes(item: dict, concept: dict) -> list[str]:
    """Prompt additions that answer the reasons a design was held."""
    out = []
    for reason in item.get("held", {}).get("reasons", []):
        r = reason.lower()
        if r.startswith(TEXT_REASONS):
            out.append(
                f'The only text is "{concept["lettering"]}", spelled exactly like that.'
                if concept.get("lettering")
                else "Do not include any text, letters or numbers."
            )
        for prefixes, fix in REGENERABLE.items():
            if r.startswith(prefixes):
                out.append(fix)
    return list(dict.fromkeys(out))


def _adopt_new(batch: Batch, c: dict, cfg: dict, taken_skus: set[str], entry=None, usage=None) -> None:
    """Record a first-time image already written to items/<id>/original.png. One save covers it all."""
    d = batch.item_dir(c["id"])
    Image.open(d / "original.png").convert("RGB").save(d / "source.png")
    sku = rules.make_sku(cfg["naming"]["sku_prefix"], c["title"], taken_skus, variant_suffix_len(cfg))
    taken_skus.add(sku)
    c["item"] = c["id"]
    c.pop("requested_at", None)
    _settle(batch, c["id"], entry, usage)
    batch.add_item(
        c["id"],
        sku,
        f"generated:{c['id']}",
        concept=c["id"],
        regenerations=0,
        lettering_expected=c.get("lettering", ""),
    )


def _adopt_regenerated(batch: Batch, item_id: str, it: dict, entry=None, usage=None) -> None:
    """Swap in the regenerated image from items/<id>/regenerated.png. Safe to repeat after a crash."""
    d = batch.item_dir(item_id)
    n = it.get("regenerations", 0) + 1
    for name in ("source", "original"):
        if (d / f"{name}.png").exists() and not (d / f"{name}_attempt{n}.png").exists():
            (d / f"{name}.png").replace(d / f"{name}_attempt{n}.png")
    os.replace(d / "regenerated.png", d / "original.png")
    Image.open(d / "original.png").convert("RGB").save(d / "source.png")
    for f in CLEAR_ON_REGENERATE:
        (d / f).unlink(missing_ok=True)
    it["regenerations"], it["last_prompt"] = n, it.pop("regen_prompt", "")
    it.pop("regen_requested_at", None)
    _settle(batch, item_id, entry, usage)
    batch.release(item_id, ["checks", "images", "mockups", "copy", "prices", "provider"])


def run(
    batch: Batch, cfg: dict, client: ImageClient, taken_skus: set[str], retry_unsaved: bool = False, cost_note: str = ""
) -> dict:
    """Generate every approved concept not yet generated, then regenerate held designs once."""
    img = cfg.get("images", {})
    price = costs.image_price(cfg)
    if price is None:
        raise ValueError(
            "no price for this model, quality and size in [images.price_usd]; add one so spend can be capped"
        )
    budget = img.get("max_batch_usd", 2.0)
    usd = 0.0 if cost_note else price
    note = cost_note or f"{img['model']} {img.get('quality')} {img.get('size')}; estimated from config"
    made, regenerated, skipped, refused = [], [], [], []

    def can_spend() -> bool:
        return _spent(batch) + price <= budget + 1e-9

    for c in batch.data.get("concepts", []):
        if c.get("decision") != "approved" or c.get("item") or c.get("refused"):
            continue
        landed = batch.item_dir(c["id"]) / "original.png"
        if c.get("requested_at") and landed.exists():  # paid and saved; the run died before recording it
            _adopt_new(batch, c, cfg, taken_skus)
            made.append(c["id"])
            continue
        if c.get("requested_at") and not retry_unsaved:
            skipped.append(
                f"{c['id']}: a paid request was sent at {c['requested_at']} but no image was saved. "
                "Check your OpenAI usage, then rerun with --retry-unsaved to request it again."
            )
            continue
        if not can_spend():
            skipped.append(f"{c['id']}: would pass the ${budget:.2f} batch budget (images.max_batch_usd)")
            continue
        c["requested_at"] = now()
        batch.save()
        try:
            png, usage, entry = _request(batch, client, C.full_prompt(c, cfg), c["id"], usd, note)
        except Refused as e:
            c.pop("requested_at", None)
            c["refused"] = str(e)
            batch.save()
            refused.append(f"{c['id']}: {e}")
            continue
        except RateLimited as e:
            c.pop("requested_at", None)
            batch.save()
            skipped.append(f"rate limited by the image API ({e}); rerun later")
            break
        _write(landed, png)
        _adopt_new(batch, c, cfg, taken_skus, entry, usage)
        made.append(c["id"])

    limit = int(img.get("auto_regenerate", 1))
    by_concept = {c["id"]: c for c in batch.data.get("concepts", [])}
    for item_id, it in list(batch.items.items()):
        concept = by_concept.get(it.get("concept"))
        if it["status"] != "held" or not concept:
            continue
        if it.get("regen_requested_at") and (batch.item_dir(item_id) / "regenerated.png").exists():
            _adopt_regenerated(batch, item_id, it)
            regenerated.append(item_id)
            continue
        if it.get("regen_requested_at") and not retry_unsaved:
            skipped.append(
                f"{item_id}: a paid regeneration was sent at {it['regen_requested_at']} but no image was saved. "
                "Check your OpenAI usage, then rerun with --retry-unsaved to request it again."
            )
            continue
        if it.get("regen_refused") or it.get("regenerations", 0) >= limit:
            continue
        fixes = _fixes(it, concept)
        if not fixes:
            continue  # e.g. a near-duplicate: a new image won't fix it
        if not can_spend():
            skipped.append(f"{item_id}: regeneration would pass the ${budget:.2f} batch budget")
            continue
        it["regen_prompt"] = f"{C.full_prompt(concept, cfg)} {' '.join(fixes)}"
        it["regen_requested_at"] = now()
        batch.save()
        try:
            png, usage, entry = _request(batch, client, it["regen_prompt"], item_id, usd, note)
        except Refused as e:
            it.pop("regen_requested_at", None)
            it["regen_refused"] = str(e)
            it["held"]["reasons"].append(f"the image API refused the regeneration: {e}")
            batch.save()
            refused.append(f"{item_id}: {e}")
            continue
        except RateLimited as e:
            it.pop("regen_requested_at", None)
            batch.save()
            skipped.append(f"rate limited by the image API ({e}); rerun later")
            break
        _write(batch.item_dir(item_id) / "regenerated.png", png)
        _adopt_regenerated(batch, item_id, it, entry, usage)
        regenerated.append(item_id)
    return {
        "generated": made,
        "regenerated": regenerated,
        "refused": refused,
        "skipped": skipped,
        "spent_usd": round(_spent(batch), 4),
        "budget_usd": budget,
    }
