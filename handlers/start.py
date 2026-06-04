import asyncio
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import CommandStart, StateFilter
from aiogram.filters.command import CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import default_state
from aiogram.types import CallbackQuery, Message

from config import ADMIN_ID, SUPPORT_LINK
from database import (
    add_user,
    count_user_orders,
    get_user,
    get_user_orders,
    search_user_orders,
    upsert_user_on_start,
)
from services.order_provider_sync import apply_provider_status_to_order
from utils.flow_transcript import (
    acknowledge_then_focus_living_ui,
    flash_step_prompt_error,
    reset_flow_transcript,
    send_flow_step_prompt,
    track_transcript_user_message,
)
from utils.fsm_prompt_cleanup import clear_last_prompt
from utils.home_screen import (
    navigate_to_main_home,
    send_main_home_photo,
    send_onboarding_welcome_photo,
)
from utils.living_ui import (
    edit_living_ui_message,
    edit_user_living_ui,
    register_living_ui_message,
)
from keyboards.account import build_account_dashboard_markup, build_account_orders_markup
from keyboards.admin import build_admin_menu
from keyboards.critical import build_critical_points_markup
from keyboards.main import build_main_menu
from keyboards.support import build_support_markup
from utils.critical_points import build_critical_points_html
from utils.ui_branding import format_breadcrumb, screen_body
from services.referral import parse_referrer_id_from_start_payload
from services.smm_api_router import smm_manager_for_account
from smm_api import SMMManager
from utils.telegram_ui import allow_new_message_fallback, safe_edit_message_text, send_finance_coming_soon_flash
from utils.money import format_dh
from utils.order_status_ar import (
    format_order_status_ar,
    is_order_in_execution_status,
    normalize_order_status_key,
)
from utils.states import AccountFlow

router = Router()
logger = logging.getLogger(__name__)
smm_manager = SMMManager()

SEARCH_STEP_RESTORE_KEY = "search_step_restore_text"


def _is_stale_callback_answer_error(exc: TelegramBadRequest) -> bool:
    text = str(exc).lower()
    return ("query is too old" in text) or ("query id is invalid" in text)


def _home_markup(user_id: int) -> object:
    return build_main_menu(is_admin=user_id == ADMIN_ID)


def _account_breadcrumb_html() -> str:
    return format_breadcrumb("حسابي وطلباتي")


def _orders_breadcrumb_html() -> str:
    return format_breadcrumb("حسابي وطلباتي", "طلباتي السابقة")


def _format_account_dashboard_html(
    *,
    display_name: str,
    user_id: int,
    balance_dh: str,
    spent_dh: str,
    total_orders: int,
    is_partner: bool,
) -> str:
    lines = [
        _account_breadcrumb_html(),
        "",
        "💠 <b>حسابي وطلباتي</b>",
        "",
        "<b>👤 بيانات الهوية</b>",
        f"• الاسم: <b>{escape(display_name)}</b>",
        f"• المعرف (ID): <code>{user_id}</code>",
    ]
    if is_partner:
        lines.extend(["", "🎖️ <b>نوع الحساب:</b> شريك — عمولة <b>25%</b>"])
    lines.extend(
        [
            "",
            "<b>💰 البيانات المالية</b>",
            f"• الرصيد الحالي: <b>{balance_dh}</b>",
            f"• إجمالي الإنفاق: <b>{spent_dh}</b>",
            f"• إجمالي الطلبات: <b>{total_orders}</b>",
        ]
    )
    return "\n".join(lines)


