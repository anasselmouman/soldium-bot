# -*- coding: utf-8 -*-
"""Active-link order protection tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import database as db
from utils.active_link_guard import (
    ACTIVE_LINK_OCCUPIED_MESSAGE,
    ACTIVE_LINK_ORDER_STATUS_KEYS,
    TERMINAL_LINK_ORDER_STATUS_KEYS,
    ActiveLinkOccupiedError,
    find_active_order_for_link,
    is_active_link_order_status,
    is_terminal_link_order_status,
    normalize_order_link,
)


def _use_temp_db(tmp_path: Path) -> Path:
    path = tmp_path / "active_link.db"
    db.DB_PATH = path
    db.init_db()
    return path


def _grant(uid: int, amount: float) -> None:
    db.add_user(uid)
    dep = db.create_deposit(uid, 0.0, "CashPlus", f"proof_{uid}_{amount}_{id(object())}")
    assert dep is not None
    assert db.finalize_approved_deposit(dep, uid, amount, "CashPlus") is True


def _create(
    uid: int,
    link: str,
    *,
    amount: float = 10.0,
    service_id: str = "svc1",
    status: str = "pending",
    fulfillment: str = "auto",
) -> int:
    oid = db.create_order_with_balance_hold(
        uid,
        "خدمة",
        service_id,
        link,
        100,
        amount,
        initial_status=status,
        fulfillment_mode=fulfillment,
    )
    assert oid is not None
    return oid


def test_normalize_strips_www_and_trailing_slash():
    a = normalize_order_link("https://www.instagram.com/example/")
    b = normalize_order_link("https://instagram.com/example")
    assert a == b == "https://instagram.com/example"


def test_normalize_preserves_query_and_distinct_paths():
    assert normalize_order_link("https://instagram.com/a?x=1") != normalize_order_link(
        "https://instagram.com/a"
    )
    assert normalize_order_link("https://instagram.com/user1") != normalize_order_link(
        "https://instagram.com/user2"
    )


def test_normalize_empty():
    assert normalize_order_link("") == ""
    assert normalize_order_link("   ") == ""
    assert normalize_order_link(None) == ""


def test_normalize_opaque_trim_only():
    assert normalize_order_link("  @Channel  ") == "@Channel"


@pytest.mark.parametrize(
    "status",
    ["pending", "pending_admin", "submitted", "in progress", "processing", "In_Progress"],
)
def test_active_statuses(status: str):
    assert is_active_link_order_status(status) is True


@pytest.mark.parametrize(
    "status",
    ["completed", "partial", "canceled", "cancelled", "failed", "refunded"],
)
def test_terminal_statuses(status: str):
    assert is_terminal_link_order_status(status) is True
    assert is_active_link_order_status(status) is False


def test_pending_admin_is_active_by_design():
    """pending_admin occupies the link: real unresolved Telegram/admin order."""
    assert is_active_link_order_status("pending_admin") is True
    assert "pending admin" in ACTIVE_LINK_ORDER_STATUS_KEYS
    assert "completed" in TERMINAL_LINK_ORDER_STATUS_KEYS


@pytest.mark.parametrize(
    "active_status",
    ["pending", "submitted", "in progress", "processing", "pending_admin"],
)
def test_active_status_blocks_same_link(tmp_path: Path, active_status: str):
    _use_temp_db(tmp_path)
    _grant(1, 100.0)
    _grant(2, 100.0)
    link = "https://instagram.com/blockme"
    if active_status == "pending_admin":
        oid = _create(1, link, status="pending_admin", fulfillment="admin")
    else:
        oid = _create(1, link, status="pending")
        if active_status == "submitted":
            assert db.set_provider_order_id(oid, "P-BLOCK")
        elif active_status in {"in progress", "processing"}:
            assert db.update_order_status(oid, active_status)

    bal_before = float(db.get_user(2)["balance"])  # type: ignore[index]
    with pytest.raises(ActiveLinkOccupiedError) as exc_info:
        db.create_order_with_balance_hold(2, "خدمة", "other", link, 50, 10.0)
    assert "الرابط مشغول" in exc_info.value.message
    assert float(db.get_user(2)["balance"]) == bal_before  # type: ignore[index]
    assert db.count_user_orders(2) == 0


@pytest.mark.parametrize(
    "terminal_status",
    ["completed", "partial", "canceled", "cancelled", "failed", "refunded"],
)
def test_terminal_status_allows_same_link(tmp_path: Path, terminal_status: str):
    _use_temp_db(tmp_path)
    _grant(1, 100.0)
    _grant(2, 100.0)
    link = "https://instagram.com/free-me"
    oid = _create(1, link)
    stored = "canceled" if terminal_status == "cancelled" else terminal_status
    if stored == "failed":
        assert db.refund_order(oid)
    elif stored in {"canceled", "refunded"}:
        assert db.apply_partial_or_full_refund(oid, 10.0, next_status=stored)
    elif stored == "partial":
        assert db.apply_partial_or_full_refund(
            oid, 5.0, next_status="partial", status_note="جزء"
        )
    else:
        assert db.update_order_status(oid, stored)

    oid2 = db.create_order_with_balance_hold(2, "خدمة", "s2", link, 50, 10.0)
    assert oid2 is not None
    assert db.count_user_orders(2) == 1


def test_different_link_allowed(tmp_path: Path):
    _use_temp_db(tmp_path)
    _grant(1, 100.0)
    _create(1, "https://instagram.com/one")
    oid2 = db.create_order_with_balance_hold(
        1, "خدمة", "s2", "https://instagram.com/two", 50, 10.0
    )
    assert oid2 is not None


def test_different_user_same_link_blocked(tmp_path: Path):
    _use_temp_db(tmp_path)
    _grant(1, 50.0)
    _grant(2, 50.0)
    link = "https://tiktok.com/@same"
    _create(1, link)
    with pytest.raises(ActiveLinkOccupiedError):
        db.create_order_with_balance_hold(2, "خدمة", "likes", link, 10, 10.0)


def test_same_user_different_link_allowed(tmp_path: Path):
    _use_temp_db(tmp_path)
    _grant(1, 50.0)
    _create(1, "https://instagram.com/a")
    assert (
        db.create_order_with_balance_hold(1, "خدمة", "s2", "https://instagram.com/b", 10, 10.0)
        is not None
    )


def test_different_service_same_link_blocked(tmp_path: Path):
    _use_temp_db(tmp_path)
    _grant(1, 50.0)
    _grant(2, 50.0)
    link = "https://instagram.com/svc-test"
    _create(1, link, service_id="followers")
    with pytest.raises(ActiveLinkOccupiedError):
        db.create_order_with_balance_hold(2, "خدمة", "likes", link, 10, 10.0)


def test_equivalent_normalized_forms_block(tmp_path: Path):
    _use_temp_db(tmp_path)
    _grant(1, 50.0)
    _grant(2, 50.0)
    _create(1, "https://www.instagram.com/example/")
    with pytest.raises(ActiveLinkOccupiedError):
        db.create_order_with_balance_hold(
            2, "خدمة", "s2", "https://instagram.com/example", 10, 10.0
        )


def test_empty_link_does_not_global_collide(tmp_path: Path):
    _use_temp_db(tmp_path)
    _grant(1, 50.0)
    _grant(2, 50.0)
    assert _create(1, "") is not None
    oid = db.create_order_with_balance_hold(2, "خدمة", "s2", "", 10, 10.0)
    assert oid is not None
    oid3 = db.create_order_with_balance_hold(1, "خدمة", "s3", "   ", 10, 10.0)
    assert oid3 is not None


def test_blocked_does_not_create_row(tmp_path: Path):
    _use_temp_db(tmp_path)
    _grant(1, 50.0)
    _grant(2, 50.0)
    link = "https://instagram.com/norow"
    _create(1, link)
    before = db.count_orders()
    with pytest.raises(ActiveLinkOccupiedError):
        db.create_order_with_balance_hold(2, "خدمة", "s2", link, 10, 10.0)
    assert db.count_orders() == before


def test_terminal_transition_frees_link(tmp_path: Path):
    _use_temp_db(tmp_path)
    _grant(1, 100.0)
    _grant(2, 100.0)
    link = "https://instagram.com/free-later"
    oid = _create(1, link)
    with pytest.raises(ActiveLinkOccupiedError):
        db.create_order_with_balance_hold(2, "خدمة", "s2", link, 10, 10.0)
    assert db.refund_order(oid) is True
    oid2 = db.create_order_with_balance_hold(2, "خدمة", "s2", link, 10, 10.0)
    assert oid2 is not None


def test_historical_terminals_do_not_block(tmp_path: Path):
    _use_temp_db(tmp_path)
    _grant(1, 200.0)
    link = "https://instagram.com/history"
    for i in range(3):
        oid = _create(1, link, amount=10.0, service_id=f"s{i}")
        assert db.refund_order(oid)
    oid_new = db.create_order_with_balance_hold(1, "خدمة", "fresh", link, 10, 10.0)
    assert oid_new is not None


def test_find_active_order_helper(tmp_path: Path):
    _use_temp_db(tmp_path)
    _grant(1, 50.0)
    link = "https://instagram.com/find-me"
    oid = _create(1, link)
    with db.get_connection() as conn:
        hit = find_active_order_for_link(conn, "https://www.instagram.com/find-me/")
    assert hit is not None
    assert int(hit["id"]) == oid


def test_unique_index_exists_after_init(tmp_path: Path):
    _use_temp_db(tmp_path)
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_orders_active_normalized_link'"
        ).fetchone()
    assert row is not None


def test_migration_clears_duplicate_normalized_link_keeps_orders(tmp_path: Path):
    path = tmp_path / "conflict.db"
    db.DB_PATH = path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE users (user_id INTEGER PRIMARY KEY, balance REAL, total_spent REAL);
        INSERT INTO users VALUES (1, 100, 0), (2, 100, 0);
        CREATE TABLE orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            service_name TEXT NOT NULL DEFAULT '',
            service_id TEXT NOT NULL,
            link TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            total_price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            provider_order_id TEXT,
            api_account TEXT NOT NULL DEFAULT 'default',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO orders (user_id, service_name, service_id, link, quantity, amount, total_price, status)
        VALUES
          (1, 'a', '1', 'https://instagram.com/dup', 1, 1, 1, 'pending'),
          (2, 'b', '2', 'https://www.instagram.com/dup/', 1, 1, 1, 'submitted');
        """
    )
    conn.commit()
    conn.close()

    db.init_db()
    with db.get_connection() as conn:
        rows = conn.execute(
            "SELECT id, status, normalized_link FROM orders ORDER BY id"
        ).fetchall()
        assert len(rows) == 2
        assert str(rows[0]["normalized_link"] or "") == "https://instagram.com/dup"
        assert rows[1]["normalized_link"] in (None, "")
        assert str(rows[0]["status"]) == "pending"
        assert str(rows[1]["status"]) == "submitted"
        hit = find_active_order_for_link(conn, "https://instagram.com/dup")
        assert hit is not None


