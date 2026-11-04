import pytest

from podagent import costs, pricing


def test_price_hits_target_margin_after_fees(cfg):
    fees = pricing.fees_from(cfg)
    price = pricing.price_for(7.50, fees, 0.30, "none")
    assert pricing.margin_at(price, 7.50, fees) == pytest.approx(0.30, abs=0.002)


def test_charm_rounding_never_lowers_the_price():
    assert pricing.round_price(13.81, "charm") == 13.99
    assert pricing.round_price(13.99, "charm") == 13.99
    assert pricing.round_price(14.00, "charm") == 14.99
    assert pricing.round_price(13.01, "whole") == 14.0
    assert pricing.round_price(13.001, "none") == 13.01


def test_fees_include_vat(cfg):
    fees = pricing.fees_from(cfg)
    assert fees.pct == pytest.approx((0.065 + 0.04) * 1.2)
    assert fees.on(10) == pytest.approx(10 * fees.pct + (0.20 + 0.16) * 1.2)


def test_impossible_margin_is_an_error(cfg):
    fees = pricing.fees_from(cfg)
    with pytest.raises(pricing.PricingError):
        pricing.price_for(5, fees, 0.95)


def test_table_flags_fixed_prices_below_minimum(cfg):
    rows = pricing.price_table(cfg)
    entry = next(r for r in rows if r.size_code == "5x7" and r.frame == "Print only")
    assert entry.fixed_price and entry.price == 9.99 and entry.low_margin
    calculated = [r for r in rows if r.enabled and not r.fixed_price]
    assert all(r.margin >= cfg["pricing"]["target_margin"] for r in calculated)


def test_print_only_sizes_disable_framed_options(cfg):
    rows = [r for r in pricing.price_table(cfg) if r.size_code == "24x36"]
    assert [r.enabled for r in rows] == [True, False, False, False]
    s = pricing.summary(pricing.price_table(cfg))
    assert s["variants"] == len(cfg["sizes"]) * 4 and s["enabled"] == s["variants"] - 3


def test_cost_estimate_from_config(cfg):
    e = costs.estimate(cfg, 5)
    assert e["known"] and e["per_image_usd"] == 0.041 and e["model"] == "gpt-image-2"
    assert e["expected_usd"] == pytest.approx(0.205) and e["worst_case_usd"] == pytest.approx(0.41)


def test_cost_estimate_unknown_model_says_so(cfg):
    cfg["images"]["model"] = "some-new-model"
    e = costs.estimate(cfg, 2)
    assert not e["known"] and "Add one" in e["note"]
