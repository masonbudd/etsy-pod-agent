"""One static HTML page per batch: every draft with its photos, copy, prices and checks,
plus held designs with the reason. The shop owner reviews here, then publishes in Etsy."""
from __future__ import annotations

import json
from html import escape
from pathlib import Path

from PIL import Image

from podagent.state import Batch

THUMB_PX = 900

CSS = """
:root{--bg:#f7f5f1;--card:#fff;--ink:#22201c;--muted:#6d675e;--line:#e4dfd6;--ok:#2f7a4f;--warn:#a5610b;--bad:#b3261e;--chip:#efebe4;--accent:#3d5a80}
@media (prefers-color-scheme:dark){:root{--bg:#171614;--card:#211f1c;--ink:#efebe4;--muted:#a59e93;--line:#37332d;--ok:#6cc391;--warn:#e3a54b;--bad:#f08a80;--chip:#2c2925;--accent:#8fb0d8}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 16px 60px}h1{font-size:26px;margin:0 0 4px}h2{font-size:19px;margin:36px 0 12px}
.sub{color:var(--muted);margin:0 0 18px}.banner{border:1px solid var(--line);border-left:4px solid var(--accent);background:var(--card);padding:12px 16px;border-radius:8px;margin-bottom:18px}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:8px}.stat{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 14px;min-width:120px}
.stat b{display:block;font-size:22px}.stat span{color:var(--muted);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:18px;margin-bottom:18px;display:grid;grid-template-columns:minmax(0,340px) minmax(0,1fr);gap:22px}
@media (max-width:760px){.card{grid-template-columns:1fr}}
.hero{width:100%;border-radius:6px;display:block}.thumbs{display:flex;gap:6px;margin-top:6px;flex-wrap:wrap}.thumbs img{width:62px;height:62px;object-fit:cover;border-radius:4px;border:1px solid var(--line)}
.title{font-size:17px;font-weight:600;margin:0 0 6px}.meta{color:var(--muted);font-size:13px;margin-bottom:10px}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 12px}.chip{background:var(--chip);border-radius:99px;padding:2px 10px;font-size:13px}
.row{margin:6px 0}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
details{margin-top:10px}summary{cursor:pointer;color:var(--accent)}table{border-collapse:collapse;margin-top:8px;font-size:13px;width:100%}
td,th{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left}tr.low td{color:var(--warn)}tr.off td{color:var(--muted)}
a.btn{display:inline-block;margin-top:12px;background:var(--accent);color:#fff;text-decoration:none;padding:8px 14px;border-radius:6px;font-weight:600}
.held .card{grid-template-columns:minmax(0,180px) minmax(0,1fr)}ul{margin:6px 0;padding-left:20px}
"""


def _thumb(src: Path, dst: Path) -> None:
    if not dst.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        im = Image.open(src).convert("RGB")
        im.thumbnail((THUMB_PX, THUMB_PX))
        im.save(dst, quality=82)


def _money(cfg: dict, v) -> str:
    sym = {"GBP": "£", "USD": "$", "EUR": "€", "AUD": "A$", "CAD": "C$"}.get(cfg["shop"]["currency"], "")
    return f"{sym}{v:.2f}" if sym else f"{v:.2f} {cfg['shop']['currency']}"


def _draft_card(batch: Batch, item_id: str, cfg: dict) -> str:
    it, d = batch.items[item_id], batch.item_dir(item_id)
    st = it["steps"]
    copy = json.loads((d / "copy.json").read_text(encoding="utf-8"))
    photos = st["mockups"]["photos"]
    rel = []
    for p in photos:
        dst = batch.dir / "review_assets" / item_id / p["file"]
        _thumb(d / "photos" / p["file"], dst)
        rel.append((dst.relative_to(batch.dir).as_posix(), p["alt"]))
    summ = st["prices"]["summary"]
    rows = "".join(_price_row(v, cfg) for v in st["prices"]["variants"])
    warn = st["checks"].get("warnings", []) + st["copy"].get("warnings", [])
    checks = "<span class='ok'>✓ Passed: border, resolution, duplicates, banned terms, spelling, IP</span>"
    if warn:
        checks += "<ul>" + "".join(f"<li class='warn'>{escape(w)}</li>" for w in warn) + "</ul>"
    prov = st.get("provider", {})
    todo = "".join(f"<li>{escape(t)}</li>" for t in prov.get("todo", []))
    draft = st.get("draft", {})
    if draft.get("dry_run"):
        etsy = f"<p class='row warn'>Dry run: no draft was created. The exact API payload is in <code>items/{escape(item_id)}/etsy_payload.json</code>.</p>"
    else:
        disc = "<span class='ok'>✓ AI disclosure set</span>" if batch.done(item_id, "disclosure") else \
               "<span class='warn'>AI disclosure still to tick (disclosure_tasks.md)</span>"
        etsy = f"<p class='row'>{disc}</p><a class='btn' href='{escape(draft.get('url', '#'))}'>Open the draft in Etsy</a>"
    low = f" <span class='warn'>⚠ below minimum margin: {escape(', '.join(summ['low_margin']))}</span>" if summ["low_margin"] else ""
    return f"""<section class="card">
<div><img class="hero" src="{escape(rel[0][0])}" alt="{escape(rel[0][1])}">
<div class="thumbs">{''.join(f'<img src="{escape(s)}" alt="{escape(a)}">' for s, a in rel[1:])}</div></div>
<div><p class="title">{escape(copy['title'])}</p>
<p class="meta">{escape(it['sku'])} · section: {escape(copy['section'])} · {len(copy['title'])}/140 characters · {len(photos)} photos</p>
<div class="chips">{''.join(f'<span class="chip">{escape(t)}</span>' for t in copy['tags'])}</div>
<p class="row"><b>{_money(cfg, summ['min_price'])} – {_money(cfg, summ['max_price'])}</b> across {summ['enabled']} options, lowest margin {summ['lowest_margin']:.0%}{low}</p>
<p class="row">{checks}</p>
<p class="row">Print provider ({escape(prov.get('adapter', ''))}): {escape(prov.get('detail', ''))}</p>{f'<ul>{todo}</ul>' if todo else ''}
<details><summary>Description</summary><pre style="white-space:pre-wrap;font:inherit">{escape(_description(copy, cfg))}</pre></details>
<details><summary>Prices and SKUs</summary><table><tr><th>Size</th><th>Frame</th><th>SKU</th><th>Price</th><th>Margin</th></tr>{rows}</table></details>
{etsy}</div></section>"""


