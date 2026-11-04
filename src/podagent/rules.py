"""Etsy's hard limits and the shop's own style rules, checked in code.

Hard limits come from the Etsy Open API v3 reference. Every check returns a list of
problem strings; an empty list means it passes.
"""

from __future__ import annotations

import re
import unicodedata

TITLE_MAX = 140
TAG_COUNT = 13
TAG_MAX = 20
SKU_MAX = 32
SECTION_MAX = 24
OPTION_MAX = 20  # longer variation option names are risky on Etsy; treated as a problem
TITLE_ONCE = "%:&+"
TITLE_EXTRA = set("™©®")
TAG_EXTRA = set("-'™©®")


def _ok_title_char(c: str) -> bool:
    cat = unicodedata.category(c)
    return cat[0] in "LP" or cat in ("Nd", "Sm", "Zs") or c in TITLE_EXTRA


def _ok_tag_char(c: str) -> bool:
    cat = unicodedata.category(c)
    return cat[0] == "L" or cat in ("Nd", "Zs") or c in TAG_EXTRA


def check_title(title: str, banned=(), avoid=(), min_len: int = 0) -> tuple[list[str], list[str]]:
    """(errors, warnings). Errors block the draft; warnings show on the review page."""
    errors, warnings = [], []
    if not title.strip():
        return ["title is empty"], []
    if len(title) > TITLE_MAX:
        errors.append(f"title is {len(title)} characters; Etsy allows {TITLE_MAX}")
    bad = sorted({c for c in title if not _ok_title_char(c)})
    if bad:
        errors.append(f"title has characters Etsy rejects: {''.join(bad)!r}")
    for c in TITLE_ONCE:
        if title.count(c) > 1:
            errors.append(f"title uses {c!r} {title.count(c)} times; Etsy allows it once")
    for term in find_terms(title, banned):
        errors.append(f"title contains banned term {term!r}")
    for term in find_terms(title, avoid):
        errors.append(f"title contains {term!r}, which this shop avoids")
    if len(title) < min_len:
        warnings.append(f"title is {len(title)} characters; the shop's style aims for {min_len}+")
    return errors, warnings


def check_tags(tags: list[str], banned=(), avoid=(), count: int = TAG_COUNT) -> list[str]:
    errors = []
    if len(tags) != count:
        errors.append(f"{len(tags)} tags; need exactly {count}")
    seen = set()
    for t in tags:
        if len(t) > TAG_MAX:
            errors.append(f"tag {t!r} is {len(t)} characters; Etsy allows {TAG_MAX}")
        if not t.strip():
            errors.append("empty tag")
        bad = sorted({c for c in t if not _ok_tag_char(c)})
        if bad:
            errors.append(f"tag {t!r} has characters Etsy rejects: {''.join(bad)!r}")
        key = t.strip().lower()
        if key in seen:
            errors.append(f"duplicate tag {t!r}")
        seen.add(key)
    joined = " , ".join(tags)
    errors += [f"tags contain banned term {term!r}" for term in find_terms(joined, banned)]
    errors += [f"tags contain {term!r}, which this shop avoids" for term in find_terms(joined, avoid)]
    return errors


def check_sku(sku: str) -> list[str]:
    errors = []
    if len(sku) > SKU_MAX:
        errors.append(f"SKU {sku!r} is {len(sku)} characters; Etsy allows {SKU_MAX}")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", sku):
        errors.append(f"SKU {sku!r}: use lowercase letters, numbers and single hyphens")
    return errors


def check_section(name: str) -> list[str]:
    errors = []
    if not name.strip():
        errors.append("section name is empty")
    if len(name) > SECTION_MAX:
        errors.append(f"section {name!r} is {len(name)} characters; Etsy allows {SECTION_MAX}")
    return errors


def check_option_name(name: str) -> list[str]:
    errors = []
    if "(" in name or ")" in name:
        errors.append("Etsy does not allow brackets in variation option names")
    if len(name) > OPTION_MAX:
        errors.append(f"option name is {len(name)} characters; keep it to {OPTION_MAX}")
    return errors


def find_terms(text: str, terms) -> list[str]:
    """Terms that appear as whole words or phrases, ignoring case."""
    hits = []
    low = text.lower()
    for term in terms:
        t = term.lower().strip()
        if t and re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", low):
            hits.append(term)
    return hits


def slugify(text: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    if len(slug) > max_len:
        slug = slug[:max_len].rsplit("-", 1)[0] or slug[:max_len]
    return slug or "design"


def make_sku(prefix: str, name: str, taken: set[str], suffix_len: int = 0) -> str:
    """Unique base SKU that leaves room for variant suffixes within Etsy's 32 characters."""
    room = SKU_MAX - len(prefix) - 1 - suffix_len
    base = f"{prefix}-{slugify(name, max_len=room - 3)}"
    sku, n = base, 2
    while sku in taken:
        sku, n = f"{base}-{n}", n + 1
    return sku


def variant_sku(base: str, size_code: str, frame_code: str | None) -> str:
    parts = [base, size_code.lower()] + ([frame_code.lower()] if frame_code else [])
    return "-".join(parts)
