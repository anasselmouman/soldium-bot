# -*- coding: utf-8 -*-
"""اختبارات مساعدات واجهة تدفق الإحالة (بدون Telegram حقيقي)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from utils.flow_transcript import FLOW_STEP_PROMPT_KEY, get_flow_step_prompt_id

from handlers.referrals import (
    _prepare_referral_nav,
    _send_referral_step_prompt,
)
from tests.test_flow_transcript import _FakeBot, _FakeState


def _fake_callback(*, user_id: int = 1, chat_id: int = 10, message_id: int = 50) -> MagicMock:
    callback = MagicMock()
    callback.from_user.id = user_id
    callback.message.chat.id = chat_id
    callback.message.message_id = message_id
    return callback


def test_prepare_referral_nav_purges_transcript() -> None:
    async def _run() -> None:
        state = _FakeState()
        bot = _FakeBot()
        await state.update_data(**{FLOW_STEP_PROMPT_KEY: 99})
        callback = _fake_callback()
        callback.message = MagicMock()
        callback.message.chat.id = 10
        callback.message.message_id = 50

        from utils.flow_transcript import track_transcript_message

        await track_transcript_message(state, 101)
        await track_transcript_message(state, 102)

        user_id = await _prepare_referral_nav(
            callback, state, bot, purge_transcript=True  # type: ignore[arg-type]
        )
        assert user_id == 1
        assert await get_flow_step_prompt_id(state) is None
        assert len(bot.deleted) >= 2

    asyncio.run(_run())


def test_send_referral_step_prompt_replaces_previous() -> None:
    async def _run() -> None:
        state = _FakeState()
        bot = _FakeBot()
        await state.update_data(**{FLOW_STEP_PROMPT_KEY: 200})

        first = await _send_referral_step_prompt(
            bot,  # type: ignore[arg-type]
            state,
            user_id=1,
            chat_id=10,
            text="step one",
            back_callback="referral:menu",
        )
        assert first is not None
        assert (10, 200) in bot.deleted

        second = await _send_referral_step_prompt(
            bot,  # type: ignore[arg-type]
            state,
            user_id=1,
            chat_id=10,
            text="step two",
            back_callback="referral:withdraw",
        )
        assert second is not None
        assert second != first
        assert await get_flow_step_prompt_id(state) == second

    asyncio.run(_run())
