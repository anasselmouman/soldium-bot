from __future__ import annotations

from pathlib import Path

import database as db
from services_config import SERVICES, reload_services
from utils.fulfillment import FULFILLMENT_ADMIN, service_requires_admin
from utils.order_status_ar import format_order_status_ar


def _use_temp_db(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "test_admin_fulfillment.db"
    db.init_db()
    reload_services()


def test_subscriptions_services_marked_admin(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    with db.get_connection() as connection:
        connection.execute(
            """
            INSERT INTO smm_services (
                catalog_id, external_service_id, service_id, category, name_ar,
                provider_price_usd, platform_key, section_key, local_item_id, fulfillment_mode
            ) VALUES ('3986', '3986', '3986', 'per_unit', 'حساب 1 شهر', 3, 'subscriptions', 'iptv_wc2026', '3986', 'admin')
            """
        )
        connection.commit()
    reload_services()
    located = None
    for section_key, section in SERVICES["subscriptions"]["sections"].items():
        for item in section.get("items", []):
            if str(item.get("id")) == "3986":
                located = item
                break
    assert located is not None
    assert located["fulfillment_mode"] == FULFILLMENT_ADMIN
    assert service_requires_admin(located)


def test_create_pending_admin_order(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    db.add_user(7001, telegram_name="عميل تجريبي")
    db.update_balance(7001, 500.0)
    order_id = db.create_order_with_balance_hold(
        user_id=7001,
        service_name="حساب IPTV",
        service_id="3986",
        link="user@email.com",
        quantity=1,
        amount=42.0,
        api_account="admin",
        initial_status="pending_admin",
        fulfillment_mode=FULFILLMENT_ADMIN,
    )
    assert order_id is not None
    pending = db.get_pending_admin_orders_ordered()
    assert len(pending) == 1
    assert pending[0]["id"] == order_id
    assert pending[0]["status"] == "pending_admin"
    assert db.count_pending_admin_orders() == 1


def test_admin_complete_and_reject_flow(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    db.add_user(7002)
    db.update_balance(7002, 100.0)
    order_id = db.create_order_with_balance_hold(
        user_id=7002,
        service_name="بانل IPTV",
        service_id="3129",
        link="panel-data",
        quantity=1,
        amount=50.0,
        api_account="admin",
        initial_status="pending_admin",
        fulfillment_mode=FULFILLMENT_ADMIN,
    )
    assert order_id is not None
    assert db.set_order_status_by_admin(order_id, "completed")
    assert db.count_pending_admin_orders() == 0

    db.add_user(7003)
    db.update_balance(7003, 80.0)
    order_id_2 = db.create_order_with_balance_hold(
        user_id=7003,
        service_name="بانل IPTV",
        service_id="3130",
        link="panel-data-2",
        quantity=1,
        amount=40.0,
        api_account="admin",
        initial_status="pending_admin",
        fulfillment_mode=FULFILLMENT_ADMIN,
    )
    assert order_id_2 is not None
    user_before = db.get_user(7003)
    balance_before = float((user_before or {})["balance"])
    assert db.set_order_status_by_admin(order_id_2, "canceled")
    user_after = db.get_user(7003)
    assert float((user_after or {})["balance"]) == balance_before + 40.0


def test_pending_admin_status_label_ar() -> None:
    assert "الإدارة" in format_order_status_ar("pending_admin")