def _price_row(v: dict, cfg: dict) -> str:
    cls = "low" if v["low_margin"] else "off" if not v["enabled"] else ""
    price = _money(cfg, v["price"]) if v["enabled"] else "not offered"
    margin = "" if v["margin"] is None else f"{v['margin']:.0%}" + (" ⚠" if v["low_margin"] else "")
    return (f"<tr class='{cls}'><td>{escape(v['size'])}</td><td>{escape(v['frame'] or '')}</td>"
            f"<td>{escape(v['sku'])}</td><td>{price}</td><td>{margin}</td></tr>")


def _description(copy: dict, cfg: dict) -> str:
    from podagent.listing_copy import description
    return description(copy, cfg)


def _held_card(batch: Batch, item_id: str) -> str:
    it, d = batch.items[item_id], batch.item_dir(item_id)
    src = d / "preview.jpg" if (d / "preview.jpg").exists() else d / "source.png"
    dst = batch.dir / "review_assets" / item_id / "held.jpg"
    _thumb(src, dst)
    h = it["held"]
    return f"""<section class="card"><div><img class="hero" src="{escape(dst.relative_to(batch.dir).as_posix())}" alt="held design {escape(item_id)}"></div>
<div><p class="title">{escape(item_id)}</p><p class="meta">{escape(it['source'])} · held at: {escape(h['step'])}</p>
<ul>{''.join(f"<li class='bad'>{escape(r)}</li>" for r in h['reasons'])}</ul><p class="row"><b>Suggested fix:</b> {escape(h['fix'])}</p></div></section>"""


def _gate1(batch: Batch) -> str:
    cs = batch.data.get("concepts", [])
    if not cs:
        return ""
    rows = "".join(
        f"<tr class='{'off' if c.get('decision') == 'rejected' else ''}'><td>{escape(c['id'])}</td><td>{escape(c['target_keyword'])}</td>"
        f"<td>{escape((c.get('decision') or 'waiting') + (', edited' if c.get('edited') else ''))}</td>"
        f"<td>{'' if c.get('estimate_usd') is None else '$' + format(c['estimate_usd'], '.3f')}</td>"
        f"<td>{escape(c.get('note') or '')}</td></tr>" for c in cs)
    return (f"<h2>Gate 1: concepts ({sum(1 for c in cs if c.get('decision') == 'approved')} of {len(cs)} approved)</h2>"
            f"<table><tr><th>Concept</th><th>Target keyword</th><th>Decision</th><th>Est. cost</th><th>Note</th></tr>{rows}</table>")


def write(batch: Batch, cfg: dict, report: dict) -> Path:
    items = batch.items
    drafted = [i for i, it in items.items() if it["steps"].get("draft", {}).get("status") == "done"]
    held = [i for i, it in items.items() if it["status"] == "held"]
    waiting = [i for i in items if i not in drafted and i not in held]
    dry = any(items[i]["steps"]["draft"].get("dry_run") for i in drafted)
    stats = [(len(items), "designs"), (len(drafted), "dry-run drafts" if dry else "Etsy drafts"), (len(held), "held"),
             (f"{report['time']['pipeline_seconds']:.0f}s", "pipeline time"), (f"${report['api_cost_usd']['total']:.2f}", "image API cost")]
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Review {escape(batch.id)}</title><style>{CSS}</style></head><body><main>
<h1>Review batch {escape(batch.id)}</h1><p class="sub">Generated {escape(report['generated'])} · mode: {escape(batch.data['mode'])}</p>
<div class="banner">Nothing here is live. Each listing below is an Etsy <b>draft</b>. Check it, then click Publish in Etsy yourself.</div>
<div class="stats">{''.join(f'<div class="stat"><b>{escape(str(n))}</b><span>{escape(l)}</span></div>' for n, l in stats)}</div>
{_gate1(batch)}
<h2>Drafts ({len(drafted)})</h2>{''.join(_draft_card(batch, i, cfg) for i in drafted) or '<p class="sub">None yet.</p>'}
<div class="held"><h2>Held ({len(held)})</h2>{''.join(_held_card(batch, i) for i in held) or '<p class="sub">Nothing held.</p>'}</div>
{f'<h2>Still in progress ({len(waiting)})</h2><p class="sub">{escape(", ".join(waiting))}: run <code>podagent run {escape(batch.id)}</code> again.</p>' if waiting else ''}
</main></body></html>"""
    out = batch.dir / "review.html"
    out.write_text(html, encoding="utf-8")
    return out
