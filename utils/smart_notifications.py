# -*- coding: utf-8 -*-
"""إشعارات ذاتية التدمير — حذف بعد 12 ثانية من أول نشاط للمستخدم."""

from __future__ import annotations

import asyncio
import logging
from typing import Final

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import add_pending_notification, pop_pending_notifications, remove_pending_notification

logger = logging.getLogger(__name__)

AUTO_DESTRUCT_SECONDS: Final[float] = 12.0
DISMISS_CALLBACK_PREFIX: Final[str] = "notify:dismiss:"

router = Router(name="smart_notifications")

# (user_id, message_id) — مؤقتات قيد التشغيل
_scheduled_deletes: set[tuple[int, int]] = set()


def dismiss_callback_data(chat_id: int, message_id: int) -> str:
    return f"{DISMISS_CALLBACK_PREFIX}{chat_id}:{message_id}"


def parse_dismiss_callback(data: str | None) -> tuple[int, int] | None:
    if not data or not data.startswith(DISMISS_CALLBACK_PREFIX):
        return None
    payload = data[len(DISMISS_CALLBACK_PREFIX) :]
    if ":" not in payload:
        return None
    chat_part, msg_part = payload.split(":", maxsplit=1)
    try:
        return int(chat_part), int(msg_part)
    except ValueError:
        return None


def build_notification_dismiss_markup(chat_id: int, message_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✖️ إخفاء الإشعار", callback_data=dismiss_callback_data(chat_id, message_id))
    builder.adjust(1)
    return builder.as_markup()


async def send_smart_notification(
    bot: Bot,
    user_id: int,
    text: str,
    *,
    parse_mode: str = "HTML",
) -> Message | None:
    """إرسال إشعار للمستخدم مع زر الإخفاء وتسجيله كمعلق حتى ينشط المستخدم."""
    chat_id = user_id
    try:
        sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode)
    except TelegramBadRequest as exc:
        logger.warning("send_smart_notification failed for %s: %s", user_id, exc)
        return None
    try:
        await bot.edit_message_reply_markup(
            chat_id=chat_id,
            message_id=sent.message_id,
            reply_markup=build_notification_dismiss_markup(chat_id, sent.message_id),
        )
    except TelegramBadRequest:
        pass
    add_pending_notification(user_id, chat_id, sent.message_id)
    return sent


async def dismiss_notification_immediately(
    bot: Bot,
    user_id: int,
    chat_id: int,
    message_id: int,
) -> None:
    remove_pending_notification(user_id, message_id)
    _scheduled_deletes.discard((user_id, message_id))
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest:
        pass


async def _delete_notification_after_delay(
    bot: Bot,
    user_id: int,
    chat_id: int,
    message_id: int,
) -> None:
    try:
        await asyncio.sleep(AUTO_DESTRUCT_SECONDS)
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except TelegramBadRequest:
        pass
    finally:
        _scheduled_deletes.discard((user_id, message_id))
        remove_pending_notification(user_id, message_id)


def schedule_pending_deletions(bot: Bot, user_id: int) -> None:
    """عند أول نشاط للمستخدم: تشغيل مؤقت 12 ثانية لكل إشعار معلق."""
    pending = pop_pending_notifications(user_id)
    for chat_id, message_id in pending:
        key = (user_id, message_id)
        if key in _scheduled_deletes:
            continue
        _scheduled_deletes.add(key)
        asyncio.create_task(_delete_notification_after_delay(bot, user_id, chat_id, message_id))


async def on_user_activity(bot: Bot, user_id: int) -> None:
    schedule_pending_deletions(bot, user_id)


@router.callback_query(F.data.startswith(DISMISS_CALLBACK_PREFIX))
async def dismiss_notification_callback(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return
    parsed = parse_dismiss_callback(callback.data)
    if parsed is None:
        await callback.answer()
        return
    chat_id, message_id = parsed
    if callback.message.chat.id != chat_id or callback.from_user.id != chat_id:
        await callback.answer()
        return
    await dismiss_notification_immediately(bot, callback.from_user.id, chat_id, message_id)
    await callback.answer()
