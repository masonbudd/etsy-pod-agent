"""Quality gate. Code measures what it can; Claude's visual review covers the rest.

Code: borders, resolution against every size sold, crop loss, near-duplicates, banned terms.
Claude (written to vision.json): spelling in lettering, IP, visible defects, a short subject line.
`verdict()` merges both into one pass/hold record with reasons and a suggested fix.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from podagent import rules
from podagent.config import parse_ratio
from podagent.images.crops import crop_loss_pct

VISION_FIELDS = {
    "lettering": str,        # exact text visible in the design, "" if none
    "spelling_ok": bool,
    "spelling_notes": str,
    "ip_ok": bool,           # no brands, logos, characters, celebrities or living artists' signature styles
    "ip_notes": str,
    "border_seen": bool,     # a margin or painted border around the art
    "defects_ok": bool,      # no garbled shapes, extra limbs, watermarks or artefacts
    "defects_notes": str,
    "subject": str,          # one short line describing the design, used for alt text
}
BAND_CAP = 0.12              # a uniform band deeper than this is background, not a border


# -- borders ---------------------------------------------------------------

def _side_band(a: np.ndarray) -> tuple[int, np.ndarray, float]:
    """Rows of `a` (row 0 = image edge, moving inward). Returns (band depth, band colour,
    share of the first non-band row that differs from the band: ~1.0 means a straight inner edge)."""
    edge = a[0].mean(0)
    cap = int(len(a) * BAND_CAP)
    k = 0
    for i in range(cap + 1):
        line = a[i]
        if line.std(0).mean() < 14 and np.abs(line.mean(0) - edge).max() < 20:
            k = i + 1
        else:
            break
    if k == 0 or k > cap:
        return 0, edge, 0.0
    nxt = a[min(k + 1, len(a) - 1)]
    differs = (np.abs(nxt - edge).max(1) > 25).mean()
    return k, edge, float(differs)


def border(img: Image.Image, min_pct: float) -> dict:
    """A border is a uniform band with a straight inner edge on at least 3 sides, all one colour."""
    small = img.convert("RGB")
    small.thumbnail((512, 512))
    a = np.asarray(small).astype(float)
    views = {"top": a, "bottom": a[::-1], "left": a.transpose(1, 0, 2), "right": a.transpose(1, 0, 2)[::-1]}
    sides = {}
    for name, v in views.items():
        k, colour, edge_share = _side_band(v)
        pct = 100 * k / len(v)
        sides[name] = {"pct": round(pct, 1), "colour": [int(c) for c in colour],
                       "straight_edge": edge_share >= 0.6 and pct >= min_pct}
    hits = [s for s in sides.values() if s["straight_edge"]]
    same = hits and max(np.abs(np.array(h["colour"]) - np.array(hits[0]["colour"])).max() for h in hits) < 30
    found = len(hits) >= 3 and bool(same)
    return {"found": found, "sides": sides,
            "crop_px": {k: round(v["pct"] / 100 * (img.height if k in ("top", "bottom") else img.width))
                        for k, v in sides.items() if v["straight_edge"]}}


def trim_border(img: Image.Image, result: dict, safety_px: int = 2) -> Image.Image:
    c = {k: result["crop_px"].get(k, 0) + (safety_px if k in result["crop_px"] else 0)
         for k in ("left", "top", "right", "bottom")}
    return img.crop((c["left"], c["top"], img.width - c["right"], img.height - c["bottom"]))


# -- resolution and crops --------------------------------------------------

def resolution(w: int, h: int, sizes: list[dict], factor: int, min_dpi: float) -> dict:
    """DPI each size would print at after upscaling and cropping to that size's ratio."""
    rows, too_small = [], []
    for s in sizes:
        r = parse_ratio(s["ratio"])
        cw, ch = (h * r, h) if w / h > r else (w, w / r)
        dpi = min(cw * factor / s["width_in"], ch * factor / s["height_in"])
        rows.append({"size": s["code"], "dpi": round(dpi)})
        if dpi < min_dpi:
            too_small.append(s["code"])
    need = max(max(s["width_in"], s["height_in"] * parse_ratio(s["ratio"])) * min_dpi / factor for s in sizes)
    return {"ok": not too_small, "too_small": too_small, "by_size": rows, "source_px": [w, h],
            "min_source_width": int(need)}


def crop_warnings(w: int, h: int, sizes: list[dict], max_pct: float) -> list[str]:
    seen, out = set(), []
    for s in sizes:
        if s["ratio"] in seen:
            continue
        seen.add(s["ratio"])
        loss = crop_loss_pct(w, h, parse_ratio(s["ratio"]))
        if loss > max_pct:
            out.append(f"{s['ratio']} sizes crop {loss}% of the design: check nothing important is cut")
    return out