async def _build_my_orders_message_html(
    user_id: int,
    bot: Bot,
    *,
    mode: str = "latest",
    orders: list[dict] | None = None,
    query: str | None = None,
) -> str:
    """نص HTML للطلبات مع مسار التنقل (يُحدَّث الحي من المزوّد عند الإمكان)."""
    total_orders = count_user_orders(user_id)
    if orders is None:
        orders = get_user_orders(user_id, limit=total_orders if mode == "all" else 5)

    lines = [_orders_breadcrumb_html(), "", "<b>📦 طلباتي</b>"]
    if not orders:
        if query:
            lines.extend(
                [
                    "",
                    f"لم أجد أي طلب مطابق لهذا البحث: <code>{escape(query)}</code>",
                    "يمكنك البحث برقم الطلب أو بإرسال الرابط كاملاً كما استعملته عند إنشاء الطلب.",
                ]
            )
        else:
            lines.append("لا يوجد لديك طلبات بعد.")
        return "\n".join(lines)

    lines.append("")
    if query:
        lines.append(f"<b>نتائج البحث عن:</b> <code>{escape(query)}</code>")
        lines.append(f"عدد النتائج: <b>{len(orders)}</b>")
    elif mode == "all":
        lines.append(f"<b>كل طلباتك</b> — العدد: <b>{len(orders)}</b>")
    else:
        lines.append("<b>آخر 5 طلبات فقط</b>")
        if total_orders > 5:
            lines.append("لعرض الطلبات القديمة اضغط زر <b>أظهر كل الطلبات</b>.")

    max_text_length = 3900
    shown_count = 0
    for order in orders:
        oid = int(order["id"])
        link = str(order.get("link") or "")
        exec_ref = order.get("provider_order_id")
        st_key = normalize_order_status_key(str(order.get("status") or ""))
        sc_val = order.get("start_count")
        status_note = order.get("status_note")
        if exec_ref:
            try:
                account = str(order.get("api_account") or "default")
                status_data = await smm_manager_for_account(account).get_order_status(
                    exec_ref
                )
                snapshot = await apply_provider_status_to_order(
                    order,
                    status_data,
                    bot,
                    notify=True,
                )
                if snapshot["status_key"]:
                    st_key = snapshot["status_key"]
                sc_val = snapshot["start_count"]
                status_note = snapshot["status_note"]
            except Exception as exc:
                logger.warning("Live status fetch failed for order %s: %s", oid, exc)

        prov = str(order.get("provider_order_id") or "").strip()
        ref_disp = escape(prov) if prov else "—"
        order_lines = [
            "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
            f"• <b>رقم الطلب:</b> <code>{ref_disp}</code>",
            f"• <b>الخدمة:</b> {escape(str(order['service_name']))}",
            f"• <b>الرابط:</b> <code>{escape(link)}</code>",
            f"• <b>الكمية:</b> <code>{order['quantity']}</code>",
            f"• <b>التكلفة:</b> <code>{format_dh(order['amount'])}</code>",
            f"• <b>الحالة:</b> {format_order_status_ar(st_key)}",
        ]
        if status_note:
            order_lines.append(f"• <b>ملاحظة:</b> {escape(str(status_note))}")
        if is_order_in_execution_status(st_key):
            if sc_val is not None:
                order_lines.append(f"• <b>العدد عند البدء:</b> <code>{sc_val}</code>")
            else:
                order_lines.append(
                    "• <b>العدد عند البدء:</b> <i>يُحدَّث تلقائياً عند توفر العداد من النظام.</i>"
                )

        next_text = "\n".join([*lines, *order_lines])
        if len(next_text) > max_text_length:
            remaining = len(orders) - shown_count
            if remaining > 0:
                lines.extend(
                    [
                        "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
                        f"تم إخفاء <b>{remaining}</b> طلبات لأن رسالة تيليجرام لا تتحمل نصاً أطول.",
                        "استخدم زر البحث برقم الطلب أو الرابط للوصول إلى طلب محدد.",
                    ]
                )
            break
        lines.extend(order_lines)
        shown_count += 1

    return "\n".join(lines)


