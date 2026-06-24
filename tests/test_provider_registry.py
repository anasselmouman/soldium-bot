# -*- coding: utf-8 -*-
"""اختبارات سجل المزوّدين."""

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
from services.provider_registry import (
    LEGACY_GOZIBRA_SLUG,
    clear_provider_caches,
    get_manager,
    list_active_provider_accounts,
    resolve_service_route,
    seed_default_gozibra_provider,
)


@pytest.fixture(autouse=True)
def _reset_provider_caches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider_db = tmp_path / "provider_registry_isolated.db"
    monkeypatch.setattr("services.provider_registry.DB_PATH", provider_db)
    import sqlite3

    with sqlite3.connect(provider_db) as connection:
        seed_default_gozibra_provider(connection)
        connection.commit()
    monkeypatch.setenv("SMM_KEY_INSTAGRAM", "ig-test-key")
    monkeypatch.setenv("SMM_KEY_FACEBOOK", "fb-test-key")
    monkeypatch.setenv("SMM_KEY_TIKTOK", "tt-test-key")
    monkeypatch.setenv("SMM_KEY_DEFAULT", "default-test-key")
    clear_provider_caches()
    yield
    clear_provider_caches()


@pytest.fixture
def temp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "providers.db"
    db.DB_PATH = path
    monkeypatch.setattr("services.provider_registry.DB_PATH", path)
    db.init_db()
    return path


def test_seed_creates_gozibra_provider(temp_db: Path) -> None:
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT slug, adapter_type, api_base_url FROM providers WHERE slug = ?",
            (LEGACY_GOZIBRA_SLUG,),
        ).fetchone()
    assert row is not None
    assert row["adapter_type"] == "gozibra_v2"
    assert str(row["api_base_url"]).startswith("http")


def test_list_active_provider_accounts(temp_db: Path) -> None:
    pairs = list_active_provider_accounts()
    keys = {account for _, account in pairs}
    assert (LEGACY_GOZIBRA_SLUG, "default") in pairs
    assert keys >= {"default", "instagram", "facebook", "tiktok"}


def test_resolve_service_route_uses_stored_account(temp_db: Path) -> None:
    route = resolve_service_route(
        provider_slug=LEGACY_GOZIBRA_SLUG,
        provider_account="instagram",
        service_category="",
        service_name="خدمة عامة",
    )
    assert route.provider_slug == LEGACY_GOZIBRA_SLUG
    assert route.account_key == "instagram"
    assert route.api_key == "ig-test-key"


def test_get_manager_uses_provider_api_url(temp_db: Path) -> None:
    manager = get_manager(LEGACY_GOZIBRA_SLUG, "default")
    assert manager.api_key == "default-test-key"
    assert manager.api_url.endswith("/api/v2")


def test_resolve_infers_account_from_service_name(temp_db: Path) -> None:
    route = resolve_service_route(
        provider_slug=LEGACY_GOZIBRA_SLUG,
        service_category="",
        service_name="متابعين تيك توك",
    )
    assert route.account_key == "tiktok"
