import numpy as np
import pytest
from PIL import Image, ImageDraw

from conftest import SAMPLES, art, vision
from podagent import checks
from podagent.images import calibrate, crops, mockups


def noisy(img, sd=4):
    a = np.asarray(img).astype(float) + np.random.default_rng(1).normal(0, sd, (img.height, img.width, 1))
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def test_border_is_found_on_a_bordered_design():
    r = checks.border(noisy(art(border=40)), 1.0)
    assert r["found"] and set(r["crop_px"]) == {"top", "bottom", "left", "right"}


def test_full_bleed_design_has_no_border():
    assert not checks.border(noisy(art()), 1.0)["found"]


def test_plain_background_with_a_motif_is_not_a_border():
    img = Image.new("RGB", (512, 768), (236, 190, 90))
    ImageDraw.Draw(img).ellipse([150, 250, 360, 460], fill=(222, 112, 72))
    assert not checks.border(noisy(img), 1.0)["found"]


def test_sample_designs_border_results():
    assert checks.border(Image.open(SAMPLES / "designs/mid-century-arches.png"), 1.0)["found"]
    assert not checks.border(Image.open(SAMPLES / "designs/sun-over-hills.png"), 1.0)["found"]
    assert not checks.border(Image.open(SAMPLES / "designs/good-morning.png"), 1.0)["found"]


def test_trim_removes_the_border():
    img = art(border=40)
    trimmed = checks.trim_border(img, checks.border(img, 1.0))
    assert not checks.border(trimmed, 1.0)["found"]
    assert trimmed.width < img.width - 70


def test_resolution_against_every_size(cfg):
    ok = checks.resolution(1024, 1536, cfg["sizes"], 4, 150)
    assert ok["ok"]
    small = checks.resolution(1024, 1536, cfg["sizes"], 2, 150)
    assert not small["ok"] and "24x36" in small["too_small"]


def test_crop_warnings(cfg):
    warns = checks.crop_warnings(1024, 1536, cfg["sizes"], 15)
    assert any(w.startswith("4:5") for w in warns)
    assert not any(w.startswith("2:3") for w in warns)


def test_near_duplicates_by_perceptual_hash():
    a, b = art(seed=1), art(seed=1).point(lambda v: min(255, v + 6))
    c = art(seed=3)
    ha = checks.dhash(a)
    assert checks.hamming(ha, checks.dhash(b)) <= 4
    assert checks.duplicates(ha, {"b": checks.dhash(b), "c": checks.dhash(c)}, 10) == ["b (distance %d)" % checks.hamming(ha, checks.dhash(b))]


def test_vision_schema():
    assert checks.validate_vision(vision()) == []
    v = vision()
    del v["subject"]
    v["ip_ok"] = "yes"
    errors = checks.validate_vision(v)
    assert "missing subject" in errors and "ip_ok should be bool" in errors


def test_verdict_merges_code_and_review(cfg):
    code = checks.code_checks(art(size=(1024, 1536)), "sun.png", cfg, {})
    assert checks.verdict(code, vision(), cfg)["verdict"] == "pass"
    held = checks.verdict(code, vision(lettering="GOOD MORNIG", spelling_ok=False, spelling_notes="MORNIG"), cfg)
    assert held["verdict"] == "hold" and any("spelling" in r for r in held["reasons"])
    ip = checks.verdict(code, vision(lettering="Star Wars fan"), cfg)
    assert any("banned term" in r for r in ip["reasons"])


def test_landscape_designs_are_held(cfg):
    code = checks.code_checks(art(size=(1536, 1024)), "wide.png", cfg, {})
    assert any("landscape" in r for r in checks.verdict(code, vision(), cfg)["reasons"])


def test_crop_ratio_and_groups(cfg):
    img = Image.new("RGB", (1000, 1500))
    assert crops.crop_ratio(img, 0.8).size == (1000, 1250)
    assert crops.crop_ratio(img, 0.5).size == (750, 1500)
    groups = crops.ratio_groups(cfg)
    assert set(groups) == {"5x7", "4x5", "iso", "3x4", "2x3"}
    assert groups["iso"]["sizes"] == ["a4", "a3", "a2"]


def test_calibration_finds_the_sample_frames():
    expected = {"living-room.jpg": (540, 340, 1060, 990), "sideboard.jpg": (404, 640, 854, 1240), "bedroom.jpg": (580, 380, 1020, 1040)}
    for name, (x0, y0, x1, y1) in expected.items():
        q = calibrate.detect_frame(Image.open(SAMPLES / "rooms" / name))
        want = np.array([[x0, y0], [x1, y0], [x1, y1], [x0, y1]], float)
        assert np.abs(q - want).max() < 4, name


def test_calibration_handles_a_frame_seen_at_an_angle():
    img = Image.new("RGB", (1000, 1000), (180, 120, 100))
    d = ImageDraw.Draw(img)
    panel = [(300, 200), (700, 240), (690, 760), (310, 730)]
    d.polygon([(270, 170), (730, 210), (720, 790), (280, 760)], fill=(40, 34, 30))
    d.polygon(panel, fill=(204, 204, 206))
    q = calibrate.detect_frame(img)
    assert np.abs(q - np.array(panel, float)).max() < 5
    assert not calibrate.fits_square_thumbnail(np.array([[0, -10], [10, 0], [10, 10], [0, 10]], float), (100, 200))


def test_homography_maps_corners():
    src = [(0, 0), (100, 0), (100, 150), (0, 150)]
    dst = [(10, 20), (120, 25), (115, 180), (8, 170)]
    h = mockups.homography(src, dst)
    H = np.append(h, 1).reshape(3, 3)
    for (x, y), (u, v) in zip(src, dst):
        p = H @ [x, y, 1]
        assert p[0] / p[2] == pytest.approx(u) and p[1] / p[2] == pytest.approx(v)


def test_mockup_puts_the_art_inside_the_frame(ws, cfg):
    rooms = mockups.load_rooms(ws.path(cfg, "rooms"))
    red = Image.new("RGB", (400, 600), (220, 30, 30))
    out = mockups.room_mockup(rooms[0], red, width=1600)
    cx, cy = rooms[0].corners.mean(0)
    r, g, b = out.getpixel((int(cx), int(cy)))
    assert r > 150 and g < 80 and b < 80
