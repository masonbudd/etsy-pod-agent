"""Generate room templates for mockups, then calibrate and let the user approve each one.

Each room is generated with one empty frame filled with flat light grey of a known ratio.
Calibration finds the frame and checks the shape. Candidates wait in rooms/candidates/
until the user approves (moved into rooms/ and added to rooms.toml) or rejects them.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from PIL import Image

from podagent import costs, rules
from podagent.config import parse_ratio
from podagent.images import calibrate

DEFAULT_PROMPT = ("Interior photograph of {scene}. One empty {frame} picture frame in portrait orientation, aspect ratio {ratio}, "
                  "is the focal point and is fully visible near the centre of the image. The inside of the frame is completely "
                  "filled with flat, even, plain light grey (#CCCCCC): no picture, texture, text, glass glare or mount. "
                  "Soft natural light, realistic, uncluttered, no people.")


def candidates_file(rooms_dir: Path) -> Path:
    return rooms_dir / "candidates" / "candidates.json"


def _load(rooms_dir: Path) -> dict:
    p = candidates_file(rooms_dir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"candidates": {}}


def _save(rooms_dir: Path, data: dict) -> None:
    p = candidates_file(rooms_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")


def plan(cfg: dict, count: int) -> list[dict]:
    rc = cfg.get("rooms", {})
    scenes, frames, ratios = rc.get("scenes", []), rc.get("frames", ["black"]), rc.get("ratios", ["4:5"])
    if not scenes:
        raise ValueError("add [rooms] scenes to config.toml")
    out = []
    for i in range(count):
        scene, frame, ratio = scenes[i % len(scenes)], frames[i % len(frames)], ratios[i % len(ratios)]
        out.append({"name": rules.slugify(f"{scene} {frame}", 40), "scene": scene, "frame": frame, "ratio": ratio,
                    "prompt": rc.get("prompt", DEFAULT_PROMPT).format(scene=scene, frame=frame, ratio=ratio)})
    return out


def estimate(cfg: dict, count: int) -> dict:
    return costs.estimate(cfg, count, retries_per_image=0)


def generate(rooms_dir: Path, cfg: dict, client, count: int) -> list[dict]:
    """Generate and calibrate candidates. Existing candidates are never regenerated."""
    data = _load(rooms_dir)
    price = costs.image_price(cfg) or 0.0
    out = []
    for p in plan(cfg, count):
        name = p["name"]
        if name in data["candidates"]:
            out.append(data["candidates"][name])
            continue
        png, _ = client.generate(p["prompt"])
        path = rooms_dir / "candidates" / f"{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
        rec = {**p, "file": path.name, "cost_usd": price, "status": "pending", **check(path, p["ratio"])}
        data["candidates"][name] = rec
        _save(rooms_dir, data)
        out.append(rec)
    return out


def regenerate(rooms_dir: Path, cfg: dict, client, name: str) -> dict:
    """Reject a candidate and generate a fresh one from the same scene, frame and ratio."""
    data = _load(rooms_dir)
    old = data["candidates"].get(name)
    if not old:
        raise KeyError(f"no room candidate {name!r}")
    old["status"] = "rejected"
    base, n = old["name"].rsplit("-v", 1)[0], 2
    while f"{base}-v{n}" in data["candidates"]:
        n += 1
    new_name = f"{base}-v{n}"
    png, _ = client.generate(old["prompt"])
    path = rooms_dir / "candidates" / f"{new_name}.png"
    path.write_bytes(png)
    rec = {**{k: old[k] for k in ("scene", "frame", "ratio", "prompt")}, "name": new_name, "file": path.name,
           "cost_usd": costs.image_price(cfg) or 0.0, "status": "pending", **check(path, old["ratio"])}
    data["candidates"][new_name] = rec
    _save(rooms_dir, data)
    return rec


def check(path: Path, ratio: str) -> dict:
    """Find the frame and compare it with the ratio that was asked for."""
    img = Image.open(path).convert("RGB")
    try:
        q = calibrate.detect_frame(img)
    except ValueError as e:
        return {"frame_found": False, "warnings": [str(e)]}
    got, want = calibrate.measured_ratio(q), parse_ratio(ratio)
    warnings = []
    if abs(got / want - 1) > calibrate.RATIO_TOLERANCE:
        warnings.append(f"frame measures {got:.3f}; asked for {ratio} ({want:.3f})")
    if not calibrate.fits_square_thumbnail(q, img.size):
        warnings.append("frame doesn't fit Etsy's square thumbnail; fine for later photos, not the main one")
    return {"frame_found": True, "ratio_measured": round(float(got), 4), "warnings": warnings}


def approve(rooms_dir: Path, name: str, alt: str) -> dict:
    data = _load(rooms_dir)
    rec = data["candidates"].get(name)
    if not rec:
        raise KeyError(f"no room candidate {name!r}")
    if not rec.get("frame_found"):
        raise ValueError(f"{name}: no frame was found, so it can't be used for mockups")
    shutil.copy2(rooms_dir / "candidates" / rec["file"], rooms_dir / rec["file"])
    with open(rooms_dir / "rooms.toml", "a", encoding="utf-8") as fh:
        fh.write(f'\n[[room]]\nfile = "{rec["file"]}"\nalt = "{alt.replace(chr(34), chr(39))}"\nratio = "{rec["ratio"]}"\n')
    rec["status"] = "approved"
    _save(rooms_dir, data)
    calibrate.calibrate_rooms(rooms_dir)
    return rec


def reject(rooms_dir: Path, name: str) -> dict:
    data = _load(rooms_dir)
    if name not in data["candidates"]:
        raise KeyError(f"no room candidate {name!r}")
    data["candidates"][name]["status"] = "rejected"
    _save(rooms_dir, data)
    return data["candidates"][name]
