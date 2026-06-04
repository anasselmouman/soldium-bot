# -*- coding: utf-8 -*-
"""اختبارات سجل التدفق المؤقت (بدون Telegram حقيقي)."""

from __future__ import annotations

import asyncio

from utils.flow_transcript import (
    FLOW_STEP_PROMPT_KEY,
    FLOW_TRANSCRIPT_KEY,
    NAV_ANCHOR_MESSAGE_KEY,
    delete_flow_step_prompt,
    discard_ephemeral_flow_messages,
    get_flow_step_prompt_id,
    get_flow_transcript_ids,
    get_nav_anchor_message_id,
    reset_flow_transcript,
    send_flow_step_prompt,
    strip_message_reply_markup,
    track_transcript_message,
    transfer_nav_anchor,
)


class _FakeState:
    def __init__(self) -> None:
        self._data: dict = {}

    async def get_data(self) -> dict:
        return dict(self._data)

    async def update_data(self, **kwargs: object) -> None:
        self._data.update(kwargs)


class _FakeBot:
    def __init__(self) -> None:
        self.deleted: list[tuple[int, int]] = []
        self.sent: list[dict] = []
        self.reply_markup_edits: list[tuple[int, int, object | None]] = []
        self._next_id = 100

    async def delete_message(self, *, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))

    async def send_message(self, **kwargs: object) -> object:
        self._next_id += 1
        mid = self._next_id

        class _Sent:
            message_id = mid

        self.sent.append(kwargs)
        return _Sent()

    async def edit_message_reply_markup(
        self,
        *,
        chat_id: int,
        message_id: int,
        reply_markup: object | None = None,
    ) -> None:
        self.reply_markup_edits.append((chat_id, message_id, reply_markup))


def test_discard_ephemeral_deletes_step_and_transcript() -> None:
    async def _run() -> None:
        state = _FakeState()
        bot = _FakeBot()
        await state.update_data(**{FLOW_STEP_PROMPT_KEY: 42, FLOW_TRANSCRIPT_KEY: [42, 55, 99]})
        await discard_ephemeral_flow_messages(bot, state, user_id=1, chat_id=1)
        assert (1, 42) in bot.deleted
        assert (1, 55) in bot.deleted
        assert (1, 99) in bot.deleted
        assert await get_flow_step_prompt_id(state) is None
        assert await get_flow_transcript_ids(state) == []

    asyncio.run(_run())


def test_reset_clears_transcript() -> None:
    async def _run() -> None:
        state = _FakeState()
        await state.update_data(
            **{
                FLOW_TRANSCRIPT_KEY: [1, 2, 3],
                FLOW_STEP_PROMPT_KEY: 99,
                NAV_ANCHOR_MESSAGE_KEY: 50,
            }
        )
        await reset_flow_transcript(state)
        assert await get_flow_transcript_ids(state) == []
        assert await get_flow_step_prompt_id(state) is None
        assert await get_nav_anchor_message_id(state) is None

    asyncio.run(_run())


def test_track_deduplicates_ids() -> None:
    async def _run() -> None:
        state = _FakeState()
        await track_transcript_message(state, 10)
        await track_transcript_message(state, 10)
        await track_transcript_message(state, 20)
        assert await get_flow_transcript_ids(state) == [10, 20]

    asyncio.run(_run())


def test_get_transcript_empty_when_missing() -> None:
    async def _run() -> None:
        state = _FakeState()
        assert await get_flow_transcript_ids(state) == []

    asyncio.run(_run())


def test_get_transcript_ignores_invalid_entries() -> None:
    async def _run() -> None:
        state = _FakeState()
        await state.update_data(**{FLOW_TRANSCRIPT_KEY: [1, "x", None, 2]})
        assert await get_flow_transcript_ids(state) == [1, 2]

    asyncio.run(_run())


