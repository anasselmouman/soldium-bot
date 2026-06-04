# -*- coding: utf-8 -*-
"""بوابة إضافة رصيد — واجهة حية وتأكيد الإيصالات."""

from __future__ import annotations

import json
import logging
import re
import time
from html import escape
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import (
    ADMIN_ID,
    DEBUG_AGENT_LOGS,
    MAX_PENDING_DEPOSITS_PER_USER,
    MAX_SINGLE_DEPOSIT_DH,
    MIN_CRYPTO_DEPOSIT_USDT,
    MIN_CRYPTO_WITHDRAW_DH,
    MIN_DEPOSIT_DH,
    MIN_PAYPAL_DEPOSIT_USD,
    RECHARGE_CREDIT_RATIO,
    USDT_TO_DH_RATE,
)
from database import (
    add_user,
    cancel_pending_withdrawal,
    count_user_deposits,
    count_user_pending_deposits,
    count_user_withdrawals,
    create_deposit,
    create_withdrawal_with_balance_hold,
    finalize_approved_deposit,
    get_active_withdrawal,
    get_deposit,
    get_user,
    get_user_withdrawable_amount,
    get_user_withdrawable_method_balances,
    get_user_deposits,
    get_user_withdrawals,
    recharge_code_in_use,
    update_pending_deposit_status,
)
from keyboards.payment import (
    build_admin_deposit_actions,
    build_admin_recharge_actions,
    build_bank_step_nav,
    build_deposit_history_menu,
    build_deposit_nav_footer,
    build_payment_methods_menu,
    build_recharge_code_step_nav,
    build_recharge_face_value_menu,
    build_recharge_telecom_menu,
    build_withdraw_confirm_menu,
    build_withdraw_history_menu,
    build_withdraw_methods_menu,
    build_withdraw_pending_screen_menu,
    build_withdraw_step_nav,
)
from utils.flow_transcript import (
    acknowledge_then_focus_living_ui,
    delete_flow_step_prompt,
    flash_step_prompt_error,
    get_flow_step_prompt_id,
    purge_flow_transcript,
    reset_flow_transcript,
    send_flow_step_prompt,
    track_transcript_user_message,
    transfer_nav_anchor,
)
from utils.fsm_prompt_cleanup import clear_last_prompt
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
from utils.living_ui import (
    delete_chat_message,
    edit_user_living_ui,
    get_living_ui,
    is_living_ui_message_id,
    register_living_ui_ids,
    register_living_ui_message,
)
from utils.telegram_ui import allow_new_message_fallback, safe_edit_message_text
from utils.money import (
    format_amount_2,
    format_dh,
    recharge_face_value_to_credit,
    to_float,
)
from utils.recharge_telecom import (
    RECHARGE_FACE_VALUES_DH,
    TELECOM_BY_KEY,
    TelecomOperator,
    is_recharge_ledger,
)
from utils.crypto_usdt import format_crypto_deposit_html
from utils.payment_banks import (
    CRYPTO_LEDGER_NAME,
    PAYPAL_LEDGER_NAME,
    PAYMENT_BY_KEY,
    PaymentMethod,
    get_payment_method_by_ledger,
    is_crypto_ledger,
    is_paypal_ledger,
)
from utils.smart_notifications import send_smart_notification
from utils.states import AdminFlow, DepositFlow, WithdrawFlow
from utils.ui_branding import screen_body

RECEIPT_ACK_TEXT = (
    "تم استلام الإيصال بنجاح! ⏳ يتم الآن معالجة الطلب من قبل الإدارة."
)
RECHARGE_CODE_ACK_TEXT = (
    "تم استلام رمز التعبئة بنجاح! ⏳ جاري التحقق من قبل الإدارة."
)
RECHARGE_CODE_MIN_LEN = 14
RECHARGE_CODE_MAX_LEN = 16
_RECHARGE_CODE_PATTERN = re.compile(rf"^\d{{{RECHARGE_CODE_MIN_LEN},{RECHARGE_CODE_MAX_LEN}}}$")
RECHARGE_CODE_INVALID_TEXT = (
    "⚠️ عذراً! رمز التعبئة غير صالح. يجب أن يتكون الرمز من أرقام فقط "
    f"(بين {RECHARGE_CODE_MIN_LEN} و {RECHARGE_CODE_MAX_LEN} رقماً) بدون أي مسافات أو حروف.\n\n"
    "يرجى التأكد من الرمز وإرساله مجدداً بشكل صحيح 👇"
)

DEPOSIT_FLASH_RESTORE_KEY = "deposit_flash_restore_text"
PENDING_DEPOSITS_LIMIT_TEXT = (
    f"⚠️ لديك بالفعل <b>{MAX_PENDING_DEPOSITS_PER_USER}</b> طلبات شحن قيد المراجعة.\n"
    "انتظر حتى تُعالج إحداها قبل إرسال طلب جديد."
)
RECHARGE_CODE_USED_TEXT = (
    "⚠️ هذا الرمز مُسجَّل مسبقاً (قيد المراجعة أو مُعتمد). "
    "تحقق من الرمز أو تواصل مع الدعم إن كنت تعتقد أن هناك خطأ."
)
_DEBUG_LOG_PATH = Path(__file__).resolve().parent.parent / "debug-e94964.log"
_DEBUG_SESSION_LOG_PATH = Path(__file__).resolve().parent.parent / "debug-2eac5b.log"


def _deposit_back_debug_log(
    hypothesis_id: str,
    location: str,
    message: str,
    data: dict,
    *,
    run_id: str = "verify",
) -> None:
    # region agent log
    try:
        with _DEBUG_SESSION_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "2eac5b",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                        "runId": run_id,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # endregion


def _withdraw_debug_log(location: str, message: str, data: dict) -> None:
    # region agent log
    if not DEBUG_AGENT_LOGS:
        return
    try:
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "e94964",
                        "hypothesisId": "H-withdraw",
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # endregion


router = Router()
logger = logging.getLogger(__name__)

_LOCAL_DH_EXCHANGE_REMINDER = (
    "⚠️ <b>تذكير مالي:</b> رصيدك داخل البوت يُحسب بالدرهم المغربي (DH). "
    "عند التحويل من عملة أخرى (USDT/USD)، يُحوّل الأدمن المبلغ إلى درهم "
    "حسب سعر الصرف لحظة المعالجة قبل إضافته لرصيدك."
)

_DEPOSIT_NOTES_HTML = (
    "💡 <b>ملاحظات:</b>\n"
    "- رصيدك وبياناتك تبقى محفوظة طالما تستخدم <b>نفس حسابك</b> على تيليجرام "
    "(حتى لو حذفت المحادثة أو حظرت البوت أو أعدت تثبيت التطبيق).\n"
    "- رصيدك قابل للسحب دائماً؛ تُعالَج السحوبات يوم الجمعة، "
    "والسحب متاح فقط لنفس الجهة البنكية التي أودعت منها."
)

_CRYPTO_USDT_EXCHANGE_NOTICE = (
    "💵 <b>سعر الصرف المعتمد:</b> يتم احتساب سعر صرف الدرهم المغربي (MAD) مقابل "
    "الدولار/USDT بناءً على (ثمن البيع في منصة Binance) في الوقت الحالي."
)


def _deposit_main_breadcrumb() -> str:
    return "🏠 <b>الرئيسي</b> &gt; <b>إضافة رصيد</b>"


def _deposit_method_breadcrumb(method: PaymentMethod) -> str:
    return f"🏠 <b>إضافة رصيد</b> &gt; <b>{escape(method.breadcrumb_label)}</b>"


def _ledger_method_for_approved_deposit(deposit_method: str) -> str:
    if is_paypal_ledger(deposit_method):
        return PAYPAL_LEDGER_NAME
    if is_crypto_ledger(deposit_method):
        return CRYPTO_LEDGER_NAME
    return deposit_method


def _deposit_approval_title(deposit_method: str) -> str:
    if is_paypal_ledger(deposit_method):
        return "PayPal"
    if is_crypto_ledger(deposit_method):
        return "الكريبتو"
    return "الشحن"


def _min_deposit_dh_for_method(deposit_method: str) -> float:
    if is_paypal_ledger(deposit_method):
        return to_float(MIN_PAYPAL_DEPOSIT_USD) * to_float(USDT_TO_DH_RATE)
    if is_crypto_ledger(deposit_method):
        return to_float(MIN_CRYPTO_DEPOSIT_USDT) * to_float(USDT_TO_DH_RATE)
    return to_float(MIN_DEPOSIT_DH)


def _validate_admin_deposit_amount(deposit_method: str, amount: float) -> str | None:
    """None إذا المبلغ صالح، وإلا نص الخطأ."""
    if amount > to_float(MAX_SINGLE_DEPOSIT_DH):
        return (
            f"⚠️ المبلغ يتجاوز الحد الأقصى المسموح "
            f"(<b>{format_dh(MAX_SINGLE_DEPOSIT_DH)}</b>)."
        )
    min_dh = _min_deposit_dh_for_method(deposit_method)
    if amount < min_dh:
        if is_paypal_ledger(deposit_method):
            label = f"PayPal ({int(MIN_PAYPAL_DEPOSIT_USD)} USD ≈ {format_dh(min_dh)})"
        elif is_crypto_ledger(deposit_method):
            label = f"Crypto ({int(MIN_CRYPTO_DEPOSIT_USDT)} USDT ≈ {format_dh(min_dh)})"
        else:
            label = format_dh(MIN_DEPOSIT_DH)
        return f"⚠️ الحد الأدنى للشحن عبر <b>{label}</b> هو <b>{format_dh(min_dh)}</b>."
    return None


def _parse_admin_amount(raw: str) -> float | None:
    text = raw.strip().replace(",", ".")
    if not text:
        return None
    try:
        amount = to_float(text)
    except Exception:
        return None
    return amount if amount > 0 else None


