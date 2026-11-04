"""Universal fallback: print files grouped by ratio plus a SKU sheet, ready to upload to any provider."""

from __future__ import annotations

import csv
import shutil

from podagent.providers.base import Check, ItemContext, Provider, Result

SHEET_FIELDS = ["sku", "base_sku", "size", "frame", "ratio", "file", "enabled"]


class LocalExport(Provider):
    name = "local_export"

    def check(self, online: bool = False) -> list[Check]:
        return [Check("local export", True, "writes provider_export/ in each batch; no account needed")]

    def export_dir(self, ctx: ItemContext):
        return ctx.batch_dir / "provider_export"

    def prepare(self, ctx: ItemContext) -> Result:
        out = self.export_dir(ctx)
        files = {}
        for key, src in ctx.print_files.items():
            dst = out / key / f"{ctx.base_sku}.jpg"
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(src, dst)
            files[key] = f"{key}/{dst.name}"
        rows = [
            {
                "sku": v["sku"],
                "base_sku": ctx.base_sku,
                "size": v["size"],
                "frame": v["frame"] or "",
                "ratio": v["ratio"],
                "file": files[_key(v["ratio"])],
                "enabled": v["enabled"],
            }
            for v in ctx.variants
        ]
        _write_sheet(out / "sku_sheet.csv", ctx.base_sku, rows)
        return Result(
            "needs_action",
            f"{len(files)} print files, {len(rows)} SKUs in provider_export/",
            ["Upload provider_export/ to your print provider and set each product's SKU from sku_sheet.csv"],
            {"files": files},
        )


def _key(ratio: str) -> str:
    from podagent.images.crops import ratio_key

    return ratio_key(ratio)


def _write_sheet(path, base_sku: str, rows: list[dict]) -> None:
    """Rewrite this item's rows in the batch-wide sheet, keeping other items' rows."""
    keep = []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            keep = [r for r in csv.DictReader(fh) if r["base_sku"] != base_sku]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, SHEET_FIELDS)
        w.writeheader()
        w.writerows(keep + rows)
