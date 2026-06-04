# -*- coding: utf-8 -*-
"""قسم «إربح المال» — واجهة حية + سجل تدفق مؤقت (مثل سحب الرصيد)."""

from __future__ import annotations

import json
import logging
import re
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MIN_REFERRAL_WITHDRAW_DH
from database import (
    cancel_pending_withdrawal,
    count_active_referred_users,
    count_invited_users,
    create_withdrawal_with_balance_hold,
    get_active_withdrawal,
    get_user,
    list_referral_invitees_summaries,
    sum_referrer_pending_commission_estimate,
    transfer_referral_balance_to_main,
)
from keyboards.referrals import (
    build_referral_back_menu,
    build_referral_list_menu,
    build_referral_main_menu,
    build_referral_step_nav,
    build_referral_upgrade_menu,
    build_referral_withdraw_confirm_menu,
    build_referral_withdraw_methods_menu,
    build_referral_withdraw_pending_menu,
    build_referral_withdraw_step_nav,
)
from services.referral import (
    MAX_REFERRAL_LEVEL,
    REFERRAL_TIERS,
    commission_rate_percent,
    next_level_spec,
    referral_level_name,
)
from utils.flow_transcript import (
    acknowledge_then_focus_living_ui,
    delete_flow_step_prompt,
    flash_step_prompt_error,
    get_flow_step_prompt_id,
    purge_flow_transcript,
    reset_flow_transcript,
    send_flow_step_prompt,
    set_living_nav_anchor,
    track_transcript_user_message,
    transfer_nav_anchor,
)
from utils.fsm_prompt_cleanup import clear_last_prompt
from utils.living_ui import (
    edit_user_living_ui,
    get_living_ui,
    register_living_ui_message,
)
from utils.money import format_dh, to_float
from utils.payment_banks import PAYMENT_BY_KEY, PAYMENT_METHODS, PaymentMethod
from utils.states import ReferralFlow
from utils.telegram_ui import allow_new_message_fallback, safe_edit_message_text
from utils.ui_branding import format_breadcrumb
from utils.crypto_usdt import (
    CRYPTO_WITHDRAW_EXCHANGE_NOTICE,
    crypto_network_fee_warning,
    format_crypto_usdt_estimate_line,
    format_crypto_withdraw_amount_prompt,
    get_crypto_withdraw_option,
)
from utils.withdraw_details import (
    format_crypto_withdraw_details_html,
    format_withdraw_details_display,
    is_crypto_withdraw_details,
    safe_withdraw_details,
)

logger = logging.getLogger(__name__)

router = Router()

PAGE_SIZE = 10
REFERRAL_FLASH_RESTORE_KEY = "referral_flash_restore_text"


async def _telegram_bot_username(bot: Bot) -> str:
    me = await bot.get_me()
    return me.username or "bot"


async def _set_referral_flash_restore(state: FSMContext, text: str) -> None:
    await state.update_data(**{REFERRAL_FLASH_RESTORE_KEY: text})


async def _prepare_referral_nav(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    *,
    purge_transcript: bool = False,
) -> int | None:
    if not callback.from_user or not callback.message:
        return None
    await clear_last_prompt(callback.message, state, bot=bot)
    if purge_transcript:
        await purge_flow_transcript(
            bot, state, callback.from_user.id, callback.message.chat.id
        )
    else:
        await delete_flow_step_prompt(bot, state, callback.message.chat.id)
    return callback.from_user.id


async def _finish_referral_flow(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
) -> None:
    await purge_flow_transcript(bot, state, user_id, chat_id)
    await state.clear()


async def _edit_referral_screen(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    text: str,
    reply_markup: object | None,
    *,
    message: Message | None = None,
) -> bool:
    if await edit_user_living_ui(bot, state, user_id, text, reply_markup):
        return True
    if message is None:
        return False
    try:
        await safe_edit_message_text(
            message, text, reply_markup=reply_markup, parse_mode="HTML", bot=bot
        )
        return True
    except TelegramBadRequest as exc:
        logger.debug("_edit_referral_screen fallback: %s", exc)
        if allow_new_message_fallback(message):
            sent = await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
            await register_living_ui_message(state, sent, user_id=user_id)
            return True
    return False


async def _ensure_referral_living(callback: CallbackQuery, state: FSMContext) -> int | None:
    if not callback.from_user or not callback.message:
        return None
    await register_living_ui_message(
        state, callback.message, user_id=callback.from_user.id
    )
    return callback.from_user.id


async def _sync_referral_nav_anchor(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    reply_markup: object | None,
) -> None:
    living_chat, living_id, _ = await get_living_ui(state, user_id)
    if not living_id or not living_chat:
        return
    await transfer_nav_anchor(
        bot,
        state,
        user_id,
        living_chat,
        new_message_id=living_id,
        new_markup=reply_markup,
        new_is_living=True,
        apply_markup=False,
    )


async def _edit_referral_from_callback(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    *,
    text: str,
    reply_markup: object | None,
    register_living: bool = False,
    sync_nav: bool = True,
) -> bool:
    if not callback.from_user:
        return False
    user_id = callback.from_user.id
    if register_living:
        await _ensure_referral_living(callback, state)
    fallback_message = callback.message
    if fallback_message is not None:
        step_id = await get_flow_step_prompt_id(state)
        if step_id == fallback_message.message_id:
            fallback_message = None
    ok = await _edit_referral_screen(
        bot,
        state,
        user_id,
        text,
        reply_markup,
        message=fallback_message,
    )
    if ok and sync_nav:
        await _sync_referral_nav_anchor(bot, state, user_id, reply_markup)
    return ok