def _parse_recharge_face_value(raw: str) -> int | None:
    """قيمة تعبئة صحيحة: عدد صحيح ضمن القيم المدعومة فقط (بدون كسور)."""
    text = raw.strip().replace(",", ".")
    if not text:
        return None
    try:
        amount = to_float(text)
    except Exception:
        return None
    if amount <= 0 or amount != int(amount):
        return None
    face = int(amount)
    if face not in RECHARGE_FACE_VALUES_DH:
        return None
    return face


def _deposit_status_ar(status: str) -> str:
    st = str(status or "").strip().lower()
    if st == "pending":
        return "⏳ قيد المراجعة"
    if st == "rejected":
        return "❌ مرفوض"
    if st.startswith("approved:"):
        return "✅ مُعتمد"
    return "🔄 غير معروف"


def _format_deposit_history_text(user_id: int, *, show_all: bool = False) -> str:
    total = count_user_deposits(user_id)
    preview_limit = 8
    max_all_limit = 50
    limit = max_all_limit if show_all else preview_limit
    rows = get_user_deposits(user_id, limit=limit)
    lines = [
        "🏠 <b>الرئيسي</b> &gt; <b>حسابي وطلباتي</b> &gt; <b>سجل الشحن</b>",
        "",
        "<b>سجل الشحن</b>",
    ]
    if not rows:
        lines.append("لا توجد عمليات شحن مسجّلة بعد.")
        return "\n".join(lines)
    lines.append("")
    for row in rows:
        method = escape(str(row["method"]))
        st = _deposit_status_ar(str(row["status"]))
        amount = float(row["amount"])
        amount_line = (
            f"• المبلغ: <b>{format_dh(amount)}</b>"
            if amount > 0 or str(row["status"]).startswith("approved:")
            else "• المبلغ: <i>يُحدَّد عند الاعتماد</i>"
        )
        lines.extend(
            [
                "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
                f"• رقم الطلب: <code>#{row['id']}</code>",
                f"• الطريقة: <b>{method}</b>",
                amount_line,
                f"• الحالة: {st}",
            ]
        )
    if not show_all and total > preview_limit:
        lines.extend(["", "<i>لعرض المزيد استخدم زر إظهار كل السجل.</i>"])
    if show_all and total > max_all_limit:
        hidden = total - max_all_limit
        lines.extend(
            [
                "",
                f"<i>يُعرض آخر <b>{max_all_limit}</b> عملية فقط. "
                f"يوجد <b>{hidden}</b> عملية أقدم غير معروضة هنا.</i>",
            ]
        )
    return "\n".join(lines)


async def _reject_pending_deposit(
    deposit_id: int,
    *,
    user_id: int,
    user_notice: str,
    admin_notice: str,
    bot: Bot,
    admin_message: Message,
) -> bool:
    if not update_pending_deposit_status(deposit_id, "rejected"):
        return False
    await send_smart_notification(bot, user_id, user_notice)
    await admin_message.answer(admin_notice, parse_mode="HTML")
    return True


def _format_deposit_gateway_text() -> str:
    return screen_body(
        _deposit_main_breadcrumb(),
        "مرحباً بك في بوابة الدفع الخاصة بـ <b>SOLDIUM</b> 🛡️",
        "",
        "أضف رصيدك الآن وانطلق في تطوير حساباتك الرقمية بأسعار الجملة الحصرية!",
        "",
        _DEPOSIT_NOTES_HTML,
        "",
        "اختر وسيلة الدفع التي تناسبك من الأسفل:",
        (
            f"(الحد الأدنى للشحن: <b>{MIN_DEPOSIT_DH}</b> دراهم للبنوك والتطبيقات، "
            f"<b>{int(MIN_PAYPAL_DEPOSIT_USD)}</b> USD لـ PayPal، "
            f"<b>{int(MIN_CRYPTO_DEPOSIT_USDT)}</b> USDT للكريبتو)"
        ),
    )


def _format_bank_transfer_text(method: PaymentMethod) -> str:
    holder = escape(method.holder_name)
    account = escape(method.account_number or "")
    bank_name = escape(method.bank_title)
    return screen_body(
        _deposit_method_breadcrumb(method),
        "يرجى تحويل المبلغ الذي تود شحنه إلى الحساب التالي:",
        "",
        f"البنك: <b>{bank_name}</b>",
        "",
        f"الاسم: <code>{holder}</code> (اضغط للنسخ)",
        "",
        f"رقم الحساب: <code>{account}</code> (اضغط للنسخ)",
        "",
        (
            f"⚠️ <b>الخطوة التالية:</b> بعد إتمام عملية التحويل (الحد الأدنى {MIN_DEPOSIT_DH} دراهم)، "
            "قم بإرسال صورة الإيصال (Screenshot) هنا في هذه المحادثة مباشرة ليتم تأكيد رصيدك."
        ),
    )


def _format_cashplus_text(method: PaymentMethod) -> str:
    phone = escape(method.phone or "")
    holder = escape(method.holder_name)
    return screen_body(
        "<b>إضافة رصيد عبر كاش بلوس (CashPlus) 💸</b>",
        "",
        "يمكنك إرسال المبلغ من هاتفك عبر تطبيق كاش بلوس، أو التوجه لأقرب وكالة كاش بلوس.",
        "إذا ذهبت للوكالة، أعطِ الموظف المعلومات التالية واطلب منه (إرسال المبلغ إلى التطبيق):",
        "",
        f"رقم الهاتف: <code>{phone}</code> (اضغط للنسخ)",
        "",
        f"الاسم: <code>{holder}</code> (اضغط للنسخ)",
        "",
        (
            "⚠️ <b>الخطوة التالية:</b> بعد الإرسال، قم بتصوير الوصل (الإيصال) الذي أعطاه لك الموظف، "
            "أو خذ لقطة شاشة (Screenshot) من التطبيق، وأرسل الصورة هنا في هذه المحادثة مباشرة."
        ),
        f"(الحد الأدنى للشحن: {MIN_DEPOSIT_DH} دراهم)",
    )


def _format_wafacash_text(method: PaymentMethod) -> str:
    phone = escape(method.phone or "")
    holder = escape(method.holder_name)
    return screen_body(
        "<b>إضافة رصيد عبر وفاكاش (Wafacash) 🪙</b>",
        "",
        "يمكنك إرسال المبلغ من هاتفك عبر تطبيق وفاكاش، أو التوجه لأقرب وكالة وفاكاش.",
        "إذا ذهبت للوكالة، أعطِ الموظف المعلومات التالية واطلب منه (إرسال المبلغ إلى التطبيق):",
        "",
        f"رقم الهاتف: <code>{phone}</code> (اضغط للنسخ)",
        "",
        f"الاسم: <code>{holder}</code> (اضغط للنسخ)",
        "",
        (
            "⚠️ <b>الخطوة التالية:</b> بعد الإرسال، قم بتصوير الوصل (الإيصال) الذي أعطاه لك الموظف، "
            "أو خذ لقطة شاشة (Screenshot) من التطبيق، وأرسل الصورة هنا في هذه المحادثة مباشرة."
        ),
        f"(الحد الأدنى للشحن: {MIN_DEPOSIT_DH} دراهم)",
    )


def _format_paypal_text(method: PaymentMethod) -> str:
    email = escape(method.paypal_email or "")
    return screen_body(
        "<b>إضافة رصيد عبر بايبال (PayPal) 🅿️</b>",
        "",
        "قم بإرسال المبلغ المراد شحنه (بالدولار USD) إلى البريد الإلكتروني التالي:",
        "",
        f"الإيميل: <code>{email}</code> (اضغط للنسخ)",
        "",
        "<b>🛑 شروط هامة جداً لحماية رصيدك:</b>",
        "",
        f"الحد الأدنى للشحن: هو {int(MIN_PAYPAL_DEPOSIT_USD)} دولار.",
        "",
        (
            "<b>طريقة الإرسال (هام جداً):</b> يجب اختيار الإرسال كـ "
            "(أصدقاء وعائلة / Friends &amp; Family) لتجنب الاقتطاعات."
        ),
        "",
        (
            "<b>الاقتطاعات:</b> إذا قمت بالإرسال كـ (سلع وخدمات / Goods &amp; Services)، "
            "سيتم خصم عمولة بايبال من الرصيد الذي سيصلنا، وسنشحن لك المبلغ الصافي الذي وصلنا فقط."
        ),
        "",
        (
            "⚠️ <b>الخطوة التالية:</b> بعد إتمام الإرسال، قم بأخذ لقطة شاشة (Screenshot) واضحة "
            "للعملية وأرسلها هنا في المحادثة مباشرة ليتم مراجعتها وتأكيد رصيدك."
        ),
    )


def _format_crypto_text(method: PaymentMethod) -> str:
    return screen_body(
        _deposit_method_breadcrumb(method),
        "",
        format_crypto_deposit_html(binance_pay_id=method.binance_pay_id or ""),
    )


def _format_payment_detail_text(method: PaymentMethod) -> str:
    if method.key == "cashplus":
        return _format_cashplus_text(method)
    if method.key == "wafacash":
        return _format_wafacash_text(method)
    if method.kind == "crypto":
        return _format_crypto_text(method)
    if method.kind == "paypal":
        return _format_paypal_text(method)
    return _format_bank_transfer_text(method)


async def _set_deposit_flash_restore(state: FSMContext, text: str) -> None:
    await state.update_data(**{DEPOSIT_FLASH_RESTORE_KEY: text})


async def _ensure_payment_living(callback: CallbackQuery, state: FSMContext) -> int | None:
    if not callback.from_user or not callback.message:
        return None
    await register_living_ui_message(
        state, callback.message, user_id=callback.from_user.id
    )
    return callback.from_user.id


