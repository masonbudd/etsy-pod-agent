"""Command line: `podagent <command>`. Run `podagent -h` for the list.

Exit codes: 0 done, 2 waiting for input (Claude's review or copy), 1 error.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import urllib.error
from pathlib import Path

from podagent import config as C
from podagent import doctor, pipeline, providers, report, review_page
from podagent.etsy.client import PublishBlocked
from podagent.http import HttpError
from podagent.state import Batch

WAITING = 2


def _ws(args) -> C.Workspace:
    return C.Workspace(Path(args.root).resolve())


def _load(args):
    ws = _ws(args)
    return ws, C.load(ws.config_path), C.load_env(ws.root)


def _etsy(ws, env, transport=None):
    from podagent.etsy.client import EtsyClient
    from podagent.etsy.oauth import TokenStore

    for k in ("ETSY_KEYSTRING", "ETSY_SHARED_SECRET"):
        if not env.get(k):
            raise SystemExit(f"{k} is missing from .env")
    kw = {"transport": transport} if transport else {}
    store = TokenStore(ws.secrets / "etsy_token.json", env["ETSY_KEYSTRING"], **kw)
    return EtsyClient(env["ETSY_KEYSTRING"], env["ETSY_SHARED_SECRET"], store, **kw)


def _batch(ws, cfg, batch_id) -> Batch:
    return Batch.load(ws.batch_dir(cfg, batch_id))


def _finish(ctx) -> None:
    review_page.write(ctx.batch, ctx.cfg, report.write(ctx.batch))


# -- commands --------------------------------------------------------------


def cmd_init(args) -> int:
    ws = _ws(args)
    made = []
    if not ws.config_path.exists():
        shutil.copy2(C.repo_file("config.example.toml"), ws.config_path)
        made.append("config.toml")
    env_example = C.REPO / ".env.example"
    if not (ws.root / ".env").exists() and env_example.exists():
        shutil.copy2(env_example, ws.root / ".env")
        made.append(".env")
    cfg = C.load(ws.config_path)
    for key in ("inbox", "batches", "existing_designs"):
        ws.path(cfg, key).mkdir(parents=True, exist_ok=True)
    rooms = ws.path(cfg, "rooms")
    if not rooms.exists():
        shutil.copytree(C.repo_file("samples/rooms"), rooms)
        made.append(f"{rooms.name}/ (sample rooms)")
    print("Created: " + (", ".join(made) or "nothing new"))
    print("Next: edit config.toml and .env, then run `podagent doctor`.")
    return 0


def cmd_doctor(args) -> int:
    ws, cfg, env = _load(args)
    checks = doctor.run(ws, cfg, env, online=args.online, etsy_factory=lambda: _etsy(ws, env))
    print(doctor.format_table(checks))
    return 0 if doctor.passed(checks) else 1


def cmd_etsy_login(args) -> int:
    from podagent.etsy.oauth import login

    ws, cfg, env = _load(args)
    client = _etsy(ws, env)
    login(client.tokens, env.get("ETSY_REDIRECT_URI", "http://localhost:3456/callback"))
    print("Signed in. Next: `podagent etsy-discover`.")
    return 0


def cmd_etsy_discover(args) -> int:
    """Find the shop's IDs and write them to config.toml. Read-only on Etsy."""
    ws, cfg, env = _load(args)
    etsy = _etsy(ws, env)
    shop = etsy.me()["shop_id"]
    found = {"shop_id": shop}
    lists = {
        "shipping_profile_id": (etsy.shipping_profiles(shop), "shipping_profile_id", "title"),
        "return_policy_id": (etsy.return_policies(shop), "return_policy_id", "return_deadline"),
        "readiness_state_id": (etsy.readiness_states(shop), "readiness_state_id", "processing_days_display_label"),
        "production_partner_id": (etsy.production_partners(shop), "production_partner_id", "partner_name"),
    }
    for key, (rows, idk, label) in lists.items():
        print(f"{key}: " + ("; ".join(f"{r[idk]} = {r.get(label)}" for r in rows) or "none found"))
        if len(rows) == 1 or (rows and args.first):
            found[key] = rows[0][idk]
        elif rows and not cfg["etsy"].get(key):
            print(f"  more than one: set {key} in config.toml yourself (or rerun with --first)")
    C.set_values(ws.config_path, "etsy", found)
    print("Saved to config.toml [etsy]: " + ", ".join(f"{k}={v}" for k, v in found.items()))
    return 0


