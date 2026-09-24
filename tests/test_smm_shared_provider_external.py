# -*- coding: utf-8 -*-
"""Integration: multiple smm_services rows may share provider_slug + external_service_id."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("SMM_KEY_DEFAULT", "default-test-key")
os.environ.setdefault("ADMIN_ID", "1")

import database as db
from services.provider_price_sync import ProviderServiceEntry, sync_provider_prices_to_db
from services_catalog_db import get_provider_limits_from_db


@pytest.fixture
def migrated_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "shared_sku_integration.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr("services.provider_price_sync.DB_PATH", path)
    monkeypatch.setattr("services_catalog_db.DB_PATH", path)
    db.init_db()
    # Fresh init must not enforce UNIQUE(provider_slug, external_service_id).
    with db.get_connection() as conn:
        assert not db._smm_provider_external_unique_enforced(conn)
    return path


def _insert_row(
    conn: sqlite3.Connection,
    *,
    catalog_id: str,
    external_service_id: str = "4210",
    name_ar: str = "",
    local_price_dh: float = 3.0,
    provider_price_usd: float = 1.0,
    min_qty: int = 10,
    max_qty: int = 1000,
    is_active: int = 1,
) -> None:
    conn.execute(
        """
        INSERT INTO smm_services (
            catalog_id, external_service_id, service_id, provider_slug,
            name_ar, provider_price_usd, local_price_dh, min_qty, max_qty,
            is_active, platform_key, local_item_id
        ) VALUES (?, ?, ?, 'gozibra', ?, ?, ?, ?, ?, ?, 'telegram', ?)
        """,
        (
            catalog_id,
            external_service_id,
            external_service_id,
            name_ar or catalog_id,
            provider_price_usd,
            local_price_dh,
            min_qty,
            max_qty,
            is_active,
            catalog_id,
        ),
    )


def test_two_rows_same_provider_external_coexist(migrated_db: Path) -> None:
    with db.get_connection() as conn:
        _insert_row(conn, catalog_id="A", name_ar="Service A", local_price_dh=3.0)
        _insert_row(conn, catalog_id="B", name_ar="Service B", local_price_dh=5.0)
        rows = conn.execute(
            """
            SELECT catalog_id, provider_slug, external_service_id, local_price_dh
            FROM smm_services
            WHERE provider_slug='gozibra' AND external_service_id='4210'
            ORDER BY catalog_id
            """
        ).fetchall()
    assert [r["catalog_id"] for r in rows] == ["A", "B"]
    assert float(rows[0]["local_price_dh"]) == 3.0
    assert float(rows[1]["local_price_dh"]) == 5.0


def test_update_by_catalog_id_does_not_mutate_twin(migrated_db: Path) -> None:
    with db.get_connection() as conn:
        _insert_row(conn, catalog_id="A", local_price_dh=3.0, name_ar="A")
        _insert_row(conn, catalog_id="B", local_price_dh=5.0, name_ar="B")
        conn.execute(
            "UPDATE smm_services SET local_price_dh = 9.9, name_ar = 'A-new' WHERE catalog_id = ?",
            ("A",),
        )
        a = conn.execute(
            "SELECT name_ar, local_price_dh FROM smm_services WHERE catalog_id='A'"
        ).fetchone()
        b = conn.execute(
            "SELECT name_ar, local_price_dh FROM smm_services WHERE catalog_id='B'"
        ).fetchone()
    assert a["name_ar"] == "A-new"
    assert float(a["local_price_dh"]) == 9.9
    assert b["name_ar"] == "B"
    assert float(b["local_price_dh"]) == 5.0


def test_remap_cases_shared_external(migrated_db: Path) -> None:
    """CASE 1–4: A/B share 4210; A remaps away and back; both remain active."""
    with db.get_connection() as conn:
        _insert_row(conn, catalog_id="A", external_service_id="4210")
        _insert_row(conn, catalog_id="B", external_service_id="4210")

        # CASE 2: A → 5000, B stays 4210
        conn.execute(
            "UPDATE smm_services SET external_service_id = ? WHERE catalog_id = ?",
            ("5000", "A"),
        )
        a = conn.execute(
            "SELECT external_service_id FROM smm_services WHERE catalog_id='A'"
        ).fetchone()
        b = conn.execute(
            "SELECT external_service_id FROM smm_services WHERE catalog_id='B'"
        ).fetchone()
        assert str(a["external_service_id"]) == "5000"
        assert str(b["external_service_id"]) == "4210"

        # CASE 3: A → 4210 again (shared with B)
        conn.execute(
            "UPDATE smm_services SET external_service_id = ? WHERE catalog_id = ?",
            ("4210", "A"),
        )
        # CASE 4: both active on 4210
        twins = conn.execute(
            """
            SELECT catalog_id, is_active FROM smm_services
            WHERE provider_slug='gozibra' AND external_service_id='4210'
            ORDER BY catalog_id
            """
        ).fetchall()
        assert [r["catalog_id"] for r in twins] == ["A", "B"]
        assert all(int(r["is_active"]) == 1 for r in twins)


def test_price_sync_updates_each_catalog_id(migrated_db: Path) -> None:
    with db.get_connection() as conn:
        _insert_row(conn, catalog_id="A", provider_price_usd=1.0)
        _insert_row(conn, catalog_id="B", provider_price_usd=1.0)
        _insert_row(
            conn,
            catalog_id="C-other",
            external_service_id="9999",
            provider_price_usd=1.0,
        )

    catalogs = {
        "gozibra": {
            "default": {
                4210: ProviderServiceEntry(4210, 8.5, 25, 2500, "default", "gozibra"),
            }
        }
    }
    result = sync_provider_prices_to_db(catalogs, active_only=False, db_path=migrated_db)
    assert result.updated == 2

    with db.get_connection() as conn:
        for cid in ("A", "B"):
            row = conn.execute(
                "SELECT provider_price_usd, min_qty, max_qty FROM smm_services WHERE catalog_id=?",
                (cid,),
            ).fetchone()
            assert float(row["provider_price_usd"]) == 8.5
            assert int(row["min_qty"]) == 25
            assert int(row["max_qty"]) == 2500
        other = conn.execute(
            "SELECT provider_price_usd FROM smm_services WHERE catalog_id='C-other'"
        ).fetchone()
        assert float(other["provider_price_usd"]) == 1.0


def test_limits_consensus_and_conflict(migrated_db: Path) -> None:
    assert get_provider_limits_from_db("gozibra", 4210) is None

    with db.get_connection() as conn:
        _insert_row(conn, catalog_id="A", min_qty=10, max_qty=100)
    assert get_provider_limits_from_db("gozibra", 4210) == (10, 100)

    with db.get_connection() as conn:
        _insert_row(conn, catalog_id="B", min_qty=10, max_qty=100)
    assert get_provider_limits_from_db("gozibra", 4210) == (10, 100)

    with db.get_connection() as conn:
        conn.execute(
            "UPDATE smm_services SET min_qty=5, max_qty=50 WHERE catalog_id='B'"
        )
    assert get_provider_limits_from_db("gozibra", 4210) is None


def test_migration_preserves_row_count_and_catalog_ids(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "preserve.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()

    # Re-introduce UNIQUE then seed twins impossible without rebuild path —
    # instead seed distinct SKUs, force UNIQUE table, re-run drop, compare.
    with db.get_connection() as conn:
        for i, cid in enumerate(("keep-1", "keep-2", "keep-3"), start=1):
            _insert_row(
                conn,
                catalog_id=cid,
                external_service_id=str(1000 + i),
                name_ar=cid,
                local_price_dh=float(i),
            )
        before_ids = {
            r["catalog_id"]
            for r in conn.execute("SELECT catalog_id FROM smm_services").fetchall()
        }
        before_count = len(before_ids)
        before_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        before_orders = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]

        # Force UNIQUE back onto a copy of current rows.
        conn.execute("DROP INDEX IF EXISTS idx_smm_services_provider_external")
        conn.execute(
            """
            CREATE TABLE smm_tmp_unique (
                catalog_id TEXT PRIMARY KEY,
                external_service_id TEXT NOT NULL,
                provider_slug TEXT NOT NULL DEFAULT 'gozibra',
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
                local_item_id TEXT NOT NULL DEFAULT '',
                platform_title TEXT NOT NULL DEFAULT '',
                section_title TEXT,
                subsection_title TEXT,
                fulfillment_mode TEXT NOT NULL DEFAULT 'auto',
                provider_api_account TEXT,
                provider_price_updated_at TEXT,
                service_id TEXT NOT NULL DEFAULT '',
                UNIQUE(provider_slug, external_service_id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO smm_tmp_unique (
                catalog_id, external_service_id, provider_slug, category, name_ar,
                provider_price_usd, local_price_dh, min_qty, max_qty, is_active,
                platform_key, section_key, subsection_key, local_item_id,
                platform_title, section_title, subsection_title,
                fulfillment_mode, provider_api_account, provider_price_updated_at, service_id
            )
            SELECT
                catalog_id, external_service_id, provider_slug, category, name_ar,
                provider_price_usd, local_price_dh, min_qty, max_qty, is_active,
                platform_key, section_key, subsection_key, local_item_id,
                platform_title, section_title, subsection_title,
                fulfillment_mode, provider_api_account, provider_price_updated_at, service_id
            FROM smm_services
            """
        )
        conn.execute("DROP TABLE smm_services")
        conn.execute("ALTER TABLE smm_tmp_unique RENAME TO smm_services")
        conn.execute(
            """
            CREATE UNIQUE INDEX idx_smm_services_provider_external
            ON smm_services (provider_slug, external_service_id)
            """
        )

    assert "smm_services_drop_provider_external_unique" in db.pending_init_db_migrations(
        db_path=path
    )
    db.init_db()

    with db.get_connection() as conn:
        after_ids = {
            r["catalog_id"]
            for r in conn.execute("SELECT catalog_id FROM smm_services").fetchall()
        }
        assert after_ids == before_ids
        assert len(after_ids) == before_count
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == before_users
        assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == before_orders
        assert not db._smm_provider_external_unique_enforced(conn)