async def _send_referral_step_prompt(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
    text: str,
    back_callback: str,
) -> int | None:
    if back_callback == "referral:withdraw":
        nav = build_referral_withdraw_step_nav()
    else:
        nav = build_referral_step_nav(back_callback)
    step_id = await send_flow_step_prompt(bot, state, chat_id, text, nav)
    if step_id is not None:
        await transfer_nav_anchor(
            bot,
            state,
            user_id,
            chat_id,
            new_message_id=step_id,
            new_markup=nav,
            new_is_living=False,
            apply_markup=False,
        )
    await _set_referral_flash_restore(state, text)
    return step_id


async def _flash_referral_input_error(
    message: Message,
    state: FSMContext,
    bot: Bot,
    error_text: str,
    *,
    restore_markup: object | None = None,
) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    restore_text = str(data.get(REFERRAL_FLASH_RESTORE_KEY, "")).strip()
    if not restore_text:
        restore_text = "<b>أعد الإدخال من جديد:</b>"
    if restore_markup is None:
        restore_markup = build_referral_step_nav()
    await flash_step_prompt_error(
        bot,
        state,
        message.chat.id,
        error_text=error_text,
        restore_text=restore_text,
        restore_markup=restore_markup,
        user_message=message,
    )


def _format_referral_main_text(
    *,
    referral_level: int,
    referral_balance: float,
    pending_estimation: float,
    bot_username: str,
    user_id: int,
) -> str:
    link = f"https://t.me/{bot_username}?start={user_id}"
    level_name = referral_level_name(referral_level)
    rate_pct = commission_rate_percent(referral_level)
    if referral_level >= MAX_REFERRAL_LEVEL:
        return (
            "👑 قسم الإحالة والربح (VIP)\n"
            "━━━━━━━━━━━━━━━\n"
            f"🏅 مستواك: شريك (عمولة {rate_pct}% - الحد الأقصى)\n"
            f"💵 أرباحك الجاهزة: {format_dh(referral_balance)}\n"
            f"⏳ أرباح معلقة: {format_dh(pending_estimation)}\n\n"
            "🔗 رابط الإحالة الخاص بك (اضغط للنسخ):\n"
            f"<code>{escape(link)}</code>\n"
            "━━━━━━━━━━━━━━━\n"
            "أنت الآن تشاركنا نصف أرباح المنصة. شكراً لكونك جزءاً من نجاحنا!"
        )
    return (
        "💰 قسم الإحالة والربح\n"
        "━━━━━━━━━━━━━━━\n"
        f"🏅 مستواك الحالي: [{level_name}] (عمولة {rate_pct}%)\n"
        f"💵 أرباحك الجاهزة: {format_dh(referral_balance)}\n"
        f"⏳ أرباح معلقة: {format_dh(pending_estimation)}\n\n"
        "🔗 رابط الإحالة الخاص بك (اضغط للنسخ):\n"
        f"<code>{escape(link)}</code>\n"
        "━━━━━━━━━━━━━━━\n"
        "شارك رابطك مرة واحدة، واربح عمولة من كل طلباتهم.. مدى الحياة! 💸"
    )


def _format_referral_upgrade_text(
    *,
    referral_level: int,
    active_users: int,
    referral_earned_total: float,
) -> str:
    current_name = referral_level_name(referral_level)
    current_rate = commission_rate_percent(referral_level)
    nxt = next_level_spec(referral_level)
    if nxt is None:
        return (
            "📊 مسار الترقية الخاص بك\n"
            "━━━━━━━━━━━━━━━\n"
            f"🏅 مستواك الحالي: {current_name} (عمولة {current_rate}%)\n\n"
            "لقد وصلت إلى أعلى مستوى في النظام. 👑"
        )

    next_level = referral_level + 1
    next_name = nxt["name"]
    next_rate = int(round(nxt["rate"] * 100))
    locked_lines: list[str] = []
    for lvl in range(next_level + 1, MAX_REFERRAL_LEVEL + 1):
        locked_lines.append(
            f"❓ مستوى [{REFERRAL_TIERS[lvl]['name']}] (مقفلة - اكتشف الشروط بعد ترقيتك)"
        )
    locked_block = "\n".join(locked_lines) if locked_lines else ""
    earned_display = format_dh(referral_earned_total)
    next_earnings_display = format_dh(nxt["earnings"])
    next_active_req = int(nxt["active_users"])

    body = (
        "📊 مسار الترقية الخاص بك\n"
        "━━━━━━━━━━━━━━━\n"
        "💡 <b>كيف تترقى؟</b>\n"
        "للوصول للمستوى التالي، تحتاج إلى دعوة أشخاص <b>نشطين</b> "
        "(شخص طلب خدمة واحدة على الأقل) وجمع مبلغ محدد من عمولاتك.\n\n"
        f"🏅 مستواك الحالي: {current_name} (عمولة {current_rate}%)\n\n"
        "📈 إنجازاتك الحالية:\n"
        f"👥 الأشخاص النشطين: {active_users}\n"
        f"💰 إجمالي ما ربحته: {earned_display}\n\n"
        f"🎯 الهدف القادم: الترقية لمستوى «{next_name}» (عمولة {next_rate}% 🔥)\n"
        "المتبقي لفتح المستوى:\n"
        f"🔸 الأشخاص النشطين: {active_users} من أصل {next_active_req}\n"
        f"🔸 الأرباح المحققة: {earned_display} من أصل {next_earnings_display}\n"
        "(أكمل هذه الشروط لرفع عمولتك الدائمة!)\n"
    )
    if locked_block:
        body += f"\n🔒 المستويات السرية القادمة:\n{locked_block}\n"
    return body