def cmd_rooms_calibrate(args) -> int:
    from podagent.images import calibrate

    ws, cfg, _ = _load(args)
    rooms = ws.path(cfg, "rooms")
    for name, r in calibrate.calibrate_rooms(rooms).items():
        print(f"{name}: ratio {r['ratio_measured']}" + "".join(f"\n  WARNING {w}" for w in r["warnings"]))
    print(f"Check the overlay: {rooms / 'calibration_check.jpg'}")
    return 0


def cmd_new(args) -> int:
    ws, cfg, _ = _load(args)
    if args.generate:
        b = pipeline.new_generate_batch(ws, cfg, count=args.count)
        print(json.dumps({"batch": b.id, "mode": "generate", "concepts_wanted": b.data["requested_count"]}, indent=1))
        return 0
    b = pipeline.new_batch(ws, cfg, count=args.count)
    print(json.dumps({"batch": b.id, "items": {i: it["sku"] for i, it in b.items.items()}}, indent=1))
    return 0


def cmd_research(args) -> int:
    from podagent import research

    ws, cfg, _ = _load(args)
    b = _batch(ws, cfg, args.batch)
    r = research.gather(b.dir, ws.root, cfg)
    print(
        json.dumps(
            {
                "sources_used": r["used"],
                "per_source": r["sources"],
                "dropped_banned": r["dropped_banned"],
                "top": [s["phrase"] for s in r["signals"][:15]],
                "file": str(b.dir / "research" / "research.json"),
            },
            indent=1,
        )
    )
    if not r["signals"]:
        print(
            "No research found. Save ProfitTree results to research/profittree.json in the batch, "
            f"or add {cfg.get('research', {}).get('keyword_csv', 'research/keywords.csv')}.",
            file=sys.stderr,
        )
        return WAITING
    return 0


def cmd_concepts(args) -> int:
    """Validate concepts.json, price it, and write the Gate 1 page. Exit 2 while decisions are missing."""
    from podagent import concepts as CC

    ws, cfg, _ = _load(args)
    b = _batch(ws, cfg, args.batch)
    data = CC.load_file(b)
    if data is None:
        print(f"waiting: write {b.dir / 'concepts.json'}")
        return WAITING
    rpath = b.dir / "research" / "research.json"
    errors, warnings = CC.validate(
        data,
        cfg,
        json.loads(rpath.read_text(encoding="utf-8")) if rpath.exists() else None,
        b.data.get("requested_count", cfg["batch"]["max_count"]),
    )
    if errors:
        print("concepts.json needs fixing:\n  " + "\n  ".join(errors))
        return WAITING
    CC.load_into_state(b, cfg, data)
    page = CC.write_gate_page(b, cfg, warnings)
    s = CC.summary(b)
    print(CC.markdown_table(b))
    print(json.dumps({**s, "warnings": warnings, "gate_page": str(page)}, indent=1))
    return WAITING if s["undecided"] else 0


def cmd_decide(args) -> int:
    from podagent import concepts as CC

    ws, cfg, _ = _load(args)
    b = _batch(ws, cfg, args.batch)
    edits = dict(args.edit or [])
    CC.decide(b, args.approve or [], args.reject or [], edits, args.note or "")
    CC.write_gate_page(b, cfg, [])
    print(CC.markdown_table(b))
    print(json.dumps(CC.summary(b), indent=1))
    return 0


def _image_client(env, cfg):
    from podagent.generate import ImageClient

    if not env.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is missing from .env")
    return ImageClient(env["OPENAI_API_KEY"], cfg)