def test_arabic_message_constant():
    assert "الرابط مشغول" in ACTIVE_LINK_OCCUPIED_MESSAGE
    assert "قيد التنفيذ" in ACTIVE_LINK_OCCUPIED_MESSAGE
    assert "جزئياً" in ACTIVE_LINK_OCCUPIED_MESSAGE
    assert "<b>" in ACTIVE_LINK_OCCUPIED_MESSAGE
    assert "database" not in ACTIVE_LINK_OCCUPIED_MESSAGE.lower()
    assert "provider" not in ACTIVE_LINK_OCCUPIED_MESSAGE.lower()


def test_is_active_link_unique_violation_narrow():
    from utils.active_link_guard import is_active_link_unique_violation

    assert is_active_link_unique_violation(
        Exception("UNIQUE constraint failed: orders.normalized_link")
    )
    assert is_active_link_unique_violation(
        Exception("unique constraint failed: index 'idx_orders_active_normalized_link'")
    )
    assert not is_active_link_unique_violation(
        Exception("UNIQUE constraint failed: users.user_id")
    )
    assert not is_active_link_unique_violation(Exception("NOT NULL constraint failed"))


def test_provider_snapshot_create_still_works(tmp_path: Path):
    _use_temp_db(tmp_path)
    _grant(42, 1000.0)
    oid = db.create_order_with_balance_hold(
        user_id=42,
        service_name="خدمة",
        service_id="100",
        link="https://example.com/ok",
        quantity=50,
        amount=12.5,
        api_account="tiktok",
        provider_slug="gozibra",
        catalog_id="cat-100",
        external_service_id_snapshot="555001",
    )
    assert oid is not None
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT catalog_id, external_service_id_snapshot, normalized_link "
            "FROM orders WHERE id=?",
            (oid,),
        ).fetchone()
    assert row["catalog_id"] == "cat-100"
    assert row["external_service_id_snapshot"] == "555001"
    assert row["normalized_link"] == "https://example.com/ok"


