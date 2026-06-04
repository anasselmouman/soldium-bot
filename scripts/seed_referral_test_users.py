#!/usr/bin/env python3
"""بيانات وهمية لاختبار قسم الإحالة — يُشغَّل يدوياً على users.db."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import database as db

REFERRER_ID = 8920123234
TEST_USER_BASE = 891_000_0001


def _grant_balance(user_id: int, amount: float) -> None:
    proof = f"seed_referral_proof_{user_id}_{amount}"
    deposit_id = db.create_deposit(user_id, 0.0, "CashPlus", proof)
    if deposit_id is None:
        raise RuntimeError(f"failed to create deposit for user {user_id}")
    if not db.finalize_approved_deposit(deposit_id, user_id, amount, "CashPlus"):
        raise RuntimeError(f"failed to grant balance for user {user_id}")


def _link_invitee(user_id: int, telegram_name: str) -> None:
    db.add_user(user_id, telegram_name=telegram_name)
    with db.get_connection() as connection:
        connection.execute(
            """
            UPDATE users
            SET referred_by = ?
            WHERE user_id = ?
              AND (referred_by IS NULL OR referred_by = ?)
            """,
            (REFERRER_ID, user_id, REFERRER_ID),
        )
        connection.commit()


def _place_order(
    user_id: int,
    amount: float,
    *,
    service_suffix: str,
    status: str | None = None,
    refund: float = 0.0,
) -> int:
    _grant_balance(user_id, amount + 10.0)
    order_id = db.create_order_with_balance_hold(
        user_id,
        "متابعين انستغرام (اختبار)",
        f"seed_{service_suffix}",
        "https://example.com/test",
        1000,
        amount,
    )
    if order_id is None:
        raise RuntimeError(f"failed to create order for user {user_id}")

    if refund > 0:
        if not db.apply_partial_or_full_refund(order_id, refund, "partial"):
            raise RuntimeError(f"failed partial refund on order {order_id}")
        return order_id

    if status:
        if not db.update_order_status(order_id, status):
            raise RuntimeError(f"failed to set status {status!r} on order {order_id}")

    return order_id


def seed_referral_test_users() -> None:
    db.init_db()
    db.add_user(REFERRER_ID, telegram_name="Fatima Da")

    # لم يطلبوا بعد
    inactive = [
        (TEST_USER_BASE + 0, "محمد_اختبار"),
        (TEST_USER_BASE + 1, "سارة_اختبار"),
        (TEST_USER_BASE + 2, "أحمد_اختبار"),
        (TEST_USER_BASE + 3, "ليلى_اختبار"),
    ]
    for user_id, name in inactive:
        _link_invitee(user_id, name)

    # طلبات معلّقة (تقدير أرباح)
    _link_invitee(TEST_USER_BASE + 4, "يوسف_اختبار")
    _place_order(TEST_USER_BASE + 4, 50.0, service_suffix="pending", status="pending")

    _link_invitee(TEST_USER_BASE + 5, "نادية_اختبار")
    _place_order(TEST_USER_BASE + 5, 80.0, service_suffix="inprogress", status="in progress")

    # طلبات مكتملة (أرباح مؤكدة + نشطون)
    _link_invitee(TEST_USER_BASE + 6, "اختبار_06_مكتمل")
    _place_order(TEST_USER_BASE + 6, 100.0, service_suffix="done1", status="completed")

    _link_invitee(TEST_USER_BASE + 7, "اختبار_07_مكتمل")
    _place_order(TEST_USER_BASE + 7, 150.0, service_suffix="done2", status="completed")

    # استرداد جزئي
    _link_invitee(TEST_USER_BASE + 8, "اختبار_08_جزئي")
    _place_order(TEST_USER_BASE + 8, 100.0, service_suffix="partial", refund=40.0)

    # مزيج: مكتمل + معلّق
    _link_invitee(TEST_USER_BASE + 9, "اختبار_09_مزيج")
    _place_order(TEST_USER_BASE + 9, 60.0, service_suffix="mix_done", status="completed")
    _place_order(TEST_USER_BASE + 9, 45.0, service_suffix="mix_pending", status="submitted")

    # طلب ملغى فقط — غير نشط
    _link_invitee(TEST_USER_BASE + 10, "اختبار_10_ملغى")
    _place_order(TEST_USER_BASE + 10, 30.0, service_suffix="canceled", status="canceled")

    ref = db.get_user(REFERRER_ID)
    assert ref is not None
    invited = db.count_invited_users(REFERRER_ID)
    active = db.count_active_referred_users(REFERRER_ID)
    pending = db.sum_referrer_pending_commission_estimate(REFERRER_ID)
    invitees = db.list_referral_invitees_summaries(REFERRER_ID)

    print(f"Referrer {REFERRER_ID}: level={ref['referral_level']}")
    print(f"  invited={invited}, active={active}")
    print(f"  referral_balance={ref['referral_balance']:.2f} DH")
    print(f"  referral_earned_total={ref['referral_earned_total']:.2f} DH")
    print(f"  pending_estimate={pending:.2f} DH")
    print(f"  invitees listed={len(invitees)}")
    for row in invitees:
        name = row["telegram_name"] or str(row["user_id"])
        print(
            f"    - uid={row['user_id']} name={name!r}: earned={float(row['earned']):.2f}, "
            f"pending={float(row['pending']):.2f}"
        )


if __name__ == "__main__":
    seed_referral_test_users()
