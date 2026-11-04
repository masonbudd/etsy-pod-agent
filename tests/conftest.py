import copy
import json
import shutil
import tomllib
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from podagent.config import EXAMPLE_CONFIG, REPO, Workspace
from podagent.etsy.client import EtsyClient
from podagent.etsy.oauth import TokenStore

SAMPLES = REPO / "samples"


@pytest.fixture
def cfg():
    with open(EXAMPLE_CONFIG, "rb") as fh:
        c = tomllib.load(fh)
    c["upscale"]["engine"] = "resize"
    c["etsy"].update(shop_id=123, shipping_profile_id=11, return_policy_id=22, readiness_state_id=33)
    return c


@pytest.fixture
def small_cfg(cfg):
    """Fewer sizes and a 2x upscale so pipeline tests run in seconds."""
    c = copy.deepcopy(cfg)
    c["sizes"] = [s for s in c["sizes"] if s["code"] in ("8x10", "a4", "12x16")]
    c["upscale"]["factor"] = 2
    c["checks"]["min_dpi"] = 100
    c["listing"]["max_photos"] = 3
    return c


@pytest.fixture
def ws(tmp_path, cfg):
    w = Workspace(tmp_path)
    shutil.copytree(SAMPLES / "rooms", w.path(cfg, "rooms"))
    w.path(cfg, "inbox").mkdir(parents=True)
    w.path(cfg, "batches").mkdir(parents=True)
    return w


def art(size=(768, 1152), seed=0, border=0) -> Image.Image:
    """A simple flat design; different seeds give clearly different pictures."""
    palette = [(222, 112, 72), (62, 92, 140), (150, 160, 120), (230, 175, 70), (40, 92, 98)]
    img = Image.new("RGB", size, (236, 228, 214) if border else palette[seed % 5])
    d = ImageDraw.Draw(img)
    w, h = size
    d.rectangle([border, border, w - border - 1, h - border - 1], fill=palette[(seed + 1) % 5])
    d.ellipse([w * 0.2 + seed * 7, h * 0.15, w * 0.7, h * 0.45], fill=palette[(seed + 2) % 5])
    d.polygon([(border, h * 0.7), (w * 0.5, h * (0.5 + 0.05 * seed)), (w - border, h * 0.8), (w - border, h - border), (border, h - border)],
              fill=palette[(seed + 3) % 5])
    return img


def vision(**over) -> dict:
    v = {"lettering": "", "spelling_ok": True, "spelling_notes": "", "ip_ok": True, "ip_notes": "", "border_seen": False,
         "defects_ok": True, "defects_notes": "", "subject": "a test print"}
    v.update(over)
    return v


def good_copy(section="Abstract Prints", title=None) -> dict:
    return {"title": title or "Abstract Sun Print | Modern Wall Art | Boho Living Room Decor | Minimalist Poster | Colourful Art Print",
            "hook": "A bold sun over simple hills in warm, earthy colours.",
            "tags": ["abstract print", "modern wall art", "boho decor", "minimalist poster", "sun print", "living room art",
                     "colourful art", "earthy decor", "hallway art", "bedroom print", "retro poster", "nature print", "gift for her"],
            "section": section}


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def token_file(tmp_path):
    p = tmp_path / ".secrets" / "etsy_token.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    write_json(p, {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600, "obtained_at": 1000})
    return p


def etsy_client(token_file, transport) -> EtsyClient:
    store = TokenStore(token_file, "keystring", transport=transport, clock=lambda: 1500)
    return EtsyClient("keystring", "shared", store, transport=transport, sleep=lambda s: None)
