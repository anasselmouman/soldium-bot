# -*- coding: utf-8 -*-
"""رسالة الواجهة الحية الواحدة — محفوظة في DB ولا تُحذف ولا تُستبدل بـ answer()."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from database import get_user_living_ui, set_user_living_ui
from utils.telegram_ui import _prepare_photo_caption

LIVING_UI_MESSAGE_KEY = "living_ui_message_id"
LIVING_UI_CHAT_KEY = "living_ui_chat_id"
LIVING_UI_HAS_PHOTO_KEY = "living_ui_has_photo"
INPUT_UI_DELAY_SECONDS = 2.0
_DEBUG_LOG_PATH = Path(__file__).resolve().parents[1] / "debug-68c0f1.log"
# delay_delete_*: للمسارات الاحتياطية فقط؛ التدفقات متعددة الخطوات تستخدم flow_transcript.


def _debug_log(run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # region agent log
    try:
        payload = {
            "sessionId": "68c0f1",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion


async def register_living_ui_ids(
    state: FSMContext,
    user_id: int,
    chat_id: int,
    message_id: int,
    *,
    has_photo: bool,
) -> None:
    set_user_living_ui(
        user_id,
        chat_id=chat_id,
        message_id=message_id,
        has_photo=has_photo,
    )
    await state.update_data(
        **{
            LIVING_UI_MESSAGE_KEY: message_id,
            LIVING_UI_CHAT_KEY: chat_id,
            LIVING_UI_HAS_PHOTO_KEY: has_photo,
        }
    )


async def register_living_ui_message(
    state: FSMContext,
    message: Message,
    *,
    user_id: int,
) -> None:
    prev_chat, prev_mid, prev_photo = get_user_living_ui(user_id)
    chat_id = message.chat.id
    message_id = message.message_id
    has_photo = bool(message.photo)
    # region agent log
    _debug_log(
        "pre-fix",
        "H-register-db",
        "utils/living_ui.py:register_living_ui_message",
        "register_living_ui_message_called",
        {
            "user_id": user_id,
            "prev_chat_id": prev_chat,
            "prev_message_id": prev_mid,
            "prev_has_photo": prev_photo,
            "new_chat_id": chat_id,
            "new_message_id": message_id,
            "new_has_photo": has_photo,
        },
    )
    # endregion
    await register_living_ui_ids(
        state, user_id, chat_id, message_id, has_photo=has_photo
    )


async def get_living_ui(
    state: FSMContext,
    user_id: int,
) -> tuple[int | None, int | None, bool]:
    """(chat_id, message_id, has_photo) — DB أولاً لأنه يبقى بعد state.clear()."""
    db_chat, db_mid, db_photo = get_user_living_ui(user_id)
    if db_mid is not None and db_chat is not None:
        return db_chat, db_mid, db_photo
    data = await state.get_data()
    mid = data.get(LIVING_UI_MESSAGE_KEY)
    chat = data.get(LIVING_UI_CHAT_KEY)
    has_photo = bool(data.get(LIVING_UI_HAS_PHOTO_KEY, True))
    if isinstance(mid, int) and isinstance(chat, int):
        return chat, mid, has_photo
    return None, None, has_photo


def is_living_ui_message_id(user_id: int, message_id: int | None) -> bool:
    _, living_mid, _ = get_user_living_ui(user_id)
    return isinstance(living_mid, int) and isinstance(message_id, int) and living_mid == message_id


# فاصل بسيط بين حذوفات متتالية ليُشغّل عميل تيليغرام أنيميشن الحذف (تلاشي).
DELETE_MESSAGE_STAGGER_SECONDS = 0.12


async def delete_chat_message(bot: Bot, chat_id: int, message_id: int) -> bool:
    """حذف رسالة عبر API — العميل يعرض أنيميشن الحذف الافتراضي."""
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True
    except TelegramBadRequest:
        return False


async def delete_chat_messages_staggered(
    bot: Bot,
    chat_id: int,
    message_ids: list[int],
    *,
    gap_seconds: float = DELETE_MESSAGE_STAGGER_SECONDS,
    newest_first: bool = True,
) -> None:
    """حذف عدة رسائل بتباعد زمني قصير لظهور أنيميشن الحذف لكل رسالة."""
    ids = list(message_ids)
    if newest_first:
        ids = list(reversed(ids))
    for index, mid in enumerate(ids):
        if index > 0 and gap_seconds > 0:
            await asyncio.sleep(gap_seconds)
        await delete_chat_message(bot, chat_id, mid)


async def delete_user_message(bot: Bot, chat_id: int, message_id: int) -> None:
    await delete_chat_message(bot, chat_id, message_id)


async def delay_delete_user_message(
    bot: Bot,
    chat_id: int,
    message_id: int,
    delay_seconds: float = INPUT_UI_DELAY_SECONDS,
) -> None:
    await asyncio.sleep(delay_seconds)
    await delete_user_message(bot, chat_id, message_id)


async def delay_delete_user_message_obj(
    bot: Bot,
    message: Message,
    delay_seconds: float = INPUT_UI_DELAY_SECONDS,
) -> None:
    await delay_delete_user_message(bot, message.chat.id, message.message_id, delay_seconds)


async def edit_living_ui_message(
    bot: Bot,
    chat_id: int,
    message_id: int,
    text: str,
    reply_markup: object | None,
    *,
    has_photo: bool,
    parse_mode: str | None = "HTML",
) -> None:
    """تعديل رسالة الواجهة الحية عبر chat_id/message_id مع fallback نص↔تعليق."""
    from utils.telegram_ui import _is_message_not_modified

    caption, caption_mode = _prepare_photo_caption(text)
    effective_mode = caption_mode if caption_mode is not None else parse_mode
    if caption_mode is None and parse_mode == "HTML":
        effective_mode = None

    async def _edit_caption(*, plain: bool = False) -> None:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=caption,
            reply_markup=reply_markup,
            parse_mode=None if plain else effective_mode,
        )

    async def _edit_text() -> None:
        await bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    async def _attempt(method: str) -> None:
        try:
            if method == "caption":
                await _edit_caption()
            else:
                await _edit_text()
        except TelegramBadRequest as exc:
            if _is_message_not_modified(exc):
                return
            if method == "caption":
                try:
                    await _edit_caption(plain=True)
                except TelegramBadRequest as exc2:
                    if _is_message_not_modified(exc2):
                        return
                    raise exc2 from exc
            raise

    order = ("caption", "text") if has_photo else ("text", "caption")
    last_exc: TelegramBadRequest | None = None
    for method in order:
        try:
            await _attempt(method)
            return
        except TelegramBadRequest as exc:
            last_exc = exc
    if last_exc is not None:
        raise last_exc


def _is_message_to_edit_not_found(exc: TelegramBadRequest) -> bool:
    msg = (getattr(exc, "message", None) or str(exc)).lower()
    return "message to edit not found" in msg


async def edit_user_living_ui(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    text: str,
    reply_markup: object | None,
    *,
    parse_mode: str | None = "HTML",
) -> bool:
    living_chat, living_id, living_photo = await get_living_ui(state, user_id)
    if not living_id or not living_chat:
        return False
    try:
        await edit_living_ui_message(
            bot,
            living_chat,
            living_id,
            text,
            reply_markup,
            has_photo=living_photo,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest as exc:
        if _is_message_to_edit_not_found(exc):
            return False
        raise
    return True


async def flash_living_ui_error(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    *,
    error_text: str,
    restore_text: str,
    restore_markup: object | None,
    user_message: Message | None = None,
    delay_seconds: float = INPUT_UI_DELAY_SECONDS,
) -> bool:
    """عرض خطأ مؤقت، انتظار، حذف رسالة المستخدم، ثم إعادة التعليمات."""
    living_chat, living_id, living_photo = await get_living_ui(state, user_id)
    if not living_id or not living_chat:
        return False
    flash_text = error_text if "⚠️" in error_text else f"⚠️ {error_text}"
    await edit_living_ui_message(
        bot,
        living_chat,
        living_id,
        flash_text,
        restore_markup,
        has_photo=living_photo,
    )
    await asyncio.sleep(delay_seconds)
    if user_message is not None:
        await delete_user_message(bot, user_message.chat.id, user_message.message_id)
    await edit_living_ui_message(
        bot,
        living_chat,
        living_id,
        restore_text,
        restore_markup,
        has_photo=living_photo,
    )
    return True
