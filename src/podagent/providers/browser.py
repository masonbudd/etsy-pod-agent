"""For providers with no API: prepare files and a task list that Claude works through with
Claude in Chrome, following a playbook written for that provider's website.

Python never clicks anything. The skill tells Claude to stop and ask before every step the
playbook marks [ASK], such as creating products or saving changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from podagent.providers.base import Check, ItemContext, Result
from podagent.providers.local_export import LocalExport

UPLOAD_LIMIT = 9_700_000  # Claude in Chrome's file_upload takes about 10 MB per call


class Browser(LocalExport):
    name = "browser"

    def playbook(self) -> Path | None:
        p = self.cfg["provider"].get("browser_playbook")
        return Path(p) if p else None

    def check(self, online: bool = False) -> list[Check]:
        pb = self.playbook()
        ok = bool(pb and pb.exists())
        return [
            Check(
                "browser playbook", ok, str(pb) if ok else "set provider.browser_playbook (see docs/browser-adapter.md)"
            ),
            Check("Claude in Chrome", True, "checked by Claude in the skill, not by Python"),
        ]

    def prepare(self, ctx: ItemContext) -> Result:
        res = super().prepare(ctx)
        out = self.export_dir(ctx)
        tasks_path = out / "browser_tasks.json"
        tasks = json.loads(tasks_path.read_text(encoding="utf-8")) if tasks_path.exists() else {"groups": {}}
        for key, rel in res.data["files"].items():
            group = tasks["groups"].setdefault(key, {"files": [], "upload_calls": []})
            if rel not in group["files"]:
                group["files"].append(rel)
            group["upload_calls"] = upload_calls(out, group["files"])
        tasks["playbook"] = str(self.playbook() or "")
        tasks["rule"] = "Stop and ask the user before any step marked [ASK] in the playbook."
        tasks_path.write_text(json.dumps(tasks, indent=1), encoding="utf-8")
        return Result(
            "needs_action",
            res.detail,
            ["Run the browser playbook with Claude in Chrome: provider_export/browser_tasks.json"],
            res.data,
        )


def upload_calls(root: Path, files: list[str]) -> list[list[str]]:
    """Split files into upload calls that each stay under the browser's size limit."""
    calls, cur, total = [], [], 0
    for rel in files:
        size = (root / rel).stat().st_size
        if cur and total + size > UPLOAD_LIMIT:
            calls.append(cur)
            cur, total = [], 0
        cur.append(rel)
        total += size
    if cur:
        calls.append(cur)
    return calls
