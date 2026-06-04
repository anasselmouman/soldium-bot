"""اختبارات سلامة العمليات المالية (سحب، استرداد، تعديل أدمن)."""

from __future__ import annotations

from pathlib import Path

import database as db


def _use_temp_db(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "test_financial.db"
    db.init_db()


def _grant_balance(uid: int, amount: float, method: str = "CashPlus") -> None:
    dep_id = db.create_deposit(uid, 0.0, method, f"proof_grant_{uid}_{amount}")
    assert dep_id is not None
    assert db.finalize_approved_deposit(dep_id, uid, amount, method) is True


def test_refund_order_is_idempotent(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 1
    db.add_user(uid)
    _grant_balance(uid, 25.0)
    oid = db.create_order_with_balance_hold(uid, "svc", "1", "http://x", 100, 25.0)
    assert oid is not None
    user = db.get_user(uid)
    assert user is not None
    assert float(user["balance"]) == 0.0

    assert db.refund_order(oid) is True
    user = db.get_user(uid)
    assert user is not None
    assert float(user["balance"]) == 25.0

    assert db.refund_order(oid) is False
    user = db.get_user(uid)
    assert float(user["balance"]) == 25.0


def test_withdrawal_pending_counts_against_available(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 2
    db.add_user(uid)
    dep_id = db.create_deposit(uid, 0.0, "CashPlus", "proof_w1")
    assert dep_id is not None
    assert db.finalize_approved_deposit(dep_id, uid, 100.0, "CashPlus") is True

    w1 = db.create_withdrawal_with_balance_hold(uid, 60.0, "CashPlus", "{}")
    assert w1 is not None
    assert db.get_user_withdrawable_amount(uid, "CashPlus") == 40.0

    w2 = db.create_withdrawal_with_balance_hold(uid, 50.0, "CashPlus", "{}")
    assert w2 is None


def test_reject_withdrawal_refunds_once(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 3
    db.add_user(uid)
    dep_id = db.create_deposit(uid, 0.0, "CIH", "proof_w2")
    assert dep_id is not None
    assert db.finalize_approved_deposit(dep_id, uid, 80.0, "بنك سي آي إتش (CIH Bank)") is True

    wid = db.create_withdrawal_with_balance_hold(uid, 30.0, "بنك سي آي إتش (CIH Bank)", "{}")
    assert wid is not None
    user = db.get_user(uid)
    assert user is not None
    assert float(user["balance"]) == 50.0

    rejected = db.reject_withdrawal_by_admin(wid)
    assert rejected is not None
    user = db.get_user(uid)
    assert float(user["balance"]) == 80.0

    assert db.reject_withdrawal_by_admin(wid) is None
    user = db.get_user(uid)
    assert float(user["balance"]) == 80.0


def test_admin_failed_status_refunds_order(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 4
    db.add_user(uid)
    _grant_balance(uid, 40.0)
    oid = db.create_order_with_balance_hold(uid, "svc", "9", "http://y", 50, 40.0)
    assert oid is not None
    assert db.set_order_status_by_admin(oid, "failed") is True
    user = db.get_user(uid)
    assert user is not None
    assert float(user["balance"]) == 40.0
    assert db.set_order_status_by_admin(oid, "completed") is False


def test_apply_partial_refund_respects_prior_refunds(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 6
    db.add_user(uid)
    _grant_balance(uid, 100.0)
    oid = db.create_order_with_balance_hold(uid, "svc", "2", "http://z", 10, 100.0)
    assert oid is not None
    assert db.apply_partial_or_full_refund(oid, 40.0, "partial") is True
    user = db.get_user(uid)
    assert user is not None
    assert float(user["balance"]) == 40.0
    assert db.apply_partial_or_full_refund(oid, 80.0, "partial") is False
    user = db.get_user(uid)
    assert float(user["balance"]) == 40.0


def test_admin_refunded_status_refunds_remaining(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 7
    db.add_user(uid)
    _grant_balance(uid, 50.0)
    oid = db.create_order_with_balance_hold(uid, "svc", "3", "http://w", 5, 50.0)
    assert oid is not None
    assert db.apply_partial_or_full_refund(oid, 20.0, "partial") is True
    assert db.set_order_status_by_admin(oid, "refunded") is True
    user = db.get_user(uid)
    assert user is not None
    assert float(user["balance"]) == 50.0


def test_referral_withdraw_below_minimum_blocked(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 5
    db.add_user(uid)
    with db.get_connection() as connection:
        connection.execute(
            "UPDATE users SET referral_balance = 100 WHERE user_id = ?",
            (uid,),
        )
        connection.commit()
    assert db.create_withdrawal_with_balance_hold(uid, 25.0, "CashPlus", "{}", withdrawal_type="referral") is None
