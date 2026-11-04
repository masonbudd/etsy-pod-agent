"""Demo mode: the whole pipeline on the bundled samples, with no accounts and no network.

Every code step runs for real (checks, upscaling, print files, room calibration, mockups,
copy validation, pricing, print-file export, the Etsy payload, review page and report).
The two judgement steps replay outputs Claude wrote for these samples, stored in
samples/recorded/. The Etsy step is a dry run: it writes the exact API payload instead of sending it.
"""
from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

from podagent import pipeline, report, review_page
from podagent.config import EXAMPLE_CONFIG, REPO, Workspace, validate
from podagent.images import calibrate
from podagent.providers.local_export import LocalExport

SAMPLES = REPO / "samples"
MARKER = ".podagent-demo"


def run(out: Path) -> dict:
    out = Path(out).resolve()
    if out.exists():
        if not (out / MARKER).exists():
            raise FileExistsError(f"{out} exists and is not a demo folder; choose another --out")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("created by podagent demo; safe to delete\n", encoding="utf-8")

    shutil.copy2(EXAMPLE_CONFIG, out / "config.toml")
    with open(out / "config.toml", "rb") as fh:
        cfg = tomllib.load(fh)
    problems = validate(cfg)
    if problems:
        raise ValueError("example config is invalid: " + "; ".join(problems))
    cfg["upscale"]["engine"] = "resize"            # same result on every machine, no GPU needed
    ws = Workspace(out)
    shutil.copytree(SAMPLES / "rooms", ws.path(cfg, "rooms"))
    for f in ("corners.json", "calibration_check.jpg"):
        (ws.path(cfg, "rooms") / f).unlink(missing_ok=True)
    calibrate.calibrate_rooms(ws.path(cfg, "rooms"))  # recalibrate to show it working

    designs = sorted((SAMPLES / "designs").glob("*.png"))
    batch = pipeline.new_batch(ws, cfg, files=designs, mode="demo", batch_id="demo", keep_originals=True)
    ctx = pipeline.Context(ws, cfg, {}, batch, dry_run=True, provider=LocalExport(cfg, {}))

    pipeline.run(ctx)                               # stops at checks: waiting for Claude's visual review
    replay(batch, "vision.json")
    pipeline.run(ctx)                               # stops at copy: waiting for Claude's listing copy
    replay(batch, "copy.json")
    result = pipeline.run(ctx, report_fn=lambda c: review_page.write(c.batch, cfg, report.write(c.batch)))
    result["review_page"] = str(batch.dir / "review.html")
    return result


def replay(batch, name: str) -> None:
    for item_id in batch.items:
        src = SAMPLES / "recorded" / item_id / name
        if src.exists() and not (batch.item_dir(item_id) / name).exists():
            shutil.copy2(src, batch.item_dir(item_id) / name)
