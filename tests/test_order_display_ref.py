"""اختبارات مرجع الطلب المعروض (رقم الموزّع فقط)."""

from __future__ import annotations

from pathlib import Path

import database as db
from services.order_provider_sync import user_visible_order_ref


def _use_temp_db(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "test_order_display_ref.db"
    db.init_db()


def test_user_visible_order_ref_provider_only(tmp_path: Path) -> None:
    assert user_visible_order_ref({"id": 99, "provider_order_id": "555123"}) == "555123"
    assert user_visible_order_ref({"id": 99, "provider_order_id": None}) == "—"
    assert user_visible_order_ref({"id": 99}) == "—"


def test_assign_provider_order_id_keeps_pending_admin(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    db.add_user(501)
    db.update_balance(501, 100.0)
    oid = db.create_order_with_balance_hold(
        501,
        "IPTV",
        "3986",
        "user@test.com",
        1,
        42.0,
        api_account="default",
        initial_status="pending_admin",
        fulfillment_mode="admin",
    )
    assert oid is not None
    assert db.assign_provider_order_id(oid, "PROV-501")
    row = db.get_user_orders(501, limit=1)[0]
    assert row["provider_order_id"] == "PROV-501"
    assert row["status"] == "pending_admin"


def test_get_order_id_by_provider_ref_not_internal_id(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    db.add_user(502)
    db.update_balance(502, 50.0)
    oid = db.create_order_with_balance_hold(
        502,
        "svc",
        "1",
        "http://example.com",
        10,
        5.0,
    )
    assert oid is not None
    db.assign_provider_order_id(oid, "888777")
    assert db.get_order_id_by_provider_ref("888777") == oid
    assert db.get_order_id_by_provider_ref("#888777") == oid
    assert db.get_order_id_by_provider_ref(str(oid)) is None


def test_search_user_orders_by_provider_ref_only(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    db.add_user(503)
    db.update_balance(503, 50.0)
    oid = db.create_order_with_balance_hold(
        503,
        "svc",
        "1",
        "http://example.com",
        10,
        5.0,
    )
    assert oid is not None
    db.assign_provider_order_id(oid, "SEARCH-503")
    assert len(db.search_user_orders(503, str(oid))) == 0
    hits = db.search_user_orders(503, "SEARCH-503")
    assert len(hits) == 1
    assert hits[0]["provider_order_id"] == "SEARCH-503"
