# -*- coding: utf-8 -*-
"""Timed announcements — broadcast on launch and on every /start while active."""

from __future__ import annotations

import logging
from typing import Final

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database import get_pending_timed_announcements_for_user

logger = logging.getLogger(__name__)

ANNOUNCE_DISMISS_CALLBACK_PREFIX: Final[str] = "announce:dismiss:"
ANNOUNCE_DISMISS_BUTTON_TEXT: Final[str] = "✖️ إخفاء الإعلان"

router = Router(name="timed_announcements")


def announce_dismiss_callback_data(announcement_id: int) -> str:
    return f"{ANNOUNCE_DISMISS_CALLBACK_PREFIX}{announcement_id}"


def parse_announce_dismiss_callback(data: str | None) -> int | None:
    if not data or not data.startswith(ANNOUNCE_DISMISS_CALLBACK_PREFIX):
        return None
    payload = data[len(ANNOUNCE_DISMISS_CALLBACK_PREFIX) :]
    try:
        return int(payload)
    except ValueError:
        return None


def build_announce_dismiss_markup(announcement_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(
        text=ANNOUNCE_DISMISS_BUTTON_TEXT,
        callback_data=announce_dismiss_callback_data(announcement_id),
    )
    builder.adjust(1)
    return builder.as_markup()


def format_timed_announcement_text(message_html: str) -> str:
    body = (message_html or "").strip()
    return f"<b>📢 SOLDIUM | إعلان مؤقت</b>\n\n{body}"


async def send_timed_announcement_to_user(
    bot: Bot,
    user_id: int,
    *,
    announcement_id: int,
    message_html: str,
    auto_delete_seconds: int | None = None,
) -> bool:
    text = format_timed_announcement_text(message_html)
    markup = build_announce_dismiss_markup(announcement_id)
    try:
        sent = await bot.send_message(
            chat_id=user_id,
            text=text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
        if auto_delete_seconds is not None:
            from utils.message_deletions import schedule_message_deletion

            schedule_message_deletion(user_id, sent.message_id, auto_delete_seconds)
        return True
    except TelegramBadRequest as exc:
        logger.warning(
            "timed announcement id=%s user_id=%s HTML send failed: %s — retry plain",
            announcement_id,
            user_id,
            exc,
        )
        plain = text.replace("<b>", "").replace("</b>", "")
        try:
            sent = await bot.send_message(
                chat_id=user_id,
                text=plain,
                reply_markup=markup,
                disable_web_page_preview=True,
            )
            if auto_delete_seconds is not None:
                from utils.message_deletions import schedule_message_deletion

                schedule_message_deletion(user_id, sent.message_id, auto_delete_seconds)
            return True
        except TelegramBadRequest as exc2:
            logger.warning(
                "timed announcement id=%s user_id=%s plain send failed: %s",
                announcement_id,
                user_id,
                exc2,
            )
            return False


async def deliver_timed_announcements_on_entry(bot: Bot, user_id: int) -> int:
    """
    Deliver all active announcements — on every /start or chat rejoin.
    """
    pending = get_pending_timed_announcements_for_user(user_id)
    if not pending:
        logger.debug("timed announcements: none pending for user_id=%s", user_id)
        return 0

    logger.info(
        "timed announcements: delivering %s item(s) to user_id=%s",
        len(pending),
        user_id,
    )

    sent = 0
    for item in pending:
        ok = await send_timed_announcement_to_user(
            bot,
            user_id,
            announcement_id=int(item["id"]),
            message_html=str(item["message_html"]),
            auto_delete_seconds=item.get("auto_delete_seconds"),
        )
        if ok:
            sent += 1
    return sent


async def deliver_pending_timed_announcements(bot: Bot, user_id: int) -> None:
    """Backward-compatible alias."""
    await deliver_timed_announcements_on_entry(bot, user_id)


@router.callback_query(F.data.startswith(ANNOUNCE_DISMISS_CALLBACK_PREFIX))
async def dismiss_timed_announcement_callback(callback: CallbackQuery, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        await callback.answer()
        return

    announcement_id = parse_announce_dismiss_callback(callback.data)
    if announcement_id is None:
        await callback.answer()
        return

    if callback.from_user.id != callback.message.chat.id:
        await callback.answer()
        return

    try:
        await bot.delete_message(
            chat_id=callback.message.chat.id,
            message_id=callback.message.message_id,
        )
    except TelegramBadRequest:
        pass

    await callback.answer("تم إخفاء الإعلان")