@router.message(CommandStart())
async def start_handler(message: Message, state: FSMContext, command: CommandObject) -> None:
    if not message.from_user:
        return
    ref_id = parse_referrer_id_from_start_payload(command.args)
    if ref_id == message.from_user.id:
        ref_id = None
    upsert_user_on_start(
        message.from_user.id,
        telegram_name=message.from_user.full_name,
        referrer_id=ref_id,
    )
    await clear_last_prompt(message, state)
    await state.clear()

    # الخطوة 1: ترحيب سينمائي بدون أزرار (يُبقى في السجل)
    try:
        await send_onboarding_welcome_photo(message)
    except TelegramNetworkError:
        logger.exception("Onboarding welcome step failed")

    # الخطوة 2: انتظار قصير قبل ظهور الأزرار
    await asyncio.sleep(2.5)

    # الخطوة 3: لوحة الرئيسية الحية (صورة + أزرار)
    try:
        sent = await send_main_home_photo(
            message,
            message.from_user.id,
            is_admin=message.from_user.id == ADMIN_ID,
        )
        if sent:
            await register_living_ui_message(state, sent, user_id=message.from_user.id)
    except TelegramNetworkError:
        logger.exception("Failed to send main dashboard photo (network/timeout)")


@router.callback_query(F.data == "menu:home")
async def home_menu_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    await clear_last_prompt(callback.message, state)
    try:
        await navigate_to_main_home(
            callback,
            state,
            bot,
            user_id=callback.from_user.id,
            is_admin=callback.from_user.id == ADMIN_ID,
        )
    except Exception:
        logger.exception("menu:home failed")
        await callback.answer("تعذر تحديث القائمة الرئيسية", show_alert=True)
        return
    try:
        await callback.answer()
    except TelegramBadRequest as exc:
        if _is_stale_callback_answer_error(exc):
            return
        raise


@router.callback_query(F.data == "menu:account")
async def account_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    await clear_last_prompt(callback.message, state)
    await state.clear()
    add_user(callback.from_user.id, telegram_name=callback.from_user.full_name)
    user = get_user(callback.from_user.id)
    if not user:
        await callback.answer("تعذر جلب البيانات", show_alert=True)
        return
    display_name = (user.get("telegram_name") or "").strip() or (
        callback.from_user.full_name or "مستخدم"
    )
    total_orders = count_user_orders(callback.from_user.id)
    text = _format_account_dashboard_html(
        display_name=display_name,
        user_id=callback.from_user.id,
        balance_dh=format_dh(user["balance"]),
        spent_dh=format_dh(user["total_spent"]),
        total_orders=total_orders,
        is_partner=int(user.get("referral_level") or 1) >= 4,
    )
    markup = build_account_dashboard_markup()
    try:
        await safe_edit_message_text(
            callback.message,
            text,
            reply_markup=markup,
            parse_mode="HTML",
            bot=bot,
        )
    except TelegramBadRequest:
        if allow_new_message_fallback(callback.message):
            await callback.message.answer(
                text,
                reply_markup=markup,
                parse_mode="HTML",
            )
    await callback.answer()


@router.callback_query(
    F.data.startswith("account:finance:")
    & ~F.data.in_(
        {
            "account:finance:deposit",
            "account:finance:withdraw",
            "account:finance:log_deposit",
            "account:finance:log_deposit:all",
            "account:finance:log_withdraw",
            "account:finance:log_withdraw:all",
        }
    )
)
async def account_finance_placeholder_callback(callback: CallbackQuery) -> None:
    if not callback.message:
        await callback.answer()
        return
    await callback.answer()
    await send_finance_coming_soon_flash(callback.message)


@router.callback_query(F.data == "menu:critical")
async def critical_points_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    await clear_last_prompt(callback.message, state)
    await state.clear()
    text = build_critical_points_html()
    markup = build_critical_points_markup()
    try:
        await safe_edit_message_text(
            callback.message,
            text,
            reply_markup=markup,
            parse_mode="HTML",
            bot=bot,
        )
    except TelegramBadRequest:
        if allow_new_message_fallback(callback.message):
            await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")
    await callback.answer()


