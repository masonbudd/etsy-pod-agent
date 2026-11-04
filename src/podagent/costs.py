"""Estimate image-generation spend before anything is generated, from prices in config."""
from __future__ import annotations


def image_price(cfg: dict, model: str | None = None, quality: str | None = None, size: str | None = None) -> float | None:
    img = cfg.get("images", {})
    key = f"{model or img.get('model')}:{quality or img.get('quality')}:{size or img.get('size')}"
    return img.get("price_usd", {}).get(key)


def estimate(cfg: dict, n_images: int, retries_per_image: int = 1) -> dict:
    """Expected cost (one image each) and worst case (every image held and regenerated once)."""
    img = cfg.get("images", {})
    each = image_price(cfg)
    if each is None:
        return {"known": False, "model": img.get("model"), "quality": img.get("quality"), "size": img.get("size"),
                "note": "No price in [images.price_usd] for this model, quality and size. Add one before generating."}
    return {"known": True, "model": img.get("model"), "quality": img.get("quality"), "size": img.get("size"),
            "per_image_usd": each, "images": n_images, "expected_usd": round(each * n_images, 4),
            "worst_case_usd": round(each * n_images * (1 + retries_per_image), 4)}
