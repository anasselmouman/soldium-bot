# -*- coding: utf-8 -*-
"""Pending order funding — intent identity, consume atomicity, concurrency."""

from __future__ import annotations

import threading
from pathlib import Path

import database as db
from utils.money import to_decimal


def _use_temp_db(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "test_pending_funding.db"
    db.init_db()


def _grant(uid: int, amount: float, method: str = "CashPlus") -> None:
    dep_id = db.create_deposit(uid, 0.0, method, f"proof_{uid}_{amount}_{method}")
    assert dep_id is not None
    assert db.finalize_approved_deposit(dep_id, uid, amount, method) is True


def _upsert(
    uid: int,
    *,
    service_id: str = "svc1",
    amount: float = 25.0,
    link: str = "https://instagram.com/p/abc",
    quantity: int = 1000,
) -> int:
    return db.upsert_pending_order_funding(
        uid,
        service_id=service_id,
        service_name="Test Service",
        platform_key="instagram",
        section_key="likes",
        subsection_key=None,
        link=link,
        quantity=quantity,
        amount_dh=amount,
        auto_quantity=False,
    )


def test_pending_table_created_by_init_db(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    assert db.pending_init_db_migrations(db_path=db.DB_PATH) == []
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pending_order_funding'"
        ).fetchone()
    assert row is not None


def test_upsert_replaces_with_new_intent_id(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 101
    db.add_user(uid)
    a = _upsert(uid, amount=25.0)
    b = _upsert(uid, amount=30.0)
    assert b != a
    assert db.get_pending_order_funding_by_intent(a, uid) is None
    current = db.get_pending_order_funding(uid)
    assert current is not None
    assert int(current["intent_id"]) == b
    assert float(current["amount_dh"]) == 30.0


def test_only_one_pending_per_user(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 102
    db.add_user(uid)
    _upsert(uid)
    _upsert(uid, amount=40.0)
    with db.get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM pending_order_funding WHERE user_id = ?",
            (uid,),
        ).fetchone()["c"]
    assert int(n) == 1


def test_insufficient_path_no_order_no_debit(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 103
    db.add_user(uid)
    _grant(uid, 10.0)
    intent_id = _upsert(uid, amount=25.0)
    user = db.get_user(uid)
    assert float(user["balance"]) == 10.0
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is not None
    with db.get_connection() as conn:
        orders = conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?", (uid,)
        ).fetchone()["c"]
    assert int(orders) == 0


def test_consume_creates_order_deletes_intent_atomically(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 104
    db.add_user(uid)
    _grant(uid, 25.0)
    intent_id = _upsert(uid, amount=25.0)
    order_id = db.create_order_with_balance_hold_consuming_funding_intent(
        intent_id,
        uid,
        "Test Service",
        "svc1",
        "https://instagram.com/p/abc",
        1000,
        25.0,
    )
    assert order_id is not None
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is None
    assert db.get_pending_order_funding(uid) is None
    user = db.get_user(uid)
    assert float(user["balance"]) == 0.0
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT id, amount, status FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
    assert row is not None
    assert float(row["amount"]) == 25.0


def test_stale_confirm_after_cancel(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 105
    db.add_user(uid)
    _grant(uid, 25.0)
    intent_id = _upsert(uid, amount=25.0)
    assert db.delete_pending_order_funding_by_intent(intent_id, uid) is True
    order_id = db.create_order_with_balance_hold_consuming_funding_intent(
        intent_id,
        uid,
        "Test Service",
        "svc1",
        "https://instagram.com/p/abc",
        1000,
        25.0,
    )
    assert order_id is None
    user = db.get_user(uid)
    assert float(user["balance"]) == 25.0


def test_stale_confirm_after_replacement(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 106
    db.add_user(uid)
    _grant(uid, 50.0)
    intent_a = _upsert(uid, amount=25.0, service_id="svcA", link="https://instagram.com/p/a")
    intent_b = _upsert(uid, amount=30.0, service_id="svcB", link="https://instagram.com/p/b")
    assert intent_a != intent_b
    # Confirm A must fail
    assert (
        db.create_order_with_balance_hold_consuming_funding_intent(
            intent_a,
            uid,
            "A",
            "svcA",
            "https://instagram.com/p/a",
            1000,
            25.0,
        )
        is None
    )
    # Confirm B succeeds
    order_id = db.create_order_with_balance_hold_consuming_funding_intent(
        intent_b,
        uid,
        "B",
        "svcB",
        "https://instagram.com/p/b",
        1000,
        30.0,
    )
    assert order_id is not None
    user = db.get_user(uid)
    assert float(user["balance"]) == 20.0
    assert db.get_pending_order_funding(uid) is None


def test_double_confirm_same_intent(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 107
    db.add_user(uid)
    _grant(uid, 50.0)
    intent_id = _upsert(uid, amount=25.0)
    first = db.create_order_with_balance_hold_consuming_funding_intent(
        intent_id,
        uid,
        "Test Service",
        "svc1",
        "https://instagram.com/p/abc",
        1000,
        25.0,
    )
    second = db.create_order_with_balance_hold_consuming_funding_intent(
        intent_id,
        uid,
        "Test Service",
        "svc1",
        "https://instagram.com/p/abc",
        1000,
        25.0,
    )
    assert first is not None
    assert second is None
    user = db.get_user(uid)
    assert float(user["balance"]) == 25.0
    with db.get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?", (uid,)
        ).fetchone()["c"]
    assert int(n) == 1


def test_consume_insufficient_balance_preserves_intent(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 108
    db.add_user(uid)
    _grant(uid, 10.0)
    intent_id = _upsert(uid, amount=25.0)
    assert (
        db.create_order_with_balance_hold_consuming_funding_intent(
            intent_id,
            uid,
            "Test Service",
            "svc1",
            "https://instagram.com/p/abc",
            1000,
            25.0,
        )
        is None
    )
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is not None
    user = db.get_user(uid)
    assert float(user["balance"]) == 10.0


def test_deposit_credit_unchanged_with_pending(tmp_path: Path) -> None:
    """Bank / CashPlus / etc. still credit normal balance; pending untouched."""
    _use_temp_db(tmp_path)
    uid = 109
    db.add_user(uid)
    intent_id = _upsert(uid, amount=25.0)
    for method, proof in (
        ("CIH", "p_cih"),
        ("CashPlus", "p_cp"),
        ("Wafacash", "p_wf"),
        ("Binance/Crypto", "p_crypto"),
        ("PayPal", "p_pp"),
    ):
        dep = db.create_deposit(uid, 0.0, method, proof)
        assert dep is not None
        assert db.finalize_approved_deposit(dep, uid, 5.0, method) is True
    user = db.get_user(uid)
    assert float(user["balance"]) == 25.0
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is not None


def test_recharge_credit_ratio_with_pending(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 110
    db.add_user(uid)
    intent_id = _upsert(uid, amount=25.0)
    for telecom, code in (
        ("Orange", "11111111111111"),
        ("Inwi", "22222222222222"),
        ("IAM", "33333333333333"),
    ):
        dep = db.create_deposit(uid, 10.0, telecom, code)
        assert dep is not None
        # 70% of 10 = 7
        assert db.finalize_approved_deposit(dep, uid, 7.0, telecom) is True
    user = db.get_user(uid)
    assert float(user["balance"]) == 21.0
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is not None


def test_cancel_deletes_intent_keeps_balance(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 111
    db.add_user(uid)
    _grant(uid, 15.0)
    intent_id = _upsert(uid, amount=25.0)
    assert db.delete_pending_order_funding_by_intent(intent_id, uid) is True
    user = db.get_user(uid)
    assert float(user["balance"]) == 15.0
    assert db.get_pending_order_funding(uid) is None


def test_intent_survives_reconnect(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 112
    db.add_user(uid)
    intent_id = _upsert(uid, amount=25.0)
    # Simulate process restart by opening a new connection path
    path = db.DB_PATH
    db.DB_PATH = path
    found = db.get_pending_order_funding_by_intent(intent_id, uid)
    assert found is not None
    assert int(found["intent_id"]) == intent_id


def test_resume_identity_check_rejects_replaced(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 113
    db.add_user(uid)
    a = _upsert(uid, amount=25.0)
    b = _upsert(uid, amount=25.0)
    assert db.get_pending_order_funding_by_intent(a, uid) is None
    assert db.get_pending_order_funding_by_intent(b, uid) is not None


def test_concurrent_double_consume(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 114
    db.add_user(uid)
    _grant(uid, 25.0)
    intent_id = _upsert(uid, amount=25.0)
    results: list[int | None] = []

    def _worker() -> None:
        results.append(
            db.create_order_with_balance_hold_consuming_funding_intent(
                intent_id,
                uid,
                "Test Service",
                "svc1",
                "https://instagram.com/p/abc",
                1000,
                25.0,
            )
        )

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    successes = [r for r in results if r is not None]
    assert len(successes) == 1
    user = db.get_user(uid)
    assert float(user["balance"]) == 0.0
    with db.get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?", (uid,)
        ).fetchone()["c"]
    assert int(n) == 1


def test_concurrent_consume_and_cancel(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 115
    db.add_user(uid)
    _grant(uid, 25.0)
    intent_id = _upsert(uid, amount=25.0)
    outcomes: dict[str, object] = {}

    def _consume() -> None:
        outcomes["order"] = db.create_order_with_balance_hold_consuming_funding_intent(
            intent_id,
            uid,
            "Test Service",
            "svc1",
            "https://instagram.com/p/abc",
            1000,
            25.0,
        )

    def _cancel() -> None:
        outcomes["deleted"] = db.delete_pending_order_funding_by_intent(intent_id, uid)

    t1 = threading.Thread(target=_consume)
    t2 = threading.Thread(target=_cancel)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # Exactly one of: order created (intent consumed) OR cancel won with no order.
    order_id = outcomes.get("order")
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is None
    if order_id is not None:
        user = db.get_user(uid)
        assert float(user["balance"]) == 0.0
    else:
        user = db.get_user(uid)
        assert float(user["balance"]) == 25.0
        with db.get_connection() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?", (uid,)
            ).fetchone()["c"]
        assert int(n) == 0


def test_concurrent_consume_and_replace(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 116
    db.add_user(uid)
    _grant(uid, 50.0)
    intent_a = _upsert(uid, amount=25.0)
    outcomes: dict[str, object] = {}

    def _consume_a() -> None:
        outcomes["order"] = db.create_order_with_balance_hold_consuming_funding_intent(
            intent_a,
            uid,
            "Test Service",
            "svc1",
            "https://instagram.com/p/abc",
            1000,
            25.0,
        )

    def _replace() -> None:
        outcomes["b"] = _upsert(uid, amount=30.0, service_id="svcB")

    t1 = threading.Thread(target=_consume_a)
    t2 = threading.Thread(target=_replace)
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    # A must never remain after either consume or replace.
    assert db.get_pending_order_funding_by_intent(intent_a, uid) is None
    order_id = outcomes.get("order")
    b = outcomes.get("b")
    if order_id is not None:
        # Consume won — B may or may not exist depending on race order.
        pass
    else:
        # Replace won before consume — B should exist and A confirm failed.
        assert b is not None
        assert int(b) != intent_a
        assert db.get_pending_order_funding_by_intent(int(b), uid) is not None


def test_normal_create_order_still_works_without_intent(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 117
    db.add_user(uid)
    _grant(uid, 25.0)
    order_id = db.create_order_with_balance_hold(
        uid, "Test Service", "svc1", "https://instagram.com/p/abc", 1000, 25.0
    )
    assert order_id is not None
    user = db.get_user(uid)
    assert float(user["balance"]) == 0.0


def test_intent_price_snapshot_decimal_compare() -> None:
    from services.order_funding import intent_price_matches_live

    intent = {
        "intent_id": 1,
        "user_id": 1,
        "service_id": "s",
        "service_name": "n",
        "platform_key": "p",
        "section_key": None,
        "subsection_key": None,
        "link": "l",
        "quantity": 1,
        "amount_dh": 25.0,
        "auto_quantity": 0,
        "created_at": "",
        "updated_at": "",
    }
    assert intent_price_matches_live(intent, to_decimal("25.00")) is True
    assert intent_price_matches_live(intent, to_decimal("25.01")) is False


def test_delete_by_user_on_new_order(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 118
    db.add_user(uid)
    intent_id = _upsert(uid)
    assert db.delete_pending_order_funding(uid) is True
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is None


def test_consume_accepts_soldium_service_id_kwarg(tmp_path: Path) -> None:
    """Finding 1 — Catalog kwargs must not TypeError on consuming path."""
    _use_temp_db(tmp_path)
    uid = 201
    db.add_user(uid)
    _grant(uid, 25.0)
    intent_id = _upsert(uid, amount=25.0)
    order_id = db.create_order_with_balance_hold_consuming_funding_intent(
        intent_id,
        uid,
        "Test Service",
        "svc1",
        "https://instagram.com/p/abc",
        1000,
        25.0,
        soldium_service_id="soldium-svc-99",
        external_service_id_snapshot="ext-1",
        catalog_id="cat-1",
    )
    assert order_id is not None
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is None
    with db.get_connection() as conn:
        cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(orders)")}
        row = conn.execute(
            "SELECT amount, soldium_service_id FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
    assert float(row["amount"]) == 25.0
    if "soldium_service_id" in cols:
        assert str(row["soldium_service_id"]) == "soldium-svc-99"


def test_consume_mismatch_amount_preserves_intent(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 202
    db.add_user(uid)
    _grant(uid, 50.0)
    intent_id = _upsert(uid, amount=25.0)
    assert (
        db.create_order_with_balance_hold_consuming_funding_intent(
            intent_id,
            uid,
            "Test Service",
            "svc1",
            "https://instagram.com/p/abc",
            1000,
            30.0,
        )
        is None
    )
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is not None
    assert float(db.get_user(uid)["balance"]) == 50.0


def test_consume_mismatch_service_or_link_preserves_intent(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 203
    db.add_user(uid)
    _grant(uid, 50.0)
    intent_id = _upsert(uid, amount=25.0, service_id="svc1")
    assert (
        db.create_order_with_balance_hold_consuming_funding_intent(
            intent_id,
            uid,
            "Test Service",
            "svcOTHER",
            "https://instagram.com/p/abc",
            1000,
            25.0,
        )
        is None
    )
    assert (
        db.create_order_with_balance_hold_consuming_funding_intent(
            intent_id,
            uid,
            "Test Service",
            "svc1",
            "https://instagram.com/p/DIFFERENT",
            1000,
            25.0,
        )
        is None
    )
    assert (
        db.create_order_with_balance_hold_consuming_funding_intent(
            intent_id,
            uid,
            "Test Service",
            "svc1",
            "https://instagram.com/p/abc",
            2000,
            25.0,
        )
        is None
    )
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is not None
    assert float(db.get_user(uid)["balance"]) == 50.0
    with db.get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?", (uid,)
        ).fetchone()["c"]
    assert int(n) == 0
