# -*- coding: utf-8 -*-
"""سجل محادثة مؤقت + رسائل خطوات صغيرة منفصلة عن الواجهة الحية."""

from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from utils.living_ui import (
    delete_chat_message,
    delete_chat_messages_staggered,
    delete_user_message,
)

FLOW_TRANSCRIPT_KEY = "flow_transcript_ids"
FLOW_STEP_PROMPT_KEY = "flow_step_prompt_id"
NAV_ANCHOR_MESSAGE_KEY = "nav_anchor_message_id"
NAV_ANCHOR_IS_LIVING_KEY = "nav_anchor_is_living"
CONFIRM_FOCUS_DELAY_SECONDS = 1.5
STEP_ERROR_FLASH_SECONDS = 2.0


async def reset_flow_transcript(state: FSMContext) -> None:
    await state.update_data(
        **{
            FLOW_TRANSCRIPT_KEY: [],
            FLOW_STEP_PROMPT_KEY: None,
            NAV_ANCHOR_MESSAGE_KEY: None,
            NAV_ANCHOR_IS_LIVING_KEY: None,
        }
    )


async def discard_ephemeral_flow_messages(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
    *,
    gap_seconds: float | None = None,
) -> None:
    """حذف رسالة الخطوة النشطة وجميع رسائل السجل المؤقت ثم مسح مفاتيح التتبع."""
    await delete_flow_step_prompt(bot, state, chat_id)
    from utils.living_ui import get_living_ui

    _, living_id, _ = await get_living_ui(state, user_id)
    exclude: set[int] = set()
    if isinstance(living_id, int):
        exclude.add(living_id)
    ids = await get_flow_transcript_ids(state)
    to_delete = [mid for mid in ids if mid not in exclude]
    if to_delete:
        from utils.living_ui import DELETE_MESSAGE_STAGGER_SECONDS

        gap = (
            DELETE_MESSAGE_STAGGER_SECONDS
            if gap_seconds is None
            else gap_seconds
        )
        await delete_chat_messages_staggered(
            bot, chat_id, to_delete, newest_first=True, gap_seconds=gap
        )
    await reset_flow_transcript(state)


async def get_nav_anchor_message_id(state: FSMContext) -> int | None:
    data = await state.get_data()
    mid = data.get(NAV_ANCHOR_MESSAGE_KEY)
    return int(mid) if isinstance(mid, int) else None


async def strip_message_reply_markup(
    bot: Bot,
    chat_id: int,
    message_id: int,
) -> None:
    """إزالة لوحة المفاتيح من رسالة دون تغيير النص."""
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=None,
        )
    except TelegramBadRequest:
        pass


async def transfer_nav_anchor(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
    *,
    new_message_id: int | None,
    new_markup: object | None = None,
    new_is_living: bool = False,
    apply_markup: bool = True,
) -> None:
    """نقل أزرار التنقل: تفريغ الحامل السابق وتعيين الحامل الجديد."""
    old_id = await get_nav_anchor_message_id(state)
    if isinstance(old_id, int) and old_id != new_message_id:
        await strip_message_reply_markup(bot, chat_id, old_id)

    if (
        apply_markup
        and isinstance(new_message_id, int)
        and new_markup is not None
    ):
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id,
                message_id=new_message_id,
                reply_markup=new_markup,
            )
        except TelegramBadRequest:
            pass

    await state.update_data(
        **{
            NAV_ANCHOR_MESSAGE_KEY: new_message_id,
            NAV_ANCHOR_IS_LIVING_KEY: new_is_living if new_message_id is not None else None,
        }
    )


async def get_flow_transcript_ids(state: FSMContext) -> list[int]:
    data = await state.get_data()
    raw = data.get(FLOW_TRANSCRIPT_KEY)
    if not isinstance(raw, list):
        return []
    return [int(mid) for mid in raw if isinstance(mid, int)]


async def get_flow_step_prompt_id(state: FSMContext) -> int | None:
    data = await state.get_data()
    mid = data.get(FLOW_STEP_PROMPT_KEY)
    return int(mid) if isinstance(mid, int) else None


async def track_transcript_message(state: FSMContext, message_id: int) -> None:
    if not isinstance(message_id, int):
        return
    ids = await get_flow_transcript_ids(state)
    if message_id in ids:
        return
    ids.append(message_id)
    await state.update_data(**{FLOW_TRANSCRIPT_KEY: ids})


async def track_transcript_user_message(state: FSMContext, message: Message) -> None:
    if message.message_id is not None:
        await track_transcript_message(state, message.message_id)


