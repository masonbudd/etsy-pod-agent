"""Image generation and room templates against mocked OpenAI Images API responses."""
import base64
import io
import json
import shutil

import pytest
from PIL import Image

from conftest import SAMPLES, art, vision, write_json
from podagent import concepts, generate, pipeline, rooms_gen
from podagent.images import samples
from podagent.http import HttpError, Recorder
from podagent.providers.local_export import LocalExport

API = "/v1/images/generations"


def png_bytes(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def openai_ok(rec, img, n=1):
    for _ in range(n):
        rec.add("POST", API, body={"created": 1, "data": [{"b64_json": base64.b64encode(png_bytes(img)).decode()}],
                                   "usage": {"total_tokens": 1056}})
    return rec


def client(rec, cfg):
    return generate.ImageClient("test-key", cfg, transport=rec, sleep=lambda s: None)


def gen_batch(ws, cfg, ids, approve=None, reject=()):
    b = pipeline.new_generate_batch(ws, cfg, count=len(ids))
    write_json(b.dir / "concepts.json", {"concepts": [
        {"id": i, "title": i.replace("-", " "), "target_keyword": "kw", "why": "w", "prompt": f"prompt for {i}",
         **({"lettering": "HELLO"} if i == "lettered" else {})} for i in ids]})
    concepts.load_into_state(b, cfg, concepts.load_file(b))
    concepts.decide(b, approve=list(ids if approve is None else approve), reject=list(reject))
    return b


def test_only_approved_concepts_are_generated(ws, small_cfg):
    b = gen_batch(ws, small_cfg, ["a", "b", "c"], approve=["a"], reject=["b"])
    rec = openai_ok(Recorder(), art(seed=1))
    res = generate.run(b, small_cfg, client(rec, small_cfg), set())
    assert res["generated"] == ["a"] and list(b.items) == ["a"] and len(rec.calls) == 1
    body = json.loads(rec.calls[0]["body"])
    assert body["model"] == "gpt-image-2" and body["size"] == "1024x1536" and body["quality"] == "medium"
    assert body["prompt"].startswith("prompt for a") and "No border" in body["prompt"]
    assert rec.calls[0]["headers"]["Authorization"] == "Bearer test-key"
    assert b.data["costs"][0]["usd"] == 0.041 and (b.item_dir("a") / "source.png").exists()


def test_rerun_never_pays_twice(ws, small_cfg):
    b = gen_batch(ws, small_cfg, ["a"])
    generate.run(b, small_cfg, client(openai_ok(Recorder(), art()), small_cfg), set())
    empty = Recorder()
    generate.run(b, small_cfg, client(empty, small_cfg), set())
    assert empty.calls == [] and len(b.data["costs"]) == 1


def test_lost_paid_request_needs_explicit_retry(ws, small_cfg):
    b = gen_batch(ws, small_cfg, ["a"])
    b.data["concepts"][0]["requested_at"] = "2026-11-04T10:00:00+00:00"   # sent, then the run died
    empty = Recorder()
    res = generate.run(b, small_cfg, client(empty, small_cfg), set())
    assert empty.calls == [] and "retry-unsaved" in res["skipped"][0]
    res = generate.run(b, small_cfg, client(openai_ok(Recorder(), art()), small_cfg), set(), retry_unsaved=True)
    assert res["generated"] == ["a"]


def test_batch_budget_is_a_hard_stop(ws, small_cfg):
    small_cfg["images"]["max_batch_usd"] = 0.05
    b = gen_batch(ws, small_cfg, ["a", "b"])
    rec = openai_ok(Recorder(), art(), n=2)
    res = generate.run(b, small_cfg, client(rec, small_cfg), set())
    assert res["generated"] == ["a"] and "budget" in res["skipped"][0] and len(rec.calls) == 1


def test_create_request_is_not_retried_on_server_error(ws, small_cfg):
    b = gen_batch(ws, small_cfg, ["a"])
    rec = Recorder().add("POST", API, 500, {"error": {"message": "server error"}})
    with pytest.raises(HttpError):
        generate.run(b, small_cfg, client(rec, small_cfg), set())
    assert len(rec.calls) == 1


def test_url_responses_are_downloaded(cfg):
    rec = Recorder().add("POST", API, body={"data": [{"url": "https://files.example.com/x.png"}]})
    rec.add("GET", "files.example.com/x.png", body=png_bytes(art()))
    data, _ = client(rec, cfg).generate("p")
    assert Image.open(io.BytesIO(data)).size == (768, 1152)


def run_checks(ws, cfg, b, vision_by_item):
    for item, v in vision_by_item.items():
        write_json(b.item_dir(item) / "vision.json", v)
    ctx = pipeline.Context(ws, cfg, {}, b, dry_run=True, provider=LocalExport(cfg, {}))
    return pipeline.run(ctx)


def test_held_design_is_regenerated_once_with_the_fix(ws, small_cfg):
    b = gen_batch(ws, small_cfg, ["framed"])
    generate.run(b, small_cfg, client(openai_ok(Recorder(), art(border=60)), small_cfg), set())
    res = run_checks(ws, small_cfg, b, {"framed": vision()})
    assert "framed" in res["held"]

    rec = openai_ok(Recorder(), art(seed=2))
    out = generate.run(b, small_cfg, client(rec, small_cfg), set())
    assert out["regenerated"] == ["framed"] and "no border, margin" in json.loads(rec.calls[0]["body"])["prompt"]
    assert b.items["framed"]["status"] == "active" and (b.item_dir("framed") / "source_attempt1.png").exists()
    assert not (b.item_dir("framed") / "vision.json").exists()                     # Claude reviews the new image

    res = run_checks(ws, small_cfg, b, {"framed": vision(border_seen=True)})        # fails again
    assert "framed" in res["held"]
    again = Recorder()
    assert generate.run(b, small_cfg, client(again, small_cfg), set())["regenerated"] == [] and again.calls == []


def test_lettering_must_match_the_concept(ws, small_cfg):
    b = gen_batch(ws, small_cfg, ["lettered"])
    generate.run(b, small_cfg, client(openai_ok(Recorder(), art()), small_cfg), set())
    res = run_checks(ws, small_cfg, b, {"lettered": vision(lettering="HELO")})
    assert "lettering reads 'HELO'" in res["held"]["lettered"][0]
    rec = openai_ok(Recorder(), art(seed=3))
    generate.run(b, small_cfg, client(rec, small_cfg), set())
    assert 'The only text is "HELLO"' in json.loads(rec.calls[0]["body"])["prompt"]


def test_near_duplicates_are_not_regenerated(ws, small_cfg):
    b = gen_batch(ws, small_cfg, ["a", "b"])
    generate.run(b, small_cfg, client(openai_ok(Recorder(), art(seed=1), n=2), small_cfg), set())
    res = run_checks(ws, small_cfg, b, {"a": vision(), "b": vision()})
    assert list(res["held"]) == ["b"]
    empty = Recorder()
    assert generate.run(b, small_cfg, client(empty, small_cfg), set())["regenerated"] == [] and empty.calls == []


@pytest.fixture
def rooms_dir(tmp_path):
    d = tmp_path / "rooms"
    shutil.copytree(SAMPLES / "rooms", d)
    return d


def test_room_plan_cycles_scenes_frames_and_ratios(cfg):
    plan = rooms_gen.plan(cfg, 4)
    assert [p["ratio"] for p in plan] == ["4:5", "3:4", "2:3", "4:5"]
    assert "plain light grey" in plan[0]["prompt"] and "aspect ratio 4:5" in plan[0]["prompt"]
    assert rooms_gen.estimate(cfg, 3)["expected_usd"] == pytest.approx(0.123)


def test_generated_rooms_are_calibrated_and_wait_for_approval(rooms_dir, cfg):
    room = png_bytes(samples.living_room())
    rec = Recorder()
    for _ in range(2):
        rec.add("POST", API, body={"data": [{"b64_json": base64.b64encode(room).decode()}]})
    out = rooms_gen.generate(rooms_dir, cfg, client(rec, cfg), 2)
    assert out[0]["frame_found"] and out[0]["warnings"] == [] and out[0]["status"] == "pending"
    assert any("asked for 3:4" in w for w in out[1]["warnings"])          # a 4:5 frame where 3:4 was wanted
    assert rooms_gen.generate(rooms_dir, cfg, client(Recorder(), cfg), 2)[0]["name"] == out[0]["name"]   # no new spend

    rooms_gen.approve(rooms_dir, out[0]["name"], "in a black frame in a calm living room")
    assert f'file = "{out[0]["file"]}"' in (rooms_dir / "rooms.toml").read_text(encoding="utf-8")
    assert out[0]["file"] in json.loads((rooms_dir / "corners.json").read_text())

    rooms_gen.reject(rooms_dir, out[1]["name"])
    rec2 = Recorder().add("POST", API, body={"data": [{"b64_json": base64.b64encode(room).decode()}]})
    new = rooms_gen.regenerate(rooms_dir, cfg, client(rec2, cfg), out[1]["name"])
    assert new["name"].endswith("-v2") and new["status"] == "pending"


def test_a_room_without_a_frame_cannot_be_approved(rooms_dir, cfg):
    blank = png_bytes(Image.new("RGB", (1024, 1536), (180, 120, 100)))
    rec = Recorder().add("POST", API, body={"data": [{"b64_json": base64.b64encode(blank).decode()}]})
    rec_out = rooms_gen.generate(rooms_dir, cfg, client(rec, cfg), 1)[0]
    assert not rec_out["frame_found"]
    with pytest.raises(ValueError):
        rooms_gen.approve(rooms_dir, rec_out["name"], "alt")
