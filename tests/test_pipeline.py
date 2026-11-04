"""The whole pipeline in dry-run mode: waiting for Claude, resuming, holding, and the demo."""
import json

import pytest

from conftest import art, good_copy, vision, write_json
from podagent import demo, pipeline, report, review_page
from podagent.images import upscale
from podagent.providers.local_export import LocalExport


def start(ws, cfg, n=2, seeds=None):
    inbox = ws.path(cfg, "inbox")
    for i, seed in enumerate(seeds or range(n)):
        art(seed=seed).save(inbox / f"design {i}.png")
    b = pipeline.new_batch(ws, cfg)
    return b, pipeline.Context(ws, cfg, {}, b, dry_run=True, provider=LocalExport(cfg, {}))


def feed(b, name, data):
    for item_id in b.items:
        write_json(b.item_dir(item_id) / name, data)


def test_new_batch_moves_designs_out_of_the_inbox(ws, small_cfg):
    b, _ = start(ws, small_cfg)
    assert list(ws.path(small_cfg, "inbox").iterdir()) == []
    assert sorted(it["sku"] for it in b.items.values()) == ["art-design-0", "art-design-1"]
    assert b.id.endswith("-01")
    art(seed=4).save(ws.path(small_cfg, "inbox") / "design 0.png")
    b2 = pipeline.new_batch(ws, small_cfg)
    assert b2.id.endswith("-02") and list(b2.items.values())[0]["sku"] == "art-design-0-2"


def test_batch_size_is_capped(ws, small_cfg):
    for i in range(25):
        art(seed=i).save(ws.path(small_cfg, "inbox") / f"d{i:02d}.png")
    assert len(pipeline.new_batch(ws, small_cfg, count=99).items) == 20


def test_run_waits_for_review_then_copy_then_drafts(ws, small_cfg):
    b, ctx = start(ws, small_cfg)
    res = pipeline.run(ctx)
    assert set(res["waiting"]) == {"vision review"} and len(res["waiting"]["vision review"]) == 2
    feed(b, "vision.json", vision())
    ctx.waiting.clear()
    res = pipeline.run(ctx)
    assert set(res["waiting"]) == {"listing copy"}
    feed(b, "copy.json", good_copy())
    ctx.waiting.clear()
    res = pipeline.run(ctx)
    assert res["waiting"] == {} and res["errors"] == {} and len(res["drafted"]) == 2


def test_rerun_skips_finished_work(ws, small_cfg, monkeypatch):
    b, ctx = start(ws, small_cfg, n=1)
    feed(b, "vision.json", vision())
    feed(b, "copy.json", good_copy())
    pipeline.run(ctx)
    before = json.dumps(b.items, sort_keys=True)

    def boom(*a, **k):
        raise AssertionError("upscaled twice")
    monkeypatch.setattr(upscale, "upscale", boom)
    res = pipeline.run(ctx)
    assert res["errors"] == {} and json.dumps(b.items, sort_keys=True) == before


def test_real_run_after_a_dry_run_still_creates_the_draft(ws, small_cfg):
    b, ctx = start(ws, small_cfg, n=1)
    feed(b, "vision.json", vision())
    feed(b, "copy.json", good_copy())
    pipeline.run(ctx)
    item = next(iter(b.items))
    assert b.step(item, "draft")["dry_run"]

    calls = []

    class FakeEtsy:
        def sections(self, shop): return []
        def create_section(self, shop, title): return {"shop_section_id": 5}
        def create_draft(self, shop, fields): calls.append(fields); return {"listing_id": 42, "state": "draft"}
        def upload_image(self, *a): return {"listing_image_id": len(calls)}
        def update_inventory(self, *a): return {}

    live = pipeline.Context(ws, small_cfg, {}, b, dry_run=False, provider=ctx.provider, etsy=FakeEtsy())
    res = pipeline.run(live)
    assert len(calls) == 1 and b.step(item, "draft")["listing_id"] == 42 and res["errors"] == {}
    assert (b.dir / "disclosure_tasks.md").exists()


def test_invalid_copy_goes_back_then_is_held_after_three_tries(ws, small_cfg):
    b, ctx = start(ws, small_cfg, n=1)
    feed(b, "vision.json", vision())
    item = next(iter(b.items))
    for n in range(3):
        bad = good_copy(title=f"Bad Title {n} | " + "x" * 140)
        write_json(b.item_dir(item) / "copy.json", bad)
        ctx.waiting.clear()
        res = pipeline.run(ctx)
    assert item in res["held"] and "copy failed validation 3 times" in res["held"][item][0]


def test_held_designs_stay_in_their_batch_and_never_draft(ws, small_cfg):
    b, ctx = start(ws, small_cfg, n=2)
    item0, item1 = list(b.items)
    write_json(b.item_dir(item0) / "vision.json", vision(spelling_ok=False, lettering="GOOD MORNIG", spelling_notes="MORNIG"))
    write_json(b.item_dir(item1) / "vision.json", vision())
    feed(b, "copy.json", good_copy())
    res = pipeline.run(ctx)
    assert item0 in res["held"] and res["drafted"] == [item1]
    assert (b.item_dir(item0) / "original.png").exists()


def test_near_duplicate_in_the_same_batch_is_held(ws, small_cfg):
    b, ctx = start(ws, small_cfg, seeds=[1, 1])
    feed(b, "vision.json", vision())
    res = pipeline.run(ctx)
    assert list(res["held"]) == [list(b.items)[1]] and "near-duplicate" in res["held"][list(b.items)[1]][0]


def test_one_failing_item_does_not_stop_the_batch(ws, small_cfg, monkeypatch):
    b, ctx = start(ws, small_cfg, n=2)
    feed(b, "vision.json", vision())
    feed(b, "copy.json", good_copy())
    real = upscale.upscale
    first = list(b.items)[0]

    def flaky(src, dst, *a, **k):
        if first in str(src):
            raise RuntimeError("GPU out of memory")
        return real(src, dst, *a, **k)
    monkeypatch.setattr(upscale, "upscale", flaky)
    res = pipeline.run(ctx)
    assert f"{first} at images" in res["errors"] and len(res["drafted"]) == 1


def test_review_page_and_report(ws, small_cfg):
    b, ctx = start(ws, small_cfg, n=1)
    feed(b, "vision.json", vision())
    feed(b, "copy.json", good_copy())
    pipeline.run(ctx)
    r = report.write(b)
    page = review_page.write(b, small_cfg, r).read_text(encoding="utf-8")
    assert r["dry_run_drafts"] == 1 and r["drafts_created"] == 0 and r["time"]["runs"] == 1
    assert "Nothing here is live" in page and good_copy()["tags"][0] in page
    assert (b.dir / "report.md").read_text(encoding="utf-8").startswith("# Run report")


@pytest.mark.slow
def test_demo_runs_end_to_end(tmp_path):
    res = demo.run(tmp_path / "demo")
    assert sorted(res["drafted"]) == ["good-morning", "sun-over-hills"]
    assert list(res["held"]) == ["mid-century-arches"] and res["errors"] == {}
    rep = json.loads((tmp_path / "demo/batches/demo/report.json").read_text())
    assert rep["passed_checks"] == 2 and rep["dry_run_drafts"] == 2
    assert (tmp_path / "demo/batches/demo/provider_export/sku_sheet.csv").exists()