async def delete_flow_step_prompt(
    bot: Bot,
    state: FSMContext,
    chat_id: int,
) -> None:
    """حذف رسالة خطوة الإدخال النشطة فقط (تبقى الرسالة الحية)."""
    mid = await get_flow_step_prompt_id(state)
    if mid is None:
        return
    anchor_id = await get_nav_anchor_message_id(state)
    await delete_chat_message(bot, chat_id, mid)
    ids = await get_flow_transcript_ids(state)
    if mid in ids:
        ids = [x for x in ids if x != mid]
    anchor_clear: dict = {FLOW_TRANSCRIPT_KEY: ids, FLOW_STEP_PROMPT_KEY: None}
    if anchor_id == mid:
        anchor_clear[NAV_ANCHOR_MESSAGE_KEY] = None
        anchor_clear[NAV_ANCHOR_IS_LIVING_KEY] = None
    await state.update_data(**anchor_clear)


async def send_flow_step_prompt(
    bot: Bot,
    state: FSMContext,
    chat_id: int,
    text: str,
    reply_markup: object | None = None,
    *,
    parse_mode: str | None = "HTML",
) -> int | None:
    """رسالة خطوة صغيرة أسفل الرسالة الحية — تُسجَّل وتصبح البرومبت النشط."""
    await delete_flow_step_prompt(bot, state, chat_id)
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest:
        return None
    if sent.message_id is None:
        return None
    await track_transcript_message(state, sent.message_id)
    await state.update_data(**{FLOW_STEP_PROMPT_KEY: sent.message_id})
    return sent.message_id


async def edit_flow_step_prompt(
    bot: Bot,
    state: FSMContext,
    chat_id: int,
    text: str,
    reply_markup: object | None = None,
    *,
    parse_mode: str | None = "HTML",
) -> bool:
    mid = await get_flow_step_prompt_id(state)
    if mid is None:
        return False
    try:
        await bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=mid,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest:
        return False
    return True


async def send_flow_step_ack(
    bot: Bot,
    state: FSMContext,
    chat_id: int,
    text: str,
    *,
    reply_to_message_id: int | None = None,
    parse_mode: str | None = "HTML",
) -> int | None:
    """رسالة تأكيد قصيرة تُسجَّل في السجل وتُحذف مع بقية التدفق لاحقاً."""
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest:
        return None
    if sent.message_id is not None:
        await track_transcript_message(state, sent.message_id)
        return sent.message_id
    return None


async def purge_flow_transcript(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
    *,
    gap_seconds: float | None = None,
) -> None:
    """حذف كل رسائل السجل المؤقت ما عدا رسالة الواجهة الحية."""
    await discard_ephemeral_flow_messages(
        bot, state, user_id, chat_id, gap_seconds=gap_seconds
    )


async def set_living_nav_anchor(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
    reply_markup: object | None,
    *,
    apply_markup: bool = True,
) -> None:
    """تعيين الرسالة الحية كحامل أزرار التنقل (كتالوج / فاتورة / رئيسية)."""
    from utils.living_ui import get_living_ui

    living_chat, living_id, _ = await get_living_ui(state, user_id)
    if not living_id or living_chat != chat_id:
        return
    await transfer_nav_anchor(
        bot,
        state,
        user_id,
        chat_id,
        new_message_id=living_id,
        new_markup=reply_markup,
        new_is_living=True,
        apply_markup=apply_markup,
    )


async def flash_step_prompt_error(
    bot: Bot,
    state: FSMContext,
    chat_id: int,
    *,
    error_text: str,
    restore_text: str,
    restore_markup: object | None = None,
    user_message: Message | None = None,
    delay_seconds: float = STEP_ERROR_FLASH_SECONDS,
) -> bool:
    """خطأ على رسالة الخطوة الصغيرة فقط — الرسالة الحية لا تتغير."""
    mid = await get_flow_step_prompt_id(state)
    if mid is None:
        if user_message is not None:
            await delete_user_message(bot, user_message.chat.id, user_message.message_id)
        return False
    flash_text = error_text if "⚠️" in error_text else f"⚠️ {error_text}"
    try:
        await bot.edit_message_text(
            text=flash_text,
            chat_id=chat_id,
            message_id=mid,
            reply_markup=restore_markup,
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        return False
    await asyncio.sleep(delay_seconds)
    if user_message is not None:
        await delete_user_message(bot, user_message.chat.id, user_message.message_id)
    try:
        await bot.edit_message_text(
            text=restore_text,
            chat_id=chat_id,
            message_id=mid,
            reply_markup=restore_markup,
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        return False
    return True


async def acknowledge_then_focus_living_ui(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
    *,
    ack_text: str,
    reply_to_message_id: int | None = None,
    delay_seconds: float = CONFIRM_FOCUS_DELAY_SECONDS,
) -> None:
    """رسالة تأكيد قصيرة، انتظار للقراءة، ثم حذف السجل — تبقى الرسالة الحية فقط."""
    await send_flow_step_ack(
        bot,
        state,
        chat_id,
        ack_text,
        reply_to_message_id=reply_to_message_id,
    )
    await asyncio.sleep(delay_seconds)
    await purge_flow_transcript(bot, state, user_id, chat_id)
