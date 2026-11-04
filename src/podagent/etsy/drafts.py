"""Turn a checked item into an Etsy draft listing: fields, photos and size x frame inventory.

Each sub-step is saved to the batch state as soon as it succeeds. If a run dies after Etsy
accepted the create request but before the ID was saved, the next run finds that draft by
title instead of creating a second one.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from podagent import listing_copy, rules
from podagent.pricing import Variant
from podagent.state import Batch

EDIT_URL = "https://www.etsy.com/your/shops/me/listing-editor/edit/{}"
SIZE_PROPERTY, FRAME_PROPERTY = 513, 514  # Etsy's two custom variation properties
QUANTITY = 999


def listing_fields(copy: dict, cfg: dict, min_price: float, section_id) -> dict:
    lst, ids = cfg["listing"], cfg["etsy"]
    fields = {
        "quantity": QUANTITY,
        "title": copy["title"],
        "description": listing_copy.description(copy, cfg),
        "price": f"{min_price:.2f}",
        "who_made": lst["who_made"],
        "when_made": lst["when_made"],
        "taxonomy_id": lst["taxonomy_id"],
        "type": "physical",
        "is_supply": "false",
        "shipping_profile_id": ids["shipping_profile_id"],
        "return_policy_id": ids["return_policy_id"],
        "readiness_state_id": ids["readiness_state_id"],
        "tags": copy["tags"],
        "materials": lst.get("materials", []),
        "shop_section_id": section_id,
    }
    if ids.get("production_partner_id"):
        fields["production_partner_ids"] = [ids["production_partner_id"]]
    return fields


def variant_rows(base_sku: str, rows: list[Variant], cfg: dict) -> list[dict]:
    """The size x frame grid with each row's SKU."""
    per_variant = cfg["variations"].get("variant_skus", False)
    out = []
    for r in rows:
        sku = rules.variant_sku(base_sku, r.size_code, r.frame_code) if per_variant else base_sku
        out.append({**r.as_dict(), "sku": sku})
    return out


def inventory_body(variants: list[dict], cfg: dict) -> dict:
    v = cfg["variations"]
    has_frames = any(r["frame"] for r in variants)
    props = [SIZE_PROPERTY] + ([FRAME_PROPERTY] if has_frames else [])
    cheapest_by_size = {}
    for r in variants:
        if r["enabled"]:
            cheapest_by_size.setdefault(r["size_code"], r["price"])
    products = []
    for r in variants:
        values = [
            {"property_id": SIZE_PROPERTY, "property_name": v["size_property"], "value_ids": [], "values": [r["size"]]}
        ]
        if has_frames:
            values.append(
                {
                    "property_id": FRAME_PROPERTY,
                    "property_name": v["frame_property"],
                    "value_ids": [],
                    "values": [r["frame"]],
                }
            )
        price = (
            r["price"] if r["enabled"] else cheapest_by_size.get(r["size_code"], 1.0)
        )  # Etsy wants a price even when off
        products.append(
            {
                "sku": r["sku"],
                "property_values": values,
                "offerings": [
                    {
                        "price": float(price),
                        "quantity": QUANTITY if r["enabled"] else 0,
                        "is_enabled": r["enabled"],
                        "readiness_state_id": cfg["etsy"]["readiness_state_id"],
                    }
                ],
            }
        )
    return {
        "products": products,
        "price_on_property": props,
        "sku_on_property": props if v.get("variant_skus") else [],
        "quantity_on_property": [],
        "readiness_state_on_property": [],
    }


def ensure_section(client, cfg: dict, name: str, cache: dict) -> int:
    shop = cfg["etsy"]["shop_id"]
    if not cache:
        cache.update({s["title"]: s["shop_section_id"] for s in client.sections(shop)})
    if name not in cache:
        cache[name] = client.create_section(shop, name)["shop_section_id"]
    return cache[name]


def _same_title(a: str, b: str) -> bool:
    """Etsy returns titles HTML-escaped (&amp;, &#39;), so compare the text, not the bytes."""

    def norm(t: str) -> str:
        return " ".join(html.unescape(t or "").split()).casefold()

    return norm(a) == norm(b)