def _format_invitee_line(*, index: int, name: str, earned: float, pending: float) -> str:
    safe_name = escape(name)
    if earned > 0:
        return f"{index}. {safe_name}: مؤكد من طلباته: <code>{format_dh(earned)}</code> ✅."
    if pending > 0:
        return f"{index}. {safe_name}: تقدير معلّق: <code>{format_dh(pending)}</code> ⏳."
    return f"{index}. {safe_name}: لم يطلب بعد."


def _parse_amount(raw: str) -> float | None:
    text = raw.strip().replace(",", ".")
    if not text:
        return None
    try:
        amount = to_float(text)
    except Exception:
        return None
    return amount if amount > 0 else None


def _withdraw_details_label(method: PaymentMethod) -> str:
    if method.kind == "bank":
        return "الاسم الكامل ورقم الحساب البنكي"
    if method.kind == "cash":
        return "رقم هاتف التطبيق"
    if method.kind == "crypto":
        return "Binance Pay ID أو عنوان المحفظة"
    if method.kind == "paypal":
        return "إيميل PayPal"
    return "معلومات السحب"


def _withdraw_details_instruction(method: PaymentMethod) -> str:
    if method.kind == "bank":
        return (
            "أرسل الاسم الكامل ورقم الحساب البنكي في نفس الرسالة، كل واحد في سطر منفصل.\n"
            "مثال:\n"
            "<code>ANASS EL MOUMAN\n"
            "123456789</code>"
        )
    if method.kind == "cash":
        return "أرسل رقم الهاتف المرتبط بتطبيق السحب."
    if method.kind == "crypto":
        return "أرسل Binance Pay ID أو عنوان المحفظة الذي تريد استقبال المبلغ عليه."
    if method.kind == "paypal":
        return "أرسل إيميل PayPal الذي تريد استقبال المبلغ عليه."
    return "أرسل معلومات السحب المطلوبة لهذه الطريقة."


def _parse_withdraw_details(method: PaymentMethod, raw: str) -> tuple[dict[str, str] | None, str]:
    text = raw.strip()
    if not text:
        return None, "أرسل معلومات السحب المطلوبة ولا تترك الرسالة فارغة."
    if method.kind == "bank":
        parts = [line.strip() for line in text.splitlines() if line.strip()]
        if len(parts) < 2:
            return None, "أرسل الاسم الكامل في السطر الأول ورقم الحساب البنكي في السطر الثاني."
        return {"name": parts[0], "account": parts[1]}, ""
    if method.kind == "cash":
        compact = re.sub(r"[\s\-+]", "", text)
        if not compact.isdigit() or len(compact) < 8:
            return None, "أرسل رقم هاتف صحيح مرتبط بالتطبيق."
        return {"phone": text}, ""
    if method.kind == "crypto":
        if len(text) < 5:
            return None, "أرسل Binance Pay ID أو عنوان محفظة صحيح."
        return {"destination": text}, ""
    if method.kind == "paypal":
        if "@" not in text or "." not in text:
            return None, "أرسل إيميل PayPal صحيح."
        return {"email": text}, ""
    return {"details": text}, ""


def _format_referral_transfer_summary(referral_balance: float) -> str:
    return (
        f"{format_breadcrumb('إربح المال', 'تحويل الأرباح إلى الرصيد')}\n\n"
        f"الأرباح المؤكدة المتاحة: <b>{format_dh(referral_balance)}</b>\n"
        "<i>لا يشمل التقدير المعلّق — التحويل من الأرباح المؤكدة فقط.</i>\n\n"
        "اتبع التعليمات في الرسالة أدناه لإكمال التحويل."
    )


def _format_referral_transfer_step_prompt() -> str:
    return (
        "<b>أرسل مبلغ التحويل</b>\n\n"
        "أرسل المبلغ الذي تريد تحويله إلى رصيدك الرئيسي بالأرقام فقط.\n"
        "مثال: <code>25</code>"
    )


def _format_referral_withdraw_details_section(
    details: dict[str, str],
    *,
    amount: float | None = None,
) -> list[str]:
    if is_crypto_withdraw_details(details):
        lines = [format_crypto_withdraw_details_html(details)]
        if amount is not None and amount > 0:
            lines.append(format_crypto_usdt_estimate_line(amount))
        fee = str(details.get("network_fee_usdt", "") or "")
        if amount is not None and fee:
            warning = crypto_network_fee_warning(amount, fee)
            if warning:
                lines.append(warning)
        return lines
    return [
        f"<code>{escape(format_withdraw_details_display(details))}</code>",
    ]


