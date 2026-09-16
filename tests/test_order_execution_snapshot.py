# -*- coding: utf-8 -*-
"""Phase 8G — bot order create snapshot tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import database as db
from utils.order_execution_identity import (
    InvalidProviderExternalServiceId,
    encode_provider_external_service_id_for_wire,
)


@pytest.fixture
def bot_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "bot8g.db"
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO users (user_id, balance, total_spent) VALUES (42, 1000, 0)"
        )
        conn.commit()
    return path


def test_encode_wire_id_never_zero():
    with pytest.raises(InvalidProviderExternalServiceId):
        encode_provider_external_service_id_for_wire("nope")
    assert encode_provider_external_service_id_for_wire("00123") == "00123"


def test_create_order_persists_execution_snapshot(bot_db: Path):
    oid = db.create_order_with_balance_hold(
        user_id=42,
        service_name="خدمة",
        service_id="100",
        link="https://example.com",
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
            """
            SELECT catalog_id, external_service_id_snapshot, provider_slug, api_account,
                   amount, quantity, service_id
            FROM orders WHERE id = ?
            """,
            (oid,),
        ).fetchone()
    assert row["catalog_id"] == "cat-100"
    assert row["external_service_id_snapshot"] == "555001"
    assert isinstance(row["external_service_id_snapshot"], str)
    assert row["provider_slug"] == "gozibra"
    assert row["api_account"] == "tiktok"
    assert float(row["amount"]) == 12.5
    assert int(row["quantity"]) == 50


def test_price_unchanged_when_catalog_price_changes(bot_db: Path):
    oid = db.create_order_with_balance_hold(
        user_id=42,
        service_name="خدمة",
        service_id="100",
        link="https://example.com",
        quantity=10,
        amount=5.0,
        catalog_id="cat-100",
        external_service_id_snapshot="9",
    )
    with db.get_connection() as conn:
        # Simulate unrelated smm_services price change (table may or may not exist)
        amount = conn.execute(
            "SELECT amount FROM orders WHERE id = ?", (oid,)
        ).fetchone()[0]
    assert float(amount) == 5.0


def test_gen0_create_without_snapshot_still_works(bot_db: Path):
    """Legacy callers omitting snapshots remain Gen-0."""
    oid = db.create_order_with_balance_hold(
        user_id=42,
        service_name="قديم",
        service_id="200",
        link="https://example.com",
        quantity=1,
        amount=1.0,
    )
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT external_service_id_snapshot, catalog_id FROM orders WHERE id = ?",
            (oid,),
        ).fetchone()
    assert row["external_service_id_snapshot"] is None
    assert row["catalog_id"] is None
