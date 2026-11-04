"""Provider adapters. Prodigi responses follow its v5 OpenAPI reference."""
import csv
import json

import pytest
from PIL import Image

from podagent import pricing, providers
from podagent.etsy import drafts
from podagent.http import Recorder
from podagent.images.crops import ratio_groups
from podagent.providers.base import ItemContext
from podagent.providers.browser import upload_calls


@pytest.fixture
def ctx(tmp_path, cfg):
    files = {}
    for key in ratio_groups(cfg):
        p = tmp_path / "print" / f"art-sun_{key}.jpg"
        p.parent.mkdir(exist_ok=True)
        Image.new("RGB", (60, 80), (10, 20, 30)).save(p)
        files[key] = p
    variants = drafts.variant_rows("art-sun", pricing.price_table(cfg), cfg)
    return ItemContext("b1", "sun", "art-sun", variants, files, tmp_path)


def test_local_export_groups_files_and_writes_sku_sheet(cfg, ctx):
    res = providers.get(cfg, {}, name="local_export").prepare(ctx)
    out = ctx.batch_dir / "provider_export"
    assert res.status == "needs_action" and (out / "iso" / "art-sun.jpg").exists()
    rows = list(csv.DictReader(open(out / "sku_sheet.csv", encoding="utf-8")))
    assert len(rows) == len(cfg["sizes"]) * 4
    assert {r["sku"] for r in rows if r["size"].startswith("A4")} == {"art-sun-a4-p", "art-sun-a4-b", "art-sun-a4-w", "art-sun-a4-o"}
    providers.get(cfg, {}, name="local_export").prepare(ctx)              # rerun replaces, never duplicates rows
    assert len(list(csv.DictReader(open(out / "sku_sheet.csv", encoding="utf-8")))) == len(rows)


def test_browser_adapter_splits_uploads_under_the_limit(tmp_path):
    for n in range(5):
        (tmp_path / f"f{n}.jpg").write_bytes(b"x" * 4_000_000)
    calls = upload_calls(tmp_path, [f"f{n}.jpg" for n in range(5)])
    assert [len(c) for c in calls] == [2, 2, 1]


def test_browser_adapter_writes_tasks(cfg, ctx, tmp_path):
    pb = tmp_path / "playbook.md"
    pb.write_text("1. Upload [ASK] Create products", encoding="utf-8")
    cfg["provider"]["browser_playbook"] = str(pb)
    prov = providers.get(cfg, {}, name="browser")
    assert all(c.ok for c in prov.check())
    prov.prepare(ctx)
    tasks = json.loads((ctx.batch_dir / "provider_export" / "browser_tasks.json").read_text())
    assert "[ASK]" in tasks["rule"] and set(tasks["groups"]) == set(ratio_groups(cfg))


def prodigi_cfg(cfg):
    p = cfg["provider"]["prodigi"]
    p["sales_channel_id"] = "ch1"
    p["catalogue"] = {s["code"]: [f"GLOBAL-FAP-{s['code'].upper()}", f"GLOBAL-CFPM-{s['code'].upper()}"] for s in cfg["sizes"]}
    return cfg


def upload_routes(rec, n):
    for i in range(n):
        rec.add("POST", "/v5/assets", body={"data": None, "uploadUrl": f"https://api.prodigi.com/v5/assets/uploads/t{i}"})
        rec.add("PUT", f"/assets/uploads/t{i}", body={"data": {"id": f"a{i}", "url": f"https://files.example.com/a{i}.jpg", "status": "ready"}})
    return rec


def test_prodigi_uploads_files_once_and_plans_mappings(cfg, ctx):
    cfg = prodigi_cfg(cfg)
    rec = upload_routes(Recorder(), len(ctx.print_files))
    prov = providers.get(cfg, {"PRINT_PROVIDER_API_KEY": "sandbox_abc"}, transport=rec, name="prodigi")
    prov.sleep = lambda s: None
    res = prov.prepare(ctx)
    assert res.status == "needs_action" and all(c["headers"]["X-API-Key"] == "sandbox_abc" for c in rec.calls)
    plan = res.data["plan"]
    assert len(plan) == pricing.summary(pricing.price_table(cfg))["enabled"]
    framed = next(r for r in plan if r["sku"] == "art-sun-16x20-b")
    assert framed["catalogueVariantId"] == "GLOBAL-CFPM-16X20" and framed["attributes"] == {"framecolour": "black"}
    assert next(r for r in plan if r["sku"] == "art-sun-16x20-p")["attributes"] == {}
    again = Recorder()
    providers.get(cfg, {"PRINT_PROVIDER_API_KEY": "sandbox_abc"}, transport=again, name="prodigi").prepare(ctx)
    assert again.calls == []


def test_prodigi_reports_sizes_without_a_sku(cfg, ctx):
    prov = providers.get(cfg, {"PRINT_PROVIDER_API_KEY": "sandbox_abc"}, name="prodigi")
    checks = {c.name: c for c in prov.check()}
    assert not checks["Prodigi SKUs for every size"].ok and "8x10" in checks["Prodigi SKUs for every size"].detail
    assert not checks["Prodigi sales channel"].ok


def test_prodigi_link_matches_synced_variants_by_sku(cfg, ctx):
    cfg = prodigi_cfg(cfg)
    plan = [{"sku": "art-sun-a4-b", "catalogueVariantId": "GLOBAL-CFPM-A4", "attributes": {"framecolour": "black"}},
            {"sku": "art-sun-a4-p", "catalogueVariantId": "GLOBAL-FAP-A4", "attributes": {}}]
    ctx.listing_id = 999
    rec = (Recorder()
           .add("GET", "/sales-channels/ch1/products", body={"data": [{"externalId": "999", "variants": [
               {"externalId": "v1", "sku": "art-sun-a4-b"}, {"externalId": "v2", "sku": "art-sun-a4-p"}]}], "limit": 100})
           .add("GET", "/sales-channels/ch1/products", body={"data": []})
           .add("GET", "/sales-channels/ch1/mappings", body={"data": [{"externalVariantId": "v2"}], "limit": 100})
           .add("GET", "/sales-channels/ch1/mappings", body={"data": []})
           .add("POST", "/sales-channels/ch1/mappings", 201, {"data": {"id": "m1"}})
           .add("GET", "/sales-channels/ch1/rules", body={"data": [{"id": "r1", "storeProductId": "999", "enabled": False}]})
           .add("PATCH", "/sales-channels/ch1/rules/r1", body={"data": {"id": "r1", "enabled": True}}))
    prov = providers.get(cfg, {"PRINT_PROVIDER_API_KEY": "live_abc"}, transport=rec, name="prodigi")
    res = prov.link(ctx, plan)
    posts = [c for c in rec.calls if c["method"] == "POST"]
    assert len(posts) == 1 and json.loads(posts[0]["body"])["externalVariantId"] == "v1"
    assert posts[0]["headers"]["Idempotency-Key"] == "999-v1"
    patch = next(c for c in rec.calls if c["method"] == "PATCH")
    assert json.loads(patch["body"]) == {"enabled": True} and res.status == "done"


def test_prodigi_link_waits_for_the_sync(cfg, ctx):
    cfg = prodigi_cfg(cfg)
    ctx.listing_id = 999
    rec = Recorder().add("GET", "/sales-channels/ch1/products", body={"data": []})
    res = providers.get(cfg, {"PRINT_PROVIDER_API_KEY": "live_abc"}, transport=rec, name="prodigi").link(ctx, [])
    assert res.status == "needs_action" and "not synced" in res.detail