def _format_support_html() -> str:
    return screen_body(
        format_breadcrumb("مساعدة ودعم"),
        "مرحباً بك في مركز مساعدة <b>SOLDIUM</b> 🛡️",
        (
            "نحن هنا لضمان حصولك على أفضل تجربة ممكنة. إذا واجهتك أي مشكلة في الشحن، "
            "أو كان لديك استفسار حول خدماتنا، فلا تتردد في التحدث إلينا!"
        ),
        "",
        "💬 <b>نحن هنا لمساعدتك في:</b>",
        "• مشكلة في <b>الإيداع</b> أو <b>السحب</b> (رصيد أو أرباح إحالة)",
        "• <b>عطل تقني</b> في البوت أو رسالة خطأ غير مفهومة",
        "• <b>استفسار</b> عن خدمة، طلب، أو حالة طلبك",
        "• <b>خدمة تريدها</b> ولم تجدها في القائمة",
        "• <b>اقتراح</b> لتحسين البوت أو تجربتك",
        "• أو <b>أي موضوع آخر</b> — فريق الدعم جاهز للرد عليك",
        "",
        "📌 عند المراسلة، أرسل <b>رقم الطلب</b> أو <b>لقطة شاشة</b> إن وُجدت؛ يسرّع الحل.",
        "",
        "⏱️ <b>أوقات العمل:</b> من 10:00 صباحاً إلى 10:00 مساءً.",
        "⚡ <b>متوسط سرعة الرد:</b> خلال دقائق معدودة.",
    )


@router.callback_query(F.data == "menu:support")
async def support_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    await clear_last_prompt(callback.message, state)
    await state.clear()
    text = _format_support_html()
    markup = build_support_markup()
    msg = callback.message
    await register_living_ui_message(state, msg, user_id=callback.from_user.id)
    try:
        await edit_living_ui_message(
            bot,
            msg.chat.id,
            msg.message_id,
            text,
            markup,
            has_photo=bool(msg.photo),
            parse_mode="HTML",
        )
    except TelegramBadRequest as exc:
        if allow_new_message_fallback(msg):
            await msg.answer(text, reply_markup=markup, parse_mode="HTML")
        else:
            raise exc
    await callback.answer()


@router.callback_query(F.data.in_({"account:orders", "menu:my_orders"}))
async def account_my_orders_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """قائمة الطلبات من لوحة حسابي؛ زر الرجوع يعيد واجهة حسابي. يُقبل menu:my_orders لأزرار قديمة."""
    if not callback.from_user or not callback.message:
        return
    await clear_last_prompt(callback.message, state)
    await state.clear()
    add_user(callback.from_user.id)
    text = await _build_my_orders_message_html(callback.from_user.id, bot, mode="latest")
    markup = build_account_orders_markup(
        show_all_button=count_user_orders(callback.from_user.id) > 5,
        include_search=True,
    )
    try:
        await safe_edit_message_text(
            callback.message,
            text,
            reply_markup=markup,
            parse_mode="HTML",
            bot=bot,
        )
    except TelegramBadRequest:
        if allow_new_message_fallback(callback.message):
            await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "account:orders:all")
async def account_all_orders_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    await clear_last_prompt(callback.message, state)
    await state.clear()
    add_user(callback.from_user.id)
    text = await _build_my_orders_message_html(callback.from_user.id, bot, mode="all")
    markup = build_account_orders_markup(show_all_button=False, include_search=True)
    try:
        await safe_edit_message_text(
            callback.message,
            text,
            reply_markup=markup,
            parse_mode="HTML",
            bot=bot,
        )
    except TelegramBadRequest:
        if allow_new_message_fallback(callback.message):
            await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")
    await callback.answer()


