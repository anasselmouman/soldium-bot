"""Phase 7: one-time migrations become no-ops when already applied."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import database as db


def test_fresh_init_db_still_works(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "fresh.db"
    assert not db.DB_PATH.exists()
    db.init_db()
    assert db.DB_PATH.exists()
    with db.get_connection() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "users" in tables
    assert "orders" in tables
    assert db.pending_init_db_migrations(db_path=db.DB_PATH) == []


def test_active_link_migration_runs_when_incomplete(tmp_path: Path) -> None:
    path = tmp_path / "active_incomplete.db"
    db.DB_PATH = path
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0);
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service_name TEXT NOT NULL DEFAULT '',
                service_id TEXT NOT NULL,
                link TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                amount REAL NOT NULL DEFAULT 0.0,
                total_price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                provider_order_id TEXT,
                api_account TEXT NOT NULL DEFAULT 'default',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO orders (
                user_id, service_name, service_id, link, quantity, amount, total_price, status
            ) VALUES
              (1, 'a', '1', 'https://instagram.com/dup', 1, 1, 1, 'pending'),
              (2, 'b', '2', 'https://www.instagram.com/dup/', 1, 1, 1, 'submitted');
            """
        )
        connection.commit()

    assert "orders_active_link_guard" in db.pending_init_db_migrations(db_path=path)
    db.init_db()
    with db.get_connection() as connection:
        assert db._orders_active_link_index_exists(connection)
        rows = connection.execute(
            "SELECT id, normalized_link FROM orders ORDER BY id"
        ).fetchall()
    assert str(rows[0]["normalized_link"] or "") == "https://instagram.com/dup"
    assert rows[1]["normalized_link"] in (None, "")


def test_active_link_complete_db_does_not_drop_index(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "active_complete.db"
    db.init_db()

    with db.get_connection() as connection:
        first = db._migrate_orders_active_link_guard(connection)
        assert first["skipped"] is True

        def deny_drop_index(action, _arg1, _arg2, _dbname, _source):
            if action == sqlite3.SQLITE_DROP_INDEX:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(deny_drop_index)
        second = db._migrate_orders_active_link_guard(connection)
        connection.set_authorizer(None)
        assert second["skipped"] is True
        assert db._orders_active_link_index_exists(connection)


def test_orders_amount_backfill_only_when_needed(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "amount.db"
    db.init_db()
    with db.get_connection() as connection:
        connection.execute(
            """
            INSERT INTO orders (
                user_id, service_name, service_id, link, quantity,
                amount, total_price, status, api_account
            ) VALUES
              (1, 'a', '1', 'https://x/a', 1, 0, 40, 'completed', 'default'),
              (1, 'b', '2', 'https://x/b', 1, 12, 12, 'completed', 'default')
            """
        )
        connection.commit()
        changed = db._backfill_orders_amount_from_total_price(connection)
        assert changed == 1
        amounts = [
            float(r[0])
            for r in connection.execute(
                "SELECT amount FROM orders ORDER BY id"
            ).fetchall()
        ]
        assert amounts == [40.0, 12.0]
        assert db._backfill_orders_amount_from_total_price(connection) == 0


def test_smm_fulfillment_backfill_only_when_needed(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "fulfill.db"
    db.init_db()
    with db.get_connection() as connection:
        connection.execute(
            """
            INSERT INTO smm_services (
                catalog_id, external_service_id, service_id, provider_slug,
                platform_key, fulfillment_mode, name_ar, local_item_id
            ) VALUES
              ('c1', '1', '1', 'gozibra', 'subscriptions', 'auto', 'Sub', 'c1'),
              ('c2', '2', '2', 'gozibra', 'instagram', 'auto', 'IG', 'c2')
            """
        )
        connection.commit()
        assert db._backfill_smm_subscription_fulfillment_mode(connection) == 1
        modes = {
            str(r[0]): str(r[1])
            for r in connection.execute(
                "SELECT platform_key, fulfillment_mode FROM smm_services"
            )
        }
        assert modes["subscriptions"] == "admin"
        assert modes["instagram"] == "auto"
        assert db._backfill_smm_subscription_fulfillment_mode(connection) == 0


def test_pending_empty_after_full_init(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "pending_empty.db"
    db.init_db()
    assert db.pending_init_db_migrations(db_path=db.DB_PATH) == []


def test_pending_detects_missing_required_indexes(tmp_path: Path) -> None:
    path = tmp_path / "missing_indexes.db"
    db.DB_PATH = path
    db.init_db()
    assert db.pending_init_db_migrations(db_path=path) == []

    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX IF EXISTS idx_deposits_active_proof_unique")
        connection.commit()
    pending = db.pending_init_db_migrations(db_path=path)
    assert "create_index:idx_deposits_active_proof_unique" in pending

    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_deposits_active_proof_unique
            ON deposits(proof_file_id)
            WHERE status = 'pending' OR status LIKE 'approved:%'
            """
        )
        connection.execute("DROP INDEX IF EXISTS idx_smm_services_provider_external")
        connection.commit()
    pending = db.pending_init_db_migrations(db_path=path)
    assert "create_index:idx_smm_services_provider_external" in pending
    assert "create_index:idx_deposits_active_proof_unique" not in pending


def test_missing_index_triggers_pre_migration_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "index_backup.db"
    db.DB_PATH = path
    db.init_db()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX IF EXISTS idx_deposits_active_proof_unique")
        connection.commit()

    backups: list[Path] = []
    real_backup = db.backup_database

    def tracking_backup(**kwargs):
        result = real_backup(**kwargs)
        backups.append(result)
        return result

    monkeypatch.setattr(db, "backup_database", tracking_backup)
    assert "create_index:idx_deposits_active_proof_unique" in db.pending_init_db_migrations(
        db_path=path
    )
    db.init_db()
    assert backups, "missing required index must trigger pre-migration backup"
    assert db.pending_init_db_migrations(db_path=path) == []


def test_phase4_smm_rebuild_rollback_still_works(tmp_path: Path) -> None:
    path = tmp_path / "smm_rollback.db"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE smm_services (
                service_id TEXT PRIMARY KEY,
                category TEXT NOT NULL DEFAULT '',
                name_ar TEXT NOT NULL DEFAULT '',
                provider_price_usd REAL NOT NULL DEFAULT 0,
                local_price_dh REAL NOT NULL DEFAULT 0,
                min_qty INTEGER NOT NULL DEFAULT 1,
                max_qty INTEGER NOT NULL DEFAULT 1000000,
                is_active INTEGER NOT NULL DEFAULT 1,
                platform_key TEXT NOT NULL DEFAULT '',
                section_key TEXT,
                subsection_key TEXT,
                local_item_id TEXT NOT NULL DEFAULT 'item-1',
                platform_title TEXT NOT NULL DEFAULT '',
                section_title TEXT,
                subsection_title TEXT,
                fulfillment_mode TEXT NOT NULL DEFAULT 'auto',
                provider_api_account TEXT,
                provider_price_updated_at TEXT,
                provider_slug TEXT NOT NULL DEFAULT 'gozibra'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO smm_services (
                service_id, category, name_ar, local_price_dh, local_item_id, provider_slug
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("1001", "ig", "Test Service", 12.5, "item-1", "gozibra"),
        )
        connection.commit()

        def deny_rename(action, _arg1, arg2, _dbname, _source):
            if action == sqlite3.SQLITE_ALTER_TABLE and arg2 == "smm_services_v2":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(deny_rename)
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            db._migrate_smm_services_catalog_identity(connection)
        connection.set_authorizer(None)

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "smm_services" in tables
        assert "smm_services_v2" not in tables
