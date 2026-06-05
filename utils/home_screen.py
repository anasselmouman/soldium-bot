# -*- coding: utf-8 -*-
"""الواجهة الرئيسية (صورة ترحيب + تعليق بالرصيد) — تحديث حي دون إغراق المحادثة."""

from __future__ import annotations

import logging
import json
import time
from pathlib import Path

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, InputMediaPhoto, Message

from database import add_user, get_user, get_user_living_ui
from keyboards.main import build_main_menu
from utils.ui_branding import ACCOUNT_PERSISTENCE_HTML

logger = logging.getLogger(__name__)
logger.info("home_screen loaded (edit API: bot.edit_message_media v2)")

_MAIN_WELCOME_PATH = Path(__file__).resolve().parent.parent / "assets" / "main_welcome.png"
_ONBOARDING_WELCOME_PATH = _MAIN_WELCOME_PATH.parent / "welcome_upload.png"
_FILE_ID_CACHE_PATH = _MAIN_WELCOME_PATH.parent / "welcome_telegram_file_id.txt"
_WELCOME_FILE_ID: str | None = None
_DEBUG_LOG_PATH = Path(__file__).resolve().parents[1] / "debug-68c0f1.log"

_ONBOARDING_CAPTION = f"""مرحباً بك في <b>سولديوم (SOLDIUM)</b> 🚀
وجهتك لنمو حساباتك على منصات التواصل بأسعار الجملة.

نُنفّذ طلباتك آلياً على مدار الساعة، بأسعار مباشرة دون وسيط.

<b>لماذا سولديوم؟</b>

🛍️ أسعار جملة حقيقية لا تُنافس في السوق.
⚡ تنفيذ سريع وآلي لطلباتك.
💰 عمولة على كل طلب يُتم عبر رابط إحالتك.
🛡️ نظام آمن يحمي بياناتك وعملياتك المالية.

{ACCOUNT_PERSISTENCE_HTML}

خلال لحظات ستظهر لك القائمة مع الأزرار — تابع معنا."""


def main_welcome_image_path() -> Path:
    return _MAIN_WELCOME_PATH


def onboarding_welcome_image_path() -> Path:
    return _ONBOARDING_WELCOME_PATH


def ensure_welcome_upload_jpeg() -> Path:
    """متوافق مع التشغيل السابق — الواجهة الحية تستخدم main_welcome.png مباشرة."""
    return _MAIN_WELCOME_PATH if _MAIN_WELCOME_PATH.is_file() else _ONBOARDING_WELCOME_PATH



def _load_persisted_file_id() -> None:
    global _WELCOME_FILE_ID
    try:
        if _FILE_ID_CACHE_PATH.is_file():
            fid = _FILE_ID_CACHE_PATH.read_text(encoding="utf-8").strip()
            if fid:
                _WELCOME_FILE_ID = fid
    except OSError as exc:
        logger.warning("Could not read welcome file_id cache: %s", exc)


