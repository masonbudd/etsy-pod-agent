"""Etsy client and drafts, against mocked responses shaped like the Open API v3 reference."""
import base64
import hashlib
import json
import urllib.parse
from datetime import datetime

import pytest
from PIL import Image

from conftest import etsy_client, good_copy, write_json
from podagent import pricing
from podagent.etsy import drafts, oauth
from podagent.etsy.client import PublishBlocked
from podagent.http import Recorder
from podagent.state import Batch


def test_pkce_challenge_is_s256_of_verifier():
    verifier, challenge = oauth.pkce_pair()
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected and 43 <= len(verifier) <= 128
    url = oauth.auth_url("key", "http://localhost:3456/callback", "st", challenge)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and "listings_w" in q["scope"][0]


def test_token_refreshes_only_when_expiring(token_file):
    rec = Recorder().add("POST", "/oauth/token", body={"access_token": "access-2", "refresh_token": "refresh-2", "expires_in": 3600})
    fresh = oauth.TokenStore(token_file, "key", transport=rec, clock=lambda: 2000)
    assert fresh.access_token() == "access-1" and rec.calls == []
    stale = oauth.TokenStore(token_file, "key", transport=rec, clock=lambda: 1000 + 3600)
    assert stale.access_token() == "access-2"
    assert b"grant_type=refresh_token" in rec.calls[0]["body"]
    assert json.loads(token_file.read_text())["refresh_token"] == "refresh-2"


def test_client_refuses_to_change_listing_state(token_file):
    rec = Recorder()
    client = etsy_client(token_file, rec)
    with pytest.raises(PublishBlocked):
        client.call("PATCH", "/shops/123/listings/9", form={"state": "active"})
    assert rec.calls == []


def test_create_is_never_retried_on_server_error(token_file):
    rec = Recorder().add("POST", "/shops/123/listings", 502, {"error": "bad gateway"})
    with pytest.raises(Exception):
        etsy_client(token_file, rec).create_draft(123, {"title": "x"})
    assert len(rec.calls) == 1


def test_reads_are_retried(token_file):
    rec = Recorder().add("GET", "/users/me", 503, {}).add("GET", "/users/me", 200, {"user_id": 1, "shop_id": 123})
    assert etsy_client(token_file, rec).me()["shop_id"] == 123


def make_ready_item(tmp_path, cfg):
    b = Batch.create(tmp_path / "batches" / "b1", "b1", "inbox")
    b.add_item("sun", "art-sun", "sun.png")
    d = b.item_dir("sun")
    write_json(d / "copy.json", good_copy())
    (d / "photos").mkdir()
    photos = []
    for n in (1, 2):
        Image.new("RGB", (40, 50), (200, 100, 50)).save(d / "photos" / f"0{n}.jpg")
        photos.append({"file": f"0{n}.jpg", "kind": "room", "alt": f"photo {n}"})
    b.mark("sun", "mockups", photos=photos)
    b.mark("sun", "prices", variants=drafts.variant_rows("art-sun", pricing.price_table(cfg), cfg))
    return b


def happy_routes():
    return (Recorder()
            .add("GET", "/shops/123/sections", body={"count": 0, "results": []})
            .add("POST", "/shops/123/sections", body={"shop_section_id": 55, "title": "Abstract Prints"})
            .add("POST", "/shops/123/listings", body={"listing_id": 999, "state": "draft"})
            .add("POST", "/listings/999/images", body={"listing_image_id": 1})
            .add("POST", "/listings/999/images", body={"listing_image_id": 2})
            .add("PUT", "/listings/999/inventory", body={"products": []}))


def test_draft_is_created_with_photos_and_inventory(tmp_path, cfg, token_file):
    b = make_ready_item(tmp_path, cfg)
    rec = happy_routes()
    rec_d = drafts.create(etsy_client(token_file, rec), b, "sun", cfg, {})
    assert rec_d["listing_id"] == 999 and rec_d["inventory"] and len(rec_d["images"]) == 2
    assert b.items["sun"]["status"] == "drafted"
    create = next(c for c in rec.calls if c["method"] == "POST" and c["url"].endswith("/shops/123/listings"))
    form = urllib.parse.parse_qs(create["body"].decode())
    assert "state" not in form and form["shop_section_id"] == ["55"] and form["readiness_state_id"] == ["33"]
    assert "AI image tools" in form["description"][0]
    inv = json.loads(next(c for c in rec.calls if c["method"] == "PUT")["body"])
    assert len(inv["products"]) == len(cfg["sizes"]) * 4 and inv["sku_on_property"] == [513, 514]
    off = [p for p in inv["products"] if not p["offerings"][0]["is_enabled"]]
    assert len(off) == 3 and all(p["offerings"][0]["quantity"] == 0 for p in off)
    assert all(len(p["sku"]) <= 32 for p in inv["products"])
    assert all("(" not in v for p in inv["products"] for pv in p["property_values"] for v in pv["values"])


def test_rerun_makes_no_requests(tmp_path, cfg, token_file):
    b = make_ready_item(tmp_path, cfg)
    drafts.create(etsy_client(token_file, happy_routes()), b, "sun", cfg, {})
    empty = Recorder()
    drafts.create(etsy_client(token_file, empty), b, "sun", cfg, {})
    assert empty.calls == []


def test_lost_draft_is_recovered_not_duplicated(tmp_path, cfg, token_file):
    b = make_ready_item(tmp_path, cfg)
    b.mark("sun", "draft", "running", requested_at="2026-11-04T10:00:00+00:00")   # crashed after the POST
    created = int(datetime.fromisoformat("2026-11-04T10:00:05+00:00").timestamp())
    rec = (Recorder()
           .add("GET", "/shops/123/listings?state=draft", body={"count": 1, "results": [
               {"listing_id": 777, "title": good_copy()["title"], "created_timestamp": created}]})
           .add("POST", "/listings/777/images", body={"listing_image_id": 1})
           .add("POST", "/listings/777/images", body={"listing_image_id": 2})
           .add("PUT", "/listings/777/inventory", body={}))
    out = drafts.create(etsy_client(token_file, rec), b, "sun", cfg, {})
    assert out["listing_id"] == 777 and out["recovered"]
    assert not any(c["method"] == "POST" and c["url"].endswith("/shops/123/listings") for c in rec.calls)


def test_dry_run_writes_the_payload_and_calls_nothing(tmp_path, cfg):
    b = make_ready_item(tmp_path, cfg)
    cfg["etsy"]["shipping_profile_id"] = 0
    out = drafts.create(None, b, "sun", cfg, {}, dry_run=True)
    payload = json.loads((b.item_dir("sun") / "etsy_payload.json").read_text(encoding="utf-8"))
    assert out["dry_run"] and payload["createDraftListing"]["shipping_profile_id"].startswith("<set by")
    assert len(payload["uploadListingImage"]) == 2


def test_disclosure_tasks_list_drafts_without_disclosure(tmp_path, cfg, token_file):
    b = make_ready_item(tmp_path, cfg)
    drafts.create(etsy_client(token_file, happy_routes()), b, "sun", cfg, {})
    tasks = drafts.write_disclosure_tasks(b, tmp_path / "tasks.md")
    assert tasks[0]["listing_id"] == 999 and "Never click Publish" in (tmp_path / "tasks.md").read_text(encoding="utf-8")
    b.mark("sun", "disclosure")
    assert drafts.disclosure_tasks(b) == []
