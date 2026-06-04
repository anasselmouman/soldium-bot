"""اختبارات مزامنة حالة المورد (استرداد + عمولة إحالة)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import database as db
from services.order_provider_sync import apply_provider_status_to_order


def _use_temp_db(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "test_order_provider_sync.db"
    db.init_db()


def _referrer_and_invitee(
    tmp_path: Path,
    *,
    referrer_id: int = 1000,
    invitee_id: int = 2000,
) -> None:
    _use_temp_db(tmp_path)
    db.add_user(referrer_id)
    db.add_user(invitee_id)
    with db.get_connection() as connection:
        connection.execute(
            "UPDATE users SET referred_by = ? WHERE user_id = ?",
            (referrer_id, invitee_id),
        )
        connection.commit()


def _grant_and_order(
    invitee_id: int,
    amount: float,
    *,
    quantity: int = 1000,
    provider_id: str = "prov_1",
) -> dict:
    dep_id = db.create_deposit(invitee_id, 0.0, "CashPlus", f"proof_{invitee_id}_{amount}")
    assert dep_id is not None
    assert db.finalize_approved_deposit(dep_id, invitee_id, amount * 2, "CashPlus") is True
    oid = db.create_order_with_balance_hold(
        invitee_id,
        "svc",
        "1",
        "http://example.com",
        quantity,
        amount,
    )
    assert oid is not None
    db.set_provider_order_id(oid, provider_id)
    row = db.get_user_orders(invitee_id, limit=1)[0]
    return dict(row)


def _run(coro):
    return asyncio.run(coro)


def test_sync_partial_refunds_invitee_and_pays_referrer(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    order = _grant_and_order(2000, 100.0, quantity=1000)
    status_data = {"status": "Partial", "charge": "0.5", "remains": "500"}

    _run(apply_provider_status_to_order(order, status_data, None, notify=False))

    invitee = db.get_user(2000)
    referrer = db.get_user(1000)
    assert invitee is not None
    assert referrer is not None
    assert float(invitee["balance"]) == 189.0
    assert float(referrer["referral_balance"]) == 1.1


def test_sync_failed_full_refund_no_referral(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    order = _grant_and_order(2000, 50.0, provider_id="prov_failed")
    _run(
        apply_provider_status_to_order(
            order,
            {"status": "Failed"},
            None,
            notify=False,
        )
    )
    invitee = db.get_user(2000)
    referrer = db.get_user(1000)
    orders = db.get_user_orders(2000, limit=1)
    assert invitee is not None
    assert referrer is not None
    assert float(invitee["balance"]) == 100.0
    assert float(referrer["referral_balance"]) == 0.0
    assert orders[0]["status"].lower() == "failed"


def test_sync_failed_idempotent(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    order = _grant_and_order(2000, 60.0, provider_id="prov_failed_twice")
    status_data = {"status": "Failed"}

    _run(apply_provider_status_to_order(order, status_data, None, notify=False))
    balance_after_first = float(db.get_user(2000)["balance"])  # type: ignore[index]

    order_refresh = dict(db.get_user_orders(2000, limit=1)[0])
    _run(apply_provider_status_to_order(order_refresh, status_data, None, notify=False))

    invitee = db.get_user(2000)
    assert invitee is not None
    assert float(invitee["balance"]) == balance_after_first


def test_sync_canceled_full_refund_no_referral(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    order = _grant_and_order(2000, 50.0, provider_id="prov_cancel")
    _run(
        apply_provider_status_to_order(
            order,
            {"status": "Canceled"},
            None,
            notify=False,
        )
    )
    invitee = db.get_user(2000)
    referrer = db.get_user(1000)
    assert invitee is not None
    assert referrer is not None
    assert float(invitee["balance"]) == 100.0
    assert float(referrer["referral_balance"]) == 0.0


def test_sync_completed_pays_referrer(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    order = _grant_and_order(2000, 100.0, provider_id="prov_done")
    _run(
        apply_provider_status_to_order(
            order,
            {"status": "Completed"},
            None,
            notify=False,
        )
    )
    referrer = db.get_user(1000)
    assert referrer is not None
    assert float(referrer["referral_balance"]) == 10.0


def test_sync_partial_idempotent(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    order = _grant_and_order(2000, 100.0, provider_id="prov_partial_twice")
    status_data = {"status": "Partial", "charge": "0.5", "remains": "500"}

    _run(apply_provider_status_to_order(order, status_data, None, notify=False))
    invitee_after_first = float(db.get_user(2000)["balance"])  # type: ignore[index]

    order_refresh = dict(db.get_user_orders(2000, limit=1)[0])
    _run(apply_provider_status_to_order(order_refresh, status_data, None, notify=False))

    invitee = db.get_user(2000)
    assert invitee is not None
    assert float(invitee["balance"]) == invitee_after_first


def test_sync_submitted_no_referral_payout(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    order = _grant_and_order(2000, 80.0, provider_id="prov_sub")
    _run(
        apply_provider_status_to_order(
            order,
            {"status": "Submitted"},
            None,
            notify=False,
        )
    )
    referrer = db.get_user(1000)
    assert referrer is not None
    assert float(referrer["referral_balance"]) == 0.0
    pending = db.sum_referrer_pending_commission_estimate(1000)
    assert pending > 0
