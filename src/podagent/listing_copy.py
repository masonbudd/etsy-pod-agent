"""Listing copy: Claude writes the creative parts to copy.json; code validates them and builds
the full description from the shop's template, so sizes, delivery and the AI disclosure are always accurate."""
from __future__ import annotations

from podagent import rules

COPY_FIELDS = {"title": str, "hook": str, "tags": list, "section": str}
MAX_ATTEMPTS = 3


def validate(copy: dict, cfg: dict) -> tuple[list[str], list[str]]:
    """(errors, warnings). Any error sends the copy back to Claude; warnings go on the review page."""
    errors = [f"missing {k}" for k in COPY_FIELDS if k not in copy]
    errors += [f"{k} should be a {t.__name__}" for k, t in COPY_FIELDS.items() if k in copy and not isinstance(copy[k], t)]
    if errors:
        return errors, []
    lst, banned = cfg["listing"], cfg["checks"]["banned_terms"]
    avoid = lst.get("avoid_words", [])
    t_err, warnings = rules.check_title(copy["title"], banned, avoid, lst.get("title_min", 0))
    errors += t_err
    errors += rules.check_tags(copy["tags"], banned, avoid, lst.get("tag_count", rules.TAG_COUNT))
    errors += rules.check_section(copy["section"])
    if copy["section"] not in lst.get("sections", []):
        warnings.append(f"new shop section {copy['section']!r} will be created")
    sep = lst.get("title_separator")
    if sep and sep.strip() and sep not in copy["title"]:
        warnings.append(f"title does not use the shop's separator {sep.strip()!r}")
    for term in rules.find_terms(copy["hook"], banned):
        errors.append(f"description contains banned term {term!r}")
    for term in rules.find_terms(copy["hook"], avoid):
        errors.append(f"description contains {term!r}, which this shop avoids")
    if not copy["hook"].strip():
        errors.append("hook is empty")
    return errors, warnings


def sizes_text(cfg: dict) -> str:
    out = []
    for s in cfg["sizes"]:
        out.append(s["name"] + (" (unframed only)" if s.get("cost_framed") is None and cfg["variations"].get("frames") else ""))
    return ", ".join(out)


def frames_text(cfg: dict) -> str:
    """The frame options exactly as buyers see them in the menu."""
    names = [f["name"] for f in cfg["variations"].get("frames", [])]
    if not names:
        return "unframed print"
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def description(copy: dict, cfg: dict) -> str:
    lst = cfg["listing"]
    return lst["description_template"].format(
        hook=copy["hook"].strip(), paper=lst.get("paper", ""), sizes=sizes_text(cfg), frames=frames_text(cfg),
        delivery=lst.get("delivery", ""), ai_disclosure=lst.get("ai_disclosure", "")).strip()


def brief(cfg: dict, items: list[dict]) -> dict:
    """Everything Claude needs to write copy for these items, in one place."""
    lst = cfg["listing"]
    return {
        "write_to": "batches/<batch>/items/<item>/copy.json",
        "format": {"title": "string", "hook": "2-4 sentences that open the description", "tags": f"{lst.get('tag_count', 13)} strings",
                   "section": "one of the shop sections, or a new name of 24 characters or fewer"},
        "voice": lst["voice"], "spelling": cfg["shop"].get("spelling", "British"),
        "title_style": lst["title_style"], "title_limit": rules.TITLE_MAX, "title_min": lst.get("title_min", 0),
        "title_rule": "Each of % : & + may appear once at most. No emoji.",
        "tag_rules": f"Exactly {lst.get('tag_count', 13)} tags, each {rules.TAG_MAX} characters or fewer, letters, numbers, spaces, - and ' only. No repeats.",
        "sections": lst.get("sections", []), "avoid_words": lst.get("avoid_words", []),
        "banned_terms": cfg["checks"]["banned_terms"],
        "the_description_adds": "sizes, frames, paper, delivery and the AI disclosure automatically; write only the hook",
        "items": items,
    }
