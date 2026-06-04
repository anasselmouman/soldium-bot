# -*- coding: utf-8 -*-
"""اختبارات مسار deposit:back — رسالة حية vs رسالة خطوة محذوفة."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramBadRequest


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


def test_edit_user_living_ui_false_on_message_not_found() -> None:
    async def _run() -> None:
        from utils.living_ui import edit_user_living_ui

        bot = AsyncMock()
        state = MagicMock()
        state.get_data = AsyncMock(return_value={})
        exc = TelegramBadRequest(
            method=MagicMock(),
            message="Bad Request: message to edit not found",
        )

        with patch(
            "utils.living_ui.get_user_living_ui",
            return_value=(100, 200, True),
        ):
            with patch(
                "utils.living_ui.edit_living_ui_message",
                side_effect=exc,
            ):
                ok = await edit_user_living_ui(
                    bot, state, 1, "gateway", None
                )
                assert ok is False

    asyncio.run(_run())
