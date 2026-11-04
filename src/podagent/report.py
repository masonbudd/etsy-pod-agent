"""Run report for a batch, as report.json and report.md."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from podagent.state import Batch


def build(batch: Batch) -> dict:
    items = batch.items
    drafted = [i for i, it in items.items() if it["steps"].get("draft", {}).get("status") == "done"]
    live_drafts = [i for i in drafted if not items[i]["steps"]["draft"].get("dry_run")]
    concepts = batch.data.get("concepts", [])
    costs: dict[str, float] = {}
    for c in batch.data.get("costs", []):
        costs[c["kind"]] = round(costs.get(c["kind"], 0) + c["usd"], 4)
    runs = batch.data.get("runs", [])
    created = datetime.fromisoformat(batch.data["created"])
    last = max((r["end"] for r in runs if r.get("end")), default=batch.data["created"])
    return {
        "batch": batch.id, "mode": batch.data["mode"], "created": batch.data["created"],
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "time": {"pipeline_seconds": round(sum(r.get("seconds") or 0 for r in runs), 1), "runs": len(runs),
                 "elapsed_seconds": round((datetime.fromisoformat(last) - created).total_seconds()),
                 "note": "pipeline_seconds is machine time; elapsed includes time spent on human review"},
        "concepts": {"proposed": len(concepts), "approved": sum(1 for c in concepts if c.get("decision") == "approved")},
        "designs": len(items),
        "passed_checks": sum(1 for it in items.values() if it["steps"].get("checks", {}).get("verdict") == "pass"),
        "held": [{"item": i, "step": it["held"]["step"], "reasons": it["held"]["reasons"], "fix": it["held"]["fix"]}
                 for i, it in items.items() if it["status"] == "held"],
        "drafts_created": len(live_drafts),
        "dry_run_drafts": len(drafted) - len(live_drafts),
        "low_margin": {i: it["steps"]["prices"]["summary"]["low_margin"] for i, it in items.items()
                       if it["steps"].get("prices", {}).get("summary", {}).get("low_margin")},
        "api_cost_usd": {"by_kind": costs, "total": round(sum(costs.values()), 4),
                         "note": "Image API spend recorded by this tool. Claude usage in Claude Code is billed separately."},
    }


def markdown(r: dict) -> str:
    t = r["time"]
    lines = [f"# Run report: {r['batch']}", "",
             f"- Mode: {r['mode']}",
             f"- Pipeline time: {t['pipeline_seconds']} s over {t['runs']} run(s); elapsed {t['elapsed_seconds']} s",
             f"- Concepts proposed / approved: {r['concepts']['proposed']} / {r['concepts']['approved']}",
             f"- Designs: {r['designs']}, passed checks: {r['passed_checks']}",
             f"- Etsy drafts created: {r['drafts_created']}" + (f" (plus {r['dry_run_drafts']} dry-run)" if r["dry_run_drafts"] else ""),
             f"- Held: {len(r['held'])}",
             f"- Image API cost: ${r['api_cost_usd']['total']:.2f}", ""]
    if r["held"]:
        lines += ["## Held", ""] + [f"- **{h['item']}** ({h['step']}): {'; '.join(h['reasons'])}. Fix: {h['fix']}" for h in r["held"]] + [""]
    if r["low_margin"]:
        lines += ["## Below minimum margin", ""] + [f"- {i}: {', '.join(v)}" for i, v in r["low_margin"].items()] + [""]
    lines += [f"_{r['api_cost_usd']['note']}_", ""]
    return "\n".join(lines)


def write(batch: Batch) -> dict:
    r = build(batch)
    (batch.dir / "report.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    (batch.dir / "report.md").write_text(markdown(r), encoding="utf-8")
    return r