async def _edit_payment_living(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    text: str,
    reply_markup: object | None,
    *,
    message: Message | None = None,
) -> bool:
    """تعديل رسالة الواجهة الحية — لا يعيد تسجيل callback.message كحية."""
    living_chat, living_id, _ = await get_living_ui(state, user_id)
    if await edit_user_living_ui(bot, state, user_id, text, reply_markup):
        return True
    if message is not None and is_living_ui_message_id(user_id, message.message_id):
        try:
            await safe_edit_message_text(
                message, text, reply_markup=reply_markup, parse_mode="HTML", bot=bot
            )
            return True
        except TelegramBadRequest:
            pass
    chat_id = living_chat or (message.chat.id if message else None)
    if chat_id is None:
        return False
    if living_chat is not None and living_id is not None:
        await delete_chat_message(bot, living_chat, living_id)
    try:
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
    except TelegramBadRequest:
        return False
    if sent is not None:
        await register_living_ui_ids(
            state,
            user_id,
            sent.chat.id,
            sent.message_id,
            has_photo=bool(sent.photo),
        )
        return True
    return False


async def _sync_payment_nav_anchor(
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


async def _edit_payment_from_callback(
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
        await _ensure_payment_living(callback, state)
    fallback_message = callback.message
    if fallback_message is not None:
        step_id = await get_flow_step_prompt_id(state)
        if step_id == fallback_message.message_id:
            fallback_message = None
    ok = await _edit_payment_living(
        bot,
        state,
        user_id,
        text,
        reply_markup,
        message=fallback_message,
    )
    if ok and sync_nav:
        await _sync_payment_nav_anchor(bot, state, user_id, reply_markup)
    return ok


async def _send_payment_step_prompt(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
    text: str,
    back_callback: str,
) -> int | None:
    from keyboards.orders import build_flow_navigation_keyboard

    nav = build_flow_navigation_keyboard(back_callback)
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
    await _set_deposit_flash_restore(state, text)
    return step_id


async def _flash_deposit_input_error(
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
    restore_text = str(data.get(DEPOSIT_FLASH_RESTORE_KEY, "")).strip()
    if not restore_text:
        restore_text = "<b>أعد الإدخال من جديد:</b>"
    markup = restore_markup or build_bank_step_nav()
    await flash_step_prompt_error(
        bot,
        state,
        message.chat.id,
        error_text=error_text,
        restore_text=restore_text,
        restore_markup=markup,
        user_message=message,
    )


async def _finish_payment_flow(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
) -> None:
    await purge_flow_transcript(bot, state, user_id, chat_id)
    await state.clear()


async def _render_deposit_gateway(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    *,
    clear_receipt_state: bool = True,
    register_living: bool = False,
) -> None:
    if not callback.message or not callback.from_user:
        return
    user_id = callback.from_user.id
    await clear_last_prompt(callback.message, state)
    if clear_receipt_state:
        await purge_flow_transcript(
            bot, state, user_id, callback.message.chat.id
        )
        await state.clear()
    text = _format_deposit_gateway_text()
    await _edit_payment_from_callback(
        callback,
        state,
        bot,
        text=text,
        reply_markup=build_payment_methods_menu(),
        register_living=register_living,
    )


def _withdraw_main_breadcrumb() -> str:
    return "🏠 <b>الرئيسي</b> &gt; <b>حسابي وطلباتي</b> &gt; <b>سحب الرصيد</b>"


def _withdraw_history_breadcrumb() -> str:
    return "🏠 <b>الرئيسي</b> &gt; <b>حسابي وطلباتي</b> &gt; <b>سجل السحب</b>"


def _withdraw_status_ar(status: str) -> str:
    mapping = {
        "pending": "⏳ معلق بانتظار معالجة الجمعة",
        "canceled": "❌ ملغي وتم إرجاع المبلغ",
        "paid": "✅ تم الدفع",
        "completed": "✅ تم الدفع",
        "rejected": "🚫 مرفوض",
    }
    return mapping.get(str(status or "").strip().lower(), "🔄 قيد المراجعة")


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


def _withdrawable_methods_for_user(user_id: int) -> list[PaymentMethod]:
    methods: list[PaymentMethod] = []
    seen: set[str] = set()
    for ledger_name in get_user_withdrawable_method_balances(user_id).keys():
        method = get_payment_method_by_ledger(ledger_name)
        if method is None or method.key in seen:
            continue
        seen.add(method.key)
        methods.append(method)
    return methods


def _withdrawable_amount_for_method(user_id: int, method: PaymentMethod) -> float:
    return get_user_withdrawable_amount(user_id, method.ledger_name)


def _format_withdraw_intro_text(user_id: int, methods: list[PaymentMethod]) -> str:
    user = get_user(user_id)
    balance = format_dh(user["balance"]) if user else format_dh(0)
    lines = [
        _withdraw_main_breadcrumb(),
        "",
        "<b>طلب سحب الرصيد</b>",
        "",
        "تتم معالجة طلبات السحب يوم الجمعة فقط.",
        "يجب إرسال طلب السحب قبل يوم الجمعة. إذا وصل الطلب متأخراً فقد تنتظر إلى الجمعة القادمة.",
        "",
        f"رصيدك الحالي: <b>{balance}</b>",
        "",
        "يمكنك السحب فقط عبر نفس الطرق التي أودعت منها سابقاً وتم قبولها، وبحد أقصى المتبقي لكل طريقة بعد خصم السحوبات السابقة منها.",
        "إذا كان إيداعك السابق عبر بطاقات التعبئة فقط، فلن تظهر لك طريقة سحب لأن التعبئة لا تعتبر قناة دفع قابلة للإرجاع النقدي.",
    ]
    if methods:
        lines.extend(["", "اختر طريقة السحب من الأسفل:"])
    else:
        lines.extend(
            [
                "",
                "<b>لا توجد لديك طريقة سحب متاحة حالياً.</b>",
                "قم بإيداع ناجح عبر بنك أو تطبيق مالي أو Binance/Crypto أو PayPal أولاً، ثم عد إلى هذه الصفحة.",
            ]
        )
    return screen_body(*lines)


def _format_withdraw_details_section(
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
    return [f"<code>{escape(format_withdraw_details_display(details))}</code>"]


def _format_active_withdrawal_block(withdrawal: dict) -> list[str]:
    details = safe_withdraw_details(withdrawal.get("details_json"))
    amount = float(withdrawal.get("amount") or 0)
    lines = [
        "<b>طلب السحب المعلق حالياً</b>",
        f"• رقم الطلب: <code>#{withdrawal['id']}</code>",
        f"• الطريقة: <b>{escape(str(withdrawal['method']))}</b>",
        f"• المبلغ: <b>{format_dh(withdrawal['amount'])}</b>",
        f"• الحالة: {_withdraw_status_ar(str(withdrawal['status']))}",
        f"• تاريخ الطلب: <code>{escape(str(withdrawal['created_at']))}</code>",
    ]
    if details:
        if is_crypto_withdraw_details(details):
            lines.append("• <b>بيانات الاستلام:</b>")
            lines.extend(_format_withdraw_details_section(details, amount=amount))
        else:
            lines.append(
                f"• معلومات السحب: <code>{escape(format_withdraw_details_display(details))}</code>"
            )
    return lines


def _format_withdraw_pending_screen_text(user_id: int) -> str:
    active = get_active_withdrawal(user_id)
    if active is None:
        return screen_body(
            _withdraw_main_breadcrumb(),
            "",
            "<b>لا يوجد طلب سحب معلق حالياً.</b>",
        )
    lines = [
        _withdraw_main_breadcrumb(),
        "",
        "<b>سحب الرصيد</b>",
        "",
        "لديك طلب سحب قيد المعالجة. لا يمكن إنشاء طلب جديد حتى تلغي الطلب الحالي.",
        "للاطلاع على الطلبات السابقة استخدم سجل السحب من حسابي وطلباتي.",
        "",
        *_format_active_withdrawal_block(active),
    ]
    return screen_body(*lines)


def _format_withdraw_method_summary(method: PaymentMethod, user_id: int) -> str:
    user = get_user(user_id)
    balance = format_dh(user["balance"]) if user else format_dh(0)
    method_available = _withdrawable_amount_for_method(user_id, method)
    return screen_body(
        _withdraw_main_breadcrumb(),
        "",
        f"<b>طريقة السحب:</b> {escape(method.breadcrumb_label)}",
        f"رصيدك الحالي: <b>{balance}</b>",
        f"المتاح للسحب عبر هذه الطريقة: <b>{format_dh(method_available)}</b>",
        "",
        "اتبع التعليمات في الرسالة أدناه لإكمال الطلب.",
    )


def _format_withdraw_amount_step_prompt(
    method: PaymentMethod,
    user_id: int,
    *,
    crypto_flow: bool = False,
) -> str:
    method_available = _withdrawable_amount_for_method(user_id, method)
    if crypto_flow:
        data_option = None
        return format_crypto_withdraw_amount_prompt(
            method_label=method.breadcrumb_label,
            available_dh=method_available,
            min_dh=MIN_CRYPTO_WITHDRAW_DH,
            option=data_option,
        )
    return (
        "<b>الخطوة 1 من 2: المبلغ</b>\n\n"
        "أرسل المبلغ الذي تريد سحبه بالأرقام فقط.\n"
        "يجب أن يكون رصيدك مساوياً للمبلغ أو أكبر منه.\n"
        f"الحد الأقصى المتاح عبر {escape(method.breadcrumb_label)} هو "
        f"<b>{format_dh(method_available)}</b>."
    )


def _format_withdraw_amount_saved_summary(method: PaymentMethod, amount: float) -> str:
    return screen_body(
        _withdraw_main_breadcrumb(),
        "",
        f"<b>طريقة السحب:</b> {escape(method.breadcrumb_label)}",
        f"<b>المبلغ:</b> {format_dh(amount)}",
        "",
        "اتبع التعليمات في الرسالة أدناه لإرسال بيانات السحب.",
    )


def _withdraw_details_instruction(method: PaymentMethod) -> str:
    if method.kind == "bank":
        detail = (
            "أرسل الاسم الكامل ورقم الحساب البنكي في نفس الرسالة، كل واحد في سطر منفصل.\n"
            "مثال:\n"
            "<code>ANASS EL MOUMAN\n"
            "123456789</code>"
        )
    elif method.kind == "cash":
        detail = (
            "أرسل رقم الهاتف المرتبط بتطبيق السحب.\n"
            "يجب أن يكون لديك تطبيق هذه الخدمة. لا يمكن إرسال المال إلى الوكالة من هنا.\n"
            "إذا كنت تحتاج الإرسال للوكالة، راسل الدعم أولاً."
        )
    elif method.kind == "crypto":
        detail = "أرسل Binance Pay ID أو عنوان المحفظة الذي تريد استقبال المبلغ عليه."
    elif method.kind == "paypal":
        detail = "أرسل إيميل PayPal الذي تريد استقبال المبلغ عليه."
    else:
        detail = "أرسل معلومات السحب المطلوبة لهذه الطريقة."
    return detail


def _format_withdraw_details_step_prompt(method: PaymentMethod) -> str:
    return f"<b>الخطوة 2 من 2: بيانات السحب</b>\n\n{_withdraw_details_instruction(method)}"


def _deposit_receipt_step_prompt() -> str:
    return (
        "<b>أرسل صورة الإيصال</b>\n\n"
        "بعد إتمام التحويل، أرسل لقطة شاشة (Screenshot) واضحة تظهر تفاصيل العملية هنا."
    )


def _recharge_code_step_prompt(telecom: TelecomOperator, face_value: float) -> str:
    return (
        "<b>أرسل رمز التعبئة</b>\n\n"
        f"قيمة البطاقة: <b>{format_dh(face_value)}</b> — {escape(telecom.display_name)}\n"
        "أرسل رمز التعبئة المكون من الأرقام في رسالة واحدة (لا ترسل صوراً)."
    )


def _format_recharge_code_living_summary(telecom: TelecomOperator, face_value: float) -> str:
    return screen_body(
        f"🏠 <b>إضافة رصيد</b> &gt; <b>بطاقات التعبئة</b> &gt; <b>{escape(telecom.display_name)}</b>",
        f"قيمة البطاقة: <b>{format_dh(face_value)}</b>",
        "",
        "اتبع التعليمات في الرسالة أدناه لإرسال رمز التعبئة.",
    )


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


def _format_withdraw_review_text(method: PaymentMethod, amount: float, details: dict[str, str]) -> str:
    lines = [
        _withdraw_main_breadcrumb(),
        "",
        "<b>مراجعة طلب السحب قبل التأكيد</b>",
        "",
        f"طريقة السحب: <b>{escape(method.breadcrumb_label)}</b>",
        f"المبلغ: <b>{format_dh(amount)}</b>",
    ]
    if is_crypto_withdraw_details(details):
        lines.append("")
        lines.extend(_format_withdraw_details_section(details, amount=amount))
        lines.extend(["", CRYPTO_WITHDRAW_EXCHANGE_NOTICE])
    else:
        lines.append(
            f"{_withdraw_details_label(method)}: "
            f"<code>{escape(format_withdraw_details_display(details))}</code>"
        )
    lines.extend(
        [
            "",
            "تأكد جيداً من صحة المعلومات. سيتم استعمال هذه البيانات لمعالجة السحب يوم الجمعة.",
        ]
    )
    return screen_body(*lines)


def _format_withdraw_success_text(withdrawal_id: int, amount: float) -> str:
    return screen_body(
        _withdraw_main_breadcrumb(),
        "",
        "<b>تم إنشاء طلب السحب بنجاح</b>",
        f"رقم طلب السحب: <code>#{withdrawal_id}</code>",
        f"المبلغ المحجوز للسحب: <b>{format_dh(amount)}</b>",
        "",
        "سيتم معالجة الطلب يوم الجمعة.",
        "يمكنك إلغاء الطلب من سجل السحب داخل حسابي وطلباتي قبل يوم الجمعة، وسيعود المبلغ إلى رصيدك فوراً.",
    )


def _format_withdrawal_history_item(withdrawal: dict) -> list[str]:
    details = safe_withdraw_details(withdrawal.get("details_json"))
    amount = float(withdrawal.get("amount") or 0)
    lines = [
        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
        f"• رقم الطلب: <code>#{withdrawal['id']}</code>",
        f"• الطريقة: <b>{escape(str(withdrawal['method']))}</b>",
        f"• المبلغ: <b>{format_dh(withdrawal['amount'])}</b>",
        f"• الحالة: {_withdraw_status_ar(str(withdrawal['status']))}",
        f"• تاريخ الطلب: <code>{escape(str(withdrawal['created_at']))}</code>",
    ]
    if details:
        if is_crypto_withdraw_details(details):
            lines.append("• <b>بيانات الاستلام:</b>")
            lines.extend(_format_withdraw_details_section(details, amount=amount))
        else:
            lines.append(
                f"• معلومات السحب: <code>{escape(format_withdraw_details_display(details))}</code>"
            )
    return lines


def _format_withdraw_history_text(user_id: int, *, show_all: bool = False) -> str:
    active = get_active_withdrawal(user_id)
    limit = None if show_all else 5
    history = get_user_withdrawals(
        user_id,
        limit=limit,
        include_pending=False,
        withdrawal_type="normal",
    )
    lines = [
        _withdraw_history_breadcrumb(),
        "",
        "<b>سجل السحب</b>",
    ]
    if active is not None:
        lines.extend(["", *_format_active_withdrawal_block(active)])
        lines.append("")
        lines.append("<b>آخر عمليات السحب الأخرى</b>" if not show_all else "<b>كل عمليات السحب الأخرى</b>")
    else:
        lines.append("")
        lines.append("<b>آخر عمليات السحب</b>" if not show_all else "<b>كل سجل السحب</b>")

    if not history:
        lines.append("لا توجد عمليات سحب سابقة بعد.")
        return "\n".join(lines)

    max_text_length = 3900
    shown_count = 0
    for withdrawal in history:
        item = _format_withdrawal_history_item(withdrawal)
        next_text = "\n".join([*lines, *item])
        if len(next_text) > max_text_length:
            remaining = len(history) - shown_count
            if remaining > 0:
                lines.extend(
                    [
                        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
                        f"تم إخفاء <b>{remaining}</b> عمليات لأن رسالة تيليجرام لا تتحمل نصاً أطول.",
                    ]
                )
            break
        lines.extend(item)
        shown_count += 1
    return "\n".join(lines)


@router.callback_query(F.data.in_({"menu:deposit", "account:finance:deposit"}))
async def deposit_gateway_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await _render_deposit_gateway(callback, state, bot, register_living=True)
    await callback.answer()


@router.callback_query(F.data == "account:finance:withdraw")
async def withdraw_gateway_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    await clear_last_prompt(callback.message, state)
    await purge_flow_transcript(
        bot, state, callback.from_user.id, callback.message.chat.id
    )
    await state.clear()
    add_user(callback.from_user.id, telegram_name=callback.from_user.full_name)
    active = get_active_withdrawal(callback.from_user.id)
    if active is not None:
        text = _format_withdraw_pending_screen_text(callback.from_user.id)
        markup = build_withdraw_pending_screen_menu(active["id"])
    else:
        methods = _withdrawable_methods_for_user(callback.from_user.id)
        text = _format_withdraw_intro_text(callback.from_user.id, methods)
        markup = build_withdraw_methods_menu(methods)
    await _edit_payment_from_callback(
        callback,
        state,
        bot,
        text=text,
        reply_markup=markup,
        register_living=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("withdraw:method:"))
async def withdraw_method_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    method_key = (callback.data or "").split(":", maxsplit=2)[-1]
    method = PAYMENT_BY_KEY.get(method_key)
    if method is None:
        await callback.answer("طريقة السحب غير معروفة", show_alert=True)
        return
    if get_active_withdrawal(callback.from_user.id) is not None:
        await callback.answer("لديك طلب سحب معلق بالفعل", show_alert=True)
        return
    allowed = {m.key for m in _withdrawable_methods_for_user(callback.from_user.id)}
    if method.key not in allowed:
        await callback.answer("لا يمكنك السحب بهذه الطريقة لأنها ليست ضمن إيداعاتك السابقة.", show_alert=True)
        return
    if method.kind == "crypto":
        from handlers.crypto_withdraw import show_crypto_withdraw_network_picker

        await show_crypto_withdraw_network_picker(
            callback,
            state,
            bot,
            method=method,
            flow_kind="normal",
            back_callback="account:finance:withdraw",
        )
        await callback.answer()
        return
    await clear_last_prompt(callback.message, state)
    await reset_flow_transcript(state)
    await state.set_state(WithdrawFlow.enter_amount)
    await state.update_data(withdraw_method_key=method.key, withdraw_method_label=method.ledger_name)
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    summary = _format_withdraw_method_summary(method, user_id)
    await _edit_payment_living(
        bot, state, user_id, summary, None, message=callback.message
    )
    await _sync_payment_nav_anchor(bot, state, user_id, None)
    step_text = _format_withdraw_amount_step_prompt(method, user_id)
    await _send_payment_step_prompt(
        bot, state, user_id, chat_id, step_text, "account:finance:withdraw"
    )
    await callback.answer()


@router.message(StateFilter(WithdrawFlow.enter_amount), F.text)
async def withdraw_amount_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    method = PAYMENT_BY_KEY.get(str(data.get("withdraw_method_key", "")))
    if method is None:
        await _finish_payment_flow(bot, state, message.from_user.id, message.chat.id)
        return
    raw = (message.text or "").strip().replace(",", ".")
    try:
        amount = to_float(raw)
    except Exception:
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            "أرسل المبلغ بالأرقام فقط. مثال: 100",
            restore_markup=build_withdraw_step_nav(),
        )
        return
    user = get_user(message.from_user.id)
    balance = float(user["balance"]) if user else 0.0
    if amount <= 0:
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            "مبلغ السحب يجب أن يكون أكبر من صفر.",
            restore_markup=build_withdraw_step_nav(),
        )
        return
    if balance < amount:
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            f"رصيدك غير كافٍ. رصيدك الحالي هو <b>{format_dh(balance)}</b>.",
            restore_markup=build_withdraw_step_nav(),
        )
        return
    method_available = _withdrawable_amount_for_method(message.from_user.id, method)
    if amount > method_available:
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            (
                f"لا يمكنك سحب هذا المبلغ عبر <b>{escape(method.breadcrumb_label)}</b>.\n"
                f"المتاح للسحب عبر هذه الطريقة هو <b>{format_dh(method_available)}</b> فقط، "
                "لأننا نحسب مجموع إيداعاتك المقبولة من نفس الطريقة ثم نخصم السحوبات السابقة المقبولة منها.\n\n"
                "إذا كنت تعتقد أن لديك حالة خاصة أو تحتاج تسوية مختلفة، يرجى التواصل مع الدعم."
            ),
            restore_markup=build_withdraw_step_nav(),
        )
        return
    if get_active_withdrawal(message.from_user.id) is not None:
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            "لديك طلب سحب معلق بالفعل. يجب معالجته أو إلغاؤه قبل إنشاء طلب جديد.",
            restore_markup=build_withdraw_step_nav(),
        )
        return

    crypto_details_json = str(data.get("withdraw_crypto_details_json", ""))
    if method.kind == "crypto":
        if not crypto_details_json:
            await _flash_deposit_input_error(
                message,
                state,
                bot,
                "انتهت جلسة اختيار الشبكة. ارجع واختر شبكة USDT من جديد.",
                restore_markup=build_withdraw_step_nav(),
            )
            return
        if amount < MIN_CRYPTO_WITHDRAW_DH:
            await _flash_deposit_input_error(
                message,
                state,
                bot,
                f"الحد الأدنى للسحب بالكريبتو هو <b>{format_dh(MIN_CRYPTO_WITHDRAW_DH)}</b>.",
                restore_markup=build_withdraw_step_nav(),
            )
            return

    await track_transcript_user_message(state, message)
    await state.update_data(withdraw_amount=amount)
    user_id = message.from_user.id
    chat_id = message.chat.id
    if method.kind == "crypto" and crypto_details_json:
        await state.set_state(WithdrawFlow.confirm)
        await state.update_data(withdraw_details_json=crypto_details_json)
        details = safe_withdraw_details(crypto_details_json)
        text = _format_withdraw_review_text(method, amount, details)
        await _set_deposit_flash_restore(state, text)
        await edit_user_living_ui(
            bot, state, user_id, text, build_withdraw_confirm_menu()
        )
        await _sync_payment_nav_anchor(bot, state, user_id, build_withdraw_confirm_menu())
        await acknowledge_then_focus_living_ui(
            bot,
            state,
            user_id,
            chat_id,
            ack_text="✅ تم حفظ المبلغ — راجع التفاصيل واضغط تأكيد",
            reply_to_message_id=message.message_id,
        )
        return
    await state.set_state(WithdrawFlow.enter_details)
    summary = _format_withdraw_amount_saved_summary(method, amount)
    await _edit_payment_living(bot, state, user_id, summary, None)
    await _sync_payment_nav_anchor(bot, state, user_id, None)
    step_text = _format_withdraw_details_step_prompt(method)
    await _send_payment_step_prompt(
        bot, state, user_id, chat_id, step_text, "account:finance:withdraw"
    )