def cmd_generate(args) -> int:
    from podagent import generate

    ws, cfg, env = _load(args)
    b = _batch(ws, cfg, args.batch)
    res = generate.run(
        b, cfg, _image_client(env, cfg), pipeline.taken_skus(ws.path(cfg, "batches")), args.retry_unsaved
    )
    print(json.dumps(res, indent=1))
    print(f"Next: podagent run {b.id}")
    return 0


def cmd_rooms_generate(args) -> int:
    from podagent import rooms_gen

    ws, cfg, env = _load(args)
    est = rooms_gen.estimate(cfg, args.count)
    print(json.dumps({"rooms": [p["name"] for p in rooms_gen.plan(cfg, args.count)], "estimate": est}, indent=1))
    if not args.confirm:
        print("Nothing generated. Rerun with --confirm to spend the estimate above.")
        return 0
    for r in rooms_gen.generate(ws.path(cfg, "rooms"), cfg, _image_client(env, cfg), args.count):
        print(
            f"{r['name']}: {'frame found' if r.get('frame_found') else 'NO FRAME'}"
            + "".join(f"\n  WARNING {w}" for w in r["warnings"])
        )
    print(f"Look at them in {ws.path(cfg, 'rooms') / 'candidates'}, then rooms-approve or rooms-regenerate each.")
    return 0


def cmd_rooms_decide(args) -> int:
    from podagent import rooms_gen

    ws, cfg, env = _load(args)
    rooms = ws.path(cfg, "rooms")
    if args.cmd == "rooms-approve":
        rec = rooms_gen.approve(rooms, args.name, args.alt)
    elif args.cmd == "rooms-reject":
        rec = rooms_gen.reject(rooms, args.name)
    else:
        if not args.confirm:
            cost = rooms_gen.estimate(cfg, 1).get("expected_usd", "?")
            print(f"Regenerating costs about ${cost}. Rerun with --confirm.")
            return 0
        rec = rooms_gen.regenerate(rooms, cfg, _image_client(env, cfg), args.name)
    print(
        json.dumps({k: rec.get(k) for k in ("name", "status", "frame_found", "ratio_measured", "warnings")}, indent=1)
    )
    return 0


def cmd_run(args) -> int:
    ws, cfg, env = _load(args)
    batch = _batch(ws, cfg, args.batch)
    ctx = pipeline.Context(
        ws, cfg, env, batch, dry_run=args.dry_run, provider=providers.get(cfg, env), etsy_factory=lambda: _etsy(ws, env)
    )
    res = pipeline.run(ctx, report_fn=_finish)
    res["review_page"] = str(batch.dir / "review.html")
    print(json.dumps(res, indent=1))
    return WAITING if res["waiting"] else (1 if res["errors"] else 0)


def cmd_brief(args) -> int:
    from podagent import listing_copy

    ws, cfg, _ = _load(args)
    b = _batch(ws, cfg, args.batch)
    items = [
        {
            "item": i,
            "sku": it["sku"],
            "subject": it["steps"].get("checks", {}).get("subject", ""),
            "lettering": it["steps"].get("checks", {}).get("lettering", ""),
            "errors_last_time": it["steps"].get("copy", {}).get("errors", []),
        }
        for i, it in b.items.items()
        if it["status"] != "held" and b.done(i, "checks") and not b.done(i, "copy")
    ]
    print(json.dumps(listing_copy.brief(cfg, items), indent=1, ensure_ascii=False))
    return 0


def cmd_status(args) -> int:
    ws, cfg, _ = _load(args)
    b = _batch(ws, cfg, args.batch)
    for i, it in b.items.items():
        steps = " ".join(f"{s}:{it['steps'].get(s, {}).get('status', '-')}" for s in pipeline.ORDER)
        print(f"{i:28} {it['status']:8} {steps}")
        if it["status"] == "held":
            print(f"{'':28} held: {'; '.join(it['held']['reasons'])}")
    return 0


def cmd_mark(args) -> int:
    ws, cfg, _ = _load(args)
    b = _batch(ws, cfg, args.batch)
    b.mark(args.item, args.step, args.status, note=args.note or "marked by hand")
    print(f"{args.item} {args.step}: {args.status}")
    return 0