def test_db_unique_rejects_second_active_insert(tmp_path: Path):
    """Concurrency safety: unique partial index rejects a second active key."""
    _use_temp_db(tmp_path)
    _grant(1, 100.0)
    _grant(2, 100.0)
    link = "https://instagram.com/race"
    _create(1, link)
    # Bypass application pre-check: insert directly should hit unique index.
    with db.get_connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO orders (
                    user_id, service_name, service_id, link, quantity,
                    amount, total_price, status, normalized_link
                )
                VALUES (2, 'x', 'y', ?, 1, 1, 1, 'pending', ?)
                """,
                (link, normalize_order_link(link)),
            )
        conn.rollback()


def test_concurrent_create_only_one_active_order(tmp_path: Path):
    """Two racing create_order calls: exactly one active order, loser is safe."""
    import threading

    _use_temp_db(tmp_path)
    _grant(1, 100.0)
    _grant(2, 100.0)
    link = "https://instagram.com/concurrent"
    barrier = threading.Barrier(2)
    results: list[object] = []
    lock = threading.Lock()

    def _attempt(uid: int) -> None:
        barrier.wait(timeout=5)
        try:
            oid = db.create_order_with_balance_hold(
                uid, "خدمة", f"svc-{uid}", link, 10, 10.0
            )
            with lock:
                results.append(("ok", oid))
        except ActiveLinkOccupiedError as exc:
            with lock:
                results.append(("blocked", str(exc.message)))

    t1 = threading.Thread(target=_attempt, args=(1,))
    t2 = threading.Thread(target=_attempt, args=(2,))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    oks = [r for r in results if r[0] == "ok"]
    blocked = [r for r in results if r[0] == "blocked"]
    assert len(oks) == 1
    assert len(blocked) == 1
    assert "الرابط مشغول" in blocked[0][1]
    with db.get_connection() as conn:
        active = conn.execute(
            """
            SELECT COUNT(*) AS c FROM orders
            WHERE LOWER(REPLACE(status, '_', ' ')) IN (
                'pending', 'pending admin', 'submitted', 'in progress', 'processing'
            )
            AND normalized_link = ?
            """,
            (normalize_order_link(link),),
        ).fetchone()
    assert int(active["c"] if hasattr(active, "keys") else active[0]) == 1
    for uid in (1, 2):
        count = db.count_user_orders(uid)
        bal = float(db.get_user(uid)["balance"])  # type: ignore[index]
        if count == 0:
            assert bal == 100.0
        else:
            assert count == 1
            assert bal == 90.0


def test_unrelated_integrity_error_not_mapped_to_active_link(tmp_path: Path):
    from utils.active_link_guard import is_active_link_unique_violation

    assert not is_active_link_unique_violation(
        sqlite3.IntegrityError("UNIQUE constraint failed: users.telegram_id")
    )


def test_active_link_keyboard_has_back():
    from keyboards.orders import build_order_active_link_occupied_keyboard
    from keyboards.nav_labels import BTN_BACK_STEP

    kb = build_order_active_link_occupied_keyboard()
    texts = [btn.text for row in kb.inline_keyboard for btn in row]
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert BTN_BACK_STEP in texts
    assert "order:nav:back" in callbacks
    assert "order:confirm:yes" not in callbacks
