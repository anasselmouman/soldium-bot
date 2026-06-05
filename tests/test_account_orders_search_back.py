# -*- coding: utf-8 -*-
"""اختبارات الرجوع من مسار البحث عن طلب — واجهة حية vs رسالة خطوة."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import Chat, Message, User


def test_living_corrupted_when_db_living_equals_step() -> None:
    step_id = 42
    living_id = 42
    corrupted = (
        step_id is not None and living_id is not None and living_id == step_id
    )
    assert corrupted is True


def test_living_not_corrupted_when_ids_differ() -> None:
    step_id = 10
    living_id = 20
    corrupted = (
        step_id is not None and living_id is not None and living_id == step_id
    )
    assert corrupted is False


def test_search_markup_back_goes_to_orders_list() -> None:
    from keyboards.account import build_account_orders_markup

    markup = build_account_orders_markup(include_search=False)
    back_buttons = [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.text == "🔙 رجوع"
    ]
    assert back_buttons == ["account:orders"]


def test_orders_list_markup_back_goes_to_account() -> None:
    from keyboards.account import build_account_orders_markup
    from keyboards.nav_labels import CB_MENU_ACCOUNT

    markup = build_account_orders_markup(include_search=True)
    back_buttons = [
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.text == "🔙 رجوع"
    ]
    assert back_buttons == [CB_MENU_ACCOUNT]


def test_restore_account_orders_purges_transcript_and_edits_living() -> None:
    async def _run() -> None:
        from handlers.start import _restore_account_orders_from_flow_back

        bot = AsyncMock()
        state = MagicMock()
        state.get_state = AsyncMock(return_value="AccountFlow:search_orders")
        state.get_data = AsyncMock(return_value={})
        state.clear = AsyncMock()
        state.update_data = AsyncMock()

        user = User(id=1, is_bot=False, first_name="Test")
        chat = Chat(id=100, type="private")
        msg = Message(message_id=20, date=MagicMock(), chat=chat, from_user=user)
        callback = MagicMock()
        callback.from_user = user
        callback.message = msg

        with patch(
            "handlers.start.get_flow_step_prompt_id",
            new_callable=AsyncMock,
            return_value=10,
        ):
            with patch(
                "handlers.start.get_living_ui",
                new_callable=AsyncMock,
                return_value=(100, 20, False),
            ):
                with patch(
                    "handlers.start.delete_flow_step_prompt",
                    new_callable=AsyncMock,
                ) as mock_delete_step:
                    with patch(
                        "handlers.start.purge_flow_transcript",
                        new_callable=AsyncMock,
                    ) as mock_purge:
                        with patch(
                            "handlers.start.edit_user_living_ui",
                            new_callable=AsyncMock,
                            return_value=True,
                        ) as mock_edit:
                            with patch(
                                "handlers.start._build_my_orders_message_html",
                                new_callable=AsyncMock,
                                return_value="orders",
                            ):
                                with patch("handlers.start.add_user"):
                                    with patch(
                                        "handlers.start.count_user_orders",
                                        return_value=0,
                                    ):
                                        ok = await _restore_account_orders_from_flow_back(
                                            callback, state, bot
                                        )

        assert ok is True
        mock_delete_step.assert_awaited_once()
        mock_purge.assert_awaited_once()
        mock_edit.assert_awaited_once()
        state.clear.assert_awaited_once()

    asyncio.run(_run())