def cmd_release(args) -> int:
    ws, cfg, _ = _load(args)
    b = _batch(ws, cfg, args.batch)
    d = b.item_dir(args.item)
    for f in ("checks.json", "copy_errors.json"):
        (d / f).unlink(missing_ok=True)
    b.release(args.item, [s for s in pipeline.ORDER if s != "draft"])
    print(f"{args.item} is back in the queue. Run `podagent run {b.id}`.")
    return 0


def cmd_trim_border(args) -> int:
    from PIL import Image

    from podagent import checks

    ws, cfg, _ = _load(args)
    b = _batch(ws, cfg, args.batch)
    d = b.item_dir(args.item)
    img = Image.open(d / "source.png")
    found = checks.border(img, cfg["checks"]["border_min_pct"])
    if not found["found"]:
        print("No border detected in code. Edit the file by hand, then `podagent release`.")
        return 1
    shutil.copy2(d / "source.png", d / "source_before_trim.png")
    checks.trim_border(img, found).save(d / "source.png")
    for f in ("master.png", "vision.json", "preview.jpg"):
        (d / f).unlink(missing_ok=True)
    shutil.rmtree(d / "print", ignore_errors=True)
    shutil.rmtree(d / "photos", ignore_errors=True)
    cmd_release(args)
    print(f"Trimmed {found['crop_px']} px. Claude reviews the trimmed design on the next run.")
    return 0


def cmd_provider_link(args) -> int:
    ws, cfg, env = _load(args)
    b = _batch(ws, cfg, args.batch)
    prov = providers.get(cfg, env)
    ctx = pipeline.Context(ws, cfg, env, b, provider=prov)
    for i, it in b.items.items():
        if not it["steps"].get("draft", {}).get("listing_id"):
            continue
        res = prov.link(pipeline.item_context(ctx, i), it["steps"].get("provider", {}).get("plan"))
        print(f"{i}: {res.status} - {res.detail}" + "".join(f"\n  - {t}" for t in res.todo))
    return 0


def cmd_review(args) -> int:
    ws, cfg, _ = _load(args)
    b = _batch(ws, cfg, args.batch)
    print(review_page.write(b, cfg, report.write(b)))
    return 0