def _format_referral_withdraw_intro(user_id: int) -> str:
    user = get_user(user_id)
    referral_balance = float((user or {}).get("referral_balance") or 0.0)
    active = get_active_withdrawal(user_id, withdrawal_type="referral")
    if active is not None:
        details = safe_withdraw_details(active.get("details_json"))
        amount = float(active.get("amount") or 0)
        lines = [
            f"{format_breadcrumb('إربح المال', 'سحب أرباح الإحالة')}\n",
            "لديك طلب سحب إحالة قيد المعالجة حالياً.\n",
            f"• رقم الطلب: <code>#{active['id']}</code>",
            f"• الطريقة: <b>{escape(str(active['method']))}</b>",
            f"• المبلغ: <b>{format_dh(active['amount'])}</b>",
            f"• تاريخ الطلب: <code>{escape(str(active['created_at']))}</code>",
        ]
        if details:
            if is_crypto_withdraw_details(details):
                lines.append("• <b>بيانات الاستلام:</b>")
                lines.extend(_format_referral_withdraw_details_section(details, amount=amount))
            else:
                lines.append(
                    f"• معلومات السحب: "
                    f"<code>{escape(format_withdraw_details_display(details))}</code>"
                )
        return "\n".join(lines)
    return (
        f"{format_breadcrumb('إربح المال', 'سحب أرباح الإحالة')}\n\n"
        f"الأرباح المؤكدة المتاحة: <b>{format_dh(referral_balance)}</b>\n"
        "<i>لا يشمل التقدير المعلّق.</i>\n"
        f"الحد الأدنى للسحب: <b>{format_dh(MIN_REFERRAL_WITHDRAW_DH)}</b>\n\n"
        "اختر طريقة السحب من الأسفل. سحب أرباح الإحالة لا يحتاج أن تكون قد أودعت بهذه الطريقة سابقاً."
    )


def _format_referral_withdraw_method_summary(method: PaymentMethod, user_id: int) -> str:
    user = get_user(user_id)
    referral_balance = float((user or {}).get("referral_balance") or 0.0)
    return (
        f"{format_breadcrumb('إربح المال', 'سحب أرباح الإحالة')}\n\n"
        f"<b>طريقة السحب:</b> {escape(method.breadcrumb_label)}\n"
        f"الأرباح المؤكدة المتاحة: <b>{format_dh(referral_balance)}</b>\n"
        "<i>لا يشمل التقدير المعلّق.</i>\n"
        f"الحد الأدنى للسحب: <b>{format_dh(MIN_REFERRAL_WITHDRAW_DH)}</b>\n\n"
        "اتبع التعليمات في الرسالة أدناه لإكمال الطلب."
    )


def _format_referral_withdraw_amount_step_prompt(
    method: PaymentMethod,
    user_id: int,
    *,
    crypto_flow: bool = False,
) -> str:
    user = get_user(user_id)
    referral_balance = float((user or {}).get("referral_balance") or 0.0)
    if crypto_flow:
        network_key = ""
        option = get_crypto_withdraw_option(network_key) if network_key else None
        return format_crypto_withdraw_amount_prompt(
            method_label=method.breadcrumb_label,
            available_dh=referral_balance,
            min_dh=MIN_REFERRAL_WITHDRAW_DH,
            option=option,
        )
    return (
        "<b>الخطوة 1 من 2: المبلغ</b>\n\n"
        "أرسل المبلغ الذي تريد سحبه بالأرقام فقط.\n"
        f"الحد الأدنى: <b>{format_dh(MIN_REFERRAL_WITHDRAW_DH)}</b> — "
        f"المتاح: <b>{format_dh(referral_balance)}</b>.\n"
        f"طريقة السحب: <b>{escape(method.breadcrumb_label)}</b>."
    )


def _format_referral_withdraw_amount_saved_summary(method: PaymentMethod, amount: float) -> str:
    return (
        f"{format_breadcrumb('إربح المال', 'سحب أرباح الإحالة')}\n\n"
        f"<b>طريقة السحب:</b> {escape(method.breadcrumb_label)}\n"
        f"<b>المبلغ:</b> {format_dh(amount)}\n\n"
        "اتبع التعليمات في الرسالة أدناه لإرسال بيانات السحب."
    )


def _format_referral_withdraw_details_step_prompt(method: PaymentMethod) -> str:
    return f"<b>الخطوة 2 من 2: بيانات السحب</b>\n\n{_withdraw_details_instruction(method)}"


def _format_referral_withdraw_review(
    method: PaymentMethod,
    amount: float,
    details: dict[str, str],
) -> str:
    lines = [
        f"{format_breadcrumb('إربح المال', 'مراجعة سحب الإحالة')}",
        "",
        "<b>راجع طلب سحب أرباح الإحالة قبل التأكيد</b>",
        "",
        f"طريقة السحب: <b>{escape(method.breadcrumb_label)}</b>",
        f"المبلغ: <b>{format_dh(amount)}</b>",
    ]
    if is_crypto_withdraw_details(details):
        lines.append("")
        lines.extend(_format_referral_withdraw_details_section(details, amount=amount))
        lines.extend(["", CRYPTO_WITHDRAW_EXCHANGE_NOTICE])
    else:
        lines.append(
            f"{_withdraw_details_label(method)}: "
            f"<code>{escape(format_withdraw_details_display(details))}</code>"
        )
    lines.extend(
        [
            "",
            "سيتم حجز المبلغ من أرباح الإحالة إلى حين معالجة الطلب يوم الجمعة.",
        ]
    )
    return "\n".join(lines)


