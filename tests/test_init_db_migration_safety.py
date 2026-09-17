"""Focused tests for init_db migration transaction hardening."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import database as db


def _legacy_smm_services_schema(connection: sqlite3.Connection) -> None:
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


def test_smm_services_rebuild_is_idempotent_and_adds_catalog_id(tmp_path: Path) -> None:
    path = tmp_path / "smm_mig.db"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        _legacy_smm_services_schema(connection)
        db._migrate_smm_services_catalog_identity(connection)
        cols = {str(r["name"]) for r in connection.execute("PRAGMA table_info(smm_services)")}
        assert "catalog_id" in cols
        row = connection.execute(
            "SELECT catalog_id, external_service_id, local_price_dh FROM smm_services"
        ).fetchone()
        assert row["catalog_id"] == "item-1"
        assert row["external_service_id"] == "1001"
        assert float(row["local_price_dh"]) == 12.5
        # Second run is a no-op (guarded by catalog_id presence).
        db._migrate_smm_services_catalog_identity(connection)
        count = connection.execute("SELECT COUNT(*) FROM smm_services").fetchone()[0]
        assert count == 1


def test_smm_services_rebuild_rolls_back_if_rename_fails(tmp_path: Path) -> None:
    path = tmp_path / "smm_rollback.db"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        _legacy_smm_services_schema(connection)

        def deny_rename(action, _arg1, arg2, _dbname, _source):
            # Deny ALTER TABLE on the temporary rebuild table (covers RENAME).
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
        cols = {row[1] for row in connection.execute("PRAGMA table_info(smm_services)")}
        assert "catalog_id" not in cols
        price = connection.execute(
            "SELECT local_price_dh FROM smm_services WHERE service_id = '1001'"
        ).fetchone()[0]
        assert float(price) == 12.5


def test_pending_referral_rebuild_rolls_back_if_rename_fails(tmp_path: Path) -> None:
    path = tmp_path / "referral_rollback.db"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        # Legacy shape: PRIMARY KEY(user_id) only, no AUTOINCREMENT id.
        connection.execute(
            """
            CREATE TABLE pending_referral_level_upgrades (
                user_id INTEGER PRIMARY KEY,
                new_level INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            "INSERT INTO pending_referral_level_upgrades (user_id, new_level) VALUES (7, 2)"
        )
        connection.commit()

        def deny_rename(action, _arg1, arg2, _dbname, _source):
            if (
                action == sqlite3.SQLITE_ALTER_TABLE
                and arg2 == "pending_referral_level_upgrades_new"
            ):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(deny_rename)
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            db._migrate_pending_referral_level_upgrades_table(connection)
        connection.set_authorizer(None)

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "pending_referral_level_upgrades" in tables
        assert "pending_referral_level_upgrades_new" not in tables
        level = connection.execute(
            "SELECT new_level FROM pending_referral_level_upgrades WHERE user_id = 7"
        ).fetchone()[0]
        assert int(level) == 2


def test_active_link_drop_index_remains_and_is_recreated(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "active_link_tx.db"
    db.init_db()
    with db.get_connection() as connection:
        before = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'idx_orders_active_normalized_link'"
        ).fetchone()
        assert before is not None
        report = db._migrate_orders_active_link_guard(connection)
        # Fully migrated DB is a no-op (Phase 7); index must remain.
        assert report["skipped"] is True
        assert report["index_created"] is False
        after = connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'idx_orders_active_normalized_link'"
        ).fetchone()
        assert after is not None
