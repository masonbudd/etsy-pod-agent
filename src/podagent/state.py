"""Per-batch state file: what is done, what is held and why, what it cost.

Every step checks the state before doing work, so a rerun skips finished items, never
creates a second Etsy draft and never pays to regenerate an image. Writes are atomic.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

STEPS = ["checks", "images", "mockups", "copy", "prices", "provider", "draft", "disclosure"]


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Batch:
    def __init__(self, folder: Path, data: dict):
        self.dir = Path(folder)
        self.data = data

    # -- files -------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self.dir / "state.json"

    @classmethod
    def create(cls, folder: Path, batch_id: str, mode: str) -> Batch:
        folder = Path(folder)
        if (folder / "state.json").exists():
            raise FileExistsError(f"batch {batch_id} already exists")
        folder.mkdir(parents=True, exist_ok=True)
        b = cls(
            folder,
            {
                "batch_id": batch_id,
                "mode": mode,
                "created": now(),
                "items": {},
                "concepts": [],
                "costs": [],
                "events": [],
                "runs": [],
            },
        )
        b.save()
        return b

    @classmethod
    def load(cls, folder: Path) -> Batch:
        p = Path(folder) / "state.json"
        if not p.exists():
            raise FileNotFoundError(f"no batch at {folder}")
        return cls(Path(folder), json.loads(p.read_text(encoding="utf-8")))

    def save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    # -- items -------------------------------------------------------------
    @property
    def id(self) -> str:
        return self.data["batch_id"]

    @property
    def items(self) -> dict:
        return self.data["items"]

    def item_dir(self, item_id: str) -> Path:
        return self.dir / "items" / item_id

    def add_item(self, item_id: str, sku: str, source: str, **info) -> dict:
        if item_id in self.items:
            raise KeyError(f"{item_id} is already in this batch")
        self.items[item_id] = {"sku": sku, "source": source, "status": "active", "steps": {}, "added": now(), **info}
        self.item_dir(item_id).mkdir(parents=True, exist_ok=True)
        self.save()
        return self.items[item_id]

    def active(self) -> list[str]:
        return [i for i, it in self.items.items() if it["status"] != "held"]

    def held(self) -> list[str]:
        return [i for i, it in self.items.items() if it["status"] == "held"]

    # -- steps -------------------------------------------------------------
    def step(self, item_id: str, step: str) -> dict:
        return self.items[item_id]["steps"].get(step, {})

    def done(self, item_id: str, step: str) -> bool:
        return self.step(item_id, step).get("status") == "done"

    def mark(self, item_id: str, step: str, status: str = "done", **info) -> None:
        if step not in STEPS:
            raise ValueError(f"unknown step {step}")
        entry = self.items[item_id]["steps"].setdefault(step, {})
        entry.update(info, status=status, at=now())
        self.save()

    def hold(self, item_id: str, step: str, reasons: list[str], fix: str = "") -> None:
        it = self.items[item_id]
        it["status"] = "held"
        it["held"] = {"step": step, "reasons": reasons, "fix": fix, "at": now()}
        self.event(f"held {item_id} at {step}: {'; '.join(reasons)}")
        self.save()

    def release(self, item_id: str, clear_steps: list[str]) -> None:
        """Put a held item back in the queue, forgetting the steps it must redo."""
        it = self.items[item_id]
        it["status"] = "active"
        it.pop("held", None)
        for s in clear_steps:
            it["steps"].pop(s, None)
        self.event(f"released {item_id}")
        self.save()

    # -- money, time, log --------------------------------------------------
    def add_cost(self, kind: str, usd: float, item: str | None = None, note: str = "", **extra) -> dict:
        entry = {"kind": kind, "usd": usd, "item": item, "note": note, "at": now(), **extra}
        self.data["costs"].append(entry)
        self.save()
        return entry

    def event(self, text: str) -> None:
        self.data["events"].append({"at": now(), "text": text})

    def start_run(self) -> float:
        self.data["runs"].append({"start": now(), "end": None, "seconds": None})
        self.save()
        return time.perf_counter()

    def end_run(self, t0: float) -> None:
        run = self.data["runs"][-1]
        run["end"], run["seconds"] = now(), round(time.perf_counter() - t0, 1)
        self.save()