async def _render_referral_home(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    *,
    register_living: bool = False,
) -> None:
    if not callback.from_user:
        return
    user_id = await _prepare_referral_nav(callback, state, bot, purge_transcript=True)
    if user_id is None:
        return
    await state.clear()
    user = get_user(user_id)
    if not user:
        await callback.answer("تعذر جلب البيانات", show_alert=True)
        return
    pending = sum_referrer_pending_commission_estimate(user_id)
    bot_username = await _telegram_bot_username(bot)
    referral_level = int(user.get("referral_level") or 1)
    text = _format_referral_main_text(
        referral_level=referral_level,
        referral_balance=float(user["referral_balance"] or 0.0),
        pending_estimation=pending,
        bot_username=bot_username,
        user_id=user_id,
    )
    markup = build_referral_main_menu(referral_level=referral_level)
    await _edit_referral_from_callback(
        callback,
        state,
        bot,
        text=text,
        reply_markup=markup,
        register_living=register_living,
    )


@router.callback_query(F.data.in_({"menu:referral", "referral_main", "referral:menu"}))
async def referral_main_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    register = callback.data == "menu:referral"
    await _render_referral_home(callback, state, bot, register_living=register)
    await callback.answer()


@router.callback_query(F.data == "referral:upgrade")
async def referral_upgrade_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user:
        return
    user_id = await _prepare_referral_nav(callback, state, bot, purge_transcript=True)
    if user_id is None:
        return
    await state.clear()
    user = get_user(user_id)
    if not user:
        await callback.answer("تعذر جلب البيانات", show_alert=True)
        return
    referral_level = int(user.get("referral_level") or 1)
    if referral_level >= MAX_REFERRAL_LEVEL:
        await _render_referral_home(callback, state, bot)
        await callback.answer()
        return
    active_users = count_active_referred_users(user_id)
    text = _format_referral_upgrade_text(
        referral_level=referral_level,
        active_users=active_users,
        referral_earned_total=float(user["referral_earned_total"] or 0.0),
    )
    await _edit_referral_from_callback(
        callback,
        state,
        bot,
        text=text,
        reply_markup=build_referral_upgrade_menu(),
    )
    await callback.answer()


@router.callback_query(
    F.data.startswith("referral_list:") | F.data.startswith("referral:list:")
)
async def referral_list_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user:
        return
    user_id = await _prepare_referral_nav(callback, state, bot, purge_transcript=True)
    if user_id is None:
        return
    await state.clear()
    parts = (callback.data or "").split(":")
    try:
        page = int(parts[-1])
    except ValueError:
        page = 0
    if page < 0:
        page = 0

    rows = list_referral_invitees_summaries(user_id)
    total = len(rows)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page >= total_pages:
        page = total_pages - 1
    start = page * PAGE_SIZE
    chunk = rows[start : start + PAGE_SIZE]

    lines: list[str] = [
        format_breadcrumb("إربح المال", "قائمة الإحالات"),
        "",
        f"قائمة أصدقائك (الإجمالي: <b>{count_invited_users(user_id)}</b> شخص):",
        "",
    ]
    if not chunk:
        lines.append("لا يوجد أشخاص في قائمتك بعد. شارك رابطك لبدء جمع الأرباح!")
    else:
        for idx, row in enumerate(chunk, start=start + 1):
            name = row.get("telegram_name") or f"مستخدم {row['user_id']}"
            earned = float(row.get("earned") or 0.0)
            pending = float(row.get("pending") or 0.0)
            lines.append(
                _format_invitee_line(
                    index=idx,
                    name=str(name),
                    earned=earned,
                    pending=pending,
                )
            )

    text = "\n".join(lines)
    has_next = page < total_pages - 1
    markup = build_referral_list_menu(page=page, has_next=has_next)
    await _edit_referral_from_callback(callback, state, bot, text=text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "referral:withdraw")
