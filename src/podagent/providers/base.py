"""Print-provider adapter interface.

An adapter gets one checked, priced item and makes sure the provider can print every
size and frame the listing offers. The core pipeline never names a provider.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ItemContext:
    batch_id: str
    item_id: str
    base_sku: str
    variants: list[dict]                   # rows from the price table, each with "sku", "ratio", "enabled"
    print_files: dict[str, Path]           # ratio key -> print-ready file
    batch_dir: Path
    listing_id: int | None = None          # set once the Etsy draft exists


@dataclass
class Result:
    status: str                            # "done", "needs_action" or "failed"
    detail: str = ""
    todo: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


class Provider(ABC):
    name = "provider"
    needs_listing_id = False               # True if prepare() needs the Etsy draft to exist first

    def __init__(self, cfg: dict, env: dict, transport=None):
        self.cfg, self.env, self.transport = cfg, env, transport

    @abstractmethod
    def check(self, online: bool = False) -> list[Check]:
        """Is the adapter configured (and, if online, reachable)?"""

    @abstractmethod
    def prepare(self, ctx: ItemContext) -> Result:
        """Get the item's print files and SKUs to the provider, or ready to upload."""

    def link(self, ctx: ItemContext, plan: list[dict] | None = None) -> Result:
        """Optional: after the listing is published, connect it to the provider."""
        return Result("done", "nothing to link for this adapter")
