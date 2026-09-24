# -*- coding: utf-8 -*-
"""Duplicate-safe preparation: provider SKU may map to N smm_services rows.

Production UNIQUE remains; these tests use an isolated schema without that
constraint so N-row behavior can be exercised safely.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("SMM_KEY_INSTAGRAM", "ig-test-key")
os.environ.setdefault("SMM_KEY_FACEBOOK", "fb-test-key")
os.environ.setdefault("SMM_KEY_TIKTOK", "tt-test-key")
os.environ.setdefault("SMM_KEY_DEFAULT", "default-test-key")
os.environ.setdefault("ADMIN_ID", "1")

import database as db
from services.provider_catalog import (
    _LIMITS,
    clear_limits_cache,
    get_provider_limits,
)
from services.provider_price_sync import (
    ProviderServiceEntry,
    sync_provider_prices_to_db,
)
from services_catalog_db import get_provider_limits_from_db


def _create_smm_without_provider_external_unique(conn) -> None:
    """Test-only schema: catalog_id PK, no UNIQUE(provider_slug, external)."""
    conn.executescript(
        """
        CREATE TABLE smm_services (
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
            local_item_id TEXT NOT NULL DEFAULT '',
            platform_title TEXT NOT NULL DEFAULT '',
            fulfillment_mode TEXT NOT NULL DEFAULT 'auto',
            provider_api_account TEXT,
            provider_price_updated_at TEXT,
            service_id TEXT NOT NULL DEFAULT ''
        );
        """
    )


def _insert(
    conn,
    *,
    catalog_id: str,
    external_service_id: str,
    provider_slug: str = "gozibra",
    provider_price_usd: float = 1.0,
    min_qty: int = 10,
    max_qty: int = 1000,
    is_active: int = 1,
    platform_key: str = "telegram",
) -> None:
    conn.execute(
        """
        INSERT INTO smm_services (
            catalog_id, external_service_id, service_id, provider_slug,
            provider_price_usd, local_price_dh, min_qty, max_qty,
            is_active, platform_key, local_item_id, name_ar
        ) VALUES (?, ?, ?, ?, ?, 10.0, ?, ?, ?, ?, ?, ?)
        """,
        (
            catalog_id,
            external_service_id,
            external_service_id,
            provider_slug,
            provider_price_usd,
            min_qty,
            max_qty,
            is_active,
            platform_key,
            catalog_id,
            catalog_id,
        ),
    )


@pytest.fixture
def multi_row_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "provider_sku_multi.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setattr("services.provider_price_sync.DB_PATH", path)
    monkeypatch.setattr("services_catalog_db.DB_PATH", path)
    with db.get_connection() as conn:
        _create_smm_without_provider_external_unique(conn)
        conn.commit()
    return path


def test_price_sync_zero_matching_rows(multi_row_db: Path) -> None:
    catalogs = {
        "gozibra": {
            "default": {4210: ProviderServiceEntry(4210, 2.0, 50, 5000, "default", "gozibra")}
        }
    }
    result = sync_provider_prices_to_db(catalogs, db_path=multi_row_db)
    assert result.rows_scanned == 0
    assert result.updated == 0
    assert result.missing_in_api == 0


def test_price_sync_one_matching_row(multi_row_db: Path) -> None:
    with db.get_connection() as conn:
        _insert(conn, catalog_id="4210", external_service_id="4210", provider_price_usd=1.0)
        conn.commit()

    catalogs = {
        "gozibra": {
            "default": {4210: ProviderServiceEntry(4210, 3.5, 20, 2000, "default", "gozibra")}
        }
    }
    result = sync_provider_prices_to_db(catalogs, db_path=multi_row_db)
    assert result.updated == 1

    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT provider_price_usd, min_qty, max_qty FROM smm_services WHERE catalog_id='4210'"
        ).fetchone()
    assert float(row["provider_price_usd"]) == 3.5
    assert int(row["min_qty"]) == 20
    assert int(row["max_qty"]) == 2000


def test_price_sync_n_matching_rows_updates_each_by_catalog_id(multi_row_db: Path) -> None:
    with db.get_connection() as conn:
        _insert(conn, catalog_id="4210", external_service_id="4210", provider_price_usd=1.0)
        _insert(
            conn,
            catalog_id="gozibra-4210",
            external_service_id="4210",
            provider_price_usd=1.0,
            is_active=0,
            platform_key="",
        )
        conn.commit()

    catalogs = {
        "gozibra": {
            "default": {4210: ProviderServiceEntry(4210, 9.0, 100, 9000, "default", "gozibra")}
        }
    }
    # active_only=False so both rows are scanned; each updated by catalog_id.
    result = sync_provider_prices_to_db(catalogs, active_only=False, db_path=multi_row_db)
    assert result.rows_scanned == 2
    assert result.updated == 2

    with db.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT catalog_id, provider_price_usd, min_qty, max_qty
            FROM smm_services
            WHERE provider_slug='gozibra' AND external_service_id='4210'
            ORDER BY catalog_id
            """
        ).fetchall()
    assert len(rows) == 2
    for row in rows:
        assert float(row["provider_price_usd"]) == 9.0
        assert int(row["min_qty"]) == 100
        assert int(row["max_qty"]) == 9000


def test_limits_zero_one_n_consensus_and_ambiguous(multi_row_db: Path) -> None:
    assert get_provider_limits_from_db("gozibra", 4210) is None

    with db.get_connection() as conn:
        _insert(conn, catalog_id="4210", external_service_id="4210", min_qty=10, max_qty=100)
        conn.commit()
    assert get_provider_limits_from_db("gozibra", 4210) == (10, 100)

    with db.get_connection() as conn:
        _insert(
            conn,
            catalog_id="gozibra-4210",
            external_service_id="4210",
            min_qty=10,
            max_qty=100,
            is_active=0,
        )
        conn.commit()
    # N agreeing rows → same SKU limits, no arbitrary fetchone.
    assert get_provider_limits_from_db("gozibra", 4210) == (10, 100)

    with db.get_connection() as conn:
        conn.execute(
            "UPDATE smm_services SET min_qty=99, max_qty=999 WHERE catalog_id='gozibra-4210'"
        )
        conn.commit()
    # Active consensus still available.
    assert get_provider_limits_from_db("gozibra", 4210) == (10, 100)

    with db.get_connection() as conn:
        _insert(
            conn,
            catalog_id="alt-4210",
            external_service_id="4210",
            min_qty=5,
            max_qty=50,
            is_active=1,
        )
        conn.commit()
    # Active rows disagree → refuse arbitrary pick.
    assert get_provider_limits_from_db("gozibra", 4210) is None


def test_provider_catalog_cache_shared_sku(multi_row_db: Path) -> None:
    clear_limits_cache()
    _LIMITS[("gozibra", 4210)] = (40, 4000)
    # Two logical Catalog consumers share one SKU cache entry.
    assert get_provider_limits(4210, provider_slug="gozibra") == (40, 4000)
    assert get_provider_limits(4210, provider_slug="gozibra") == (40, 4000)
    clear_limits_cache()
