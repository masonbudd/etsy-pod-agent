"""Crop a master image to each print ratio and save print-ready files."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageCms

from podagent.config import parse_ratio

SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()


def ratio_key(ratio: str) -> str:
    """Folder-safe name for a ratio: "4:5" -> "4x5", "ISO" -> "iso"."""
    return "iso" if str(ratio).upper() == "ISO" else str(ratio).replace(":", "x")


def crop_ratio(img: Image.Image, ratio: float, bias_top: float = 0.5) -> Image.Image:
    """Centre crop to width/height = ratio. bias_top is the share of trimmed height taken from the top."""
    w, h = img.size
    if w / h > ratio:
        nw = round(h * ratio)
        x = (w - nw) // 2
        return img.crop((x, 0, x + nw, h))
    nh = round(w / ratio)
    y = round((h - nh) * bias_top)
    return img.crop((0, y, w, y + nh))


def crop_loss_pct(w: int, h: int, ratio: float) -> float:
    """Share of the design (by width or height) that a crop to `ratio` removes."""
    have = w / h
    return round(100 * abs(1 - min(have, ratio) / max(have, ratio)), 1)


def ratio_groups(cfg: dict) -> dict[str, dict]:
    """Unique print ratios in the size table, each with the sizes that use it."""
    groups: dict[str, dict] = {}
    for s in cfg["sizes"]:
        g = groups.setdefault(ratio_key(s["ratio"]), {"ratio": s["ratio"], "value": parse_ratio(s["ratio"]), "sizes": []})
        g["sizes"].append(s["code"])
    return groups


def save_print(img: Image.Image, path: Path, quality: int = 95) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(path, "JPEG", quality=quality, subsampling=0, dpi=(300, 300), icc_profile=SRGB)


def make_print_files(master: Path, out_dir: Path, cfg: dict, sku: str, bias_top: float = 0.5) -> dict[str, str]:
    """One JPEG per ratio, named <sku>_<ratio>.jpg. Existing files are kept (resumable)."""
    files, img = {}, None
    for key, g in ratio_groups(cfg).items():
        out = out_dir / f"{sku}_{key}.jpg"
        if not out.exists():
            if img is None:
                img = Image.open(master).convert("RGB")
            save_print(crop_ratio(img, g["value"], bias_top), out)
        files[key] = out.name
    return files