# -- duplicates ------------------------------------------------------------

def dhash(img: Image.Image, n: int = 8) -> int:
    g = np.asarray(img.convert("L").resize((n + 1, n), Image.LANCZOS)).astype(int)
    bits = (g[:, 1:] > g[:, :-1]).flatten()
    return int("".join("1" if b else "0" for b in bits), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def duplicates(h: int, others: dict[str, int], max_distance: int) -> list[str]:
    return [f"{name} (distance {hamming(h, o)})" for name, o in others.items() if hamming(h, o) <= max_distance]


def hashes_of(folder: Path) -> dict[str, int]:
    out = {}
    if folder.is_dir():
        for p in sorted(folder.iterdir()):
            if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                out[f"existing/{p.name}"] = dhash(Image.open(p))
    return out


# -- Claude's review -------------------------------------------------------

def validate_vision(data: dict) -> list[str]:
    errors = [f"missing {k}" for k in VISION_FIELDS if k not in data]
    errors += [f"{k} should be {t.__name__}" for k, t in VISION_FIELDS.items() if k in data and not isinstance(data[k], t)]
    return errors


# -- merge -----------------------------------------------------------------

def code_checks(img: Image.Image, name: str, cfg: dict, others: dict[str, int]) -> dict:
    c = cfg["checks"]
    w, h = img.size
    up = int(cfg.get("upscale", {}).get("factor", 4))
    h_ = dhash(img)
    return {
        "orientation": {"ok": h >= w, "detail": f"{w}x{h}"},
        "border": border(img, c["border_min_pct"]),
        "resolution": resolution(w, h, cfg["sizes"], up, c["min_dpi"]),
        "duplicates": duplicates(h_, others, c["duplicate_distance"]),
        "banned_in_filename": rules.find_terms(Path(name).stem.replace("-", " ").replace("_", " "), c["banned_terms"]),
        "crop_warnings": crop_warnings(w, h, cfg["sizes"], c["max_crop_pct"]),
        "dhash": h_,
    }


def _words(text: str) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text.upper()).split())


def verdict(code: dict, vision: dict, cfg: dict, expected_lettering: str = "") -> dict:
    reasons, fixes = [], []
    if expected_lettering and _words(vision.get("lettering", "")) != _words(expected_lettering):
        reasons.append(f"lettering reads {vision.get('lettering', '')!r}; the concept says {expected_lettering!r}")
        fixes.append("regenerate with the exact wording")
    if not code["orientation"]["ok"]:
        reasons.append("landscape design: this version supports portrait prints only")
        fixes.append("rotate or recompose as portrait")
    if code["border"]["found"] or vision.get("border_seen"):
        reasons.append("border or margin around the art" + (" (seen by review)" if not code["border"]["found"] else ""))
        fixes.append("trim it with `podagent trim-border`, or regenerate full-bleed")
    res = code["resolution"]
    if not res["ok"]:
        reasons.append(f"too small for {', '.join(res['too_small'])} at {cfg['checks']['min_dpi']} dpi")
        fixes.append(f"supply a file at least {res['min_source_width']} px wide, or remove the largest sizes")
    if code["duplicates"]:
        reasons.append("near-duplicate of " + ", ".join(code["duplicates"]))
        fixes.append("drop it, or make it clearly different")
    banned = code["banned_in_filename"] + rules.find_terms(vision.get("lettering", ""), cfg["checks"]["banned_terms"])
    if banned:
        reasons.append("banned term: " + ", ".join(sorted(set(banned))))
    if not vision.get("ip_ok", True):
        reasons.append("possible IP: " + (vision.get("ip_notes") or "flagged by review"))
    if banned or not vision.get("ip_ok", True):
        fixes.append("remove the brand, character or name and regenerate")
    if not vision.get("spelling_ok", True):
        reasons.append("spelling: " + (vision.get("spelling_notes") or vision.get("lettering", "")))
        fixes.append("fix the lettering and regenerate")
    if not vision.get("defects_ok", True):
        reasons.append("visible defect: " + (vision.get("defects_notes") or "flagged by review"))
        fixes.append("regenerate or retouch")
    return {"verdict": "hold" if reasons else "pass", "reasons": reasons, "suggested_fix": "; ".join(dict.fromkeys(fixes)),
            "warnings": code["crop_warnings"], "lettering": vision.get("lettering", ""), "subject": vision.get("subject", "")}