@router.message(StateFilter(WithdrawFlow.enter_details), F.text)
async def withdraw_details_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    method = PAYMENT_BY_KEY.get(str(data.get("withdraw_method_key", "")))
    if method is None:
        await _finish_payment_flow(bot, state, message.from_user.id, message.chat.id)
        return
    if method.kind == "crypto":
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            "للسحب بالكريبتو اختر الشبكة من القائمة ثم أرسل العنوان — لا تُدخل البيانات هنا.",
            restore_markup=build_withdraw_step_nav(),
        )
        return
    details, error = _parse_withdraw_details(method, message.text or "")
    if details is None:
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            error,
            restore_markup=build_withdraw_step_nav(),
        )
        return
    amount = to_float(data.get("withdraw_amount", 0))
    await track_transcript_user_message(state, message)
    await state.set_state(WithdrawFlow.confirm)
    await state.update_data(withdraw_details_json=json.dumps(details, ensure_ascii=False))
    text = _format_withdraw_review_text(method, amount, details)
    await _set_deposit_flash_restore(state, text)
    user_id = message.from_user.id
    await edit_user_living_ui(
        bot, state, user_id, text, build_withdraw_confirm_menu()
    )
    await _sync_payment_nav_anchor(bot, state, user_id, build_withdraw_confirm_menu())
    await acknowledge_then_focus_living_ui(
        bot,
        state,
        message.from_user.id,
        message.chat.id,
        ack_text="✅ تم حفظ معلومات السحب — راجع التفاصيل في الرسالة أعلاه واضغط تأكيد",
        reply_to_message_id=message.message_id,
    )


