"""Scan files for secrets and personal data before they are committed.

Usage:
  python scripts/scan_secrets.py --staged   # files staged for commit (the pre-commit hook runs this)
  python scripts/scan_secrets.py --all      # every tracked and untracked, non-ignored file
  python scripts/scan_secrets.py PATH ...   # specific files

Add your own private terms (shop name, real name, IDs) to .scan-denylist, one per line.
That file is gitignored. Prefix a line with "re:" for a regular expression, and add
" @ <glob>" to allow the term in matching paths only, e.g. "Example Ltd @ LICENSE".
A line containing "scan:allow" is skipped.
Exit code 1 means findings.
"""

from __future__ import annotations

import fnmatch
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATTERNS = {  # scan:allow
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # scan:allow
    "OpenAI key": re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}"),  # scan:allow
    "Anthropic key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),  # scan:allow
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),  # scan:allow
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # scan:allow
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),  # scan:allow
    "JWT": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # scan:allow
    "OAuth token": re.compile(r"\b\d{6,}\.[A-Za-z0-9_-]{40,}"),  # scan:allow
    "secret assignment": re.compile(  # scan:allow
        r"(?i)\b[A-Z0-9_]*(?:api_?key|secret|token|password|keystring)[A-Z0-9_]*\s*[:=]\s*['\"]?[A-Za-z0-9_\-./+]{16,}"
    ),  # scan:allow
    "email address": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),  # scan:allow
    "home folder path": re.compile(r"(?:[A-Za-z]:\\+[Uu]sers\\+|/Users/|/home/)(?!<)[A-Za-z0-9._-]+"),  # scan:allow
}
SAFE_EMAIL = re.compile(r"(?i)@(?:example\.(?:com|org)|users\.noreply\.github\.com)$|^noreply@")  # scan:allow
BLOCKED_NAMES = [".env", "*.pem", "*.key", "*token*.json", "pkce*.json", "*.log", "*tracker*.csv"]
ALLOWED_NAMES = [".env.example"]
TEXT_LIMIT = 2_000_000
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def load_denylist() -> list[tuple[re.Pattern, str | None, str]]:
    path = ROOT / ".scan-denylist"
    rules = []
    if not path.exists():
        return rules
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        term, _, allow = (p.strip() for p in line.partition(" @ "))
        rx = re.compile(term[3:], re.I) if term.startswith("re:") else re.compile(re.escape(term), re.I)
        rules.append((rx, allow or None, "private term"))
    return rules


def git_files(*args: str) -> list[Path]:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    return [ROOT / f for f in out.splitlines() if f.strip()]


def image_text(path: Path) -> str:
    """Text stored inside an image: PNG text chunks and EXIF values."""
    try:
        from PIL import Image

        with Image.open(path) as im:
            parts = [f"{k}={v}" for k, v in im.info.items() if isinstance(v, (str, bytes))]
            parts += [str(v) for v in im.getexif().values()]
        return "\n".join(p.decode(errors="ignore") if isinstance(p, bytes) else p for p in parts)
    except Exception:
        return ""


def scan_file(path: Path, deny) -> list[str]:
    rel = path.relative_to(ROOT).as_posix()
    if any(fnmatch.fnmatch(path.name, b) for b in BLOCKED_NAMES) and path.name not in ALLOWED_NAMES:
        return [f"{rel}: file type must never be committed"]
    if path.suffix.lower() in IMAGE_EXT:
        text = image_text(path)
    else:
        data = path.read_bytes()[:TEXT_LIMIT]
        if b"\0" in data[:8000]:
            return []
        text = data.decode("utf-8", errors="ignore")
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        if "scan:allow" in line:
            continue
        for name, rx in PATTERNS.items():
            for m in rx.finditer(line):
                if name == "email address" and SAFE_EMAIL.search(m.group(0)):
                    continue
                found.append(f"{rel}:{n}: {name}: {mask(m.group(0))}")
        for rx, allow, name in deny:
            if rx.search(line) and not (allow and fnmatch.fnmatch(rel, allow)):
                found.append(f"{rel}:{n}: {name}: {mask(rx.search(line).group(0))}")
    return found


def mask(s: str) -> str:
    return s if len(s) <= 6 else s[:3] + "*" * (len(s) - 5) + s[-2:]


def main(argv: list[str]) -> int:
    if "--staged" in argv:
        files = git_files("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    elif "--all" in argv:
        files = git_files("ls-files", "--cached", "--others", "--exclude-standard")
    else:
        files = [Path(a).resolve() for a in argv]
    files = [f for f in files if f.is_file() and f.name != ".scan-denylist"]
    deny = load_denylist()
    findings = [hit for f in files for hit in scan_file(f, deny)]
    print(
        f"Secret and personal-data scan: {len(files)} files, "
        f"{len(PATTERNS)} patterns, {len(deny)} private terms, {len(findings)} findings"
    )
    for hit in findings:
        print("  " + hit)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
