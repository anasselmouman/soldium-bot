"""اختبارات دفع عمولة الإحالة والفصل بين التقدير المعلّق والأرباح المؤكدة."""

from __future__ import annotations

from pathlib import Path

import database as db


def _use_temp_db(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "test_referral_payout.db"
    db.init_db()


def _grant_balance(uid: int, amount: float) -> None:
    dep_id = db.create_deposit(uid, 0.0, "CashPlus", f"proof_grant_{uid}_{amount}")
    assert dep_id is not None
    assert db.finalize_approved_deposit(dep_id, uid, amount, "CashPlus") is True


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


def test_pending_estimate_shows_immediately_after_order(tmp_path: Path) -> None:
    """تقدير معلّق يظهر فور إنشاء الطلب (حالة pending) دون انتظار in progress."""
    _referrer_and_invitee(tmp_path)
    _grant_balance(2000, 100.0)
    oid = db.create_order_with_balance_hold(2000, "svc", "1", "http://x", 10, 100.0)
    assert oid is not None

    pending = db.sum_referrer_pending_commission_estimate(1000)
    assert pending == 10.0

    ref = db.get_user(1000)
    assert ref is not None
    assert float(ref["referral_balance"]) == 0.0


def test_pending_estimate_does_not_increase_referral_balance(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    _grant_balance(2000, 50.0)
    oid = db.create_order_with_balance_hold(2000, "svc", "1b", "http://x", 10, 30.0)
    assert oid is not None
    db.update_order_status(oid, "in progress")

    pending = db.sum_referrer_pending_commission_estimate(1000)
    assert pending > 0

    ref = db.get_user(1000)
    assert ref is not None
    assert float(ref["referral_balance"]) == 0.0


def test_try_apply_on_completed_credits_referrer(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    _grant_balance(2000, 100.0)
    oid = db.create_order_with_balance_hold(2000, "svc", "2", "http://y", 5, 100.0)
    assert oid is not None
    db.update_order_status(oid, "completed")

    ref = db.get_user(1000)
    assert ref is not None
    assert float(ref["referral_balance"]) == 10.0


def test_try_apply_ignored_for_submitted(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    _grant_balance(2000, 50.0)
    oid = db.create_order_with_balance_hold(2000, "svc", "3", "http://z", 5, 50.0)
    assert oid is not None
    db.update_order_status(oid, "submitted")

    db.try_apply_referral_payout_for_order(oid)
    ref = db.get_user(1000)
    assert ref is not None
    assert float(ref["referral_balance"]) == 0.0


def test_try_apply_on_partial_uses_net_spent(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    _grant_balance(2000, 100.0)
    oid = db.create_order_with_balance_hold(2000, "svc", "4", "http://w", 5, 100.0)
    assert oid is not None
    assert db.apply_partial_or_full_refund(oid, 40.0, "partial") is True

    ref = db.get_user(1000)
    assert ref is not None
    assert float(ref["referral_balance"]) == 6.0


def test_admin_partial_without_refund_rejected(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    _grant_balance(2000, 80.0)
    oid = db.create_order_with_balance_hold(2000, "svc", "5", "http://a", 5, 80.0)
    assert oid is not None
    db.update_order_status(oid, "submitted")

    assert db.set_order_status_by_admin(oid, "partial") is False


def test_admin_partial_with_refund_pays_commission(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    _grant_balance(2000, 100.0)
    oid = db.create_order_with_balance_hold(2000, "svc", "6", "http://b", 5, 100.0)
    assert oid is not None
    assert db.apply_partial_or_full_refund(oid, 25.0, "partial") is True

    assert db.set_order_status_by_admin(oid, "partial") is True
    ref = db.get_user(1000)
    assert ref is not None
    assert float(ref["referral_balance"]) == 7.5


def test_update_order_status_partial_without_refund_no_payout(tmp_path: Path) -> None:
    """تحديث الحالة إلى partial دون استرداد (مثلاً من واجهة الطلبات) لا يصرف عمولة."""
    _referrer_and_invitee(tmp_path)
    _grant_balance(2000, 100.0)
    oid = db.create_order_with_balance_hold(2000, "svc", "8", "http://d", 5, 100.0)
    assert oid is not None
    db.update_order_status(oid, "in progress")
    db.update_order_status(oid, "partial")

    ref = db.get_user(1000)
    assert ref is not None
    assert float(ref["referral_balance"]) == 0.0


def test_transfer_uses_confirmed_balance_only(tmp_path: Path) -> None:
    _referrer_and_invitee(tmp_path)
    _grant_balance(2000, 100.0)
    oid = db.create_order_with_balance_hold(2000, "svc", "7", "http://c", 5, 100.0)
    assert oid is not None
    db.update_order_status(oid, "in progress")

    assert db.sum_referrer_pending_commission_estimate(1000) > 0
    assert db.transfer_referral_balance_to_main(1000, 1.0) is False

    db.update_order_status(oid, "completed")
    assert db.transfer_referral_balance_to_main(1000, 10.0) is True
    ref = db.get_user(1000)
    assert ref is not None
    assert float(ref["referral_balance"]) == 0.0
    assert float(ref["balance"]) == 10.0