@router.callback_query(F.data == "withdraw:back:details")
async def withdraw_back_to_details_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    data = await state.get_data()
    method = PAYMENT_BY_KEY.get(str(data.get("withdraw_method_key", "")))
    amount = to_float(data.get("withdraw_amount", 0))
    if method is None or amount <= 0:
        await withdraw_gateway_handler(callback, state, bot)
        return
    if method.kind == "crypto":
        network_key = str(data.get("withdraw_crypto_network_key", ""))
        from handlers.crypto_withdraw import _format_crypto_withdraw_living_summary
        from utils.crypto_usdt import format_crypto_withdraw_address_prompt

        option = get_crypto_withdraw_option(network_key)
        if option is None:
            await withdraw_gateway_handler(callback, state, bot)
            await callback.answer()
            return
        saved_details = safe_withdraw_details(str(data.get("withdraw_crypto_details_json", "")))
        destination = str(saved_details.get("destination", "") or "") or None
        await state.set_state(WithdrawFlow.enter_crypto_address)
        summary = _format_crypto_withdraw_living_summary(
            "normal",
            callback.from_user.id,
            method,
            option,
            destination=destination,
        )
        user_id = callback.from_user.id
        chat_id = callback.message.chat.id
        await _edit_payment_living(
            bot, state, user_id, summary, None, message=callback.message
        )
        await _sync_payment_nav_anchor(bot, state, user_id, None)
        await _send_payment_step_prompt(
            bot,
            state,
            user_id,
            chat_id,
            format_crypto_withdraw_address_prompt(option),
            "withdraw:crypto:nets",
        )
        await callback.answer()
        return
    await state.set_state(WithdrawFlow.enter_details)
    summary = _format_withdraw_amount_saved_summary(method, amount)
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    await _edit_payment_living(
        bot, state, user_id, summary, None, message=callback.message
    )
    await _sync_payment_nav_anchor(bot, state, user_id, None)
    step_text = _format_withdraw_details_step_prompt(method)
    await _send_payment_step_prompt(
        bot, state, user_id, chat_id, step_text, "account:finance:withdraw"
    )
    await callback.answer()


@router.callback_query(StateFilter(WithdrawFlow.confirm), F.data == "withdraw:confirm")
async def withdraw_confirm_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    data = await state.get_data()
    method = PAYMENT_BY_KEY.get(str(data.get("withdraw_method_key", "")))
    if method is None:
        if callback.message and callback.from_user:
            await _finish_payment_flow(
                bot, state, callback.from_user.id, callback.message.chat.id
            )
        await callback.answer("انتهت الجلسة", show_alert=True)
        return
    amount = to_float(data.get("withdraw_amount", 0))
    details_json = str(data.get("withdraw_details_json", "{}"))
    user = get_user(callback.from_user.id)
    balance = float(user["balance"]) if user else 0.0
    method_available = _withdrawable_amount_for_method(callback.from_user.id, method)
    if amount <= 0 or balance < amount or amount > method_available:
        await callback.answer(
            "تعذر التأكيد: تغيّر الرصيد أو المتاح للسحب. أعد إنشاء الطلب.",
            show_alert=True,
        )
        return
    if get_active_withdrawal(callback.from_user.id) is not None:
        await callback.answer("لديك طلب سحب معلق بالفعل.", show_alert=True)
        return
    withdrawal_id = create_withdrawal_with_balance_hold(
        callback.from_user.id,
        amount,
        method.ledger_name,
        details_json,
    )
    if withdrawal_id is None:
        await _finish_payment_flow(bot, state, callback.from_user.id, callback.message.chat.id)
        text = screen_body(
            _withdraw_main_breadcrumb(),
            "",
            "<b>تعذر إنشاء طلب السحب.</b>",
            "قد يكون رصيدك غير كافٍ أو لديك طلب سحب معلق بالفعل.",
            "راجع سجل السحب أو أعد المحاولة لاحقاً.",
        )
        active = get_active_withdrawal(callback.from_user.id)
        markup = build_withdraw_history_menu(
            pending_withdrawal_id=active["id"] if active else None,
            show_all_button=count_user_withdrawals(
                callback.from_user.id,
                include_pending=False,
                withdrawal_type="normal",
            ) > 5,
            show_new_withdraw_button=(active is None),
        )
        await _edit_payment_from_callback(
            callback, state, bot, text=text, reply_markup=markup
        )
        await callback.answer()
        return
    await _finish_payment_flow(bot, state, callback.from_user.id, callback.message.chat.id)
    text = _format_withdraw_success_text(withdrawal_id, amount)
    markup = build_withdraw_history_menu(
        pending_withdrawal_id=withdrawal_id,
        show_all_button=count_user_withdrawals(
            callback.from_user.id,
            include_pending=False,
            withdrawal_type="normal",
        ) > 5,
        show_new_withdraw_button=False,
    )
    await _edit_payment_from_callback(
        callback, state, bot, text=text, reply_markup=markup
    )
    from services.withdraw_admin_notify import notify_admin_new_withdrawal

    await notify_admin_new_withdrawal(
        bot,
        withdrawal_id=withdrawal_id,
        user_id=callback.from_user.id,
        telegram_name=callback.from_user.full_name,
        amount=amount,
        method_label=method.ledger_name,
        details_json=details_json,
        withdrawal_type="normal",
    )
    # region agent log
    _withdraw_debug_log(
        "handlers/payment.py:withdraw_confirm_handler",
        "withdraw_confirm_success",
        {"user_id": callback.from_user.id, "withdrawal_id": withdrawal_id},
    )
    # endregion
    await callback.answer()


