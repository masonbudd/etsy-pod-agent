"""Adapter registry. Add a provider by subclassing providers.base.Provider and listing it here."""
from __future__ import annotations

from podagent.providers.base import Provider
from podagent.providers.browser import Browser
from podagent.providers.local_export import LocalExport
from podagent.providers.prodigi import Prodigi

ADAPTERS: dict[str, type[Provider]] = {"local_export": LocalExport, "browser": Browser, "prodigi": Prodigi}


def get(cfg: dict, env: dict, transport=None, name: str | None = None) -> Provider:
    name = name or cfg["provider"]["adapter"]
    if name not in ADAPTERS:
        raise KeyError(f"unknown provider adapter {name!r}; choose one of {', '.join(ADAPTERS)}")
    return ADAPTERS[name](cfg, env, transport) if transport else ADAPTERS[name](cfg, env)
