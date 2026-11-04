"""Demo mode: the whole pipeline on bundled samples, with no accounts and no network.

Two batches in one workspace:
  demo           bring-your-own designs: three sample designs, one held for a border.
  demo-generate  generate mode: sample research, Claude's concepts, sample Gate 1 decisions
                 (approve, edit, reject), then image generation through the real OpenAI client
                 code with the network swapped for a replay that returns the sample designs.

Every code step runs for real. The judgement steps replay outputs Claude wrote for these
samples (samples/recorded/). The Etsy step is a dry run: it writes the exact API payload.
"""
from __future__ import annotations

import base64
import json
import shutil
import tomllib
from pathlib import Path

from podagent import concepts, generate, pipeline, report, research, review_page
from podagent.config import EXAMPLE_CONFIG, REPO, Workspace, validate
from podagent.http import Response
from podagent.images import calibrate
from podagent.providers.local_export import LocalExport

SAMPLES = REPO / "samples"
MARKER = ".podagent-demo"


def run(out: Path, mode: str = "both") -> dict:
    """Each mode gets its own workspace (out/inbox, out/generate), as two separate shops would."""
    out = Path(out).resolve()
    if out.exists():
        if not (out / MARKER).exists():
            raise FileExistsError(f"{out} exists and is not a demo folder; choose another --out")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / MARKER).write_text("created by podagent demo; safe to delete\n", encoding="utf-8")
    results = {}
    if mode in ("inbox", "both"):
        results["demo"] = _inbox(*_workspace(out / "inbox"))
    if mode in ("generate", "both"):
        results["demo-generate"] = _generate(*_workspace(out / "generate"))
    return results


def _workspace(out: Path) -> tuple[Workspace, dict]:
    out.mkdir(parents=True)
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
    return ws, cfg


def _finish(ctx: pipeline.Context) -> dict:
    """Run until Claude's review is needed, replay it, then copy, then finish with the review page."""
    pipeline.run(ctx)
    replay(ctx.batch, "vision.json")
    ctx.waiting.clear()
    pipeline.run(ctx)
    replay(ctx.batch, "copy.json")
    ctx.waiting.clear()
    result = pipeline.run(ctx, report_fn=lambda c: review_page.write(c.batch, c.cfg, report.write(c.batch)))
    result["review_page"] = str(ctx.batch.dir / "review.html")
    return result


def _inbox(ws: Workspace, cfg: dict) -> dict:
    designs = sorted((SAMPLES / "designs").glob("*.png"))
    batch = pipeline.new_batch(ws, cfg, files=designs, mode="demo", batch_id="demo", keep_originals=True)
    return _finish(pipeline.Context(ws, cfg, {}, batch, dry_run=True, provider=LocalExport(cfg, {})))


def _generate(ws: Workspace, cfg: dict) -> dict:
    (ws.root / "research").mkdir(exist_ok=True)
    shutil.copy2(SAMPLES / "research" / "keywords.csv", ws.root / "research" / "keywords.csv")
    batch = pipeline.new_generate_batch(ws, cfg, count=3, batch_id="demo-generate")
    found = research.gather(batch.dir, ws.root, cfg)

    rec = SAMPLES / "recorded" / "generate"
    shutil.copy2(rec / "concepts.json", batch.dir / "concepts.json")
    data = concepts.load_file(batch)
    errors, warnings = concepts.validate(data, cfg, found, batch.data["requested_count"])
    if errors:
        raise ValueError("recorded concepts are invalid: " + "; ".join(errors))
    concepts.load_into_state(batch, cfg, data)
    concepts.write_gate_page(batch, cfg, warnings)

    d = json.loads((rec / "decisions.json").read_text(encoding="utf-8"))           # Gate 1
    concepts.decide(batch, approve=d["approve"], edits=d["edit"])
    concepts.decide(batch, reject=d["reject"], note=d["reject_note"])
    concepts.write_gate_page(batch, cfg, warnings)

    images = [SAMPLES / "designs" / f"{c['id']}.png" for c in batch.data["concepts"] if c["decision"] == "approved"]
    client = generate.ImageClient("demo", cfg, transport=Replay(images))
    gen = generate.run(batch, cfg, client, pipeline.taken_skus(ws.path(cfg, "batches")),
                       cost_note=f"demo replay, not charged (live estimate ${concepts.summary(batch)['approved_estimate_usd']:.3f} for the batch)")
    result = _finish(pipeline.Context(ws, cfg, {}, batch, dry_run=True, provider=LocalExport(cfg, {})))
    result.update(generated=gen["generated"], gate_page=str(batch.dir / "concepts.html"),
                  research_dropped=found["dropped_banned"])
    return result


class Replay:
    """Stands in for the network: answers each image request with the next sample file,
    in the same JSON shape as the OpenAI Images API, and keeps the requests for inspection."""

    def __init__(self, files: list[Path]):
        self.files, self.requests = list(files), []

    def __call__(self, method, url, headers, body):
        self.requests.append(json.loads(body))
        png = self.files.pop(0).read_bytes()
        return Response(200, json.dumps({"created": 0, "data": [{"b64_json": base64.b64encode(png).decode()}],
                                         "usage": {"total_tokens": 0}}).encode())


def replay(batch, name: str) -> None:
    for item_id in batch.items:
        src = SAMPLES / "recorded" / item_id / name
        if src.exists() and not (batch.item_dir(item_id) / name).exists():
            shutil.copy2(src, batch.item_dir(item_id) / name)
