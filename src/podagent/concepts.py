"""Design concepts and Gate 1.

Claude writes batches/<id>/concepts.json from the research. Code validates each concept,
adds its cost estimate, and writes a page for the user to decide on. Decisions are
recorded with `podagent decide`, and generation only runs for approved concepts. If a
concept's prompt changes after approval, the approval is cleared and it must be decided again.
"""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from podagent import costs, rules
from podagent.state import Batch, now

FIELDS = {"id": str, "title": str, "target_keyword": str, "why": str, "prompt": str}
OPTIONAL = {"lettering": str, "research_phrases": list}


def validate(data: dict, cfg: dict, research: dict | None, max_count: int) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    items = data.get("concepts")
    if not isinstance(items, list) or not items:
        return ['concepts.json needs a non-empty "concepts" list'], []
    if len(items) > max_count:
        errors.append(f"{len(items)} concepts; this batch asked for {max_count}")
    phrases = {s["phrase"] for s in (research or {}).get("signals", [])}
    banned = cfg["checks"]["banned_terms"]
    limit = cfg.get("images", {}).get("prompt_max", 4000)
    seen = set()
    for n, c in enumerate(items, 1):
        cid = c.get("id", f"#{n}")
        errors += [f"{cid}: missing {k}" for k in FIELDS if not c.get(k)]
        errors += [
            f"{cid}: {k} should be {t.__name__}"
            for k, t in {**FIELDS, **OPTIONAL}.items()
            if k in c and not isinstance(c[k], t)
        ]
        if cid in seen:
            errors.append(f"{cid}: duplicate id")
        seen.add(cid)
        if c.get("id") and rules.slugify(cid) != cid:
            errors.append(f"{cid}: id must be lowercase words joined by hyphens, e.g. {rules.slugify(cid)}")
        text = " ".join(str(c.get(k, "")) for k in ("title", "target_keyword", "prompt", "lettering"))
        errors += [f"{cid}: banned term {t!r}" for t in rules.find_terms(text, banned)]
        if len(c.get("prompt", "")) > limit:
            errors.append(f"{cid}: prompt is {len(c['prompt'])} characters; the limit is {limit}")
        if phrases and c.get("target_keyword", "").lower() not in phrases:
            warnings.append(f"{cid}: target keyword {c.get('target_keyword')!r} is not in this batch's research")
    return errors, warnings


def full_prompt(concept: dict, cfg: dict) -> str:
    suffix = cfg.get("images", {}).get("prompt_suffix", "")
    return f"{concept['prompt'].strip()} {suffix}".strip()


def load_into_state(batch: Batch, cfg: dict, data: dict) -> list[dict]:
    """Merge concepts.json into state, keeping decisions only where the prompt is unchanged."""
    old = {c["id"]: c for c in batch.data.get("concepts", [])}
    each = costs.estimate(cfg, 1, cfg.get("images", {}).get("auto_regenerate", 1))
    merged = []
    for c in data["concepts"]:
        prev = old.get(c["id"], {})
        keep = prev.get("prompt") == c["prompt"] and prev.get("decision")
        merged.append(
            {
                **c,
                "estimate_usd": each.get("expected_usd"),
                "worst_case_usd": each.get("worst_case_usd"),
                "decision": prev.get("decision") if keep else None,
                "decided_at": prev.get("decided_at") if keep else None,
                "item": prev.get("item"),
                "requested_at": prev.get("requested_at"),
            }
        )
    batch.data["concepts"] = merged
    batch.save()
    return merged