def test_delete_flow_step_prompt_clears_active_id() -> None:
    async def _run() -> None:
        state = _FakeState()
        bot = _FakeBot()
        await state.update_data(**{FLOW_STEP_PROMPT_KEY: 42, FLOW_TRANSCRIPT_KEY: [42, 99]})
        await delete_flow_step_prompt(bot, state, 1)
        assert bot.deleted == [(1, 42)]
        assert await get_flow_step_prompt_id(state) is None
        assert await get_flow_transcript_ids(state) == [99]

    asyncio.run(_run())


def test_delete_flow_step_prompt_clears_nav_anchor_when_same() -> None:
    async def _run() -> None:
        state = _FakeState()
        bot = _FakeBot()
        await state.update_data(
            **{FLOW_STEP_PROMPT_KEY: 42, FLOW_TRANSCRIPT_KEY: [42], NAV_ANCHOR_MESSAGE_KEY: 42}
        )
        await delete_flow_step_prompt(bot, state, 1)
        assert await get_nav_anchor_message_id(state) is None

    asyncio.run(_run())


def test_send_flow_step_prompt_replaces_previous() -> None:
    async def _run() -> None:
        state = _FakeState()
        bot = _FakeBot()
        await state.update_data(**{FLOW_STEP_PROMPT_KEY: 50, FLOW_TRANSCRIPT_KEY: [50]})
        mid = await send_flow_step_prompt(bot, state, 1, "prompt", None)
        assert bot.deleted == [(1, 50)]
        assert mid == 101
        assert await get_flow_step_prompt_id(state) == 101

    asyncio.run(_run())


def test_send_flow_step_prompt_with_reply_markup() -> None:
    async def _run() -> None:
        state = _FakeState()
        bot = _FakeBot()
        nav = object()
        await send_flow_step_prompt(bot, state, 1, "أرسل الرابط", nav)
        assert len(bot.sent) == 1
        assert bot.sent[0].get("reply_markup") is nav

    asyncio.run(_run())


def test_strip_message_reply_markup() -> None:
    async def _run() -> None:
        bot = _FakeBot()
        await strip_message_reply_markup(bot, 1, 10)
        assert bot.reply_markup_edits == [(1, 10, None)]

    asyncio.run(_run())


def test_transfer_nav_anchor_strips_old_and_records_new() -> None:
    async def _run() -> None:
        state = _FakeState()
        bot = _FakeBot()
        await state.update_data(**{NAV_ANCHOR_MESSAGE_KEY: 10})
        nav = object()
        await transfer_nav_anchor(
            bot,
            state,
            999,
            1,
            new_message_id=20,
            new_markup=nav,
            new_is_living=False,
            apply_markup=True,
        )
        assert (1, 10, None) in bot.reply_markup_edits
        assert (1, 20, nav) in bot.reply_markup_edits
        assert await get_nav_anchor_message_id(state) == 20

    asyncio.run(_run())


def test_delete_chat_messages_staggered_newest_first() -> None:
    async def _run() -> None:
        from utils.living_ui import delete_chat_messages_staggered

        bot = _FakeBot()
        await delete_chat_messages_staggered(
            bot, 1, [10, 20, 30], gap_seconds=0, newest_first=True
        )
        assert bot.deleted == [(1, 30), (1, 20), (1, 10)]

    asyncio.run(_run())


def test_transfer_nav_anchor_skip_apply_when_markup_on_send() -> None:
    async def _run() -> None:
        state = _FakeState()
        bot = _FakeBot()
        await state.update_data(**{NAV_ANCHOR_MESSAGE_KEY: 10})
        nav = object()
        await transfer_nav_anchor(
            bot,
            state,
            999,
            1,
            new_message_id=20,
            new_markup=nav,
            new_is_living=False,
            apply_markup=False,
        )
        assert (1, 10, None) in bot.reply_markup_edits
        assert not any(e[1] == 20 for e in bot.reply_markup_edits)
        assert await get_nav_anchor_message_id(state) == 20

    asyncio.run(_run())