def _persist_welcome_file_id(file_id: str) -> None:
    global _WELCOME_FILE_ID
    if not file_id or file_id == _WELCOME_FILE_ID:
        return
    _WELCOME_FILE_ID = file_id
    try:
        _FILE_ID_CACHE_PATH.write_text(file_id, encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not persist welcome file_id: %s", exc)

def _clear_persisted_file_id() -> None:
    global _WELCOME_FILE_ID
    _WELCOME_FILE_ID = None
    try:
        if _FILE_ID_CACHE_PATH.is_file():
            _FILE_ID_CACHE_PATH.unlink()
    except OSError as exc:
        logger.warning("Could not clear welcome file_id cache: %s", exc)


def _invalid_cached_file_error(exc: TelegramBadRequest) -> bool:
    msg = (getattr(exc, "message", None) or str(exc)).lower()
    return "wrong file" in msg or "file_id" in msg or "file reference" in msg


def _format_balance_for_caption(balance: float) -> str:
    return f"{float(balance):.2f}"


def build_main_home_caption(balance: float) -> str:
    bal = _format_balance_for_caption(balance)
    return (
        f"🏠 <b>الرئيسية</b>\n\n"
        f"💵 <b>رصيدك الحالي:</b> {bal} درهم\n\n"
        "💎 <b>سولديوم (SOLDIUM)</b> — شريكك لنمو حساباتك على منصات التواصل، بأسعار جملة مباشرة.\n\n"
        "<b>كيف تطلب؟</b>\n"
        "من «الخدمات والأسعار» اختر المنصة والخدمة، ثم أكّد طلبك. "
        "إذا احتجت رصيداً إضافياً، استخدم «إضافة رصيد».\n\n"
        "<b>ابدأ من الأزرار أدناه.</b>"
    )


def user_balance_for_home(user_id: int) -> float:
    add_user(user_id)
    row = get_user(user_id)
    return float(row["balance"]) if row else 0.0


def main_home_markup(user_id: int, *, is_admin: bool) -> object:
    return build_main_menu(is_admin=is_admin)


def _is_not_modified(exc: TelegramBadRequest) -> bool:
    msg = (getattr(exc, "message", None) or str(exc)).lower()
    return "message is not modified" in msg or "message_not_modified" in msg


def _is_message_to_edit_not_found(exc: TelegramBadRequest) -> bool:
    msg = (getattr(exc, "message", None) or str(exc)).lower()
    return "message to edit not found" in msg


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


def _welcome_image_bytes() -> int:
    path = _MAIN_WELCOME_PATH
    return path.stat().st_size if path.is_file() else 0


def _remember_file_id_from_message(message: Message) -> None:
    if message.photo:
        _persist_welcome_file_id(message.photo[-1].file_id)


def _welcome_photo_media(path: Path) -> str | FSInputFile:
    if _WELCOME_FILE_ID:
        return _WELCOME_FILE_ID
    return FSInputFile(path)


def _home_photo_source_available() -> bool:
    return _MAIN_WELCOME_PATH.is_file() or bool(_WELCOME_FILE_ID)


async def _send_main_home_as_text(message: Message, caption: str, markup: object) -> Message:
    return await message.answer(caption, reply_markup=markup, parse_mode=ParseMode.HTML)


def _main_home_content(user_id: int, *, is_admin: bool) -> tuple[str, object]:
    balance = user_balance_for_home(user_id)
    caption = build_main_home_caption(balance)
    markup = main_home_markup(user_id, is_admin=is_admin)
    return caption, markup


async def send_onboarding_welcome_photo(message: Message) -> Message | None:
    """الخطوة 1 من /start: ترحيب بدون أزرار — تبقى في سجل المحادثة."""
    path = _ONBOARDING_WELCOME_PATH
    if not path.is_file():
        logger.error("Onboarding welcome image missing: %s", path)
        return await message.answer(_ONBOARDING_CAPTION, parse_mode=ParseMode.HTML)

    try:
        return await message.answer_photo(
            photo=FSInputFile(path),
            caption=_ONBOARDING_CAPTION,
            parse_mode=ParseMode.HTML,
            request_timeout=180,
        )
    except TelegramNetworkError:
        logger.exception("Onboarding welcome photo failed; sending text only")
        return await message.answer(_ONBOARDING_CAPTION, parse_mode=ParseMode.HTML)
    except TelegramBadRequest as exc:
        logger.warning("Onboarding welcome photo rejected: %s", exc)
        return await message.answer(_ONBOARDING_CAPTION, parse_mode=ParseMode.HTML)


async def send_main_home_photo(message: Message, user_id: int, *, is_admin: bool) -> Message | None:
    """إرسال لوحة الرئيسية (صورة أو نص) — الخطوة 3 من /start أو استعادة."""
    caption, markup = _main_home_content(user_id, is_admin=is_admin)
    path = _MAIN_WELCOME_PATH
    if not _home_photo_source_available():
        logger.warning("Main welcome image missing and no cached file_id: %s", path)
        return await _send_main_home_as_text(message, caption, markup)

    photo = _welcome_photo_media(path) if path.is_file() else _WELCOME_FILE_ID
    last_exc: BaseException | None = None
    for attempt in (1, 2):
        try:
            sent = await message.answer_photo(
                photo=photo,
                caption=caption,
                reply_markup=markup,
                parse_mode=ParseMode.HTML,
                request_timeout=180,
            )
            if sent.photo:
                _persist_welcome_file_id(sent.photo[-1].file_id)
            return sent
        except TelegramNetworkError as exc:
            last_exc = exc
            if attempt == 1:
                continue
            return await _send_main_home_as_text(message, caption, markup)
        except TelegramBadRequest as exc:
            if _invalid_cached_file_error(exc) and _WELCOME_FILE_ID:
                _clear_persisted_file_id()
                if path.is_file():
                    photo = FSInputFile(path)
                    continue
            logger.warning("Main home photo rejected, text fallback: %s", exc)
            return await _send_main_home_as_text(message, caption, markup)
    if last_exc:
        return await _send_main_home_as_text(message, caption, markup)
    return await _send_main_home_as_text(message, caption, markup)


async def _bot_edit_home_caption(
    bot: Bot,
    chat_id: int,
    message_id: int,
    *,
    caption: str,
    markup: object,
) -> None:
    await bot.edit_message_caption(
        chat_id=chat_id,
        message_id=message_id,
        caption=caption,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
        request_timeout=120,
    )


async def _bot_edit_home_text(
    bot: Bot,
    chat_id: int,
    message_id: int,
    *,
    caption: str,
    markup: object,
) -> None:
    await bot.edit_message_text(
        text=caption,
        chat_id=chat_id,
        message_id=message_id,
        reply_markup=markup,
        parse_mode=ParseMode.HTML,
        request_timeout=120,
    )


async def _bot_edit_home_media(
    bot: Bot,
    chat_id: int,
    message_id: int,
    path: Path,
    *,
    caption: str,
    markup: object,
    force_upload: bool = False,
) -> Message | bool | None:
    global _WELCOME_FILE_ID
    media_source: str | FSInputFile
    if force_upload or not _WELCOME_FILE_ID:
        if path.is_file():
            media_source = FSInputFile(path)
        elif _WELCOME_FILE_ID:
            media_source = _WELCOME_FILE_ID
        else:
            return None
    else:
        media_source = _WELCOME_FILE_ID
    return await bot.edit_message_media(
        chat_id=chat_id,
        message_id=message_id,
        media=InputMediaPhoto(
            media=media_source,
            caption=caption,
            parse_mode=ParseMode.HTML,
        ),
        reply_markup=markup,
        request_timeout=180,
    )


async def _resolve_living_home_target(
    state: FSMContext | None,
    user_id: int,
    callback: CallbackQuery | None,
) -> tuple[int, int, bool] | None:
    """رسالة الواجهة الحية من DB/FSM — وليس رسالة الزر إن كانت خطوة مؤقتة."""
    from utils.living_ui import get_living_ui

    living_chat: int | None = None
    living_id: int | None = None
    living_photo = True
    if state is not None:
        living_chat, living_id, living_photo = await get_living_ui(state, user_id)
    if living_id is None or living_chat is None:
        db_chat, db_mid, db_photo = get_user_living_ui(user_id)
        living_chat, living_id, living_photo = db_chat, db_mid, db_photo
    if isinstance(living_id, int) and isinstance(living_chat, int):
        return living_chat, living_id, living_photo
    if callback and callback.message:
        msg = callback.message
        return msg.chat.id, msg.message_id, bool(msg.photo)
    return None


async def edit_main_home_by_id(
    bot: Bot,
    chat_id: int,
    message_id: int,
    has_photo: bool,
    user_id: int,
    *,
    is_admin: bool,
) -> None:
    """تحديث الرسالة الحية للرئيسية عبر chat_id/message_id (وليس callback من خطوة مؤقتة)."""
    caption, markup = _main_home_content(user_id, is_admin=is_admin)
    path = _MAIN_WELCOME_PATH
    can_edit_photo = _home_photo_source_available()

    try:
        if has_photo and can_edit_photo:
            try:
                result = await _bot_edit_home_media(
                    bot, chat_id, message_id, path, caption=caption, markup=markup
                )
                if isinstance(result, Message) and result.photo:
                    _persist_welcome_file_id(result.photo[-1].file_id)
                return
            except TelegramBadRequest as exc:
                if _is_not_modified(exc):
                    return
                if not _invalid_cached_file_error(exc):
                    await _bot_edit_home_caption(
                        bot, chat_id, message_id, caption=caption, markup=markup
                    )
                    return
                _clear_persisted_file_id()
                result = await _bot_edit_home_media(
                    bot,
                    chat_id,
                    message_id,
                    path,
                    caption=caption,
                    markup=markup,
                    force_upload=True,
                )
                if isinstance(result, Message) and result.photo:
                    _persist_welcome_file_id(result.photo[-1].file_id)
                return

        if not can_edit_photo:
            logger.warning("Main welcome image missing for edit_message_media: %s", path)
            await _bot_edit_home_text(
                bot, chat_id, message_id, caption=caption, markup=markup
            )
            return

        try:
            result = await _bot_edit_home_media(
                bot, chat_id, message_id, path, caption=caption, markup=markup
            )
        except TelegramBadRequest as exc:
            if _is_not_modified(exc):
                return
            if _invalid_cached_file_error(exc):
                _clear_persisted_file_id()
                result = await _bot_edit_home_media(
                    bot,
                    chat_id,
                    message_id,
                    path,
                    caption=caption,
                    markup=markup,
                    force_upload=True,
                )
            else:
                logger.warning("edit_message_media failed, text home fallback: %s", exc)
                await _bot_edit_home_text(
                    bot, chat_id, message_id, caption=caption, markup=markup
                )
                return

        if isinstance(result, Message) and result.photo:
            _persist_welcome_file_id(result.photo[-1].file_id)
    except TelegramBadRequest as exc:
        if _is_not_modified(exc):
            return
        try:
            await _bot_edit_home_text(bot, chat_id, message_id, caption=caption, markup=markup)
        except TelegramBadRequest as exc2:
            if not _is_not_modified(exc2):
                raise
    except TelegramNetworkError:
        raise
    except Exception as exc:
        logger.exception("edit_main_home_by_id unexpected error: %s", exc)
        try:
            await _bot_edit_home_text(bot, chat_id, message_id, caption=caption, markup=markup)
        except Exception:
            pass


async def edit_main_home_from_callback(
    callback: CallbackQuery,
    user_id: int,
    *,
    is_admin: bool,
    bot: Bot,
    state: FSMContext | None = None,
) -> None:
    """تحديث الرسالة الحية للرئيسية — لا يعتمد على callback.message إن كانت خطوة مؤقتة."""
    if callback.message:
        _remember_file_id_from_message(callback.message)
    target = await _resolve_living_home_target(state, user_id, callback)
    if target is None:
        logger.warning("edit_main_home: no living UI target for user %s", user_id)
        return
    chat_id, message_id, has_photo = target
    await edit_main_home_by_id(
        bot, chat_id, message_id, has_photo, user_id, is_admin=is_admin
    )


async def navigate_to_main_home(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    *,
    user_id: int,
    is_admin: bool,
) -> None:
    """مسار موحّد للرجوع للرئيسية من menu:home و order:nav:home."""
    from utils.flow_transcript import purge_flow_transcript
    from utils.living_ui import delete_chat_message, register_living_ui_ids
    from utils.timed_announcements import deliver_timed_announcements_on_entry

    callback_msg = callback.message
    chat_id = callback_msg.chat.id if callback_msg else None
    callback_mid = callback_msg.message_id if callback_msg else None
    deliver_announcements = False

    try:
        target = await _resolve_living_home_target(state, user_id, callback)

        if chat_id is not None:
            await purge_flow_transcript(bot, state, user_id, chat_id)
        await state.clear()

        if target is None:
            if callback_msg is not None:
                sent = await send_main_home_photo(
                    callback_msg, user_id, is_admin=is_admin
                )
                if sent is not None:
                    await register_living_ui_ids(
                        state,
                        user_id,
                        sent.chat.id,
                        sent.message_id,
                        has_photo=bool(sent.photo),
                    )
            deliver_announcements = True
            return

        living_chat_id, living_message_id, has_photo = target

        if (
            callback_mid is not None
            and chat_id is not None
            and callback_mid != living_message_id
        ):
            await delete_chat_message(bot, chat_id, callback_mid)

        try:
            await edit_main_home_by_id(
                bot,
                living_chat_id,
                living_message_id,
                has_photo,
                user_id,
                is_admin=is_admin,
            )
        except TelegramBadRequest as exc:
            if not _is_message_to_edit_not_found(exc):
                raise
            logger.warning(
                "navigate_to_main_home: living message missing chat=%s msg=%s",
                living_chat_id,
                living_message_id,
            )
            if callback_msg is not None:
                sent = await send_main_home_photo(
                    callback_msg, user_id, is_admin=is_admin
                )
                if sent is not None:
                    living_chat_id = sent.chat.id
                    living_message_id = sent.message_id
                    has_photo = bool(sent.photo)
            deliver_announcements = True

        await register_living_ui_ids(
            state,
            user_id,
            living_chat_id,
            living_message_id,
            has_photo=True,
        )
    finally:
        if deliver_announcements:
            try:
                await deliver_timed_announcements_on_entry(bot, user_id)
            except Exception:
                logger.exception(
                    "Failed to deliver timed announcements after home reset user_id=%s",
                    user_id,
                )


async def edit_living_screen(
    bot: Bot,
    message: Message,
    text: str,
    reply_markup: object | None,
    *,
    parse_mode: str | None = "HTML",
) -> None:
    """تعديل رسالة الواجهة الحية (تعليق الصورة أو النص) دون رسالة جديدة."""
    from utils.telegram_ui import safe_edit_message

    await safe_edit_message(
        message, bot, text, reply_markup=reply_markup, parse_mode=parse_mode
    )


_load_persisted_file_id()