def decide(batch: Batch, approve=(), reject=(), edits: dict | None = None, note: str = "") -> list[dict]:
    """Record the user's Gate 1 decisions. An edit replaces the prompt and approves it."""
    by_id = {c["id"]: c for c in batch.data.get("concepts", [])}
    unknown = [i for i in [*approve, *reject, *(edits or {})] if i not in by_id]
    if unknown:
        raise KeyError(f"no such concept: {', '.join(unknown)}")
    for cid, prompt in (edits or {}).items():
        c = by_id[cid]
        c.setdefault("original_prompt", c["prompt"])
        c["prompt"], c["edited"] = prompt, True
        c.pop("refused", None)  # a new prompt gets a fresh try
        approve = [*approve, cid]
    for cid in approve:
        if by_id[cid].get("item"):
            continue  # already generated; nothing to change
        by_id[cid].update(decision="approved", decided_at=now(), note=note)
    for cid in reject:
        by_id[cid].update(decision="rejected", decided_at=now(), note=note)
    batch.event(f"gate 1: approved {list(approve)}, rejected {list(reject)}")
    batch.save()
    data = load_file(batch)
    if data and edits:  # keep concepts.json in step with the edited prompts
        for c in data["concepts"]:
            if c.get("id") in edits:
                c["prompt"] = edits[c["id"]]
        (batch.dir / "concepts.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    return batch.data["concepts"]


def summary(batch: Batch) -> dict:
    cs = batch.data.get("concepts", [])
    approved = [c for c in cs if c.get("decision") == "approved"]
    return {
        "proposed": len(cs),
        "approved": len(approved),
        "rejected": sum(1 for c in cs if c.get("decision") == "rejected"),
        "undecided": [c["id"] for c in cs if not c.get("decision")],
        "refused": [c["id"] for c in cs if c.get("refused")],
        "approved_estimate_usd": round(sum(c.get("estimate_usd") or 0 for c in approved), 4),
        "approved_worst_case_usd": round(sum(c.get("worst_case_usd") or 0 for c in approved), 4),
    }


def markdown_table(batch: Batch) -> str:
    rows = ["| Concept | Target keyword | Why it should sell | Est. cost | Decision |", "|---|---|---|---|---|"]
    for c in batch.data.get("concepts", []):
        cost = f"${c['estimate_usd']:.3f}" if c.get("estimate_usd") is not None else "unknown"
        rows.append(
            f"| **{c['id']}**: {c['title']} | {c['target_keyword']} | {c['why']} | {cost} | {c.get('decision') or 'waiting'}{', refused by the image API' if c.get('refused') else ''} |"
        )
    return "\n".join(rows)


def write_gate_page(batch: Batch, cfg: dict, warnings: list[str]) -> Path:
    from podagent.review_page import CSS

    s = summary(batch)
    budget = cfg.get("images", {}).get("max_batch_usd")
    over = budget is not None and s["approved_worst_case_usd"] > budget
    cards = []
    for c in batch.data.get("concepts", []):
        cost = (
            f"${c['estimate_usd']:.3f} (worst case ${c['worst_case_usd']:.3f})"
            if c.get("estimate_usd") is not None
            else "no price in config"
        )
        decision = (c.get("decision") or "waiting") + (", prompt edited" if c.get("edited") else "")
        note = (
            f"<p class='row'><b>Your note:</b> {escape(c['note'])}</p>" if c.get("note") and c.get("decision") else ""
        )
        if c.get("refused"):
            note += f"<p class='row bad'><b>Refused by the image API:</b> {escape(c['refused'])}. Edit the prompt to try again.</p>"
        cards.append(f"""<section class="card" style="grid-template-columns:1fr"><div>
<p class="title">{escape(c["id"])}: {escape(c["title"])}</p>
<p class="meta">Target keyword: {escape(c["target_keyword"])} · estimated cost {escape(cost)} · decision: <b>{escape(decision)}</b></p>
<p class="row"><b>Why it should sell:</b> {escape(c["why"])}</p>{note}
<details><summary>Image prompt</summary><pre style="white-space:pre-wrap;font:inherit">{escape(full_prompt(c, cfg))}</pre></details>
</div></section>""")
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Concepts {escape(batch.id)}</title><style>{CSS}</style></head><body><main>
<h1>Gate 1: concepts for batch {escape(batch.id)}</h1>
<div class="banner">Nothing is generated until you approve, edit or reject each concept. Tell Claude your decision for each one.</div>
<div class="stats"><div class="stat"><b>{s["proposed"]}</b><span>proposed</span></div><div class="stat"><b>{s["approved"]}</b><span>approved</span></div>
<div class="stat"><b>${s["approved_estimate_usd"]:.2f}</b><span>approved, expected</span></div>
<div class="stat"><b>${s["approved_worst_case_usd"]:.2f}</b><span>worst case (one retry each)</span></div></div>
{f'<p class="row warn">Worst case is over the batch budget of ${budget:.2f} (images.max_batch_usd).</p>' if over else ""}
{"".join(f'<p class="row warn">{escape(w)}</p>' for w in warnings)}
{"".join(cards)}</main></body></html>"""
    out = batch.dir / "concepts.html"
    out.write_text(html, encoding="utf-8")
    return out


def load_file(batch: Batch) -> dict | None:
    p = batch.dir / "concepts.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