@router.callback_query(F.data == "account:orders:search")
async def account_orders_search_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    await clear_last_prompt(callback.message, state)
    await reset_flow_transcript(state)
    await state.set_state(AccountFlow.search_orders)
    text = "\n".join(
        [
            _orders_breadcrumb_html(),
            "",
            "<b>🔎 البحث عن طلب</b>",
            "يمكنك البحث برقم الطلب أو بالرابط الكامل الذي استعملته في الطلب.",
            "عند البحث بالرابط، أعرض كل الطلبات المرتبطة به.",
            "",
            "اتبع التعليمات في الرسالة أدناه.",
        ]
    )
    step_text = (
        "<b>أرسل رقم الطلب أو الرابط</b>\n\n"
        "اكتب رقم الطلب أو الصق الرابط الكامل في رسالة واحدة."
    )
    markup = build_account_orders_markup(
        show_all_button=count_user_orders(callback.from_user.id) > 5,
        include_search=False,
    )
    try:
        await safe_edit_message_text(
            callback.message,
            text,
            reply_markup=markup,
            parse_mode="HTML",
            bot=bot,
        )
        await register_living_ui_message(
            state, callback.message, user_id=callback.from_user.id
        )
    except TelegramBadRequest:
        if allow_new_message_fallback(callback.message):
            sent = await callback.message.answer(
                text, reply_markup=markup, parse_mode="HTML"
            )
            await register_living_ui_message(
                state, sent, user_id=callback.from_user.id
            )
    await state.update_data(**{SEARCH_STEP_RESTORE_KEY: step_text})
    await send_flow_step_prompt(bot, state, callback.message.chat.id, step_text, markup)
    await callback.answer()


@router.message(AccountFlow.search_orders)
async def account_orders_search_submit(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user:
        return
    query = (message.text or "").strip()
    user_id = message.from_user.id
    if not query:
        data = await state.get_data()
        restore_text = str(data.get(SEARCH_STEP_RESTORE_KEY, "")).strip() or (
            "<b>أرسل رقم الطلب أو الرابط</b>\n\nلا ترسل رسالة فارغة."
        )
        markup = build_account_orders_markup(
            show_all_button=count_user_orders(user_id) > 5,
            include_search=False,
        )
        await flash_step_prompt_error(
            bot,
            state,
            message.chat.id,
            error_text="لا ترسل رسالة فارغة. أرسل رقم الطلب أو الرابط الكامل.",
            restore_text=restore_text,
            restore_markup=markup,
            user_message=message,
        )
        return

    results = search_user_orders(user_id, query)
    text = await _build_my_orders_message_html(
        user_id,
        bot,
        mode="search",
        orders=results,
        query=query,
    )
    markup = build_account_orders_markup(
        show_all_button=count_user_orders(user_id) > 5,
        include_search=True,
    )
    await track_transcript_user_message(state, message)
    updated = await edit_user_living_ui(bot, state, user_id, text, markup)
    if not updated:
        sent = await message.answer(text, reply_markup=markup, parse_mode="HTML")
        await register_living_ui_message(state, sent, user_id=user_id)
    await acknowledge_then_focus_living_ui(
        bot,
        state,
        user_id,
        message.chat.id,
        ack_text="✅ تم البحث — النتائج في الرسالة أعلاه",
        reply_to_message_id=message.message_id,
    )
    await state.clear()


@router.callback_query(F.data == "menu:admin")
async def admin_menu_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("غير مصرح", show_alert=True)
        return
    await clear_last_prompt(callback.message, state)
    await state.clear()
    text = screen_body(
        format_breadcrumb("لوحة التحكم"),
        "💠 <b>🛠 لوحة تحكم الإدارة</b>",
        "",
        "اختر أحد الخيارات أدناه:",
    )
    markup = build_admin_menu()
    try:
        await safe_edit_message_text(
            callback.message,
            text,
            reply_markup=markup,
            parse_mode="HTML",
            bot=bot,
        )
    except TelegramBadRequest:
        if allow_new_message_fallback(callback.message):
            await callback.message.answer(text, reply_markup=markup, parse_mode="HTML")
    await callback.answer()


@router.message(StateFilter(default_state))
async def fallback_handler(message: Message, state: FSMContext) -> None:
    if not message.from_user:
        return
    logger.info("Fallback message from user %s", message.from_user.id)
    await clear_last_prompt(message, state)
    await state.clear()
    await message.answer(
        "<b>⚠️ الرجاء استخدام الأزرار الظاهرة فقط.</b>",
        reply_markup=_home_markup(message.from_user.id),
    )
