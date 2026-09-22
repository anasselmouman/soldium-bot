# -*- coding: utf-8 -*-
"""Handler/integration tests for pending order funding (review findings 2/4/5)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import database as db
from services.order_funding import (
    PENDING_FUNDING_INTENT_ID_KEY,
    resume_pending_order_after_deposit,
)
from utils.money import to_decimal


def _use_temp_db(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "test_pending_funding_handlers.db"
    db.init_db()


def _grant(uid: int, amount: float, method: str = "CashPlus") -> None:
    dep_id = db.create_deposit(uid, 0.0, method, f"proof_h_{uid}_{amount}_{method}")
    assert dep_id is not None
    assert db.finalize_approved_deposit(dep_id, uid, amount, method) is True


def _upsert(
    uid: int,
    *,
    amount: float = 25.0,
    service_id: str = "svc1",
    link: str = "https://instagram.com/p/abc",
    quantity: int = 1000,
) -> int:
    return db.upsert_pending_order_funding(
        uid,
        service_id=service_id,
        service_name="Test Service",
        platform_key="instagram",
        section_key="likes",
        subsection_key=None,
        link=link,
        quantity=quantity,
        amount_dh=amount,
        auto_quantity=False,
    )


def _mock_state(data: dict | None = None) -> MagicMock:
    state = MagicMock()
    store = dict(data or {})

    async def _get_data():
        return dict(store)

    async def _update_data(**kwargs):
        store.update(kwargs)

    async def _set_state(value):
        store["__state__"] = value

    async def _get_state():
        return store.get("__state__")

    async def _clear():
        store.clear()

    state.get_data = AsyncMock(side_effect=_get_data)
    state.update_data = AsyncMock(side_effect=_update_data)
    state.set_state = AsyncMock(side_effect=_set_state)
    state.get_state = AsyncMock(side_effect=_get_state)
    state.clear = AsyncMock(side_effect=_clear)
    state._store = store
    return state


def test_gate_sufficient_clears_orphan_pending_and_shows_confirm(tmp_path: Path) -> None:
    """Finding 2 + 4A sufficient."""
    from handlers.orders import _gate_balance_then_confirm_or_fund

    _use_temp_db(tmp_path)
    uid = 301
    db.add_user(uid)
    _grant(uid, 50.0)
    orphan = _upsert(uid, amount=25.0)

    bot = AsyncMock()
    state = _mock_state({PENDING_FUNDING_INTENT_ID_KEY: orphan})
    service = {"id": "svc1", "name": "Test Service", "price": 25.0}

    with (
        patch("handlers.orders._edit_order_living_ui", new_callable=AsyncMock) as edit_ui,
        patch("handlers.orders._sync_living_nav_anchor", new_callable=AsyncMock),
        patch("handlers.orders.delete_flow_step_prompt", new_callable=AsyncMock),
        patch(
            "handlers.orders._order_breadcrumb_from_state",
            new_callable=AsyncMock,
            return_value="bc",
        ),
        patch("handlers.orders.build_invoice_text", return_value="invoice"),
        patch("handlers.orders.build_order_confirm_keyboard", return_value="kb"),
    ):
        asyncio.run(
            _gate_balance_then_confirm_or_fund(
                bot,
                state,
                uid,
                chat_id=uid,
                service=service,
                platform_key="instagram",
                section_key="likes",
                subsection_key=None,
                link="https://instagram.com/p/abc",
                quantity=1000,
                total_price=to_decimal("25"),
                auto_quantity=False,
            )
        )

    assert db.get_pending_order_funding(uid) is None
    assert db.get_pending_order_funding_by_intent(orphan, uid) is None
    assert state._store.get(PENDING_FUNDING_INTENT_ID_KEY) is None
    edit_ui.assert_awaited()


def test_gate_insufficient_creates_intent_not_confirm(tmp_path: Path) -> None:
    """Finding 4A insufficient."""
    from handlers.orders import _gate_balance_then_confirm_or_fund

    _use_temp_db(tmp_path)
    uid = 302
    db.add_user(uid)
    _grant(uid, 10.0)

    bot = AsyncMock()
    state = _mock_state()
    service = {"id": "svc1", "name": "Test Service", "price": 25.0}

    with (
        patch("handlers.orders._show_order_funding_screen", new_callable=AsyncMock) as fund_ui,
        patch("handlers.orders._edit_order_living_ui", new_callable=AsyncMock) as confirm_ui,
        patch("handlers.orders.delete_flow_step_prompt", new_callable=AsyncMock),
    ):
        asyncio.run(
            _gate_balance_then_confirm_or_fund(
                bot,
                state,
                uid,
                chat_id=uid,
                service=service,
                platform_key="instagram",
                section_key="likes",
                subsection_key=None,
                link="https://instagram.com/p/abc",
                quantity=1000,
                total_price=to_decimal("25"),
                auto_quantity=False,
            )
        )

    pending = db.get_pending_order_funding(uid)
    assert pending is not None
    assert float(pending["amount_dh"]) == 25.0
    fund_ui.assert_awaited()
    confirm_ui.assert_not_awaited()


def test_resume_sufficient_deposit_restores_confirm_no_order(tmp_path: Path) -> None:
    """Finding 4B."""
    _use_temp_db(tmp_path)
    uid = 303
    db.add_user(uid)
    _grant(uid, 10.0)
    intent_id = _upsert(uid, amount=25.0)
    _grant(uid, 100.0, method="CIH")
    assert float(db.get_user(uid)["balance"]) == 110.0

    bot = AsyncMock()
    bot.id = 1
    storage = MagicMock()

    with (
        patch(
            "handlers.orders.restore_confirm_from_funding_intent",
            new_callable=AsyncMock,
            return_value=True,
        ) as restore,
        patch(
            "services.order_funding.find_service_location",
            return_value=(
                {
                    "id": "svc1",
                    "name": "Test Service",
                    "external_service_id_text": "ext-1",
                },
                "instagram",
                "likes",
                None,
            ),
        ),
        patch(
            "services.order_funding.order_total_price_dh",
            return_value=to_decimal("25"),
        ),
        patch(
            "storefront.get_storefront",
            return_value=MagicMock(
                backend_name="legacy",
                uses_catalog_order_contract=lambda *_: False,
            ),
        ),
    ):
        asyncio.run(resume_pending_order_after_deposit(bot, uid, storage=storage))

    restore.assert_awaited()
    assert restore.await_args.args[2] == intent_id
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is not None
    with db.get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?", (uid,)
        ).fetchone()["c"]
    assert int(n) == 0


def test_resume_insufficient_deposit_keeps_intent_and_remaining(tmp_path: Path) -> None:
    """Finding 4C."""
    _use_temp_db(tmp_path)
    uid = 304
    db.add_user(uid)
    _grant(uid, 10.0)
    intent_id = _upsert(uid, amount=25.0)
    _grant(uid, 5.0, method="Wafacash")
    assert float(db.get_user(uid)["balance"]) == 15.0

    bot = AsyncMock()
    bot.send_message = AsyncMock()

    with (
        patch(
            "handlers.orders.restore_confirm_from_funding_intent",
            new_callable=AsyncMock,
        ) as restore,
        patch(
            "services.order_funding.find_service_location",
            return_value=(
                {
                    "id": "svc1",
                    "name": "Test Service",
                    "external_service_id_text": "ext-1",
                },
                "instagram",
                "likes",
                None,
            ),
        ),
        patch(
            "services.order_funding.order_total_price_dh",
            return_value=to_decimal("25"),
        ),
        patch(
            "storefront.get_storefront",
            return_value=MagicMock(
                backend_name="legacy",
                uses_catalog_order_contract=lambda *_: False,
            ),
        ),
        patch(
            "utils.living_ui.get_user_living_ui",
            return_value=(None, None, False),
        ),
        patch(
            "services.order_funding.send_smart_notification",
            new_callable=AsyncMock,
        ),
    ):
        asyncio.run(resume_pending_order_after_deposit(bot, uid))

    restore.assert_not_awaited()
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is not None
    bot.send_message.assert_awaited()
    markup = bot.send_message.await_args.kwargs.get("reply_markup")
    assert markup is not None
    raw = str(markup)
    assert "order_fund:open" in raw
    assert "order_fund:cancel" in raw


def test_cancel_handler_deletes_intent_keeps_balance(tmp_path: Path) -> None:
    """Finding 4D."""
    from handlers.orders import order_fund_cancel_handler

    _use_temp_db(tmp_path)
    uid = 305
    db.add_user(uid)
    _grant(uid, 15.0)
    intent_id = _upsert(uid, amount=25.0)

    bot = AsyncMock()
    state = _mock_state({PENDING_FUNDING_INTENT_ID_KEY: intent_id})
    callback = MagicMock()
    callback.from_user = MagicMock(id=uid)
    callback.message = MagicMock(chat=MagicMock(id=uid))
    callback.answer = AsyncMock()

    with (
        patch("handlers.orders._finish_order_flow", new_callable=AsyncMock),
        patch("handlers.orders._edit_order_living_ui", new_callable=AsyncMock),
        patch("handlers.orders._home", return_value="home"),
    ):
        asyncio.run(order_fund_cancel_handler(callback, state, bot))

    assert db.get_pending_order_funding_by_intent(intent_id, uid) is None
    assert float(db.get_user(uid)["balance"]) == 15.0
    with db.get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?", (uid,)
        ).fetchone()["c"]
    assert int(n) == 0


def test_back_from_funding_real_nav_deletes_pending(tmp_path: Path) -> None:
    """Finding 4E — real back navigation deletes pending intent."""
    from handlers.orders import _handle_back_navigation
    from utils.states import OrderFlow

    _use_temp_db(tmp_path)
    uid = 307
    db.add_user(uid)
    intent_id = _upsert(uid, amount=25.0)

    bot = AsyncMock()
    state = _mock_state(
        {
            PENDING_FUNDING_INTENT_ID_KEY: intent_id,
            "service_id": "svc1",
            "link": "https://instagram.com/p/abc",
            "platform_key": "instagram",
            "section_key": "likes",
        }
    )
    state.get_state = AsyncMock(return_value=OrderFlow.confirm_order.state)
    callback = MagicMock()
    callback.from_user = MagicMock(id=uid)
    callback.message = MagicMock(chat=MagicMock(id=uid), message_id=1)

    with (
        patch(
            "handlers.orders._sync_service_context_in_state",
            new_callable=AsyncMock,
            return_value=(
                {"id": "svc1", "name": "Test", "auto_quantity": 1000},
                "instagram",
                "likes",
                None,
            ),
        ),
        patch(
            "handlers.orders._edit_message_to_link_entry_prompt",
            new_callable=AsyncMock,
        ),
        patch("handlers.orders.delete_flow_step_prompt", new_callable=AsyncMock),
    ):
        asyncio.run(_handle_back_navigation(callback, state, bot))

    assert db.get_pending_order_funding_by_intent(intent_id, uid) is None


def test_order_fund_back_does_not_open_menu_deposit(tmp_path: Path) -> None:
    """Finding 4E — order_fund:back stays in order navigation."""
    from handlers.orders import order_fund_back_handler
    from utils.states import OrderFlow

    _use_temp_db(tmp_path)
    uid = 306
    db.add_user(uid)
    intent_id = _upsert(uid, amount=25.0)

    bot = AsyncMock()
    state = _mock_state({PENDING_FUNDING_INTENT_ID_KEY: intent_id})
    state.get_state = AsyncMock(return_value=OrderFlow.confirm_order.state)
    callback = MagicMock()
    callback.from_user = MagicMock(id=uid)
    callback.message = MagicMock(chat=MagicMock(id=uid), message_id=1)
    callback.answer = AsyncMock()
    callback.data = "order_fund:back"

    with (
        patch("handlers.orders.clear_last_prompt", new_callable=AsyncMock),
        patch(
            "handlers.orders._handle_back_navigation",
            new_callable=AsyncMock,
        ) as back_nav,
        patch(
            "handlers.payment._render_deposit_gateway",
            new_callable=AsyncMock,
        ) as deposit_gw,
    ):
        asyncio.run(order_fund_back_handler(callback, state, bot))

    back_nav.assert_awaited()
    deposit_gw.assert_not_awaited()


def test_new_order_deletes_old_pending(tmp_path: Path) -> None:
    """Finding 4F."""
    from handlers.orders import order_start_callback

    _use_temp_db(tmp_path)
    uid = 308
    db.add_user(uid)
    old_id = _upsert(uid, amount=25.0)

    bot = AsyncMock()
    state = _mock_state()
    callback = MagicMock()
    callback.from_user = MagicMock(id=uid)
    callback.message = MagicMock(chat=MagicMock(id=uid))
    callback.answer = AsyncMock()
    callback.data = "menu:order"

    with (
        patch("handlers.orders._clear_awaiting_prompt", new_callable=AsyncMock),
        patch("handlers.orders.reset_flow_transcript", new_callable=AsyncMock),
        patch("handlers.orders.register_living_ui_message", new_callable=AsyncMock),
        patch("handlers.orders._show_platforms", new_callable=AsyncMock) as platforms,
        patch(
            "handlers.orders._try_recover_funded_pending_on_order_start",
            new_callable=AsyncMock,
            return_value=False,
        ),
    ):
        asyncio.run(order_start_callback(callback, state, bot))

    platforms.assert_awaited()
    assert db.get_pending_order_funding_by_intent(old_id, uid) is None
    assert db.get_pending_order_funding(uid) is None


def test_resume_price_change_deletes_intent_no_confirm(tmp_path: Path) -> None:
    """Finding 4G."""
    _use_temp_db(tmp_path)
    uid = 309
    db.add_user(uid)
    _grant(uid, 50.0)
    intent_id = _upsert(uid, amount=25.0)

    bot = AsyncMock()

    with (
        patch(
            "handlers.orders.restore_confirm_from_funding_intent",
            new_callable=AsyncMock,
        ) as restore,
        patch(
            "services.order_funding.find_service_location",
            return_value=(
                {
                    "id": "svc1",
                    "name": "Test Service",
                    "external_service_id_text": "ext-1",
                },
                "instagram",
                "likes",
                None,
            ),
        ),
        patch(
            "services.order_funding.order_total_price_dh",
            return_value=to_decimal("30"),
        ),
        patch(
            "storefront.get_storefront",
            return_value=MagicMock(
                backend_name="legacy",
                uses_catalog_order_contract=lambda *_: False,
            ),
        ),
        patch(
            "services.order_funding.send_smart_notification",
            new_callable=AsyncMock,
        ) as notify,
    ):
        asyncio.run(resume_pending_order_after_deposit(bot, uid))

    restore.assert_not_awaited()
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is None
    notify.assert_awaited()
    with db.get_connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM orders WHERE user_id = ?", (uid,)
        ).fetchone()["c"]
    assert int(n) == 0


def test_stale_resume_after_replace_does_not_restore_a(tmp_path: Path) -> None:
    """Finding 4H."""
    from handlers.orders import restore_confirm_from_funding_intent

    _use_temp_db(tmp_path)
    uid = 310
    db.add_user(uid)
    _grant(uid, 50.0)
    intent_a = _upsert(uid, amount=25.0, service_id="svcA")
    intent_b = _upsert(uid, amount=25.0, service_id="svcB")
    assert intent_a != intent_b
    assert db.get_pending_order_funding_by_intent(intent_a, uid) is None

    bot = AsyncMock()
    with (
        patch("utils.smart_notifications.send_smart_notification", new_callable=AsyncMock),
        patch("utils.living_ui.get_user_living_ui", return_value=(None, None, False)),
    ):
        ok = asyncio.run(restore_confirm_from_funding_intent(bot, uid, intent_a))
    assert ok is False
    assert db.get_pending_order_funding_by_intent(intent_b, uid) is not None


def test_restore_uses_catalog_contract_branch(tmp_path: Path) -> None:
    """Finding 5 — Catalog validation path used when storefront is catalog."""
    from handlers.orders import restore_confirm_from_funding_intent

    _use_temp_db(tmp_path)
    uid = 311
    db.add_user(uid)
    _grant(uid, 50.0)
    intent_id = _upsert(uid, amount=25.0)

    bot = AsyncMock()
    bot.send_message = AsyncMock()
    bridge = MagicMock()
    bridge.amount_dh = 25.0
    bridge.external_service_id_snapshot = "ext-catalog-1"

    storefront = MagicMock()
    storefront.backend_name = "catalog"
    storefront.resolve_order_intent = MagicMock(return_value=MagicMock())
    storefront._connection = None

    with (
        patch(
            "handlers.orders.find_service_location",
            return_value=(
                {"id": "svc1", "name": "Test Service"},
                "instagram",
                "likes",
                None,
            ),
        ),
        patch("handlers.orders._validate_order_link", return_value=(True, "")),
        patch(
            "handlers.orders._effective_limits",
            new_callable=AsyncMock,
            return_value=(1, 10000),
        ),
        patch("handlers.orders.get_storefront", return_value=storefront),
        patch(
            "storefront.order_intent_to_create_bridge",
            return_value=bridge,
        ),
        patch("handlers.orders.get_provider_credentials_for_service") as legacy_creds,
        patch("utils.living_ui.get_user_living_ui", return_value=(None, None, False)),
        patch("utils.smart_notifications.send_smart_notification", new_callable=AsyncMock),
        patch("handlers.orders.build_invoice_text", return_value="invoice"),
        patch("handlers.orders.build_order_confirm_keyboard", return_value="kb"),
        patch("handlers.orders._order_flow_header", return_value="hdr"),
    ):
        ok = asyncio.run(restore_confirm_from_funding_intent(bot, uid, intent_id))

    assert ok is True
    storefront.resolve_order_intent.assert_called()
    legacy_creds.assert_not_called()
    assert db.get_pending_order_funding_by_intent(intent_id, uid) is not None