def _find_lost_draft(client, cfg: dict, title: str, requested_at: str) -> int | None:
    """The newest draft with this title created since the request. A draft has no SKU until its
    inventory is set, which happens after this crash window, so the title is what there is."""
    since = datetime.fromisoformat(requested_at).timestamp() - 120
    found = [
        L
        for L in client.draft_listings(cfg["etsy"]["shop_id"])
        if _same_title(L.get("title", ""), title)
        and (L.get("created_timestamp") or L.get("creation_timestamp") or 0) >= since
    ]
    return max(found, key=lambda L: L.get("created_timestamp") or 0)["listing_id"] if found else None


def create(client, batch: Batch, item_id: str, cfg: dict, sections: dict, dry_run: bool = False) -> dict:
    """Create (or finish) the draft for one item. Returns the draft record saved in state."""
    it = batch.items[item_id]
    d = batch.item_dir(item_id)
    copy = json.loads((d / "copy.json").read_text(encoding="utf-8"))
    variants = it["steps"]["prices"]["variants"]
    photos = it["steps"]["mockups"]["photos"]
    min_price = min(r["price"] for r in variants if r["enabled"])
    rec = batch.step(item_id, "draft")

    if dry_run:
        fields = listing_fields(copy, cfg, min_price, f"<section: {copy['section']}>")
        for k in ("shipping_profile_id", "return_policy_id", "readiness_state_id"):
            fields[k] = fields[k] or "<set by podagent etsy-discover>"
        payload = {
            "createDraftListing": fields,
            "uploadListingImage": [
                {"rank": n, "file": p["file"], "alt_text": p["alt"]} for n, p in enumerate(photos, 1)
            ],
            "updateListingInventory": inventory_body(variants, cfg),
        }
        (d / "etsy_payload.json").write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
        batch.mark(item_id, "draft", "done", dry_run=True, payload="etsy_payload.json")
        return batch.step(item_id, "draft")

    shop = cfg["etsy"]["shop_id"]
    if not rec.get("listing_id"):
        lost = _find_lost_draft(client, cfg, copy["title"], rec["requested_at"]) if rec.get("requested_at") else None
        if lost:
            batch.mark(item_id, "draft", "running", listing_id=lost, recovered=True)
        else:
            fields = listing_fields(copy, cfg, min_price, ensure_section(client, cfg, copy["section"], sections))
            batch.mark(item_id, "draft", "running", requested_at=datetime.now().astimezone().isoformat())
            r = client.create_draft(shop, fields)
            batch.mark(item_id, "draft", "running", listing_id=r["listing_id"], etsy_state=r.get("state"))
    lid = batch.step(item_id, "draft")["listing_id"]

    uploaded = dict(batch.step(item_id, "draft").get("images", {}))
    for rank, p in enumerate(photos, 1):
        if p["file"] in uploaded:
            continue
        r = client.upload_image(shop, lid, d / "photos" / p["file"], rank, p["alt"])
        uploaded[p["file"]] = r["listing_image_id"]
        batch.mark(item_id, "draft", "running", images=uploaded)

    if not batch.step(item_id, "draft").get("inventory"):
        client.update_inventory(lid, inventory_body(variants, cfg))
        batch.mark(item_id, "draft", "running", inventory=True)

    batch.mark(item_id, "draft", "done", url=EDIT_URL.format(lid))
    it["status"] = "drafted"
    batch.save()
    return batch.step(item_id, "draft")


def disclosure_tasks(batch: Batch) -> list[dict]:
    """Drafts that still need Etsy's AI disclosure ticked in the listing editor (no API field exists)."""
    out = []
    for item_id, it in batch.items.items():
        rec = it["steps"].get("draft", {})
        if rec.get("listing_id") and not batch.done(item_id, "disclosure"):
            out.append({"item": item_id, "listing_id": rec["listing_id"], "url": EDIT_URL.format(rec["listing_id"])})
    return out


def write_disclosure_tasks(batch: Batch, path: Path) -> list[dict]:
    tasks = disclosure_tasks(batch)
    lines = [
        "# AI disclosure: Claude in Chrome tasks",
        "",
        "Etsy's API has no field for this, so it is set in the listing editor.",
        "",
        "For each draft below:",
        "1. Open the edit link in Chrome.",
        '2. Under "How it\'s made" (or the AI question), tick the option saying the design was made '
        "with an AI generator.",
        "3. Click **Save as draft** only. Never click Publish. If there is no Save as draft button, stop and ask.",
        "4. Run `podagent mark <batch> <item> disclosure` to record it.",
        "",
    ]
    lines += [f"- [ ] {t['item']}: {t['url']}" for t in tasks]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tasks
