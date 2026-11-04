"""Composite a design into calibrated room templates, keeping the room's light and shadow."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from podagent.config import parse_ratio
from podagent.images.calibrate import load_room_list
from podagent.images.crops import crop_ratio

OUT_WIDTH = 2000


@dataclass
class Room:
    file: str
    path: Path
    alt: str
    ratio: str
    corners: np.ndarray       # TL, TR, BR, BL in the room image's pixels
    ratio_measured: float


def load_rooms(rooms_dir: Path) -> list[Room]:
    cal_path = rooms_dir / "corners.json"
    if not cal_path.exists():
        raise FileNotFoundError(f"{cal_path} missing: run `podagent rooms-calibrate`")
    cal = json.loads(cal_path.read_text(encoding="utf-8"))
    rooms = []
    for r in load_room_list(rooms_dir):
        if r["file"] not in cal:
            raise KeyError(f"{r['file']} is not calibrated: run `podagent rooms-calibrate`")
        c = cal[r["file"]]
        rooms.append(Room(r["file"], rooms_dir / r["file"], r["alt"], r["ratio"], np.array(c["corners"], float),
                          c["ratio_measured"]))
    return rooms


def homography(src, dst) -> np.ndarray:
    A, b = [], []
    for (x, y), (u, v) in zip(src, dst):
        A.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); b.append(u)
        A.append([0, 0, 0, x, y, 1, -v * x, -v * y]); b.append(v)
    return np.linalg.solve(np.array(A, float), np.array(b, float))


def poly_mask(size: tuple[int, int], quad, ss: int = 4) -> Image.Image:
    """Anti-aliased polygon mask, drawn at ss times the size then reduced."""
    W, H = size
    m = Image.new("L", (W * ss, H * ss), 0)
    ImageDraw.Draw(m).polygon([(x * ss, y * ss) for x, y in quad], fill=255)
    return m.resize((W, H), Image.LANCZOS)


def place(room: Image.Image, quad, art: Image.Image, light_strength: float = 1.0, cast_strength: float = 0.5) -> Image.Image:
    """Warp art into quad and multiply it by the blank panel's light, so shadows and gradients carry over."""
    q = np.array(quad, float)
    x0, y0 = np.maximum(np.floor(q.min(0)).astype(int) - 2, 0)
    x1, y1 = np.minimum(np.ceil(q.max(0)).astype(int) + 2, room.size)
    bw, bh = x1 - x0, y1 - y0
    ql = q - [x0, y0]
    tw = int(max(np.linalg.norm(ql[1] - ql[0]), np.linalg.norm(ql[2] - ql[3])) * 1.15)
    th = int(max(np.linalg.norm(ql[3] - ql[0]), np.linalg.norm(ql[2] - ql[1])) * 1.15)
    coeffs = homography(ql, [(0, 0), (tw, 0), (tw, th), (0, th)])
    warped = art.resize((tw, th), Image.LANCZOS).transform((bw, bh), Image.PERSPECTIVE, tuple(coeffs), Image.BICUBIC)
    m = np.asarray(poly_mask((bw, bh), ql)).astype(float) / 255
    patch = np.asarray(room.crop((x0, y0, x1, y1))).astype(float)
    inside = m > 0.99
    lum = np.asarray(Image.fromarray(patch.mean(2).astype(np.uint8)).filter(ImageFilter.GaussianBlur(2))).astype(float)
    ref = np.percentile(lum[inside], 92) if inside.any() else 200
    light = 1 + (np.clip(lum / ref, 0.5, 1.1) - 1) * light_strength
    cast = patch[inside].mean(0) / patch[inside].mean() if inside.any() else np.ones(3)
    cast = 1 + (cast - 1) * cast_strength
    lit = np.asarray(warped).astype(float) * light[..., None] * cast[None, None, :]
    out = patch * (1 - m[..., None]) + np.clip(lit, 0, 255) * m[..., None]
    room = room.copy()
    room.paste(Image.fromarray(out.astype(np.uint8)), (int(x0), int(y0)))
    return room


def room_mockup(room: Room, art: Image.Image, width: int = OUT_WIDTH) -> Image.Image:
    base = Image.open(room.path).convert("RGB")
    framed = crop_ratio(art, room.ratio_measured or parse_ratio(room.ratio))
    longest = int(max(base.size) * 0.8)
    framed.thumbnail((longest, longest), Image.LANCZOS)          # no need to warp a 25 MP master
    out = place(base, room.corners, framed)
    if out.width != width:
        out = out.resize((width, round(out.height * width / out.width)), Image.LANCZOS)
    return out


def detail_shot(art: Image.Image, width: int = OUT_WIDTH) -> Image.Image:
    """Close-up of the centre of the design, so buyers can see texture and detail."""
    w, h = art.size
    cw, ch = int(w * 0.55), int(w * 0.55 * 1.25)
    ch = min(ch, h)
    x, y = (w - cw) // 2, (h - ch) // 2
    crop = art.crop((x, y, x + cw, y + ch))
    return crop.resize((width, round(width * ch / cw)), Image.LANCZOS)


def build(master: Path, out_dir: Path, rooms: list[Room], subject: str, max_photos: int) -> list[dict]:
    """Room photos in rooms.toml order (the first is the listing's main photo), then a detail shot."""
    out_dir.mkdir(parents=True, exist_ok=True)
    art, photos = None, []

    def master_art() -> Image.Image:
        nonlocal art
        if art is None:
            art = Image.open(master).convert("RGB")
        return art

    for n, room in enumerate(rooms[: max_photos - 1], 1):
        out = out_dir / f"{n:02d}_{Path(room.file).stem}.jpg"
        if not out.exists():
            room_mockup(room, master_art()).save(out, "JPEG", quality=90)
        photos.append({"file": out.name, "kind": "room", "alt": f"{subject}, shown {room.alt}"[:250]})
    out = out_dir / f"{len(photos) + 1:02d}_detail.jpg"
    if not out.exists():
        detail_shot(master_art()).save(out, "JPEG", quality=90)
    photos.append({"file": out.name, "kind": "detail", "alt": f"Close-up detail of {subject}"[:250]})
    return photos
