"""Find the blank frame in a room template and record its four corners.

Room templates show an empty frame filled with plain light grey. Calibration:
1. finds the largest neutral-grey blob on a downscaled copy,
2. grows it into the whole panel and fits the largest four-sided shape to it,
3. refines each edge at full size by finding where grey meets the frame,
4. checks the measured shape against the ratio declared in rooms.toml.
"""
from __future__ import annotations

import json
import math
import tomllib
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from podagent.config import parse_ratio

WORK_WIDTH = 400
RATIO_TOLERANCE = 0.03


def neutral_mask(a: np.ndarray) -> np.ndarray:
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    lum = a.mean(2)
    return (np.abs(R - G) < 9) & (B >= R - 4) & (B - R < 22) & (lum > 150) & (lum < 236)


def largest_blob_seed(mask: np.ndarray) -> tuple[int, int] | None:
    """Centre-most pixel of the largest 4-connected blob in mask."""
    H, W = mask.shape
    seen = np.zeros_like(mask, bool)
    best_n, best_pixels = 0, None
    for y, x in zip(*np.nonzero(mask)):
        if seen[y, x]:
            continue
        q, pixels = deque([(y, x)]), []
        seen[y, x] = True
        while q:
            cy, cx = q.popleft()
            pixels.append((cy, cx))
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < H and 0 <= nx < W and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    q.append((ny, nx))
        if len(pixels) > best_n:
            best_n, best_pixels = len(pixels), pixels
    if not best_pixels:
        return None
    pts = np.array(best_pixels)
    c = pts.mean(0)
    return tuple(pts[np.argmin(((pts - c) ** 2).sum(1))])


def grow(a: np.ndarray, seed: tuple[int, int], tol: float = 7.0) -> np.ndarray:
    """Flood from seed through light, low-saturation pixels whose colour changes gradually."""
    H, W, _ = a.shape
    ok = ((a.max(2) - a.min(2)) < 34) & (a.mean(2) > 110)
    seen = np.zeros((H, W), bool)
    seen[seed] = True
    q = deque([seed])
    while q:
        y, x = q.popleft()
        c = a[y, x]
        for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= ny < H and 0 <= nx < W and not seen[ny, nx] and ok[ny, nx] and np.abs(a[ny, nx] - c).max() < tol:
                seen[ny, nx] = True
                q.append((ny, nx))
    return seen


def _hull(points: np.ndarray) -> np.ndarray:
    pts = sorted(set(map(tuple, points.tolist())))

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return np.array(lower[:-1] + upper[:-1], float)


def fit_quad(mask: np.ndarray) -> np.ndarray:
    """Largest-area quad from the mask's hull, corners ordered TL, TR, BR, BL as (x, y)."""
    ys, xs = np.nonzero(mask)
    hp = _hull(np.stack([xs, ys], 1))
    best = None
    for deg in np.arange(-45, 45, 0.5):
        t = math.radians(deg)
        r = hp @ np.array([[math.cos(t), -math.sin(t)], [math.sin(t), math.cos(t)]])
        idx = [np.argmin(r[:, 0] + r[:, 1]), np.argmax(r[:, 0] - r[:, 1]),
               np.argmax(r[:, 0] + r[:, 1]), np.argmin(r[:, 0] - r[:, 1])]
        q = hp[idx]
        area = 0.5 * abs(sum(q[i][0] * q[(i + 1) % 4][1] - q[(i + 1) % 4][0] * q[i][1] for i in range(4)))
        if best is None or area > best[0]:
            best = (area, q)
    return order_corners(best[1])


def order_corners(q: np.ndarray) -> np.ndarray:
    c = q.mean(0)
    q = q[np.argsort(np.arctan2(q[:, 1] - c[1], q[:, 0] - c[0]))]
    return np.roll(q, -int(np.argmin(q[:, 0] + q[:, 1])), 0)


def _bilinear(L, xs, ys):
    H, W = L.shape
    xs, ys = np.clip(xs, 0, W - 1.001), np.clip(ys, 0, H - 1.001)
    x0, y0 = np.floor(xs).astype(int), np.floor(ys).astype(int)
    fx, fy = xs - x0, ys - y0
    return (L[y0, x0] * (1 - fx) * (1 - fy) + L[y0, x0 + 1] * fx * (1 - fy)
            + L[y0 + 1, x0] * (1 - fx) * fy + L[y0 + 1, x0 + 1] * fx * fy)


