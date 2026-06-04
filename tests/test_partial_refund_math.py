from decimal import Decimal

from utils.partial_refund_math import compute_partial_refund_from_status


def test_partial_refund_uses_charge_usd_times_22():
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


def test_partial_refund_prefers_cost_over_charge():
    r = compute_partial_refund_from_status(
        Decimal("22"),
        100,
        {"cost": "0.25", "charge": "0.99"},
    )
    assert r is not None
    _, actual_usd, _, method = r
    assert method == "cost_or_charge_usd"
    assert actual_usd == Decimal("0.25")


def test_partial_remains_fallback_proportional_when_no_usd():
    # 1000 qty, 400 remains → 600/1000 of implied full; orig 110 DH → refund 44 DH
    r = compute_partial_refund_from_status(Decimal("110"), 1000, {"remains": "400"})
    assert r is not None
    refund, _, _, method = r
    assert method == "remains_scaled"
    assert refund == Decimal("44")
