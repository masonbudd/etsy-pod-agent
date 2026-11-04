"""Draw two of the bundled sample designs in code, plus simple test rooms with a known frame position.

Run `python scripts/make_samples.py` to redraw them. Output is deterministic. The third sample,
kingfisher-moon.png, is a painted design with a margin added by `add_margin`.
The drawn rooms are test fixtures for calibration; the demo's sample rooms are images in samples/rooms.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

DESIGN_SIZE = (1024, 1536)    # the shape most image models produce for portrait art
ROOM_SIZE = (1600, 2000)
PANEL_GREY = (204, 204, 206)


def _grain(img: Image.Image, sd: float, seed: int) -> Image.Image:
    a = np.asarray(img).astype(float)
    a += np.random.default_rng(seed).normal(0, sd, a.shape[:2])[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _light(img: Image.Image, left: float, right: float, top: float = 1.0, bottom: float = 1.0) -> Image.Image:
    """Multiply by a soft left-right and top-bottom light gradient."""
    W, H = img.size
    gx = np.linspace(left, right, W)[None, :]
    gy = np.linspace(top, bottom, H)[:, None]
    a = np.asarray(img).astype(float) * (gx * gy)[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _wave(y0: float, amp: float, freq: float, phase: float, W: int, H: int) -> list[tuple[float, float]]:
    pts = [(x, y0 + amp * math.sin(freq * x / W * 2 * math.pi + phase)) for x in range(0, W + 8, 8)]
    return pts + [(W, H), (0, H)]


def _font(size: int) -> ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


# -- designs ---------------------------------------------------------------

def sun_over_hills() -> Image.Image:
    W, H = DESIGN_SIZE
    img = Image.new("RGB", DESIGN_SIZE, (244, 226, 200))
    img = _light(img, 1.0, 1.0, 1.02, 0.94)
    d = ImageDraw.Draw(img)
    d.ellipse([512 - 170, 560 - 170, 512 + 170, 560 + 170], fill=(222, 112, 72))
    d.polygon(_wave(840, 40, 1.0, 0.8, W, H), fill=(216, 170, 128))
    d.polygon(_wave(1010, 55, 1.4, 2.6, W, H), fill=(150, 160, 120))
    d.polygon(_wave(1210, 45, 0.9, 4.1, W, H), fill=(78, 98, 80))
    for x, y in ((300, 330), (360, 300)):
        d.line([(x - 22, y - 10), (x, y), (x + 22, y - 10)], fill=(70, 60, 55), width=5)
    return img


def good_morning() -> Image.Image:
    W, H = DESIGN_SIZE
    img = Image.new("RGB", DESIGN_SIZE, (236, 190, 90))
    d = ImageDraw.Draw(img)
    text, size = "GOOD MORNING", 140
    while d.textlength(text, font=_font(size)) > 840:
        size -= 4
    f = _font(size)
    d.text(((W - d.textlength(text, font=f)) / 2, 190), text, font=f, fill=(44, 40, 36))
    d.ellipse([212, 1180, 812, 1290], fill=(250, 244, 232))                      # saucer
    d.rounded_rectangle([302, 760, 722, 1230], radius=90, fill=(250, 244, 232))  # cup
    d.rectangle([302, 860, 722, 930], fill=(62, 92, 140))                        # blue band
    d.ellipse([680, 860, 860, 1080], outline=(250, 244, 232), width=34)          # handle
    for x in (420, 512, 604):                                                    # steam
        pts = [(x + 18 * math.sin(t / 14), 700 - t) for t in range(0, 230, 6)]
        d.line(pts, fill=(250, 244, 232), width=12, joint="curve")
    return img


MARGIN_CREAM = (250, 246, 236)


def add_margin(img: Image.Image, px: int = 64, colour=MARGIN_CREAM) -> Image.Image:
    """Surround a design with a flat cream margin, the kind image models paint in by mistake.
    samples/designs/kingfisher-moon.png was made this way, so the demo has a design the border check holds."""
    out = Image.new("RGB", (img.width + 2 * px, img.height + 2 * px), colour)
    out.paste(img.convert("RGB"), (px, px))
    return out


DESIGNS = {"sun-over-hills.png": sun_over_hills, "good-morning.png": good_morning}


# -- rooms -----------------------------------------------------------------

def _frame(img: Image.Image, panel: tuple[int, int, int, int], frame_rgb, thick: int, shadow=(18, 26)) -> Image.Image:
    """Shadow, frame moulding, then a flat grey panel the calibrator can find."""
    x0, y0, x1, y1 = panel
    sh = Image.new("L", img.size, 0)
    ImageDraw.Draw(sh).rectangle([x0 - thick + shadow[0], y0 - thick + shadow[1], x1 + thick + shadow[0], y1 + thick + shadow[1]], fill=110)
    sh = sh.filter(ImageFilter.GaussianBlur(22))
    img = Image.composite(Image.new("RGB", img.size, (30, 26, 22)), img, sh)
    d = ImageDraw.Draw(img)
    d.rectangle([x0 - thick, y0 - thick, x1 + thick, y1 + thick], fill=frame_rgb)
    d.rectangle([x0 - thick, y0 - thick, x1 + thick, y0 - thick + 4], fill=tuple(min(255, c + 25) for c in frame_rgb))
    panel_img = _light(Image.new("RGB", (x1 - x0, y1 - y0), PANEL_GREY), 1.04, 0.95, 1.01, 0.97)
    img.paste(panel_img, (x0, y0))
    return img


def living_room() -> Image.Image:
    W, H = ROOM_SIZE
    img = _light(Image.new("RGB", ROOM_SIZE, (170, 182, 154)), 1.08, 0.88, 1.02, 0.94)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 1600, W, H], fill=(176, 132, 92))
    for y in range(1640, H, 70):
        d.line([(0, y), (W, y)], fill=(150, 110, 76), width=3)
    d.rectangle([0, 1570, W, 1600], fill=(226, 220, 206))
    img = _frame(img, (540, 340, 1060, 990), (32, 30, 30), 26)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([220, 1150, 1380, 1520], radius=50, fill=(176, 92, 62))         # sofa back
    d.rounded_rectangle([170, 1300, 1430, 1560], radius=40, fill=(160, 82, 54))         # seat
    d.rounded_rectangle([300, 1190, 760, 1360], radius=40, fill=(190, 104, 70))          # cushions
    d.rounded_rectangle([840, 1190, 1300, 1360], radius=40, fill=(190, 104, 70))
    for x in (230, 1350):
        d.rectangle([x, 1560, x + 24, 1620], fill=(60, 40, 28))
    d.line([(1490, 1600), (1490, 900)], fill=(50, 44, 40), width=10)                    # floor lamp
    d.polygon([(1400, 900), (1580, 900), (1540, 760), (1440, 760)], fill=(236, 212, 158))
    return _grain(img, 2, 11)


def sideboard() -> Image.Image:
    W, H = ROOM_SIZE
    img = _light(Image.new("RGB", ROOM_SIZE, (206, 152, 128)), 0.92, 1.06, 1.02, 0.95)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 1720, W, H], fill=(150, 120, 96))
    img = _frame(img, (404, 640, 854, 1240), (196, 154, 108), 24, shadow=(10, 6))
    d = ImageDraw.Draw(img)
    d.rectangle([150, 1264, 1450, 1690], fill=(110, 72, 48))                            # sideboard
    d.rectangle([150, 1264, 1450, 1290], fill=(134, 92, 62))
    for x in (580, 1020):
        d.line([(x, 1300), (x, 1680)], fill=(88, 56, 38), width=4)
    for x in (200, 1380):
        d.rectangle([x, 1690, x + 22, 1760], fill=(70, 46, 30))
    d.ellipse([1080, 1040, 1260, 1264], fill=(52, 82, 80))                              # vase
    for ang in (-60, -30, 0, 25, 55):
        t = math.radians(ang - 90)
        tip = (1170 + 300 * math.cos(t), 1060 + 300 * math.sin(t))
        d.line([(1170, 1060), tip], fill=(86, 92, 60), width=6)
        d.ellipse([tip[0] - 26, tip[1] - 14, tip[0] + 26, tip[1] + 14], fill=(110, 124, 74))
    d.rectangle([930, 1204, 1050, 1264], fill=(230, 175, 70))                           # books
    d.rectangle([945, 1160, 1040, 1204], fill=(48, 96, 104))
    return _grain(img, 2, 12)


def bedroom() -> Image.Image:
    W, H = ROOM_SIZE
    img = _light(Image.new("RGB", ROOM_SIZE, (156, 176, 198)), 1.05, 0.92, 1.03, 0.94)
    img = _frame(img, (580, 380, 1020, 1040), (244, 242, 236), 24)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([250, 1180, 1350, 1520], radius=30, fill=(120, 90, 70))          # headboard
    d.rectangle([200, 1420, 1400, H], fill=(238, 232, 222))                             # bedding
    d.rounded_rectangle([320, 1360, 760, 1500], radius=40, fill=(230, 222, 206))         # pillows
    d.rounded_rectangle([840, 1360, 1280, 1500], radius=40, fill=(230, 222, 206))
    d.rectangle([200, 1700, 1400, 1800], fill=(200, 120, 90))                           # throw
    d.rectangle([1440, 1500, 1580, 1720], fill=(150, 112, 80))                          # bedside table
    d.rectangle([1490, 1380, 1510, 1500], fill=(60, 54, 50))
    d.polygon([(1440, 1380), (1560, 1380), (1530, 1290), (1470, 1290)], fill=(238, 214, 160))
    return _grain(img, 2, 13)


# drawn test rooms and the panel each one's frame holds (x0, y0, x1, y1)
TEST_ROOMS = {"living-room": (living_room, (540, 340, 1060, 990)), "sideboard": (sideboard, (404, 640, 854, 1240)),
              "bedroom": (bedroom, (580, 380, 1020, 1040))}


def write_designs(designs_dir: Path) -> None:
    designs_dir.mkdir(parents=True, exist_ok=True)
    for name, draw in DESIGNS.items():
        draw().save(designs_dir / name, optimize=True)
