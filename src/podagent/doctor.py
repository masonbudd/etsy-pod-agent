"""`podagent doctor`: check every connection and say what is missing and how to fix it."""

from __future__ import annotations

from podagent import providers
from podagent.config import Workspace
from podagent.etsy.oauth import TokenStore
from podagent.images.upscale import find_realesrgan
from podagent.providers.base import Check


def run(ws: Workspace, cfg: dict, env: dict, online: bool = False, etsy_factory=None, transport=None) -> list[Check]:
    out = [Check("config.toml", True, "valid")]

    keys = bool(env.get("ETSY_KEYSTRING") and env.get("ETSY_SHARED_SECRET"))
    out.append(
        Check(
            "Etsy app keys",
            keys,
            "set"
            if keys
            else "add ETSY_KEYSTRING and ETSY_SHARED_SECRET to .env (create an app at etsy.com/developers/your-apps)",
        )
    )
    store = TokenStore(ws.secrets / "etsy_token.json", env.get("ETSY_KEYSTRING", ""))
    out.append(Check("Etsy sign-in", store.exists(), "token saved" if store.exists() else "run `podagent etsy-login`"))
    ids = cfg["etsy"]
    missing = [
        k for k in ("shop_id", "shipping_profile_id", "return_policy_id", "readiness_state_id") if not ids.get(k)
    ]
    out.append(
        Check(
            "Etsy shop settings",
            not missing,
            "found" if not missing else f"run `podagent etsy-discover` to fill: {', '.join(missing)}",
        )
    )
    if online and keys and store.exists() and etsy_factory:
        try:
            me = etsy_factory().me()
            out.append(Check("Etsy API", True, f"signed in; shop {me.get('shop_id')}"))
        except Exception as e:  # noqa: BLE001
            out.append(Check("Etsy API", False, str(e)[:200]))

    out += providers.get(cfg, env, transport).check(online)

    exe = find_realesrgan(env)
    out.append(
        Check(
            "Real-ESRGAN",
            exe is not None,
            str(exe)
            if exe
            else "optional: upscaling falls back to a plain resize (softer at large sizes). "
            "Get realesrgan-ncnn-vulkan from github.com/xinntao/Real-ESRGAN/releases and set REALESRGAN_PATH",
            optional=True,
        )
    )

    rooms = ws.path(cfg, "rooms")
    out.append(
        Check(
            "Room templates",
            (rooms / "corners.json").exists(),
            "calibrated" if (rooms / "corners.json").exists() else f"run `podagent rooms-calibrate` ({rooms})",
        )
    )

    out.append(
        Check(
            "OpenAI API key",
            bool(env.get("OPENAI_API_KEY")),
            "set"
            if env.get("OPENAI_API_KEY")
            else "optional: only needed to generate designs or rooms; add OPENAI_API_KEY to .env",
            optional=True,
        )
    )
    csv = ws.root / cfg.get("research", {}).get("keyword_csv", "research/keywords.csv")
    out.append(
        Check(
            "Research source",
            True,
            "keyword CSV found"
            if csv.exists()
            else "Claude checks for the ProfitTree MCP in the skill; otherwise add research/keywords.csv",
        )
    )
    out.append(
        Check(
            "Claude in Chrome",
            True,
            "checked by Claude in the skill; needed for the AI-disclosure tick and the browser adapter",
        )
    )
    return out


def format_table(checks: list[Check]) -> str:
    w = max(len(c.name) for c in checks)
    return "\n".join(f"{'OK' if c.ok else '--' if c.optional else 'NO'}  {c.name.ljust(w)}  {c.detail}" for c in checks)


def passed(checks: list[Check]) -> bool:
    return all(c.ok or c.optional for c in checks)
