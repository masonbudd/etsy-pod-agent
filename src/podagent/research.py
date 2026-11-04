"""Research: what themes and buyer phrases are selling, from pluggable sources.

Sources, in priority order:
  profittree   Claude calls the ProfitTree MCP inside the skill and saves the results to
               batches/<id>/research/profittree.json. Python only reads that file.
  keyword_csv  research/keywords.csv: keyword plus optional searches, competition, sales.

Research keeps phrases and numbers only. Anything that looks like a specific listing
(titles, listing IDs, shop names, URLs) is dropped on load, so concepts are built from
themes and buyer language, never from someone else's listing.
"""

from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from podagent import rules

LISTING_KEYS = {"title", "listing_id", "listing", "shop", "shop_name", "url", "image", "image_url", "seller"}
URL = re.compile(r"https?://|etsy\.com/listing", re.I)


@dataclass
class Signal:
    phrase: str
    source: str
    score: float  # 0-1, comparable across sources
    searches: float | None = None
    competition: float | None = None
    sales: float | None = None


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", "")) if v not in (None, "") else None
    except ValueError:
        return None


def _score(searches, competition, sales) -> float:
    """Demand over competition, squashed to 0-1. Missing numbers count as neutral."""
    demand = math.log10(1 + (searches or 0)) + math.log10(1 + (sales or 0))
    crowd = math.log10(10 + (competition or 1000))
    return round(1 - math.exp(-demand / crowd), 4) if demand else 0.1


def clean_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", str(text)).strip().lower()


def _make(raw: dict, source: str) -> Signal | None:
    phrase = clean_phrase(raw.get("phrase") or raw.get("keyword") or "")
    if not phrase or URL.search(phrase) or len(phrase) > 60:
        return None
    s, c, n = _num(raw.get("searches")), _num(raw.get("competition")), _num(raw.get("sales"))
    return Signal(phrase, source, _score(s, c, n), s, c, n)


def from_keyword_csv(path: Path) -> list[Signal]:
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return [
            s
            for row in csv.DictReader(fh)
            if (s := _make({k.strip().lower(): v for k, v in row.items() if k}, "keyword_csv"))
        ]


def from_profittree(path: Path) -> list[Signal]:
    """Load the file Claude saved from the ProfitTree MCP: {"keywords": [{phrase, searches, competition, sales}]}."""
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("keywords", []) if isinstance(data, dict) else data
    out = []
    for row in rows:
        row = {k: v for k, v in row.items() if k not in LISTING_KEYS}
        if s := _make(row, "profittree"):
            out.append(s)
    return out


def gather(batch_dir: Path, root: Path, cfg: dict) -> dict:
    """Merge every available source, drop banned terms and duplicates, rank, and save research.json."""
    rcfg = cfg.get("research", {})
    sources = {
        "profittree": from_profittree(batch_dir / "research" / "profittree.json"),
        "keyword_csv": from_keyword_csv(root / rcfg.get("keyword_csv", "research/keywords.csv")),
    }
    banned = cfg["checks"]["banned_terms"] + cfg["listing"].get("avoid_words", [])
    best: dict[str, Signal] = {}
    dropped = []
    for name in ("profittree", "keyword_csv"):  # earlier sources win ties
        for s in sources[name]:
            if rules.find_terms(s.phrase, banned):
                dropped.append(s.phrase)
                continue
            if s.phrase not in best or s.score > best[s.phrase].score:
                best[s.phrase] = s
    ranked = sorted(best.values(), key=lambda s: -s.score)[: rcfg.get("top_n", 30)]
    result = {
        "sources": {k: len(v) for k, v in sources.items()},
        "used": [k for k, v in sources.items() if v],
        "dropped_banned": sorted(set(dropped)),
        "signals": [asdict(s) for s in ranked],
    }
    out = batch_dir / "research" / "research.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result