def refine(L: np.ndarray, q: np.ndarray, unit: float, margin: float = 1.5) -> np.ndarray:
    """Move each edge to the first strong brightness step outward (the frame's inner edge),
    fit a straight line through ~23 samples per side, then intersect neighbouring lines."""
    c = q.mean(0)
    ts = np.arange(-6 * unit, 10 * unit + 0.01, 0.5)
    lines = []
    for i in range(4):
        a, b = q[i], q[(i + 1) % 4]
        d = (b - a) / np.linalg.norm(b - a)
        n = np.array([d[1], -d[0]])
        if np.dot(n, (a + b) / 2 - c) < 0:
            n = -n
        fs, offs = [], []
        for f in np.linspace(0.08, 0.92, 23):
            p = a + (b - a) * f
            prof = _bilinear(L, p[0] + n[0] * ts, p[1] + n[1] * ts)
            dif = np.abs(np.diff(prof))
            if dif.max() < 12:
                continue
            j = int(np.argmax(dif >= 0.5 * dif.max()))
            fs.append(f)
            offs.append((ts[j] + ts[j + 1]) / 2)
        fs, offs = np.array(fs), np.array(offs)
        if len(offs) >= 5:
            keep = np.abs(offs - np.median(offs)) <= 3 * unit
            k1, k0 = np.polyfit(fs[keep], offs[keep], 1) if keep.sum() >= 5 else (0.0, float(np.median(offs)))
        else:
            k1, k0 = 0.0, 0.0
        pa, pb = a + n * (k0 + margin), b + n * (k0 + k1 + margin)
        lines.append((pa, (pb - pa) / np.linalg.norm(pb - pa)))
    out = []
    for i in range(4):
        (p1, d1), (p2, d2) = lines[i - 1], lines[i]
        t = np.linalg.solve(np.array([d1, -d2]).T, p2 - p1)
        out.append(p1 + d1 * t[0])
    return np.array(out)


def detect_frame(img: Image.Image) -> np.ndarray:
    """Corners (TL, TR, BR, BL) of the grey panel in full-size pixel coordinates."""
    full = img.convert("RGB")
    scale = full.width / WORK_WIDTH
    small = np.asarray(full.resize((WORK_WIDTH, round(full.height / scale)), Image.BILINEAR)).astype(float)
    seed = largest_blob_seed(neutral_mask(small))
    if seed is None:
        raise ValueError("no blank light-grey frame panel found")
    q = fit_quad(grow(small, seed)) * scale
    L = np.asarray(full).astype(float).mean(2)
    return refine(L, q, unit=max(1.0, full.width / 1000))


def measured_ratio(q: np.ndarray) -> float:
    w = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    h = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    return w / h


def fits_square_thumbnail(q: np.ndarray, size: tuple[int, int]) -> bool:
    """Etsy search shows a centred square crop of the first photo; the whole frame should be inside it."""
    W, H = size
    s = min(W, H)
    x0, y0 = (W - s) / 2, (H - s) / 2
    return bool((q[:, 0] >= x0).all() and (q[:, 0] <= x0 + s).all() and (q[:, 1] >= y0).all() and (q[:, 1] <= y0 + s).all())


def load_room_list(rooms_dir: Path) -> list[dict]:
    with open(rooms_dir / "rooms.toml", "rb") as fh:
        return tomllib.load(fh)["room"]


def calibrate_rooms(rooms_dir: Path) -> dict:
    """Detect every room's frame, write corners.json and an overlay sheet to check by eye."""
    rooms = load_room_list(rooms_dir)
    out, tiles = {}, []
    for i, r in enumerate(rooms):
        img = Image.open(rooms_dir / r["file"]).convert("RGB")
        q = detect_frame(img)
        got, want = measured_ratio(q), parse_ratio(r["ratio"])
        warnings = []
        if abs(got / want - 1) > RATIO_TOLERANCE:
            warnings.append(f"frame measures {got:.3f} wide per 1 high; rooms.toml says {r['ratio']} ({want:.3f})")
        if i == 0 and not fits_square_thumbnail(q, img.size):
            warnings.append("first room: the frame does not fit Etsy's square search thumbnail")
        out[r["file"]] = {"size": list(img.size), "corners": [[round(float(x), 1), round(float(y), 1)] for x, y in q],
                          "ratio_measured": round(float(got), 4), "warnings": warnings}
        tiles.append(_overlay(img, q, r["file"]))
    (rooms_dir / "corners.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    _sheet(tiles).save(rooms_dir / "calibration_check.jpg", quality=85)
    return out


def overlay_sheet(rooms_dir: Path) -> Path:
    """Redraw calibration_check.jpg from the saved corners.json, without recalibrating."""
    cal = json.loads((rooms_dir / "corners.json").read_text(encoding="utf-8"))
    tiles = [_overlay(Image.open(rooms_dir / r["file"]).convert("RGB"), np.array(cal[r["file"]]["corners"]), r["file"])
             for r in load_room_list(rooms_dir)]
    out = rooms_dir / "calibration_check.jpg"
    _sheet(tiles).save(out, quality=85)
    return out


def _overlay(img: Image.Image, q: np.ndarray, label: str) -> Image.Image:
    ov = img.copy()
    d = ImageDraw.Draw(ov)
    w = max(3, img.width // 300)
    d.polygon([tuple(p) for p in q], outline=(255, 0, 80), width=w)
    for x, y in q:
        d.ellipse([x - 3 * w, y - 3 * w, x + 3 * w, y + 3 * w], fill=(255, 0, 80))
    d.text((12, 12), label, fill=(255, 0, 80))
    ov.thumbnail((480, 600))
    return ov


def _sheet(tiles: list[Image.Image]) -> Image.Image:
    sheet = Image.new("RGB", (480 * len(tiles), 600), "white")
    for i, t in enumerate(tiles):
        sheet.paste(t, (i * 480, 0))
    return sheet