async def referral_withdraw(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user:
        return
    user_id = await _prepare_referral_nav(callback, state, bot, purge_transcript=True)
    if user_id is None:
        return
    await state.clear()
    active = get_active_withdrawal(user_id, withdrawal_type="referral")
    text = _format_referral_withdraw_intro(user_id)
    markup = (
        build_referral_withdraw_pending_menu(active["id"])
        if active is not None
        else build_referral_withdraw_methods_menu(PAYMENT_METHODS)
    )
    await _edit_referral_from_callback(callback, state, bot, text=text, reply_markup=markup)
    await callback.answer()


@router.callback_query(F.data == "referral:transfer")
async def referral_transfer_start(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    await clear_last_prompt(callback.message, state)
    await reset_flow_transcript(state)
    user = get_user(callback.from_user.id)
    referral_balance = float((user or {}).get("referral_balance") or 0.0)
    if referral_balance <= 0:
        await callback.answer("لا توجد أرباح إحالة متاحة للتحويل حالياً.", show_alert=True)
        return
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    await state.set_state(ReferralFlow.transfer_amount)
    summary = _format_referral_transfer_summary(referral_balance)
    await _edit_referral_from_callback(
        callback,
        state,
        bot,
        text=summary,
        reply_markup=None,
        sync_nav=False,
    )
    await _sync_referral_nav_anchor(bot, state, user_id, None)
    await _send_referral_step_prompt(
        bot,
        state,
        user_id,
        chat_id,
        _format_referral_transfer_step_prompt(),
        "referral:menu",
    )
    await callback.answer()


@router.message(StateFilter(ReferralFlow.transfer_amount), F.text)
async def referral_transfer_amount_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user:
        return
    user_id = message.from_user.id
    chat_id = message.chat.id
    amount = _parse_amount(message.text or "")
    user = get_user(user_id)
    referral_balance = float((user or {}).get("referral_balance") or 0.0)
    nav = build_referral_step_nav()
    if amount is None:
        await _flash_referral_input_error(
            message,
            state,
            bot,
            "أرسل مبلغاً صحيحاً بالأرقام فقط. مثال: 25",
            restore_markup=nav,
        )
        return
    if amount > referral_balance:
        await _flash_referral_input_error(
            message,
            state,
            bot,
            f"المبلغ أكبر من الأرباح المؤكدة المتاحة. المتاح حالياً: <b>{format_dh(referral_balance)}</b>.",
            restore_markup=nav,
        )
        return

    ok = transfer_referral_balance_to_main(user_id, amount)
    updated = get_user(user_id)
    if not ok or not updated:
        await _flash_referral_input_error(
            message,
            state,
            bot,
            "تعذر تحويل الأرباح الآن. أعد المحاولة لاحقاً.",
            restore_markup=nav,
        )
        return

    await track_transcript_user_message(state, message)
    await delete_flow_step_prompt(bot, state, chat_id)
    text = (
        f"{format_breadcrumb('إربح المال', 'تحويل الأرباح إلى الرصيد')}\n\n"
        f"تم تحويل <b>{format_dh(amount)}</b> إلى رصيدك الرئيسي بنجاح.\n\n"
        f"أرباح الإحالة المتبقية: <b>{format_dh(updated['referral_balance'])}</b>\n"
        f"رصيدك الرئيسي الحالي: <b>{format_dh(updated['balance'])}</b>"
    )
    back_menu = build_referral_back_menu()
    await _edit_referral_screen(bot, state, user_id, text, back_menu)
    await acknowledge_then_focus_living_ui(
        bot,
        state,
        user_id,
        chat_id,
        ack_text="✅ تم التحويل — التفاصيل في الرسالة أعلاه",
        reply_to_message_id=message.message_id,
    )
    await set_living_nav_anchor(bot, state, user_id, chat_id, back_menu)
    await state.clear()


@router.callback_query(F.data.startswith("referral:withdraw:method:"))
async def referral_withdraw_method_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    method_key = (callback.data or "").split(":")[-1]
    method = PAYMENT_BY_KEY.get(method_key)
    if method is None:
        await callback.answer("طريقة السحب غير معروفة", show_alert=True)
        return
    if get_active_withdrawal(callback.from_user.id, withdrawal_type="referral") is not None:
        await callback.answer("لديك طلب سحب إحالة معلق بالفعل", show_alert=True)
        return
    user = get_user(callback.from_user.id)
    referral_balance = float((user or {}).get("referral_balance") or 0.0)
    if referral_balance < MIN_REFERRAL_WITHDRAW_DH:
        await callback.answer(
            f"الحد الأدنى لسحب أرباح الإحالة هو {format_dh(MIN_REFERRAL_WITHDRAW_DH)}.",
            show_alert=True,
        )
        return
    if method.kind == "crypto":
        from handlers.crypto_withdraw import show_crypto_withdraw_network_picker

        await show_crypto_withdraw_network_picker(
            callback,
            state,
            bot,
            method=method,
            flow_kind="referral",
            back_callback="referral:withdraw",
        )
        await callback.answer()
        return
    await clear_last_prompt(callback.message, state)
    await reset_flow_transcript(state)
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    await state.set_state(ReferralFlow.withdraw_amount)
    await state.update_data(referral_withdraw_method_key=method.key)
    summary = _format_referral_withdraw_method_summary(method, user_id)
    await _edit_referral_from_callback(
        callback,
        state,
        bot,
        text=summary,
        reply_markup=None,
        sync_nav=False,
    )
    await _sync_referral_nav_anchor(bot, state, user_id, None)
    await _send_referral_step_prompt(
        bot,
        state,
        user_id,
        chat_id,
        _format_referral_withdraw_amount_step_prompt(method, user_id),
        "referral:withdraw",
    )
    await callback.answer()


@router.message(StateFilter(ReferralFlow.withdraw_amount), F.text)
async def referral_withdraw_amount_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    method = PAYMENT_BY_KEY.get(str(data.get("referral_withdraw_method_key", "")))
    if method is None:
        await _finish_referral_flow(bot, state, message.from_user.id, message.chat.id)
        return
    user_id = message.from_user.id
    chat_id = message.chat.id
    nav = build_referral_withdraw_step_nav()
    amount = _parse_amount(message.text or "")
    user = get_user(user_id)
    referral_balance = float((user or {}).get("referral_balance") or 0.0)
    if amount is None:
        await _flash_referral_input_error(
            message,
            state,
            bot,
            "أرسل مبلغاً صحيحاً بالأرقام فقط. مثال: 50",
            restore_markup=nav,
        )
        return
    if amount < MIN_REFERRAL_WITHDRAW_DH:
        await _flash_referral_input_error(
            message,
            state,
            bot,
            f"الحد الأدنى لسحب أرباح الإحالة هو <b>{format_dh(MIN_REFERRAL_WITHDRAW_DH)}</b>.",
            restore_markup=nav,
        )
        return
    if amount > referral_balance:
        await _flash_referral_input_error(
            message,
            state,
            bot,
            f"المبلغ أكبر من الأرباح المؤكدة المتاحة. المتاح حالياً: <b>{format_dh(referral_balance)}</b>.",
            restore_markup=nav,
        )
        return
    if get_active_withdrawal(user_id, withdrawal_type="referral") is not None:
        await _flash_referral_input_error(
            message,
            state,
            bot,
            "لديك طلب سحب إحالة معلق بالفعل.",
            restore_markup=nav,
        )
        return

    crypto_details_json = str(data.get("withdraw_crypto_details_json", ""))
    if method.kind == "crypto":
        if not crypto_details_json:
            await _flash_referral_input_error(
                message,
                state,
                bot,
                "انتهت جلسة اختيار الشبكة. ارجع واختر شبكة USDT من جديد.",
                restore_markup=nav,
            )
            return

    await track_transcript_user_message(state, message)
    await state.update_data(referral_withdraw_amount=amount)
    if method.kind == "crypto" and crypto_details_json:
        await state.set_state(ReferralFlow.withdraw_confirm)
        await state.update_data(referral_withdraw_details_json=crypto_details_json)
        details = safe_withdraw_details(crypto_details_json)
        review_text = _format_referral_withdraw_review(method, amount, details)
        confirm_kb = build_referral_withdraw_confirm_menu()
        await delete_flow_step_prompt(bot, state, chat_id)
        await _edit_referral_screen(bot, state, user_id, review_text, confirm_kb)
        await set_living_nav_anchor(bot, state, user_id, chat_id, confirm_kb)
        await acknowledge_then_focus_living_ui(
            bot,
            state,
            user_id,
            chat_id,
            ack_text="✅ تم حفظ المبلغ — راجع التفاصيل واضغط تأكيد",
            reply_to_message_id=message.message_id,
        )
        return
    await state.set_state(ReferralFlow.withdraw_details)
    summary = _format_referral_withdraw_amount_saved_summary(method, amount)
    await _edit_referral_screen(bot, state, user_id, summary, None)
    await _sync_referral_nav_anchor(bot, state, user_id, None)
    await _send_referral_step_prompt(
        bot,
        state,
        user_id,
        chat_id,
        _format_referral_withdraw_details_step_prompt(method),
        "referral:withdraw",
    )


@router.message(StateFilter(ReferralFlow.withdraw_details), F.text)
async def referral_withdraw_details_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    method = PAYMENT_BY_KEY.get(str(data.get("referral_withdraw_method_key", "")))
    amount = to_float(data.get("referral_withdraw_amount", 0))
    if method is None or amount <= 0:
        await _finish_referral_flow(bot, state, message.from_user.id, message.chat.id)
        return
    if method.kind == "crypto":
        nav = build_referral_withdraw_step_nav()
        await _flash_referral_input_error(
            message,
            state,
            bot,
            "للسحب بالكريبتو اختر الشبكة من القائمة ثم أرسل العنوان — لا تُدخل البيانات هنا.",
            restore_markup=nav,
        )
        return
    details, error = _parse_withdraw_details(method, message.text or "")
    nav = build_referral_withdraw_step_nav()
    if details is None:
        await _flash_referral_input_error(
            message,
            state,
            bot,
            error,
            restore_markup=nav,
        )
        return

    user_id = message.from_user.id
    chat_id = message.chat.id
    await track_transcript_user_message(state, message)
    await state.set_state(ReferralFlow.withdraw_confirm)
    await state.update_data(referral_withdraw_details_json=json.dumps(details, ensure_ascii=False))
    review_text = _format_referral_withdraw_review(method, amount, details)
    confirm_kb = build_referral_withdraw_confirm_menu()
    await delete_flow_step_prompt(bot, state, chat_id)
    await _edit_referral_screen(bot, state, user_id, review_text, confirm_kb)
    await set_living_nav_anchor(bot, state, user_id, chat_id, confirm_kb)
    await acknowledge_then_focus_living_ui(
        bot,
        state,
        user_id,
        chat_id,
        ack_text="✅ تم حفظ بيانات السحب — راجع التفاصيل في الرسالة أعلاه واضغط تأكيد",
        reply_to_message_id=message.message_id,
    )


@router.callback_query(StateFilter(ReferralFlow.withdraw_confirm), F.data == "referral:withdraw:back_details")
async def referral_withdraw_back_details_handler(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    if not callback.from_user or not callback.message:
        return
    data = await state.get_data()
    method = PAYMENT_BY_KEY.get(str(data.get("referral_withdraw_method_key", "")))
    amount = to_float(data.get("referral_withdraw_amount", 0))
    if method is None or amount <= 0:
        await referral_withdraw(callback, state, bot)
        return
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    if method.kind == "crypto":
        network_key = str(data.get("withdraw_crypto_network_key", ""))
        from handlers.crypto_withdraw import _format_crypto_withdraw_living_summary
        from utils.crypto_usdt import format_crypto_withdraw_address_prompt

        option = get_crypto_withdraw_option(network_key)
        if option is None:
            await referral_withdraw(callback, state, bot)
            await callback.answer()
            return
        saved_details = safe_withdraw_details(str(data.get("withdraw_crypto_details_json", "")))
        destination = str(saved_details.get("destination", "") or "") or None
        await state.set_state(ReferralFlow.withdraw_crypto_address)
        summary = _format_crypto_withdraw_living_summary(
            "referral",
            user_id,
            method,
            option,
            destination=destination,
        )
        await _edit_referral_from_callback(
            callback, state, bot, text=summary, reply_markup=None, sync_nav=False
        )
        await _sync_referral_nav_anchor(bot, state, user_id, None)
        await _send_referral_step_prompt(
            bot,
            state,
            user_id,
            chat_id,
            format_crypto_withdraw_address_prompt(option),
            "referral:withdraw:crypto:nets",
        )
        await callback.answer()
        return
    await state.set_state(ReferralFlow.withdraw_details)
    summary = _format_referral_withdraw_amount_saved_summary(method, amount)
    await _edit_referral_from_callback(
        callback,
        state,
        bot,
        text=summary,
        reply_markup=None,
        sync_nav=False,
    )
    await _sync_referral_nav_anchor(bot, state, user_id, None)
    await _send_referral_step_prompt(
        bot,
        state,
        user_id,
        chat_id,
        _format_referral_withdraw_details_step_prompt(method),
        "referral:withdraw",
    )
    await callback.answer()


@router.callback_query(StateFilter(ReferralFlow.withdraw_confirm), F.data == "referral:withdraw:confirm")
async def referral_withdraw_confirm_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    data = await state.get_data()
    method = PAYMENT_BY_KEY.get(str(data.get("referral_withdraw_method_key", "")))
    amount = to_float(data.get("referral_withdraw_amount", 0))
    details_json = str(data.get("referral_withdraw_details_json", "{}"))
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    if method is None or amount <= 0:
        await _finish_referral_flow(bot, state, user_id, chat_id)
        await callback.answer("انتهت الجلسة", show_alert=True)
        return
    user = get_user(user_id)
    referral_balance = float((user or {}).get("referral_balance") or 0.0)
    if amount < MIN_REFERRAL_WITHDRAW_DH or amount > referral_balance:
        await callback.answer(
            "تعذر التأكيد: تغيّرت أرباح الإحالة. أعد إنشاء الطلب.",
            show_alert=True,
        )
        return
    if get_active_withdrawal(user_id, withdrawal_type="referral") is not None:
        await callback.answer("لديك طلب سحب إحالة معلق بالفعل.", show_alert=True)
        return
    withdrawal_id = create_withdrawal_with_balance_hold(
        user_id,
        amount,
        method.ledger_name,
        details_json,
        withdrawal_type="referral",
    )
    if withdrawal_id is None:
        text = (
            f"{format_breadcrumb('إربح المال', 'سحب أرباح الإحالة')}\n\n"
            "<b>تعذر إنشاء طلب السحب.</b>\n"
            "تحقق من أرباح الإحالة المتاحة أو من وجود طلب سحب إحالة معلق بالفعل."
        )
        await _edit_referral_from_callback(
            callback,
            state,
            bot,
            text=text,
            reply_markup=build_referral_back_menu(),
        )
        await _finish_referral_flow(bot, state, user_id, chat_id)
        await callback.answer("تعذر إنشاء طلب السحب", show_alert=True)
        return
    text = (
        f"{format_breadcrumb('إربح المال', 'سحب أرباح الإحالة')}\n\n"
        "<b>تم إنشاء طلب سحب أرباح الإحالة بنجاح</b>\n"
        f"رقم الطلب: <code>#{withdrawal_id}</code>\n"
        f"المبلغ المحجوز: <b>{format_dh(amount)}</b>\n\n"
        "سيتم معالجة الطلب يوم الجمعة."
    )
    pending_menu = build_referral_withdraw_pending_menu(withdrawal_id)
    await _edit_referral_from_callback(
        callback,
        state,
        bot,
        text=text,
        reply_markup=pending_menu,
    )
    await _finish_referral_flow(bot, state, user_id, chat_id)
    from services.withdraw_admin_notify import notify_admin_new_withdrawal

    await notify_admin_new_withdrawal(
        bot,
        withdrawal_id=withdrawal_id,
        user_id=user_id,
        telegram_name=callback.from_user.full_name,
        amount=amount,
        method_label=method.ledger_name,
        details_json=details_json,
        withdrawal_type="referral",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("referral:withdraw:cancel:"))
async def referral_withdraw_cancel_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    try:
        withdrawal_id = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("طلب غير صالح", show_alert=True)
        return
    ok = cancel_pending_withdrawal(withdrawal_id, callback.from_user.id)
    if not ok:
        await callback.answer("تعذر إلغاء الطلب. قد يكون تمت معالجته سابقاً.", show_alert=True)
        return
    await _render_referral_home(callback, state, bot)
    await callback.answer("تم إلغاء طلب سحب الإحالة وإرجاع المبلغ")