@router.callback_query(F.data.in_({"account:finance:log_withdraw", "account:finance:log_withdraw:all"}))
async def withdraw_history_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    await clear_last_prompt(callback.message, state)
    await state.clear()
    show_all = callback.data == "account:finance:log_withdraw:all"
    active = get_active_withdrawal(callback.from_user.id)
    total_history = count_user_withdrawals(
        callback.from_user.id,
        include_pending=False,
        withdrawal_type="normal",
    )
    text = _format_withdraw_history_text(callback.from_user.id, show_all=show_all)
    await _edit_payment_from_callback(
        callback,
        state,
        bot,
        text=text,
        reply_markup=build_withdraw_history_menu(
            pending_withdrawal_id=active["id"] if active else None,
            show_all_button=(not show_all and total_history > 5),
            show_new_withdraw_button=(active is None),
        ),
        register_living=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("withdraw:cancel:"))
async def withdraw_cancel_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    try:
        withdrawal_id = int((callback.data or "").split(":")[-1])
    except ValueError:
        await callback.answer("طلب غير صالح", show_alert=True)
        return
    ok = cancel_pending_withdrawal(withdrawal_id, callback.from_user.id)
    if not ok:
        await callback.answer("تعذر إلغاء الطلب. قد يكون تمت معالجته أو إلغاؤه سابقاً.", show_alert=True)
        return
    await clear_last_prompt(callback.message, state)
    await state.clear()
    total_history = count_user_withdrawals(
        callback.from_user.id,
        include_pending=False,
        withdrawal_type="normal",
    )
    text = (
        "<b>تم إلغاء طلب السحب بنجاح.</b>\n"
        "تم إرجاع المبلغ إلى رصيدك.\n\n"
        f"{_format_withdraw_history_text(callback.from_user.id, show_all=False)}"
    )
    await _edit_payment_from_callback(
        callback,
        state,
        bot,
        text=text,
        reply_markup=build_withdraw_history_menu(
            pending_withdrawal_id=None,
            show_all_button=total_history > 5,
        ),
    )
    await callback.answer()


async def _restore_deposit_gateway_from_step_back(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> bool:
    """الرجوع لبوابة الشحن من رسالة خطوة — لا يعدّل/يسجّل رسالة الخطوة المحذوفة."""
    if not callback.message or not callback.from_user:
        return False
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    callback_msg_id = callback.message.message_id
    step_id = await get_flow_step_prompt_id(state)
    living_chat, living_id, living_photo = await get_living_ui(state, user_id)
    saved_living_id = living_id
    living_corrupted = (
        step_id is not None
        and living_id is not None
        and living_id == step_id
    )
    from_step = step_id is not None and step_id == callback_msg_id
    # region agent log
    _deposit_back_debug_log(
        "H5",
        "payment.py:_restore_deposit_gateway_from_step_back:entry",
        "deposit_back_pressed",
        {
            "user_id": user_id,
            "callback_msg_id": callback_msg_id,
            "step_prompt_id": step_id,
            "living_id": saved_living_id,
            "from_step_message": from_step,
            "living_corrupted": living_corrupted,
        },
    )
    # endregion
    if living_corrupted:
        living_chat, living_id = None, None
    logger.info(
        "deposit_back start user=%s step=%s living=%s corrupted=%s",
        user_id,
        step_id,
        saved_living_id,
        living_corrupted,
    )
    await delete_flow_step_prompt(bot, state, chat_id)
    await clear_last_prompt(callback.message, state)
    await purge_flow_transcript(bot, state, user_id, chat_id)
    await state.clear()

    text = _format_deposit_gateway_text()
    markup = build_payment_methods_menu()
    edited = False
    if living_chat is not None and living_id is not None:
        edited = await edit_user_living_ui(bot, state, user_id, text, markup)
        if edited:
            await register_living_ui_ids(
                state, user_id, living_chat, living_id, has_photo=living_photo
            )

    # region agent log
    _deposit_back_debug_log(
        "H3",
        "payment.py:_restore_deposit_gateway_from_step_back:after_edit",
        "living_edit_result",
        {"edited": edited, "living_id": living_id, "living_chat": living_chat},
    )
    # endregion

    if not edited:
        if (
            living_chat is not None
            and saved_living_id is not None
            and not living_corrupted
        ):
            await delete_chat_message(bot, living_chat, saved_living_id)
        sent = await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            parse_mode="HTML",
        )
        await register_living_ui_ids(
            state,
            user_id,
            sent.chat.id,
            sent.message_id,
            has_photo=bool(sent.photo),
        )
        logger.info(
            "deposit_back ok fallback msg=%s chat=%s",
            sent.message_id,
            sent.chat.id,
        )
        # region agent log
        _deposit_back_debug_log(
            "H3",
            "payment.py:_restore_deposit_gateway_from_step_back:exit",
            "deposit_back_ok_fallback",
            {"new_msg_id": sent.message_id, "chat_id": sent.chat.id},
        )
        # endregion
    else:
        logger.info(
            "deposit_back ok edited living=%s chat=%s",
            living_id,
            living_chat,
        )
        # region agent log
        _deposit_back_debug_log(
            "H3",
            "payment.py:_restore_deposit_gateway_from_step_back:exit",
            "deposit_back_ok_edited",
            {"living_id": living_id, "living_chat": living_chat},
        )
        # endregion
    return True


@router.callback_query(F.data == "deposit:back")
async def deposit_back_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    try:
        await _restore_deposit_gateway_from_step_back(callback, state, bot)
    except TelegramBadRequest as exc:
        # region agent log
        _deposit_back_debug_log(
            "H3",
            "payment.py:deposit_back_handler:except",
            "deposit_back_failed",
            {"error": str(exc)[:200]},
        )
        # endregion
        logger.warning("deposit_back_handler failed: %s", exc)
        await callback.answer("تعذر الرجوع لطرق الشحن", show_alert=True)
        return
    await callback.answer()


def _format_recharge_telecom_selection_text() -> str:
    pct = int(RECHARGE_CREDIT_RATIO * 100)
    example_credit = int(10 * RECHARGE_CREDIT_RATIO)
    return screen_body(
        "<b>إضافة رصيد عبر بطاقات التعبئة 📱</b>",
        "",
        "اختر شركة الاتصالات الخاصة بالبطاقة التي تود شحنها:",
        (
            f"⚠️ <b>تذكير:</b> سيتم تحويل {pct}% فقط من قيمة التعبئة إلى رصيدك. "
            f"(مثال: تعبئة 10 دراهم = {example_credit} دراهم في رصيدك)."
        ),
    )


def _is_valid_recharge_code(raw: str) -> bool:
    """أرقام فقط، بدون مسافات أو رموز، الطول 14–16."""
    return bool(_RECHARGE_CODE_PATTERN.fullmatch(raw.strip()))


def _format_recharge_face_value_prompt(telecom: TelecomOperator) -> str:
    return screen_body(
        f"🏠 <b>إضافة رصيد</b> &gt; <b>بطاقات التعبئة</b> &gt; <b>{escape(telecom.display_name)}</b>",
        "",
        "اختر قيمة بطاقة التعبئة من الأزرار أدناه:",
    )


def _format_recharge_code_prompt(telecom: TelecomOperator, face_value: float) -> str:
    return screen_body(
        f"🏠 <b>إضافة رصيد</b> &gt; <b>بطاقات التعبئة</b> &gt; <b>{escape(telecom.display_name)}</b>",
        f"قيمة البطاقة: <b>{format_dh(face_value)}</b>",
        "",
        "أرسل الآن رمز التعبئة المكون من الأرقام في رسالة واحدة هنا 👇 "
        "(لا ترسل صور، أرسل الأرقام فقط).",
    )


async def _render_recharge_face_value_step(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    telecom: TelecomOperator,
) -> None:
    if not callback.message:
        return
    await clear_last_prompt(callback.message, state)
    await state.update_data(
        recharge_telecom_key=telecom.key,
        recharge_telecom_label=telecom.ledger_name,
    )
    await _edit_payment_from_callback(
        callback,
        state,
        bot,
        text=_format_recharge_face_value_prompt(telecom),
        reply_markup=build_recharge_face_value_menu(),
    )


async def _render_recharge_telecom_menu(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
) -> None:
    if not callback.message:
        return
    await clear_last_prompt(callback.message, state)
    await state.clear()
    await _edit_payment_from_callback(
        callback,
        state,
        bot,
        text=_format_recharge_telecom_selection_text(),
        reply_markup=build_recharge_telecom_menu(),
    )


@router.callback_query(F.data == "deposit:recharge")
async def deposit_recharge_menu_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    await _render_recharge_telecom_menu(callback, state, bot)
    await callback.answer()


