"""Run a batch. Each step handles every item that is ready for it and skips work already done,
so `podagent run <batch>` can be repeated safely after a crash, a fix or Claude's input.

Steps: checks -> images -> mockups -> copy -> prices -> provider -> draft.
Two steps wait for Claude: checks (vision.json) and copy (copy.json). The run reports what it
is waiting for and Claude supplies it, then reruns.
"""
from __future__ import annotations

import json
import shutil
import traceback
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from PIL import Image

from podagent import checks, listing_copy, pricing, rules
from podagent.config import Workspace, variant_suffix_len
from podagent.etsy import drafts
from podagent.images import crops, mockups, upscale
from podagent.providers.base import ItemContext
from podagent.state import Batch

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}
ORDER = ["checks", "images", "mockups", "copy", "prices", "provider", "draft"]
PREVIEW_PX = 1024


@dataclass
class Context:
    ws: Workspace
    cfg: dict
    env: dict
    batch: Batch
    dry_run: bool = False
    provider: object = None
    etsy_factory: object = None          # builds the EtsyClient on first use, so dry runs need no account
    etsy: object = None
    waiting: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    _sections: dict = field(default_factory=dict)

    def get_etsy(self):
        if self.etsy is None:
            if self.etsy_factory is None:
                raise RuntimeError("no Etsy client: run with --dry-run or set up Etsy first")
            self.etsy = self.etsy_factory()
        return self.etsy

    @property
    def rooms_dir(self) -> Path:
        return self.ws.path(self.cfg, "rooms")


# -- batches ---------------------------------------------------------------

def next_batch_id(cfg: dict, batches: Path, today: date | None = None) -> str:
    today = today or date.today()
    for seq in range(1, 1000):
        bid = cfg["naming"]["batch_id"].format(date=today.isoformat(), seq=seq)
        if not (batches / bid).exists():
            return bid
    raise RuntimeError("too many batches today")


def taken_skus(batches: Path) -> set[str]:
    out = set()
    for st in batches.glob("*/state.json"):
        out |= {it["sku"] for it in json.loads(st.read_text(encoding="utf-8"))["items"].values()}
    return out


def inbox_files(ws: Workspace, cfg: dict) -> list[Path]:
    inbox = ws.path(cfg, "inbox")
    return sorted(p for p in inbox.iterdir() if p.suffix.lower() in IMAGE_EXT) if inbox.is_dir() else []


def new_batch(ws: Workspace, cfg: dict, count: int | None = None, files: list[Path] | None = None,
              mode: str = "inbox", batch_id: str | None = None, keep_originals: bool = False) -> Batch:
    """Start a batch from the inbox (files are moved into the batch so they are never picked twice)."""
    limit = min(count or cfg["batch"]["default_count"], cfg["batch"]["max_count"])
    picks = (files if files is not None else inbox_files(ws, cfg))[:limit]
    if not picks:
        raise FileNotFoundError(f"no designs in {ws.path(cfg, 'inbox')}")
    batches = ws.path(cfg, "batches")
    bid = batch_id or next_batch_id(cfg, batches)
    batch = Batch.create(batches / bid, bid, mode)
    taken = taken_skus(batches)
    suffix = variant_suffix_len(cfg)
    for p in picks:
        item_id = rules.slugify(p.stem)
        while item_id in batch.items:
            item_id += "-x"
        sku = rules.make_sku(cfg["naming"]["sku_prefix"], p.stem, taken, suffix)
        taken.add(sku)
        batch.add_item(item_id, sku, p.name)
        d = batch.item_dir(item_id)
        original = d / f"original{p.suffix.lower()}"
        (shutil.copy2 if keep_originals else shutil.move)(str(p), original)
        Image.open(original).convert("RGB").save(d / "source.png")
    batch.event(f"created with {len(picks)} designs")
    batch.save()
    return batch


def new_generate_batch(ws: Workspace, cfg: dict, count: int | None = None, batch_id: str | None = None) -> Batch:
    """An empty batch for generate mode: items are added when approved concepts are generated."""
    batches = ws.path(cfg, "batches")
    bid = batch_id or next_batch_id(cfg, batches)
    batch = Batch.create(batches / bid, bid, "generate")
    batch.data["requested_count"] = min(count or cfg["batch"]["default_count"], cfg["batch"]["max_count"])
    batch.save()
    return batch


# -- steps -----------------------------------------------------------------

