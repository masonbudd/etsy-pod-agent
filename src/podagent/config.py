"""Load and validate config.toml, and find the workspace folders."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]  # the cloned repo (samples, config.example.toml)
EXAMPLE_CONFIG = REPO / "config.example.toml"
NOT_A_CLONE = (
    "{path} not found. podagent runs from a clone of the repository, installed with "
    "`pip install -e .`; a plain `pip install .` copies only the Python package."
)


def repo_file(rel: str) -> Path:
    """A file that ships with the repository (example config, samples), or a clear error."""
    p = REPO / rel
    if not p.exists():
        raise FileNotFoundError(NOT_A_CLONE.format(path=rel))
    return p


class ConfigError(ValueError):
    pass


@dataclass
class Workspace:
    """A user's working folder: config.toml, .env, designs, batches and rooms live here."""

    root: Path

    @property
    def config_path(self) -> Path:
        return self.root / "config.toml"

    @property
    def secrets(self) -> Path:
        return self.root / ".secrets"

    def path(self, cfg: dict, key: str) -> Path:
        return self.root / cfg["paths"][key]

    def batch_dir(self, cfg: dict, batch_id: str) -> Path:
        return self.path(cfg, "batches") / batch_id


def load(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"{path} not found. Run `podagent init` first.")
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh)
    problems = validate(cfg)
    if problems:
        raise ConfigError("config problems:\n  " + "\n  ".join(problems))
    return cfg


def parse_ratio(text: str) -> float:
    """Width / height. "ISO" is the A-series ratio 1:sqrt(2)."""
    if str(text).upper() == "ISO":
        return 1 / 2**0.5
    w, _, h = str(text).partition(":")
    return float(w) / float(h)


def validate(cfg: dict) -> list[str]:
    from podagent import pricing, rules

    out = []
    for section in ("naming", "paths", "checks", "listing", "variations", "pricing", "provider", "etsy"):
        if section not in cfg:
            out.append(f"missing [{section}]")
    if out:
        return out
    if not cfg.get("sizes"):
        out.append("add at least one [[sizes]] entry")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", cfg["naming"]["sku_prefix"]):
        out.append("naming.sku_prefix: lowercase letters, numbers and hyphens only")
    seen = set()
    for s in cfg.get("sizes", []):
        name = s.get("name", "?")
        for key in ("name", "code", "ratio", "width_in", "height_in", "cost_print"):
            if key not in s:
                out.append(f"size {name}: missing {key}")
        if s.get("code") in seen:
            out.append(f"size {name}: duplicate code {s.get('code')}")
        seen.add(s.get("code"))
        out += [f"size {name}: {p}" for p in rules.check_option_name(name)]
        try:
            parse_ratio(s.get("ratio", ""))
        except (ValueError, ZeroDivisionError):
            out.append(f"size {name}: ratio must be 'w:h' or 'ISO'")
    for f in cfg["variations"].get("frames", []):
        out += [f"frame {f.get('name')}: {p}" for p in rules.check_option_name(f.get("name", ""))]
    for sec in cfg["listing"].get("sections", []):
        out += [f"section {sec!r}: {p}" for p in rules.check_section(sec)]
    try:
        pricing.fees_from(cfg).check_margin(cfg["pricing"]["target_margin"])
    except (pricing.PricingError, KeyError) as e:
        out.append(f"pricing: {e}")
    room = rules.SKU_MAX - len(cfg["naming"]["sku_prefix"]) - 1 - variant_suffix_len(cfg)
    if room < 8:
        out.append("SKU prefix plus variant codes leave fewer than 8 characters for the design name")
    return out


def variant_suffix_len(cfg: dict) -> int:
    if not cfg["variations"].get("variant_skus"):
        return 0
    size = max((len(s.get("code", "")) for s in cfg.get("sizes", [])), default=0)
    frame = max((len(f.get("code", "")) for f in cfg["variations"].get("frames", [])), default=0)
    return 1 + size + (1 + frame if frame else 0)


def set_values(path: Path, section: str, values: dict) -> None:
    """Rewrite `key = value` lines inside one [section] of a TOML file, keeping comments."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    current, done = None, set()
    for i, line in enumerate(lines):
        head = re.match(r"\s*\[([^\[\]]+)\]\s*(#.*)?$", line)
        if head:
            current = head.group(1).strip()
            continue
        m = re.match(r"(\s*)([A-Za-z0-9_]+)(\s*=\s*)([^#\n]*?)(\s*#.*)?(\r?\n)?$", line)
        if current == section and m and m.group(2) in values:
            key = m.group(2)
            lines[i] = f"{m.group(1)}{key}{m.group(3)}{toml_value(values[key])}{m.group(5) or ''}{m.group(6) or ''}"
            done.add(key)
    missing = set(values) - done
    if missing:
        raise ConfigError(f"[{section}] has no line for: {', '.join(sorted(missing))}")
    path.write_text("".join(lines), encoding="utf-8")


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def load_env(root: Path) -> dict:
    """Values from .env, overridden by real environment variables."""
    env = {}
    p = root / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in os.environ.items():
        if k in env or k.startswith(("ETSY_", "OPENAI_", "PRINT_PROVIDER_", "REALESRGAN_")):
            env[k] = v
    return {k: v for k, v in env.items() if v}