@router.callback_query(F.data == "deposit:recharge:back")
async def deposit_recharge_back_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    try:
        await _restore_deposit_gateway_from_step_back(callback, state, bot)
    except TelegramBadRequest as exc:
        logger.warning("deposit_recharge_back_handler failed: %s", exc)
        await callback.answer("تعذر الرجوع لطرق الشحن", show_alert=True)
        return
    await callback.answer()


@router.callback_query(F.data == "deposit:recharge:telecom_menu")
async def deposit_recharge_telecom_menu_handler(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    await _render_recharge_telecom_menu(callback, state, bot)
    await callback.answer()


@router.callback_query(F.data == "deposit:recharge:back_face")
async def deposit_recharge_back_face_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.message:
        return
    await delete_flow_step_prompt(bot, state, callback.message.chat.id)
    await state.set_state(None)
    data = await state.get_data()
    telecom = TELECOM_BY_KEY.get(str(data.get("recharge_telecom_key", "")))
    if telecom is None:
        await _render_recharge_telecom_menu(callback, state, bot)
        await callback.answer()
        return
    await _render_recharge_face_value_step(callback, state, bot, telecom)
    await callback.answer()


@router.callback_query(F.data.startswith("deposit:recharge:"))
async def deposit_recharge_routing_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.message or not callback.from_user:
        return
    parts = (callback.data or "").split(":")
    if len(parts) < 3:
        return
    action = parts[2]
    if action in {"back", "telecom_menu", "back_face"}:
        return

    if action == "val" and len(parts) == 4:
        try:
            face_value = float(parts[3])
        except ValueError:
            await callback.answer("قيمة غير صالحة", show_alert=True)
            return
        data = await state.get_data()
        telecom = TELECOM_BY_KEY.get(str(data.get("recharge_telecom_key", "")))
        if telecom is None:
            await callback.answer("اختر شركة الاتصالات أولاً", show_alert=True)
            await _render_recharge_telecom_menu(callback, state, bot)
            return
        if int(face_value) not in RECHARGE_FACE_VALUES_DH:
            await callback.answer("قيمة البطاقة غير مدعومة", show_alert=True)
            return
        await clear_last_prompt(callback.message, state)
        await reset_flow_transcript(state)
        await state.set_state(DepositFlow.waiting_for_recharge_code)
        await state.update_data(
            recharge_telecom_key=telecom.key,
            recharge_telecom_label=telecom.ledger_name,
            recharge_face_value=face_value,
        )
        user_id = callback.from_user.id
        chat_id = callback.message.chat.id
        await _edit_payment_living(
            bot,
            state,
            user_id,
            _format_recharge_code_living_summary(telecom, face_value),
            None,
            message=callback.message,
        )
        await _sync_payment_nav_anchor(bot, state, user_id, None)
        step_text = _recharge_code_step_prompt(telecom, face_value)
        await _send_payment_step_prompt(
            bot, state, user_id, chat_id, step_text, "deposit:recharge:back_face"
        )
        await callback.answer()
        return

    telecom = TELECOM_BY_KEY.get(action)
    if telecom is None:
        await callback.answer("خيار غير معروف", show_alert=True)
        return
    await _render_recharge_face_value_step(callback, state, bot, telecom)
    await callback.answer()


@router.message(StateFilter(DepositFlow.waiting_for_recharge_code), F.text)
async def receive_recharge_code_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user or not message.text:
        return
    raw = message.text.strip()
    if not _is_valid_recharge_code(raw):
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            RECHARGE_CODE_INVALID_TEXT,
            restore_markup=build_recharge_code_step_nav(),
        )
        return

    code = raw
    if recharge_code_in_use(code):
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            RECHARGE_CODE_USED_TEXT,
            restore_markup=build_recharge_code_step_nav(),
        )
        return
    if count_user_pending_deposits(message.from_user.id) >= MAX_PENDING_DEPOSITS_PER_USER:
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            PENDING_DEPOSITS_LIMIT_TEXT,
            restore_markup=build_recharge_code_step_nav(),
        )
        return

    data = await state.get_data()
    telecom_label = str(data.get("recharge_telecom_label", "غير محدد"))
    face_value = to_float(data.get("recharge_face_value", 0))
    if face_value <= 0:
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            "⚠️ اختر قيمة البطاقة أولاً من القائمة.",
            restore_markup=build_recharge_telecom_menu(),
        )
        return
    user = message.from_user
    add_user(user.id, telegram_name=user.full_name)

    deposit_id = create_deposit(user.id, face_value, telecom_label, code)
    if deposit_id is None:
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            RECHARGE_CODE_USED_TEXT,
            restore_markup=build_recharge_code_step_nav(),
        )
        return

    admin_text = (
        "<b>🔔 طلب تعبئة جديد</b>\n\n"
        f"🆔 معرف المستخدم: <code>{user.id}</code>\n"
        f"👤 الاسم: <code>{escape(user.full_name or '—')}</code>\n"
        f"📱 شركة الاتصالات: <b>{escape(telecom_label)}</b>\n"
        f"💰 قيمة البطاقة: <b>{format_dh(face_value)}</b>\n"
        f"🔢 رمز التعبئة: <code>{escape(code)}</code>\n"
        f"📋 رقم الطلب: <code>#{deposit_id}</code>"
    )
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=admin_text,
            parse_mode="HTML",
            reply_markup=build_admin_recharge_actions(deposit_id),
        )
    except TelegramBadRequest as exc:
        logger.exception("Failed to notify admin for recharge %s: %s", deposit_id, exc)

    await track_transcript_user_message(state, message)
    await edit_user_living_ui(
        bot,
        state,
        user.id,
        RECHARGE_CODE_ACK_TEXT,
        build_deposit_nav_footer(),
    )
    await _sync_payment_nav_anchor(bot, state, user.id, build_deposit_nav_footer())
    await acknowledge_then_focus_living_ui(
        bot,
        state,
        user.id,
        message.chat.id,
        ack_text="✅ تم حفظ المعلومات — بانتظار تأكيد الإدارة",
        reply_to_message_id=message.message_id,
    )
    await state.clear()


@router.message(StateFilter(DepositFlow.waiting_for_recharge_code))
async def invalid_recharge_code_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    await _flash_deposit_input_error(
        message,
        state,
        bot,
        RECHARGE_CODE_INVALID_TEXT,
        restore_markup=build_recharge_code_step_nav(),
    )


@router.callback_query(F.data.startswith("deposit:bank:"))
async def deposit_bank_selected_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.message or not callback.from_user:
        return
    method_key = (callback.data or "").split(":", maxsplit=2)[-1]
    method = PAYMENT_BY_KEY.get(method_key)
    if method is None:
        await callback.answer("وسيلة الدفع غير معروفة", show_alert=True)
        return
    await clear_last_prompt(callback.message, state)
    await reset_flow_transcript(state)
    await state.set_state(DepositFlow.waiting_for_receipt)
    await state.update_data(
        deposit_bank_key=method.key,
        deposit_bank_label=method.ledger_name,
    )
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    await _edit_payment_living(
        bot,
        state,
        user_id,
        _format_payment_detail_text(method),
        None,
        message=callback.message,
    )
    await _sync_payment_nav_anchor(bot, state, user_id, None)
    await _send_payment_step_prompt(
        bot, state, user_id, chat_id, _deposit_receipt_step_prompt(), "deposit:back"
    )
    await callback.answer()


@router.message(StateFilter(DepositFlow.waiting_for_receipt), F.photo)
async def receive_deposit_receipt_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user or not message.photo:
        return
    data = await state.get_data()
    bank_label = str(data.get("deposit_bank_label", "غير محدد"))
    proof_file_id = message.photo[-1].file_id
    user = message.from_user
    add_user(user.id, telegram_name=user.full_name)
    if count_user_pending_deposits(user.id) >= MAX_PENDING_DEPOSITS_PER_USER:
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            PENDING_DEPOSITS_LIMIT_TEXT,
            restore_markup=build_bank_step_nav(),
        )
        return

    deposit_id = create_deposit(user.id, 0.0, bank_label, proof_file_id)
    if deposit_id is None:
        await _flash_deposit_input_error(
            message,
            state,
            bot,
            "⚠️ هذا الإيصال مُسجَّل مسبقاً في طلب قيد المراجعة أو مُعتمد. "
            "إذا كان خطأً تواصل مع الدعم.",
            restore_markup=build_bank_step_nav(),
        )
        return

    first_name = escape(user.first_name or "—")
    admin_caption = (
        "<b>🔔 إيصال شحن جديد</b>\n\n"
        f"🆔 معرف المستخدم: <code>{user.id}</code>\n"
        f"👤 اسم المستخدم: <b>{first_name}</b>\n"
        f"💳 وسيلة الدفع: <b>{escape(bank_label)}</b>\n"
        f"📋 رقم الطلب: <code>#{deposit_id}</code>"
    )
    try:
        await bot.send_photo(
            chat_id=ADMIN_ID,
            photo=proof_file_id,
            caption=admin_caption,
            parse_mode="HTML",
            reply_markup=build_admin_deposit_actions(deposit_id),
        )
    except TelegramBadRequest as exc:
        logger.exception("Failed to notify admin for deposit %s: %s", deposit_id, exc)

    await track_transcript_user_message(state, message)
    await edit_user_living_ui(
        bot,
        state,
        user.id,
        RECEIPT_ACK_TEXT,
        build_deposit_nav_footer(),
    )
    await _sync_payment_nav_anchor(bot, state, user.id, build_deposit_nav_footer())
    await acknowledge_then_focus_living_ui(
        bot,
        state,
        user.id,
        message.chat.id,
        ack_text="✅ تم حفظ المعلومات — بانتظار مراجعة الإدارة",
        reply_to_message_id=message.message_id,
    )
    await state.clear()


