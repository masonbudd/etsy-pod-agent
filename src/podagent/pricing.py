"""Price every size and frame from base cost, Etsy fees and a target margin.

margin = (price - cost - fees(price)) / price, where
fees(price) = (price * percentage fees + fixed fees) * (1 + VAT on fees).
Solving for the price that hits the target margin:
price = (cost + fixed * (1 + vat)) / (1 - pct * (1 + vat) - margin)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict


class PricingError(ValueError):
    pass


@dataclass
class Fees:
    transaction_pct: float
    payment_pct: float
    payment_fixed: float
    listing_fee: float
    offsite_ads_pct: float = 0.0
    vat_on_fees_pct: float = 0.0

    @property
    def pct(self) -> float:
        return (self.transaction_pct + self.payment_pct + self.offsite_ads_pct) * (1 + self.vat_on_fees_pct)

    @property
    def fixed(self) -> float:
        return (self.payment_fixed + self.listing_fee) * (1 + self.vat_on_fees_pct)

    def on(self, price: float) -> float:
        return price * self.pct + self.fixed

    def check_margin(self, margin: float) -> None:
        if not 0 <= margin < 1:
            raise PricingError(f"target margin {margin} must be between 0 and 1")
        if self.pct + margin >= 1:
            raise PricingError(f"fees ({self.pct:.0%}) plus target margin ({margin:.0%}) leave nothing to cover cost")


def fees_from(cfg: dict) -> Fees:
    return Fees(**cfg["pricing"]["fees"])


def round_price(price: float, rule: str = "charm") -> float:
    """Round up so the margin never drops below target."""
    cents = math.ceil(round(price * 100, 6)) / 100
    if rule == "charm":
        return round(math.ceil(round(cents + 0.01, 2)) - 0.01, 2)
    if rule == "whole":
        return float(math.ceil(round(cents, 2)))
    if rule == "none":
        return cents
    raise PricingError(f"unknown rounding {rule!r}")


def price_for(cost: float, fees: Fees, margin: float, rule: str = "charm") -> float:
    fees.check_margin(margin)
    return round_price((cost + fees.fixed) / (1 - fees.pct - margin), rule)


def margin_at(price: float, cost: float, fees: Fees) -> float:
    return (price - cost - fees.on(price)) / price


@dataclass
class Variant:
    size: str
    size_code: str
    frame: str | None
    frame_code: str | None
    ratio: str
    cost: float | None
    price: float | None
    margin: float | None
    enabled: bool
    fixed_price: bool = False
    low_margin: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def price_table(cfg: dict) -> list[Variant]:
    """One row per size x frame. Sizes without a framed cost are print only (framed rows disabled)."""
    fees = fees_from(cfg)
    target, floor = cfg["pricing"]["target_margin"], cfg["pricing"]["min_margin"]
    rule = cfg["pricing"].get("rounding", "charm")
    frames = cfg["variations"].get("frames") or [{"name": None, "code": None, "framed": False}]
    rows = []
    for s in cfg["sizes"]:
        for f in frames:
            framed = f.get("framed", False)
            cost = s.get("cost_framed") if framed else s["cost_print"]
            fixed = s.get("price_framed" if framed else "price_print")
            if cost is None:
                rows.append(Variant(s["name"], s["code"], f["name"], f.get("code"), s["ratio"], None, None, None, False))
                continue
            price = round(float(fixed), 2) if fixed is not None else price_for(cost, fees, target, rule)
            m = margin_at(price, cost, fees)
            rows.append(Variant(s["name"], s["code"], f["name"], f.get("code"), s["ratio"], cost, price, round(m, 4),
                                True, fixed is not None, m < floor))
    return rows


def summary(rows: list[Variant]) -> dict:
    live = [r for r in rows if r.enabled]
    return {"min_price": min(r.price for r in live), "max_price": max(r.price for r in live),
            "variants": len(rows), "enabled": len(live), "low_margin": [f"{r.size} / {r.frame}" for r in live if r.low_margin],
            "lowest_margin": min(r.margin for r in live)}
