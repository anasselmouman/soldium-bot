# -*- coding: utf-8 -*-
"""Helpers for Telegram app-like UI: safe edits, ephemeral errors."""

from __future__ import annotations

import asyncio
import logging
import random
import re

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

logger = logging.getLogger(__name__)

ORDER_UI_DISMISS = "order:ui:dismiss"
_CAPTION_MAX = 1024


def _strip_html(text: str) -> str:
    plain = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\n{3,}", "\n\n", plain).strip()


def _prepare_photo_caption(text: str, *, max_len: int = _CAPTION_MAX) -> tuple[str, str | None]:
    """قصّ آمن للتعليق: HTML إن أمكن، وإلا نص عادي."""
    if len(text) <= max_len:
        return text, "HTML"
    plain = _strip_html(text)
    if len(plain) <= max_len:
        return plain, None
    return plain[: max_len - 1] + "…", None

FINANCE_COMING_SOON_AR = (
    "نحن نجهز النظام المالي، ستتوفر هذه الميزة قريباً مع حساباتنا البنكية الجديدة."
)


def order_ui_dismiss_markup() -> object:
    builder = InlineKeyboardBuilder()
    builder.button(text="❌ إغلاق", callback_data=ORDER_UI_DISMISS)
    builder.adjust(1)
    return builder.as_markup()


def _is_message_not_modified(exc: TelegramBadRequest) -> bool:
    msg = (getattr(exc, "message", None) or str(exc)).lower()
    return "message is not modified" in msg or "message_not_modified" in msg


async def safe_edit_message(
    message: Message,
    bot: Bot,
    text: str,
    reply_markup: object | None = None,
    parse_mode: str | None = "HTML",
) -> None:
    """Edit in-place: caption if photo, else text (requires Bot for photo messages)."""
    if message.photo:
        caption, caption_mode = _prepare_photo_caption(text)
        effective_mode = caption_mode if caption_mode is not None else parse_mode
        if caption_mode is None and parse_mode == "HTML":
            effective_mode = None
        try:
            await bot.edit_message_caption(
                chat_id=message.chat.id,
                message_id=message.message_id,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=effective_mode,
            )
        except TelegramBadRequest as exc:
            if _is_message_not_modified(exc):
                return
            try:
                await bot.edit_message_caption(
                    chat_id=message.chat.id,
                    message_id=message.message_id,
                    caption=caption,
                    reply_markup=reply_markup,
                )
            except TelegramBadRequest as exc2:
                if _is_message_not_modified(exc2):
                    return
                logger.debug("caption_edit_failed: %s", exc2)
                raise exc2 from exc
        return
    try:
        await message.edit_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as exc:
        if _is_message_not_modified(exc):
            return
        raise


def allow_new_message_fallback(message: Message) -> bool:
    """رسالة الصورة الرئيسية يجب أن تُعدَّل فقط — لا نُنشئ رسالة جديدة عند فشل التعديل."""
    return not bool(message.photo)


async def _edit_photo_caption(
    message: Message,
    caption: str,
    reply_markup: object | None,
    parse_mode: str | None,
    bot: Bot | None,
) -> None:
    if bot is not None:
        await safe_edit_message(message, bot, caption, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    try:
        await message.edit_caption(
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest as exc:
        if _is_message_not_modified(exc):
            return
        await message.edit_caption(
            caption=caption,
            reply_markup=reply_markup,
        )


async def safe_edit_message_text(
    message: Message,
    text: str,
    reply_markup: object | None = None,
    parse_mode: str | None = "HTML",
    *,
    bot: Bot | None = None,
) -> None:
    """واجهة حية: تعديل الرسالة الحالية (صورة + تعليق) دون إرسال رسالة جديدة."""
    if bot is not None:
        await safe_edit_message(
            message, bot, text, reply_markup=reply_markup, parse_mode=parse_mode
        )
        return
    if message.photo:
        caption, caption_mode = _prepare_photo_caption(text)
        await _edit_photo_caption(message, caption, reply_markup, caption_mode or parse_mode, bot)
        return
    try:
        await message.edit_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except TelegramBadRequest as exc:
        if _is_message_not_modified(exc):
            return
        raise


async def send_ephemeral_error(message: Message, bot: Bot, text: str, parse_mode: str = "HTML") -> Message:
    """Reply with an error, dismiss button, and auto-delete after 5 seconds."""
    sent = await message.answer(text, parse_mode=parse_mode, reply_markup=order_ui_dismiss_markup())

    async def _delete_later() -> None:
        await asyncio.sleep(5)
        try:
            await sent.delete()
        except TelegramBadRequest:
            pass

    asyncio.create_task(_delete_later())
    return sent


async def send_auto_delete_notice(
    message: Message,
    text: str,
    *,
    parse_mode: str | None = "HTML",
    delay_seconds: float | None = None,
) -> Message:
    """رسالة قصيرة تُحذف تلقائياً بعد 3–5 ثوانٍ (افتراضياً) للحفاظ على نظافة المحادثة."""
    delay = delay_seconds if delay_seconds is not None else random.uniform(3.0, 5.0)
    sent = await message.answer(text, parse_mode=parse_mode)

    async def _delete_later() -> None:
        await asyncio.sleep(delay)
        try:
            await sent.delete()
        except TelegramBadRequest:
            pass

    asyncio.create_task(_delete_later())
    return sent


async def send_finance_coming_soon_flash(message: Message) -> Message:
    """تنبيه مؤقت لوظائف الشحن/السحب/السجلات قيد التطوير."""
    html = f"<b>🛡️ SOLDIUM</b> 🤖\n\n{FINANCE_COMING_SOON_AR}"
    return await send_auto_delete_notice(message, html, parse_mode="HTML")
