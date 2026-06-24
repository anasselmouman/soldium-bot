# -*- coding: utf-8 -*-
"""اختبارات مزامنة أسعار المورد."""

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
from services.provider_price_sync import (
    ProviderServiceEntry,
    resolve_api_account,
    sync_provider_prices_to_db,
)


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "provider_price_sync.db"
    db.DB_PATH = path
    monkeypatch.setattr("services.provider_registry.DB_PATH", path)
    monkeypatch.setattr("services.provider_price_sync.DB_PATH", path)
    db.init_db()
    return path


def _insert_service(
    connection,
    *,
    catalog_id: str,
    external_service_id: str,
    platform_key: str = "telegram",
    category: str = "",
    name_ar: str = "مشاهدات",
    provider_price_usd: float = 0.0,
    local_price_dh: float = 10.0,
    fulfillment_mode: str = "auto",
    provider_slug: str = "gozibra",
) -> None:
    connection.execute(
        """
        INSERT INTO smm_services (
            catalog_id, external_service_id, service_id, category, name_ar,
            provider_price_usd, local_price_dh, platform_key, local_item_id,
            is_active, fulfillment_mode, provider_slug
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """,
        (
            catalog_id,
            external_service_id,
            external_service_id,
            category,
            name_ar,
            provider_price_usd,
            local_price_dh,
            platform_key,
            catalog_id,
            fulfillment_mode,
            provider_slug,
        ),
    )


@pytest.mark.parametrize(
    "platform_key,expected",
    [
        ("instagram", "instagram"),
        ("facebook", "facebook"),
        ("tiktok", "tiktok"),
        ("telegram", "default"),
        ("x", "default"),
        ("youtube", "default"),
        ("", "default"),
    ],
)
def test_resolve_api_account_by_platform(platform_key: str, expected: str) -> None:
    assert (
        resolve_api_account(platform_key=platform_key, category="", name_ar="Random")
        == expected
    )


def test_resolve_api_account_by_name_keywords() -> None:
    assert (
        resolve_api_account(platform_key="", category="", name_ar="متابعين انستغرام")
        == "instagram"
    )


def test_sync_updates_provider_price_and_limits(temp_db: Path) -> None:
    with db.get_connection() as connection:
        _insert_service(connection, catalog_id="1001", external_service_id="1001", provider_price_usd=1.0)
        connection.commit()

    catalogs = {
        "gozibra": {
            "default": {
                1001: ProviderServiceEntry(
                    service_id=1001,
                    rate_usd=2.5,
                    min_qty=50,
                    max_qty=5000,
                    api_account="default",
                    provider_slug="gozibra",
                )
            }
        }
    }
    result = sync_provider_prices_to_db(catalogs, db_path=temp_db)
    assert result.updated == 1
    assert result.success

    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT provider_price_usd, min_qty, max_qty, provider_api_account, provider_price_updated_at "
            "FROM smm_services WHERE catalog_id = '1001'"
        ).fetchone()
    assert float(row["provider_price_usd"]) == 2.5
    assert int(row["min_qty"]) == 50
    assert int(row["max_qty"]) == 5000
    assert row["provider_api_account"] == "default"
    assert row["provider_price_updated_at"]


def test_sync_skips_admin_fulfillment(temp_db: Path) -> None:
    with db.get_connection() as connection:
        _insert_service(
            connection,
            catalog_id="2001",
            external_service_id="2001",
            platform_key="subscriptions",
            fulfillment_mode="admin",
            provider_price_usd=3.0,
        )
        connection.commit()

    catalogs = {
        "gozibra": {
            "default": {
                2001: ProviderServiceEntry(
                    service_id=2001,
                    rate_usd=9.0,
                    min_qty=1,
                    max_qty=1,
                    api_account="default",
                    provider_slug="gozibra",
                )
            }
        }
    }
    result = sync_provider_prices_to_db(catalogs, db_path=temp_db)
    assert result.skipped_admin == 1
    assert result.updated == 0

    with db.get_connection() as connection:
        rate = connection.execute(
            "SELECT provider_price_usd FROM smm_services WHERE catalog_id = '2001'"
        ).fetchone()[0]
    assert float(rate) == 3.0


def test_sync_active_only_scope(temp_db: Path) -> None:
    with db.get_connection() as connection:
        _insert_service(connection, catalog_id="3001", external_service_id="3001", provider_price_usd=1.0)
        connection.execute(
            """
            INSERT INTO smm_services (
                catalog_id, external_service_id, service_id, category, name_ar,
                provider_price_usd, local_price_dh, platform_key, local_item_id, is_active
            ) VALUES ('3002', '3002', '3002', '', 'inactive', 1.0, 1.0, '', '3002', 0)
            """
        )
        connection.commit()

    catalogs = {
        "gozibra": {
            "default": {
                3001: ProviderServiceEntry(3001, 4.0, 10, 100, "default", "gozibra"),
                3002: ProviderServiceEntry(3002, 4.0, 10, 100, "default", "gozibra"),
            }
        }
    }
    result = sync_provider_prices_to_db(catalogs, active_only=True, db_path=temp_db)
    assert result.updated == 1

    with db.get_connection() as connection:
        inactive_rate = connection.execute(
            "SELECT provider_price_usd FROM smm_services WHERE catalog_id = '3002'"
        ).fetchone()[0]
    assert float(inactive_rate) == 1.0


def test_same_external_id_different_providers(temp_db: Path) -> None:
    with db.get_connection() as connection:
        _insert_service(
            connection,
            catalog_id="gozibra-100",
            external_service_id="100",
            provider_slug="gozibra",
            provider_price_usd=1.0,
        )
        _insert_service(
            connection,
            catalog_id="panel2-100",
            external_service_id="100",
            provider_slug="panel2",
            provider_price_usd=2.0,
        )
        connection.commit()

    catalogs = {
        "gozibra": {"default": {100: ProviderServiceEntry(100, 5.0, 10, 1000, "default", "gozibra")}},
        "panel2": {"default": {100: ProviderServiceEntry(100, 7.0, 20, 2000, "default", "panel2")}},
    }
    result = sync_provider_prices_to_db(catalogs, db_path=temp_db)
    assert result.updated == 2

    with db.get_connection() as connection:
        goz = connection.execute(
            "SELECT provider_price_usd FROM smm_services WHERE provider_slug='gozibra' AND external_service_id='100'"
        ).fetchone()[0]
        p2 = connection.execute(
            "SELECT provider_price_usd FROM smm_services WHERE provider_slug='panel2' AND external_service_id='100'"
        ).fetchone()[0]
    assert float(goz) == 5.0
    assert float(p2) == 7.0