def step_checks(ctx: Context, item_id: str) -> None:
    b, d = ctx.batch, ctx.batch.item_dir(item_id)
    vision_path = d / "vision.json"
    if not vision_path.exists():
        preview = d / "preview.jpg"
        if not preview.exists():
            im = Image.open(d / "source.png")
            im.thumbnail((PREVIEW_PX, PREVIEW_PX))
            im.save(preview, quality=90)
        ctx.waiting.setdefault("vision review", []).append(str(preview.relative_to(ctx.ws.root)))
        return
    vision = json.loads(vision_path.read_text(encoding="utf-8"))
    problems = checks.validate_vision(vision)
    if problems:
        ctx.waiting.setdefault("fix vision.json", []).append(f"{item_id}: {'; '.join(problems)}")
        return
    img = Image.open(d / "source.png")
    code = checks.code_checks(img, b.items[item_id]["source"], ctx.cfg, _other_hashes(ctx, item_id))
    v = checks.verdict(code, vision, ctx.cfg, b.items[item_id].get("lettering_expected", ""))
    (d / "checks.json").write_text(json.dumps({"code": code, "vision": vision, **v}, indent=1, default=str), encoding="utf-8")
    b.mark(item_id, "checks", verdict=v["verdict"], warnings=v["warnings"], reasons=v["reasons"], dhash=code["dhash"],
           subject=vision["subject"], lettering=vision["lettering"])
    if v["verdict"] == "hold":
        b.hold(item_id, "checks", v["reasons"], v["suggested_fix"])


def _other_hashes(ctx: Context, item_id: str) -> dict[str, int]:
    """Hashes to compare against: earlier items in this batch, items in other batches, existing designs."""
    out = checks.hashes_of(ctx.ws.path(ctx.cfg, "existing_designs"))
    for st in ctx.ws.path(ctx.cfg, "batches").glob("*/state.json"):
        data = json.loads(st.read_text(encoding="utf-8"))
        if data["batch_id"] == ctx.batch.id:
            continue
        for iid, it in data["items"].items():
            h = it["steps"].get("checks", {}).get("dhash")
            if h is not None:
                out[f"{data['batch_id']}/{iid}"] = h
    for iid in ctx.batch.items:
        if iid == item_id:
            break
        out[f"{ctx.batch.id}/{iid}"] = checks.dhash(Image.open(ctx.batch.item_dir(iid) / "source.png"))
    return out


def step_images(ctx: Context, item_id: str) -> None:
    d, sku = ctx.batch.item_dir(item_id), ctx.batch.items[item_id]["sku"]
    master = d / "master.png"
    info = ctx.batch.step(item_id, "images").get("upscale")
    if not master.exists():
        info = upscale.upscale(d / "source.png", master, ctx.cfg, ctx.env)
    files = crops.make_print_files(master, d / "print", ctx.cfg, sku)
    ctx.batch.mark(item_id, "images", upscale=info, print_files=files)


def step_mockups(ctx: Context, item_id: str) -> None:
    d = ctx.batch.item_dir(item_id)
    subject = ctx.batch.step(item_id, "checks").get("subject") or "Art print"
    rooms = mockups.load_rooms(ctx.rooms_dir)
    photos = mockups.build(d / "master.png", d / "photos", rooms, subject[:1].upper() + subject[1:],
                           ctx.cfg["listing"].get("max_photos", 10))
    ctx.batch.mark(item_id, "mockups", photos=photos)


def step_copy(ctx: Context, item_id: str) -> None:
    d = ctx.batch.item_dir(item_id)
    path = d / "copy.json"
    if not path.exists():
        ctx.waiting.setdefault("listing copy", []).append(item_id)
        return
    copy = json.loads(path.read_text(encoding="utf-8"))
    errors, warnings = listing_copy.validate(copy, ctx.cfg)
    if not errors:
        (d / "copy_errors.json").unlink(missing_ok=True)
        ctx.batch.mark(item_id, "copy", warnings=warnings, title=copy["title"])
        return
    seen = ctx.batch.step(item_id, "copy")
    tried = {json.dumps(x, sort_keys=True) for x in seen.get("rejected", [])}
    rejected = seen.get("rejected", [])
    if json.dumps(copy, sort_keys=True) not in tried:
        rejected = rejected + [copy]
    (d / "copy_errors.json").write_text(json.dumps(errors, indent=1), encoding="utf-8")
    ctx.batch.mark(item_id, "copy", "waiting", rejected=rejected, errors=errors)
    if len(rejected) >= listing_copy.MAX_ATTEMPTS:
        ctx.batch.hold(item_id, "copy", [f"copy failed validation {len(rejected)} times: " + "; ".join(errors)],
                       "edit copy.json by hand, then `podagent release`")
    else:
        ctx.waiting.setdefault("fix listing copy", []).append(f"{item_id}: {'; '.join(errors)}")


