from decimal import Decimal

from utils.partial_refund_math import (
    compute_partial_refund,
    compute_partial_refund_from_status,
)


def test_partial_refund_uses_charge_usd_times_22_legacy():
    # 0.5 USD × 22 = 11 DH executed → refund 44 − 11 = 33 DH
    r = compute_partial_refund_from_status(
        Decimal("44"),
        1000,
        {"status": "Partial", "charge": "0.5", "remains": "500"},
    )
    assert r is not None
    refund, actual_usd, final_dh, method = r
    assert method == "cost_or_charge_usd"
    assert actual_usd == Decimal("0.5")
    assert final_dh == Decimal("11")
    assert refund == Decimal("33")


def test_partial_refund_prefers_cost_over_charge_legacy():
    r = compute_partial_refund_from_status(
        Decimal("22"),
        100,
        {"cost": "0.25", "charge": "0.99"},
    )
    assert r is not None
    _, actual_usd, _, method = r
    assert method == "cost_or_charge_usd"
    assert actual_usd == Decimal("0.25")


def test_partial_remains_fallback_proportional_when_no_usd_legacy():
    # 1000 qty, 400 remains → 600/1000 of implied full; orig 110 DH → refund 44 DH
    r = compute_partial_refund_from_status(Decimal("110"), 1000, {"remains": "400"})
    assert r is not None
    refund, _, _, method = r
    assert method == "remains_scaled"
    assert refund == Decimal("44")


def test_margin_proportional_preserves_customer_margin():
    # paid 30 DH, provider snapshot 14 DH (≈1 USD), executed 0.5 USD → charge 7 DH → refund 15
    r = compute_partial_refund(
        amount_paid_dh=Decimal("30"),
        quantity=1000,
        status_data={"charge": "0.5"},
        provider_cost_dh=14.0,
    )
    assert r is not None
    refund, actual_usd, final_dh, method = r
    assert method == "margin_proportional"
    assert actual_usd == Decimal("0.5")
    assert final_dh == Decimal("15")
    assert refund == Decimal("15")


def test_margin_proportional_high_margin_service():
    # paid 37 DH, snapshot 14 DH, 0.5 USD executed → final 18.5, refund 18.5
    r = compute_partial_refund(
        amount_paid_dh=Decimal("37"),
        quantity=1000,
        status_data={"cost": "0.5"},
        provider_cost_dh=14.0,
    )
    assert r is not None
    refund, _, final_dh, method = r
    assert method == "margin_proportional"
    assert final_dh == Decimal("18.5")
    assert refund == Decimal("18.5")


def test_margin_proportional_minimum_margin_not_overcharged():
    # paid 14 DH (×14 min), 0.5 USD → final 7, refund 7 (legacy ×22 would refund only 3)
    r = compute_partial_refund(
        amount_paid_dh=Decimal("14"),
        quantity=1000,
        status_data={"charge": "0.5"},
        provider_cost_dh=14.0,
    )
    assert r is not None
    refund, _, final_dh, method = r
    assert method == "margin_proportional"
    assert final_dh == Decimal("7")
    assert refund == Decimal("7")


def test_quantity_proportional_with_snapshot_no_usd():
    r = compute_partial_refund(
        amount_paid_dh=Decimal("30"),
        quantity=1000,
        status_data={"remains": "500"},
        provider_cost_dh=14.0,
    )
    assert r is not None
    refund, _, final_dh, method = r
    assert method == "quantity_proportional"
    assert refund == Decimal("15")
    assert final_dh == Decimal("15")


def test_compute_partial_refund_falls_back_to_legacy_without_snapshot():
    r = compute_partial_refund(
        amount_paid_dh=Decimal("44"),
        quantity=1000,
        status_data={"charge": "0.5", "remains": "500"},
        provider_cost_dh=0.0,
    )
    assert r is not None
    refund, _, final_dh, method = r
    assert method == "legacy_cost_or_charge_usd"
    assert final_dh == Decimal("11")
    assert refund == Decimal("33")


def test_margin_proportional_caps_when_provider_cost_exceeds_snapshot():
    # provider charged more USD than snapshot implies — customer never pays above original
    r = compute_partial_refund(
        amount_paid_dh=Decimal("20"),
        quantity=1000,
        status_data={"charge": "2.0"},
        provider_cost_dh=14.0,
    )
    assert r is None