@router.message(StateFilter(DepositFlow.waiting_for_receipt))
async def invalid_deposit_receipt_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    await _flash_deposit_input_error(
        message,
        state,
        bot,
        "⚠️ يرجى إرسال <b>صورة الإيصال</b> فقط بعد إتمام التحويل.",
        restore_markup=build_bank_step_nav(),
    )


@router.callback_query(F.data.in_({"account:finance:log_deposit", "account:finance:log_deposit:all"}))
async def deposit_history_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    await clear_last_prompt(callback.message, state)
    await state.clear()
    show_all = callback.data == "account:finance:log_deposit:all"
    text = _format_deposit_history_text(callback.from_user.id, show_all=show_all)
    await _edit_payment_from_callback(
        callback,
        state,
        bot,
        text=text,
        reply_markup=build_deposit_history_menu(show_all_button=not show_all),
        register_living=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin:deposit:"))
async def admin_deposit_action_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    action = parts[2]
    try:
        deposit_id = int(parts[3])
    except ValueError:
        await callback.answer("رقم طلب غير صالح", show_alert=True)
        return
    deposit = get_deposit(deposit_id)
    if not deposit:
        await callback.answer("الطلب غير موجود", show_alert=True)
        return
    if deposit["status"] != "pending":
        await callback.answer("تمت المعالجة سابقًا", show_alert=True)
        return
    if is_recharge_ledger(str(deposit["method"])):
        await callback.answer(
            "هذا طلب تعبئة — استخدم زر «تأكيد التعبئة» وليس تأكيد الشحن.",
            show_alert=True,
        )
        return

    if action == "approve":
        await state.set_state(AdminFlow.confirm_deposit_amount)
        method_name = str(deposit["method"])
        title = _deposit_approval_title(method_name)
        await state.update_data(
            confirm_deposit_id=deposit_id,
            confirm_deposit_method=method_name,
        )
        amount_prompt = (
            f"<b>✅ تأكيد شحن {title}</b> — الطلب <code>#{deposit_id}</code>\n"
            "أدخل المبلغ المراد إضافته لرصيد المستخدم <b>بالدرهم (MAD)</b> فقط:\n"
            "<i>إذا كان الإيداع بالدولار، حوّله يدوياً إلى درهم حسب سعر الصرف قبل الإدخال.</i>"
        )
        await callback.message.answer(amount_prompt, parse_mode="HTML")
        await callback.answer()
        return

    if action == "reject":
        ok = await _reject_pending_deposit(
            deposit_id,
            user_id=deposit["user_id"],
            user_notice=(
                "<b>⚠️ SOLDIUM | تم رفض الإيصال</b>\n"
                "تعذّر اعتماد إيصال الشحن بعد المراجعة. يرجى التحقق من التحويل والتواصل مع الدعم إن لزم."
            ),
            admin_notice="<b>❌ تم رفض طلب الشحن.</b>",
            bot=bot,
            admin_message=callback.message,
        )
        if not ok:
            await callback.answer("تمت المعالجة سابقًا", show_alert=True)
            return
        await callback.answer()
        return

    await callback.answer("إجراء غير معروف", show_alert=True)


@router.message(AdminFlow.confirm_deposit_amount)
async def admin_confirm_deposit_amount_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user or message.from_user.id != ADMIN_ID:
        return
    amount = _parse_admin_amount(message.text or "")
    if amount is None:
        await message.answer("⚠️ أدخل مبلغًا صحيحًا (أرقام فقط).")
        return

    data = await state.get_data()
    deposit_id = int(data.get("confirm_deposit_id", 0))
    deposit = get_deposit(deposit_id)
    if not deposit or deposit["status"] != "pending":
        await state.clear()
        await message.answer("⚠️ الطلب غير صالح أو تمت معالجته.")
        return
    if is_recharge_ledger(str(deposit["method"])):
        await state.clear()
        await message.answer("⚠️ هذا طلب تعبئة — استخدم مسار تأكيد التعبئة.")
        return

    deposit_method = str(deposit["method"])
    amount_error = _validate_admin_deposit_amount(deposit_method, amount)
    if amount_error:
        await message.answer(amount_error, parse_mode="HTML")
        return

    add_user(deposit["user_id"])
    balance_dh = amount
    ledger_record = _ledger_method_for_approved_deposit(deposit_method)
    if not finalize_approved_deposit(
        deposit_id,
        deposit["user_id"],
        balance_dh,
        ledger_record,
    ):
        await state.clear()
        await message.answer("⚠️ تم اعتماد هذا الطلب مسبقاً أو لم يعد معلّقاً.")
        return
    title = _deposit_approval_title(deposit_method)
    user_notice = (
        f"<b>✨ SOLDIUM | تم تأكيد شحن {title}</b>\n"
        f"تمت إضافة <code>{format_dh(balance_dh)}</code> إلى رصيدك بنجاح.\n"
        "شكراً لثقتك بنا!"
    )
    admin_notice = f"<b>✅ تمت إضافة {format_dh(balance_dh)} لرصيد المستخدم.</b>"

    await send_smart_notification(bot, deposit["user_id"], user_notice)
    await state.clear()
    await message.answer(admin_notice, parse_mode="HTML")


@router.callback_query(F.data.startswith("admin:recharge:"))
async def admin_recharge_action_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return

    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("بيانات غير صالحة", show_alert=True)
        return
    action = parts[2]
    try:
        deposit_id = int(parts[3])
    except ValueError:
        await callback.answer("رقم طلب غير صالح", show_alert=True)
        return
    deposit = get_deposit(deposit_id)
    if not deposit:
        await callback.answer("الطلب غير موجود", show_alert=True)
        return
    if deposit["status"] != "pending":
        await callback.answer("تمت المعالجة سابقًا", show_alert=True)
        return
    if not is_recharge_ledger(str(deposit["method"])):
        await callback.answer("هذا الطلب ليس تعبئة", show_alert=True)
        return

    if action == "approve":
        await state.set_state(AdminFlow.confirm_recharge_face_value)
        await state.update_data(confirm_recharge_deposit_id=deposit_id)
        declared_face = float(deposit["amount"]) if float(deposit["amount"]) > 0 else None
        face_hint = (
            f"\nالقيمة المختارة من المستخدم: <b>{format_dh(declared_face)}</b>\n"
            if declared_face is not None
            else "\n"
        )
        await callback.message.answer(
            f"<b>✅ تأكيد التعبئة</b> — الطلب <code>#{deposit_id}</code>\n"
            f"{face_hint}"
            "أدخل <b>قيمة التعبئة الأصلية</b> بالدرهم للتأكيد (مثال: 10 أو 20 أو 50):\n"
            f"<i>سيُضاف للمستخدم {int(RECHARGE_CREDIT_RATIO * 100)}% من القيمة تلقائياً.</i>",
            parse_mode="HTML",
        )
        await callback.answer()
        return

    if action == "reject":
        ok = await _reject_pending_deposit(
            deposit_id,
            user_id=deposit["user_id"],
            user_notice=(
                "<b>⚠️ SOLDIUM | رمز التعبئة مرفوض</b>\n"
                "الرمز خاطئ أو مستعمل مسبقاً. يرجى التحقق وإعادة المحاولة."
            ),
            admin_notice="<b>❌ تم رفض رمز التعبئة.</b>",
            bot=bot,
            admin_message=callback.message,
        )
        if not ok:
            await callback.answer("تمت المعالجة سابقًا", show_alert=True)
            return
        await callback.answer()
        return

    await callback.answer("إجراء غير معروف", show_alert=True)


@router.message(AdminFlow.confirm_recharge_face_value)
async def admin_confirm_recharge_face_value_handler(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    if not message.from_user or message.from_user.id != ADMIN_ID:
        return
    face_value = _parse_recharge_face_value(message.text or "")
    if face_value is None:
        allowed = ", ".join(str(v) for v in RECHARGE_FACE_VALUES_DH)
        await message.answer(
            "⚠️ أدخل قيمة التعبئة كعدد صحيح بدون كسور.\n"
            f"القيم المسموحة: <b>{allowed}</b> درهم.",
            parse_mode="HTML",
        )
        return

    data = await state.get_data()
    deposit_id = int(data.get("confirm_recharge_deposit_id", 0))
    deposit = get_deposit(deposit_id)
    if not deposit or deposit["status"] != "pending":
        await state.clear()
        await message.answer("⚠️ الطلب غير صالح أو تمت معالجته.")
        return
    if not is_recharge_ledger(str(deposit["method"])):
        await state.clear()
        await message.answer("⚠️ هذا الطلب ليس تعبئة.")
        return
    if recharge_code_in_use(str(deposit["proof_file_id"]), exclude_deposit_id=deposit_id):
        await state.clear()
        await message.answer("⚠️ رمز التعبئة مُعتمد أو قيد مراجعة في طلب آخر.")
        return

    credit_dh = recharge_face_value_to_credit(face_value, RECHARGE_CREDIT_RATIO)
    telecom_method = str(deposit["method"])
    add_user(deposit["user_id"])
    if not finalize_approved_deposit(
        deposit_id,
        deposit["user_id"],
        credit_dh,
        telecom_method,
    ):
        await state.clear()
        await message.answer("⚠️ تم اعتماد هذا الطلب مسبقاً أو لم يعد معلّقاً.")
        return

    face_display = format_amount_2(face_value)
    credit_display = format_amount_2(credit_dh)
    await send_smart_notification(
        bot,
        deposit["user_id"],
        (
            f"تم قبول بطاقة التعبئة بقيمة <b>{face_display}</b> درهم. "
            f"تمت إضافة مبلغ <b>{credit_display}</b> DH بنجاح إلى رصيدك! ✅"
        ),
    )
    await state.clear()
    await message.answer(
        f"<b>✅ تعبئة {escape(telecom_method)}:</b> {face_display} DH → "
        f"أُضيف <code>{format_dh(credit_dh)}</code> للمستخدم.",
        parse_mode="HTML",
    )