def cmd_demo(args) -> int:
    from podagent import demo

    results = demo.run(Path(args.out), args.mode)
    failed = False
    for name, res in results.items():
        print(f"\n[{name}]")
        if "generated" in res:
            print(f"Gate 1 page: {res['gate_page']}")
            print(f"Generated (replayed, not charged): {', '.join(res['generated']) or 'none'}")
        print(f"Drafted (dry run): {', '.join(res['drafted']) or 'none'}")
        for item, reasons in res["held"].items():
            print(f"Held: {item}: {'; '.join(reasons)}")
        print(f"Review page: {res['review_page']}")
        failed = failed or bool(res["errors"])
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="podagent", description="Checked Etsy draft listings for print-on-demand wall art."
    )
    p.add_argument("--root", default=".", help="workspace folder holding config.toml (default: current folder)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create config.toml, .env and folders").set_defaults(fn=cmd_init)
    s = sub.add_parser("doctor", help="check every connection")
    s.add_argument("--online", action="store_true", help="also make free read-only API calls")
    s.set_defaults(fn=cmd_doctor)
    sub.add_parser("etsy-login", help="sign in to Etsy (OAuth PKCE)").set_defaults(fn=cmd_etsy_login)
    s = sub.add_parser("etsy-discover", help="find shop, shipping, returns and processing IDs")
    s.add_argument("--first", action="store_true", help="take the first option where there are several")
    s.set_defaults(fn=cmd_etsy_discover)
    sub.add_parser("rooms-calibrate", help="detect frame corners in the room templates").set_defaults(
        fn=cmd_rooms_calibrate
    )
    s = sub.add_parser("new", help="start a batch from designs/inbox, or an empty one with --generate")
    s.add_argument("--count", type=int)
    s.add_argument("--generate", action="store_true")
    s.set_defaults(fn=cmd_new)
    s = sub.add_parser("research", help="merge research sources into the batch's research.json")
    s.add_argument("batch")
    s.set_defaults(fn=cmd_research)
    s = sub.add_parser("concepts", help="check concepts.json, price it and write the Gate 1 page")
    s.add_argument("batch")
    s.set_defaults(fn=cmd_concepts)
    s = sub.add_parser("decide", help="record Gate 1 decisions")
    s.add_argument("batch")
    s.add_argument("--approve", nargs="*")
    s.add_argument("--reject", nargs="*")
    s.add_argument("--edit", nargs=2, action="append", metavar=("ID", "PROMPT"), help="replace a prompt and approve it")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_decide)
    s = sub.add_parser("generate", help="generate approved concepts and regenerate held designs once")
    s.add_argument("batch")
    s.add_argument(
        "--retry-unsaved", action="store_true", help="request again an image that was paid for but not saved"
    )
    s.set_defaults(fn=cmd_generate)
    s = sub.add_parser("rooms-generate", help="generate room templates (shows the cost first)")
    s.add_argument("--count", type=int, default=3)
    s.add_argument("--confirm", action="store_true")
    s.set_defaults(fn=cmd_rooms_generate)
    s = sub.add_parser("rooms-approve", help="use a generated room")
    s.add_argument("name")
    s.add_argument("--alt", required=True)
    s.set_defaults(fn=cmd_rooms_decide)
    s = sub.add_parser("rooms-reject", help="drop a generated room")
    s.add_argument("name")
    s.set_defaults(fn=cmd_rooms_decide)
    s = sub.add_parser("rooms-regenerate", help="drop a generated room and make a new one")
    s.add_argument("name")
    s.add_argument("--confirm", action="store_true")
    s.set_defaults(fn=cmd_rooms_decide)
    s = sub.add_parser("run", help="advance a batch as far as it can go")
    s.add_argument("batch")
    s.add_argument("--dry-run", action="store_true", help="write Etsy payloads instead of creating drafts")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("brief", help="print the copywriting brief for Claude")
    s.add_argument("batch")
    s.set_defaults(fn=cmd_brief)
    s = sub.add_parser("status", help="show each item's steps")
    s.add_argument("batch")
    s.set_defaults(fn=cmd_status)
    s = sub.add_parser("mark", help="record a step done by hand (e.g. disclosure)")
    s.add_argument("batch")
    s.add_argument("item")
    s.add_argument("step")
    s.add_argument("--status", default="done")
    s.add_argument("--note")
    s.set_defaults(fn=cmd_mark)
    s = sub.add_parser("release", help="put a held design back in the queue")
    s.add_argument("batch")
    s.add_argument("item")
    s.set_defaults(fn=cmd_release)
    s = sub.add_parser("trim-border", help="cut a detected border off a held design")
    s.add_argument("batch")
    s.add_argument("item")
    s.set_defaults(fn=cmd_trim_border)
    s = sub.add_parser("provider-link", help="after publishing: connect listings at the print provider")
    s.add_argument("batch")
    s.set_defaults(fn=cmd_provider_link)
    s = sub.add_parser("review", help="rebuild the review page and report")
    s.add_argument("batch")
    s.set_defaults(fn=cmd_review)
    s = sub.add_parser("demo", help="run the pipeline on the bundled samples, no accounts needed")
    s.add_argument("--out", default="demo_output")
    s.add_argument("--mode", choices=["both", "inbox", "generate"], default="both")
    s.set_defaults(fn=cmd_demo)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except (C.ConfigError, FileNotFoundError, FileExistsError, KeyError, ValueError, PublishBlocked) as e:
        print(f"error: {e}", file=sys.stderr)
    except HttpError as e:
        print(f"error: {e.short()}", file=sys.stderr)
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        print(
            f"error: network problem: {getattr(e, 'reason', e)}. Check your connection and try again.", file=sys.stderr
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