def step_prices(ctx: Context, item_id: str) -> None:
    sku = ctx.batch.items[item_id]["sku"]
    rows = pricing.price_table(ctx.cfg)
    variants = drafts.variant_rows(sku, rows, ctx.cfg)
    bad = [e for v in variants for e in rules.check_sku(v["sku"])]
    if bad:
        ctx.batch.hold(item_id, "prices", bad, "shorten naming.sku_prefix or the size/frame codes")
        return
    ctx.batch.mark(item_id, "prices", variants=variants, summary=pricing.summary(rows))


def item_context(ctx: Context, item_id: str) -> ItemContext:
    it, d = ctx.batch.items[item_id], ctx.batch.item_dir(item_id)
    files = {k: d / "print" / f for k, f in it["steps"]["images"]["print_files"].items()}
    return ItemContext(ctx.batch.id, item_id, it["sku"], it["steps"]["prices"]["variants"], files, ctx.batch.dir,
                       it["steps"].get("draft", {}).get("listing_id"))


def step_provider(ctx: Context, item_id: str) -> None:
    res = ctx.provider.prepare(item_context(ctx, item_id))
    if res.status == "failed":
        ctx.batch.mark(item_id, "provider", "failed", adapter=ctx.provider.name, detail=res.detail)
        ctx.waiting.setdefault("fix provider settings", []).append(f"{item_id}: {res.detail}")
        return
    ctx.batch.mark(item_id, "provider", adapter=ctx.provider.name, result=res.status, detail=res.detail, todo=res.todo,
                   plan=res.data.get("plan"))


def step_draft(ctx: Context, item_id: str) -> None:
    client = None if ctx.dry_run else ctx.get_etsy()
    drafts.create(client, ctx.batch, item_id, ctx.cfg, ctx._sections, dry_run=ctx.dry_run)


STEP_FUNCS = {"checks": step_checks, "images": step_images, "mockups": step_mockups, "copy": step_copy,
              "prices": step_prices, "provider": step_provider, "draft": step_draft}


def ready(batch: Batch, item_id: str, step: str) -> bool:
    it = batch.items[item_id]
    if it["status"] == "held" or batch.done(item_id, step):
        return False
    return all(batch.done(item_id, s) for s in ORDER[:ORDER.index(step)])


def run(ctx: Context, report_fn=None) -> dict:
    """Advance every item as far as it can go. Returns what happened and what is waiting."""
    t0 = ctx.batch.start_run()
    if not ctx.dry_run:                  # a dry-run "draft" is only a payload: the real run must still create it
        for it in ctx.batch.items.values():
            if it["steps"].get("draft", {}).get("dry_run"):
                del it["steps"]["draft"]
        ctx.batch.save()
    try:
        for step in ORDER:
            for item_id in list(ctx.batch.items):
                if not ready(ctx.batch, item_id, step):
                    continue
                try:
                    STEP_FUNCS[step](ctx, item_id)
                except Exception as e:  # noqa: BLE001 - one item's failure must not stop the batch
                    ctx.errors[f"{item_id} at {step}"] = f"{type(e).__name__}: {e}"
                    ctx.batch.mark(item_id, step, "failed", error=str(e)[:500], trace=traceback.format_exc()[-1500:])
        if not ctx.dry_run and any(it["steps"].get("draft", {}).get("listing_id") for it in ctx.batch.items.values()):
            drafts.write_disclosure_tasks(ctx.batch, ctx.batch.dir / "disclosure_tasks.md")
    finally:
        ctx.batch.end_run(t0)
    if report_fn:
        report_fn(ctx)
    return summary(ctx)


def summary(ctx: Context) -> dict:
    items = ctx.batch.items
    return {"batch": ctx.batch.id, "dir": str(ctx.batch.dir),
            "drafted": [i for i, it in items.items() if it["steps"].get("draft", {}).get("status") == "done"],
            "held": {i: it["held"]["reasons"] for i, it in items.items() if it["status"] == "held"},
            "waiting": ctx.waiting, "errors": ctx.errors}
