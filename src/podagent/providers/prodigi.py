"""Prodigi (API v5) adapter: wall-art prints and frames, made in the UK, EU and US.

Prodigi fulfils Etsy orders for listings it did not create once each Etsy variant is mapped
to a Prodigi catalogue SKU. This adapter:
  prepare(): uploads the print files as Prodigi assets and plans one mapping per variant SKU.
  link():    after you publish and Prodigi has synced the listing, matches the synced variants
             by SKU, creates the mappings and switches on automatic fulfilment.
Connecting the Etsy shop to Prodigi is a one-time step in Prodigi's dashboard.
Reference: https://www.prodigi.com/print-api/docs/openapi.yml
"""

from __future__ import annotations

import json
import mimetypes
import time
import urllib.parse
from pathlib import Path

from podagent.http import request, urllib_transport
from podagent.images.crops import ratio_key
from podagent.providers.base import Check, ItemContext, Provider, Result

API = "https://api.prodigi.com/v5"
API_HOST = urllib.parse.urlsplit(API).hostname
ARTWORK_NOTE = (
    "Prodigi's API reference does not say how artwork attaches to a mapped Etsy variant. "
    "Open one mapped product in Prodigi's dashboard and confirm the artwork before your first sale."
)


class Prodigi(Provider):
    name = "prodigi"
    external = True

    def __init__(self, cfg: dict, env: dict, transport=None, sleep=time.sleep):
        super().__init__(cfg, env, transport or urllib_transport)
        self.sleep = sleep
        self.p = cfg["provider"].get("prodigi", {})
        self.key = env.get("PRINT_PROVIDER_API_KEY", "")

    # -- http --------------------------------------------------------------
    def _call(
        self,
        method: str,
        path_or_url: str,
        body: dict | None = None,
        raw: bytes | None = None,
        ctype: str | None = None,
        idem_key: str | None = None,
        idempotent: bool = True,
    ) -> dict:
        url = path_or_url if path_or_url.startswith("http") else API + path_or_url
        headers = (
            {"X-API-Key": self.key} if urllib.parse.urlsplit(url).hostname == API_HOST else {}
        )  # never to other hosts
        data = raw
        if body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        elif ctype:
            headers["Content-Type"] = ctype
        if idem_key:
            headers["Idempotency-Key"] = idem_key
        return request(self.transport, method, url, headers, data, idempotent=idempotent, sleep=self.sleep).json()

    def _pages(self, path: str) -> list[dict]:
        out, offset = [], 0
        while True:
            page = self._call("GET", f"{path}{'&' if '?' in path else '?'}limit=100&offset={offset}")
            data = page.get("data") or []
            out += data
            if not data:
                return out
            offset += page.get("limit") or 100

    # -- setup -------------------------------------------------------------
    def check(self, online: bool = False) -> list[Check]:
        out = [
            Check(
                "Prodigi API key",
                self.key.startswith(("sandbox_", "live_")),
                "set PRINT_PROVIDER_API_KEY to a sandbox_ or live_ key"
                if not self.key
                else self.key.split("_")[0] + " key",
            ),
            Check(
                "Prodigi sales channel",
                bool(self.p.get("sales_channel_id")),
                "connect Etsy in Prodigi's dashboard, then set provider.prodigi.sales_channel_id",
            ),
        ]
        cat = self.p.get("catalogue", {})
        missing = [s["code"] for s in self.cfg["sizes"] if s["code"] not in cat]
        out.append(
            Check(
                "Prodigi SKUs for every size",
                not missing,
                "all sizes mapped"
                if not missing
                else "add provider.prodigi.catalogue entries for: " + ", ".join(missing),
            )
        )
        if online and self.key:
            try:
                self._call("GET", "/merchant")
                out.append(Check("Prodigi reachable", True, "GET /merchant ok"))
            except Exception as e:  # noqa: BLE001 - report any failure as a failed check
                out.append(Check("Prodigi reachable", False, str(e)[:200]))
        return out

    # -- per item ----------------------------------------------------------
    def catalogue_for(self, v: dict) -> tuple[str | None, dict]:
        """(catalogue SKU, attributes) for one size/frame row."""
        skus = self.p.get("catalogue", {}).get(v["size_code"])
        if not skus:
            return None, {}
        framed = bool(v["frame_code"]) and v["frame_code"] in self.p.get("frame_colours", {})
        if not framed:
            return skus[0], {}
        if len(skus) < 2 or not skus[1]:
            return None, {}
        return skus[1], {self.p.get("frame_attribute", "framecolour"): self.p["frame_colours"][v["frame_code"]]}

    def upload(self, path: Path, tags: list[str]) -> dict:
        start = self._call(
            "POST",
            "/assets",
            {
                "filename": path.name,
                "mimeType": mimetypes.guess_type(path.name)[0] or "image/jpeg",
                "sizeBytes": path.stat().st_size,
                "tags": tags,
            },
            idempotent=False,
        )
        done = self._call("PUT", start["uploadUrl"], raw=path.read_bytes(), ctype="image/jpeg", idempotent=False)
        asset = done.get("data") or done
        return {"id": asset.get("id"), "url": asset.get("url"), "status": asset.get("status")}

    def prepare(self, ctx: ItemContext) -> Result:
        store = ctx.batch_dir / "provider_export" / "prodigi_assets.json"
        assets = json.loads(store.read_text(encoding="utf-8")) if store.exists() else {}
        mine = assets.setdefault(ctx.base_sku, {})
        if ctx.dry_run:  # plan only: nothing leaves the machine
            mine = {
                key: mine.get(key, {"id": None, "url": None, "status": "not uploaded (dry run)"})
                for key in ctx.print_files
            }
        for key, path in ctx.print_files.items():
            if key not in mine:  # resumable: never re-upload a file
                mine[key] = self.upload(Path(path), [ctx.base_sku, key])
                store.parent.mkdir(parents=True, exist_ok=True)
                store.write_text(json.dumps(assets, indent=1), encoding="utf-8")
        plan, unmapped = [], []
        for v in ctx.variants:
            if not v["enabled"]:
                continue
            sku, attrs = self.catalogue_for(v)
            if not sku:
                unmapped.append(f"{v['size']} / {v['frame']}")
                continue
            plan.append(
                {"sku": v["sku"], "catalogueVariantId": sku, "attributes": attrs, "asset": mine[ratio_key(v["ratio"])]}
            )
        if unmapped:
            return Result("failed", "no Prodigi SKU for: " + ", ".join(unmapped), data={"plan": plan})
        if ctx.dry_run:
            return Result(
                "needs_action",
                f"dry run: would upload {len(mine)} print files; {len(plan)} variant mappings planned",
                ["A real run uploads the print files to Prodigi"],
                {"plan": plan},
            )
        return Result(
            "needs_action",
            f"{len(mine)} print files uploaded, {len(plan)} variant mappings planned",
            ["After you publish, run `podagent provider-link <batch>` to connect it in Prodigi", ARTWORK_NOTE],
            {"plan": plan, "assets": mine},
        )

    def link(self, ctx: ItemContext, plan: list[dict] | None = None) -> Result:
        """Match Prodigi's synced copy of the Etsy listing by SKU and map every variant."""
        if not ctx.listing_id:
            return Result("needs_action", "no Etsy listing yet")
        channel = self.p["sales_channel_id"]
        product = next(
            (
                p
                for p in self._pages(f"/sales-channels/{channel}/products")
                if str(p.get("externalId")) == str(ctx.listing_id)
            ),
            None,
        )
        if not product:
            return Result(
                "needs_action",
                "Prodigi has not synced this listing yet. Publish it in Etsy, wait for the sync, then rerun.",
            )
        by_sku = {row["sku"]: row for row in plan or []}
        mapped = {m.get("externalVariantId") for m in self._pages(f"/sales-channels/{channel}/mappings")}
        made, missing = 0, []
        for var in product.get("variants", []):
            row = by_sku.get(var.get("sku"))
            if not row:
                missing.append(var.get("sku") or var.get("title"))
                continue
            if var["externalId"] in mapped:
                continue
            self._call(
                "POST",
                f"/sales-channels/{channel}/mappings",
                {
                    "externalProductId": str(product["externalId"]),
                    "externalVariantId": str(var["externalId"]),
                    "catalogueVariantId": row["catalogueVariantId"],
                    "attributes": row["attributes"],
                },
                idem_key=f"{ctx.listing_id}-{var['externalId']}",
                idempotent=False,
            )
            made += 1
        rules = self._call("GET", f"/sales-channels/{channel}/rules").get("data", [])
        rule = next((r for r in rules if str(r.get("storeProductId")) == str(product["externalId"])), None)
        if rule and not rule.get("enabled"):
            self._call("PATCH", f"/sales-channels/{channel}/rules/{rule['id']}", {"enabled": True})
        status = "done" if not missing and rule else "needs_action"
        todo = (
            ([f"variants with no matching SKU: {', '.join(map(str, missing))}"] if missing else [])
            + ([] if rule else ["no auto-fulfil rule found yet; check the product in Prodigi's dashboard"])
            + [ARTWORK_NOTE]
        )
        return Result(status, f"{made} new mappings; auto-fulfil {'on' if rule else 'not found'}", todo)
