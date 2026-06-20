# -*- coding: utf-8 -*-
import logging
import time

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import ADMIN_ID, CURRENCY_DISPLAY, MAX_ORDER_QUANTITY_CAP
from database import (
    add_user,
    assign_provider_order_id,
    create_order_with_balance_hold,
    get_user,
    refund_order,
    set_provider_order_id,
)
from keyboards.main import build_main_menu
from keyboards.orders import (
    build_flow_navigation_keyboard,
    build_sections_menu,
    build_subsections_menu,
    build_services_menu,
    build_order_confirm_keyboard,
    build_order_insufficient_balance_keyboard,
    build_order_success_nav_keyboard,
    build_platforms_menu,
    build_order_critical_points_markup,
    build_order_coming_soon_markup,
    build_auto_interactions_disclaimer_keyboard,
    CB_ORDER_OTHER_SERVICES,
)
from utils.critical_points import build_critical_points_html
from utils.ui_branding import ACCOUNT_PERSISTENCE_HTML
from services_config import SERVICES, reload_services
from smm_api import ProviderAuthError, SMMManager
from utils.money import format_amount_2, to_decimal, to_float
from utils.flow_transcript import (
    acknowledge_then_focus_living_ui,
    delete_flow_step_prompt,
    flash_step_prompt_error,
    purge_flow_transcript,
    reset_flow_transcript,
    send_flow_step_prompt,
    strip_message_reply_markup,
    transfer_nav_anchor,
    track_transcript_message,
    track_transcript_user_message,
)
from services.order_admin_notify import notify_admin_new_manual_order
from utils.fulfillment import FULFILLMENT_ADMIN, service_requires_admin
from utils.order_flow import (
    build_invoice_text,
    build_link_step_prompt,
    build_quantity_limits_text,
    build_quantity_step_prompt,
    build_service_summary_text,
    build_service_summary_text_short,
    build_subsections_preview,
    format_order_breadcrumb,
    format_order_flow_header,
    format_service_price_per_1000,
    order_input_step_total,
    parse_subsection_callback,
    validate_platform_link,
)
from utils.services import (
    compute_effective_limits,
    find_service_by_id,
    find_service_location,
    order_total_price_dh,
)
from utils.states import OrderFlow
from utils.fsm_prompt_cleanup import clear_last_prompt
from utils.living_ui import (
    delete_chat_message,
    edit_living_ui_message,
    edit_user_living_ui,
    get_living_ui,
    register_living_ui_message,
)
from utils.home_screen import edit_main_home_from_callback, navigate_to_main_home
from utils.telegram_ui import (
    ORDER_UI_DISMISS,
    allow_new_message_fallback,
    safe_edit_message_text,
)
from services.provider_catalog import get_provider_limits, refresh_provider_catalog
from services.smm_api_router import get_provider_credentials, smm_manager_for_account
from utils.notices import resolve_link_prompt, resolve_section_notice

router = Router()
logger = logging.getLogger(__name__)
smm_manager = SMMManager()
ORDER_FLASH_RESTORE_KEY = "order_flash_restore_text"
IPTV_PANEL_NOTES_MSG_KEY = "iptv_panel_notes_message_id"


async def _sync_service_context_in_state(
    state: FSMContext, service_id: str
) -> tuple[dict, str, str | None, str | None] | None:
    located = find_service_location(service_id)
    if not located:
        return None
    service, platform_key, section_key, subsection_key = located
    await state.update_data(
        service_id=str(service["id"]),
        platform_key=platform_key,
        section_key=section_key or "",
        subsection_key=subsection_key or "",
    )
    return located


def _validate_order_link(
    link: str,
    platform_key: str,
    section_key: str | None,
    subsection_key: str | None,
    service: dict,
) -> tuple[bool, str]:
    _, allow_username, allow_free_text = resolve_link_prompt(
        platform_key, section_key, subsection_key, service=service
    )
    if allow_free_text:
        return (True, "") if str(link or "").strip() else (False, "اكتب اسمك أو دولتك في خانة الرابط.")
    return validate_platform_link(
        link,
        platform_key,
        section_key=section_key,
        subsection_key=subsection_key,
        service=service,
        allow_username=allow_username,
    )


ORDER_READ_WARNING = (
    "🚨 <b>تنبيه إلزامي:</b> يُرجى قراءة <b>«اقرأ قبل الشراء»</b> كاملةً قبل اختيار الخدمة أو تأكيد الطلب — "
    "أي مخالفة للشروط قد يؤدي إلى <b>ضياع الطلب دون إمكانية التعويض</b>.\n"
    "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
)

ORDER_READ_WARNING_SHORT = (
    "🚨 <b>اقرأ قسم «اقرأ قبل الشراء» قبل إتمام الطلب</b>📜 .\n"
)

GUIDANCE_TEXT = (
    "📌 <b>اقرأ قبل الشراء:</b>\n"
    f"{ACCOUNT_PERSISTENCE_HTML}\n\n"
    "• تأكد أن الحساب أو المحتوى المستهدف <b>عام (Public)</b> وليس خاصاً حيث يلزم ذلك.\n"
    "• لا تضع أكثر من طلب لنفس الرابط في نفس الوقت حتى لا تتداخل الأنظمة وتضيع النتائج.\n"
    "• تأكد أن عداد المشتركين/المشاهدات أو ما شابه <b>ظاهر للجميع</b> إذا كانت الخدمة تعتمد على ذلك.\n"
    "• بعد إرسال الطلب للتنفيذ <b>لا يمكن إلغاؤه أو تعديله</b> من جهتنا وفق سياسة المزودين.\n"
    "• تحويل الحساب إلى خاص أو حذف المنشور/البث بعد الطلب يُعد الطلب منجزاً من جهة المنصة.\n"
    "• يُمنع استخدام الخدمات لمحتوى مخالف (إباحي، متطرف، أو يخالف القانون والسياسات).\n"
    "• راجع الرابط والكمية والخدمة المناسبة قبل التأكيد.\n\n"
    "💡 <b>خصوصاً المتابعين وبعض خدمات التفاعل:</b>\n"
    "قد يظهر <b>نقصان جزئي</b> مع الوقت؛ هذا مرتبط بطبيعة منصات التواصل (تنظيف الحسابات الراكدة، تحديثات الخوارزميات، "
    "إلغاء متابعات تلقائية…) وهو أمر شائع في السوق <b>عالمياً</b> وليس خاصاً بمتجر أو مزود واحد. "
    "لذلك خفّضنا سرعة الإيصال مقارنة بكثير من العروض السريعة في السوق عمداً لصالح <b>استقرار أفضل</b> قدر الإمكان، "
    "ويُستحسن غالباً اختيار كمية أعلى قليلاً لتغطية أي تراجع متوقع على المدى القصير.\n\n"
    "⏱️ <b>وقت بدء التنفيذ:</b>\n"
    "حسب الضغط على الخدمة وحالة المزود قد يبدأ الطلب <b>بسرعة</b> أو بعد <b>تأخير بسيط</b>؛ "
    "في أوقات الذروة قد يمتد الانتظار قليلاً دون أن يعني ذلك رفض الطلب.\n\n"
    "💰 <b>فشل الطلب أو التعذر التقني:</b>\n"
    "إذا تعذر تنفيذ الطلب لأي سبب من جهة المزود أو النظام، يُعاد المبلغ إلى <b>رصيدك</b> داخل البوت وفق آلية الاسترداد المعتمدة؛ "
    "احتفظ بـ<b>رقم الطلب</b> الظاهر بعد التأكيد عند مراسلة الدعم.\n\n"
    "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
    "<b>🚨 تنبيه هام جداً للمستخدم:</b>\n"
    "الشروط في الأعلى ليست مجرد نص، بل هي <b>ضوابط تقنية صارمة</b>. أي خطأ في اتباعها "
    "(مثل الرابط الخطأ أو طلبين متداخلين) قد يؤدي إلى <b>ضياع الطلب دون إمكانية التعويض</b>.\n"
    "⚠️ <b>يرجى التأكد من قراءتها بدقة قبل الضغط على زر الطلب.</b>\n"
    "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
)

# نص قصير لقائمة المنصات عند تعديل رسالة الصورة (حد تيليجرام 1024 للتعليق)
_ORDER_PHOTO_FOOTER = "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"


def _trail_from_order_context(
    platform_key: str | None = None,
    section_key: str | None = None,
    subsection_key: str | None = None,
    service_name: str | None = None,
    step_label: str | None = None,
) -> tuple[str, ...]:
    parts: list[str] = []
    pk = str(platform_key or "").strip()
    if pk:
        category = SERVICES.get(pk) or {}
        parts.append(str(category.get("title", pk)))
    sk = str(section_key or "").strip()
    if sk and sk.lower() not in {"none", "direct", ""}:
        section = ((SERVICES.get(pk) or {}).get("sections") or {}).get(sk) or {}
        parts.append(str(section.get("title", sk)))
    ssk = str(subsection_key or "").strip()
    if ssk and pk and sk:
        section = ((SERVICES.get(pk) or {}).get("sections") or {}).get(sk) or {}
        subsection = (section.get("subsections") or {}).get(ssk) or {}
        parts.append(str(subsection.get("title", ssk)))
    if service_name:
        name = str(service_name).strip()
        if len(name) > 42:
            name = name[:39] + "…"
        parts.append(name)
    if step_label:
        parts.append(step_label)
    return tuple(parts)


def _order_breadcrumb_line(*trail: str) -> str:
    return format_order_breadcrumb(*trail)


def _user_balance_amount(user_id: int) -> float:
    user = get_user(user_id)
    return float((user or {}).get("balance") or 0.0)


def _order_flow_header(user_id: int, *trail: str) -> str:
    return format_order_flow_header(
        _user_balance_amount(user_id),
        CURRENCY_DISPLAY,
        *trail,
    )


async def _order_breadcrumb_from_state(
    state: FSMContext,
    *,
    user_id: int,
    service_name: str | None = None,
    step_label: str | None = None,
) -> str:
    data = await state.get_data()
    trail = _trail_from_order_context(
        data.get("platform_key"),
        data.get("section_key"),
        data.get("subsection_key"),
        service_name=service_name,
        step_label=step_label,
    )
    return _order_flow_header(user_id, *trail)


def _order_pre_service_full(
    body: str,
    *trail: str,
    platform_key: str | None = None,
    user_id: int,
) -> str:
    header = _order_flow_header(user_id, *trail)
    if str(platform_key or "").strip() == "subscriptions":
        return f"{header}\n\n{body}"
    return f"{header}\n\n{ORDER_READ_WARNING}{GUIDANCE_TEXT}\n\n{body}"


def _order_pre_service_short(
    body: str,
    *trail: str,
    platform_key: str | None = None,
    user_id: int,
) -> str:
    header = _order_flow_header(user_id, *trail)
    if str(platform_key or "").strip() == "subscriptions":
        return f"{header}\n\n{body}"
    return f"{header}\n\n{ORDER_READ_WARNING_SHORT}{body}"


def _order_caption_text(
    full_text: str,
    short_body: str,
    *trail: str,
    has_photo: bool = False,
    platform_key: str | None = None,
    user_id: int,
) -> str:
    """على رسالة الصورة: تعليق قصير يبقى تحت حد 1024 حرف."""
    if has_photo:
        return (
            f"{_order_pre_service_short(short_body, *trail, platform_key=platform_key, user_id=user_id)}"
            f"\n{_ORDER_PHOTO_FOOTER}"
        )
    return full_text


async def _living_has_photo(state: FSMContext, user_id: int) -> bool:
    _, _, has_photo = await get_living_ui(state, user_id)
    return has_photo


async def _order_chat_id(
    state: FSMContext,
    user_id: int,
    message: Message | None = None,
) -> int | None:
    if message is not None:
        return message.chat.id
    living_chat, _, _ = await get_living_ui(state, user_id)
    return living_chat


async def _clear_iptv_panel_notes_message(
    bot: Bot,
    state: FSMContext,
    chat_id: int,
) -> None:
    data = await state.get_data()
    mid = data.get(IPTV_PANEL_NOTES_MSG_KEY)
    if isinstance(mid, int):
        await delete_chat_message(bot, chat_id, mid)
    await state.update_data(**{IPTV_PANEL_NOTES_MSG_KEY: None})


async def _send_subscription_section_notes(
    bot: Bot,
    state: FSMContext,
    chat_id: int,
    text: str,
    *,
    persist_for_panel: bool = False,
) -> None:
    """ملاحظات التصنيف كاملة في رسالة نصية."""
    if persist_for_panel:
        await _clear_iptv_panel_notes_message(bot, state, chat_id)
    try:
        sent = await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")
    except TelegramBadRequest:
        return
    if sent.message_id is None:
        return
    if persist_for_panel:
        await state.update_data(**{IPTV_PANEL_NOTES_MSG_KEY: sent.message_id})
    else:
        await track_transcript_message(state, sent.message_id)


SUBSCRIPTION_NOTES_ABOVE_HINT = (
    "📜 <b>اقرأ الملاحظات في الرسالة أعلاه</b> قبل اختيار الخدمة.\n\n"
)


def _subscription_section_main_body(title: str, *, notes_above: bool = False) -> str:
    hint = SUBSCRIPTION_NOTES_ABOVE_HINT if notes_above else ""
    return f"<b>{title}</b>\n\n{hint}اختر الخدمة:"


def _subscription_section_living_body(title: str, *, with_notes: bool, desc: str) -> str:
    if with_notes and desc:
        return f"<b>{title}</b>\n\n{desc}\n\nاختر الخدمة:"
    return _subscription_section_main_body(title)


async def _edit_order_screen(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    text: str,
    reply_markup: object,
    *,
    message: Message | None = None,
) -> bool:
    """تعديل رسالة الواجهة الحية؛ fallback على رسالة الـ callback عند الفشل."""
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
        logger.debug("_edit_order_screen fallback: %s", exc)
        try:
            await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")
            return True
        except TelegramBadRequest:
            return False
    return False


async def _sync_living_nav_anchor(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    reply_markup: object | None,
) -> None:
    """بعد تعديل الرسالة الحية: تسجيلها كحامل الأزرار (مع تفريغ الحامل السابق)."""
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


async def _prepare_order_nav(
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
        await _clear_iptv_panel_notes_message(
            bot, state, callback.message.chat.id
        )
        await purge_flow_transcript(
            bot, state, callback.from_user.id, callback.message.chat.id
        )
    else:
        await delete_flow_step_prompt(bot, state, callback.message.chat.id)
    return callback.from_user.id


def _build_order_success_receipt_html(
    provider_order_ref: str,
    link: str,
    quantity: int,
    total_price_display: str,
    *,
    breadcrumb_line: str | None = None,
    admin_fulfillment: bool = False,
) -> str:
    from html import escape

    ref = escape(str(provider_order_ref).strip()) if str(provider_order_ref).strip() else "—"
    sep = "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬"
    body = (
        f"{sep}\n"
        "<b>تم إنشاء الطلب بنجاح</b>\n"
        f"{sep}\n"
        f"• <b>رقم الطلب:</b> <code>{ref}</code>\n"
        f"• <b>الرابط:</b> <code>{escape(link)}</code>\n"
        f"• <b>الكمية:</b> <code>{quantity}</code>\n"
        f"• <b>التكلفة:</b> <code>{escape(str(total_price_display))} DH</code>\n"
    )
    if admin_fulfillment:
        body += (
            "• <b>حالة الطلب:</b> بانتظار تنفيذ الإدارة — ستصلك التفاصيل قريباً.\n"
        )
    else:
        body += (
            "• <b>حالة الطلب:</b> تم إرساله للتنفيذ وهو الآن قيد المعالجة.\n"
        )
    body += (
        f"{sep}\n"
        "يمكنك متابعة حالته من قسم [ طلباتي ] داخل [ حسابي ]. احتفظ برقم الطلب عند التواصل مع الدعم."
    )
    if breadcrumb_line:
        return f"{breadcrumb_line}\n\n{body}"
    return body


async def _submit_order_to_provider(
    service: dict,
    link: str,
    quantity: int,
    api_account: str,
) -> str:
    provider_response = await smm_manager_for_account(api_account).add_order(
        service=int(service["provider_id"]),
        link=link,
        quantity=quantity,
    )
    provider_order_id = str(provider_response.get("order", "")).strip()
    if not provider_order_id:
        raise ValueError(f"استجابة غير صالحة من واجهة الطلبات: {provider_response}")
    return provider_order_id


async def _build_service_summary_for_state(
    service: dict,
    state: FSMContext,
    *,
    user_id: int,
    has_photo: bool,
) -> str:
    await _refresh_provider_limits_cache()
    mn, mx = await _effective_limits(service)
    intro_breadcrumb = await _order_breadcrumb_from_state(
        state,
        user_id=user_id,
        service_name=str(service["name"]),
        step_label="تفاصيل الخدمة",
    )
    fixed = service.get("auto_quantity")
    if has_photo:
        return build_service_summary_text_short(
            service,
            CURRENCY_DISPLAY,
            mn,
            mx,
            fixed_quantity=fixed,
            breadcrumb_line=intro_breadcrumb,
        )
    return build_service_summary_text(
        service,
        CURRENCY_DISPLAY,
        mn,
        mx,
        fixed_quantity=fixed,
        breadcrumb_line=intro_breadcrumb,
    )


async def _send_order_link_step_prompt(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
    *,
    platform_key: str,
    section_key: str | None,
    subsection_key: str | None = None,
    service: dict,
) -> None:
    link_prompt_text, allow_username, allow_free_text = resolve_link_prompt(
        platform_key,
        section_key,
        subsection_key,
        service=service,
    )
    step_total = order_input_step_total(service)
    step_label = (
        "الخطوة 1 من 1: الرابط" if step_total <= 1 else "الخطوة 1 من 2: الرابط"
    )
    prompt = build_link_step_prompt(
        link_prompt_text,
        allow_username=allow_username,
        allow_free_text=allow_free_text,
        step_label=step_label,
    )
    nav = build_flow_navigation_keyboard()
    step_id = await send_flow_step_prompt(bot, state, chat_id, prompt, nav)
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
    await _set_order_flash_restore(state, prompt)


async def _send_order_quantity_step_prompt(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
    *,
    service: dict,
    link: str,
) -> None:
    mn, mx = await _effective_limits(service)
    step_total = order_input_step_total(service)
    step_label = (
        "الخطوة 1 من 1: الكمية" if step_total <= 1 else "الخطوة 2 من 2: الكمية"
    )
    prompt = build_quantity_step_prompt(
        service, link, mn, mx, CURRENCY_DISPLAY, step_label=step_label
    )
    nav = build_flow_navigation_keyboard()
    step_id = await send_flow_step_prompt(bot, state, chat_id, prompt, nav)
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
    await _set_order_flash_restore(state, prompt)


async def _set_order_flash_restore(state: FSMContext, text: str) -> None:
    await state.update_data(**{ORDER_FLASH_RESTORE_KEY: text})


async def _flash_order_input_error(
    message: Message,
    state: FSMContext,
    bot: Bot,
    error_text: str,
) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    restore_text = str(data.get(ORDER_FLASH_RESTORE_KEY, "")).strip() or "<b>🔗 أرسل الرابط من جديد:</b>"
    await flash_step_prompt_error(
        bot,
        state,
        message.chat.id,
        error_text=error_text,
        restore_text=restore_text,
        restore_markup=build_flow_navigation_keyboard(),
        user_message=message,
    )


async def _edit_order_living_ui(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    text: str,
    reply_markup: object,
) -> bool:
    return await edit_user_living_ui(bot, state, user_id, text, reply_markup)


async def _edit_order_result(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    user_id: int,
    text: str,
    reply_markup: object,
) -> None:
    if await edit_user_living_ui(bot, state, user_id, text, reply_markup):
        return
    if callback.message:
        await safe_edit_message_text(
            callback.message,
            text,
            reply_markup=reply_markup,
            parse_mode="HTML",
            bot=bot,
        )


def _home(user_id: int) -> object:
    return build_main_menu(is_admin=user_id == ADMIN_ID)


async def _finish_order_flow(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
) -> None:
    await purge_flow_transcript(bot, state, user_id, chat_id)
    await state.clear()


async def _clear_awaiting_prompt(
    callback: CallbackQuery, state: FSMContext, bot: Bot | None = None
) -> None:
    if callback.message:
        await clear_last_prompt(callback.message, state, bot=bot)


async def _show_home(
    callback: CallbackQuery,
    bot: Bot,
    clear_state: FSMContext | None = None,
) -> None:
    if not callback.from_user:
        return
    if clear_state is not None:
        await navigate_to_main_home(
            callback,
            clear_state,
            bot,
            user_id=callback.from_user.id,
            is_admin=callback.from_user.id == ADMIN_ID,
        )
    else:
        await edit_main_home_from_callback(
            callback,
            callback.from_user.id,
            is_admin=callback.from_user.id == ADMIN_ID,
            bot=bot,
            state=clear_state,
        )


def _safe_int(value: object, default: int) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


async def _refresh_provider_limits_cache(force: bool = False) -> None:
    await refresh_provider_catalog(smm_manager, force=force)


async def _effective_limits(service: dict) -> tuple[int, int]:
    fixed_qty = service.get("auto_quantity")
    if fixed_qty is not None:
        q = _safe_int(fixed_qty, default=1000)
        return q, q

    local_min = _safe_int(service.get("min", 1), default=1)
    local_max = _safe_int(service.get("max", 1_000_000_000), default=1_000_000_000)
    provider_id = _safe_int(service.get("provider_id"), default=0)
    provider_limits: tuple[int, int] | None = None
    if provider_id > 0:
        try:
            await _refresh_provider_limits_cache()
            if get_provider_limits(provider_id) is None:
                await _refresh_provider_limits_cache(force=True)
            provider_limits = get_provider_limits(provider_id)
        except Exception as exc:
            logger.warning("Failed to refresh provider limits cache: %s", exc)
    return compute_effective_limits(
        local_min,
        local_max,
        provider_limits,
        quantity_cap=MAX_ORDER_QUANTITY_CAP,
    )


async def _provider_limits_for_service(service: dict) -> tuple[int, int] | None:
    provider_id = _safe_int(service.get("provider_id"), default=0)
    if provider_id <= 0:
        return None
    try:
        await _refresh_provider_limits_cache()
        if get_provider_limits(provider_id) is None:
            await _refresh_provider_limits_cache(force=True)
        return get_provider_limits(provider_id)
    except Exception as exc:
        logger.warning("Failed to read provider limits for service %s: %s", service.get("id"), exc)
        return None


async def _auto_quantity_provider_check(service: dict) -> tuple[bool, str]:
    auto_qty = service.get("auto_quantity")
    if auto_qty is None:
        return True, ""
    quantity = _safe_int(auto_qty, default=0)
    if quantity <= 0:
        return False, "الكمية الثابتة لهذه الخدمة غير صالحة. تواصل مع الدعم."
    limits = await _provider_limits_for_service(service)
    if limits is None:
        return True, ""
    provider_min, provider_max = limits
    if quantity < provider_min or quantity > provider_max:
        return (
            False,
            "<b>الكمية الثابتة لهذه الخدمة غير متوافقة مع حدود المزود حالياً.</b>\n"
            f"الكمية المطلوبة: <b>{quantity}</b> — ما يقبله المزود: من <b>{provider_min}</b> إلى <b>{provider_max}</b>.\n"
            "جرّب لاحقاً أو تواصل مع الدعم.",
        )
    return True, ""


async def _show_platforms(
    state: FSMContext,
    *,
    bot: Bot,
    user_id: int,
    message: Message | None = None,
    use_edit: bool = False,
) -> None:
    reload_services()
    await state.set_state(OrderFlow.choose_category)
    await state.update_data(platform_key=None, section_key=None, subsection_key=None, service_id=None, parent_section_key=None)
    markup = build_platforms_menu()
    short_body = "<b>اختر المنصة:</b>"
    full_text = _order_pre_service_full(short_body, user_id=user_id)
    has_photo = await _living_has_photo(state, user_id)
    text = _order_caption_text(full_text, short_body, has_photo=has_photo, user_id=user_id)
    if use_edit:
        if await _edit_order_screen(bot, state, user_id, text, markup, message=message):
            await _sync_living_nav_anchor(bot, state, user_id, markup)
    elif message is not None:
        await message.answer(text, reply_markup=markup, parse_mode="HTML")


ORDER_COMING_SOON_TEXT = "<b>قريباً</b>"

async def _show_order_coming_soon(
    state: FSMContext,
    *,
    bot: Bot,
    user_id: int,
    message: Message | None = None,
    use_edit: bool = False,
) -> None:
    await state.set_state(OrderFlow.choose_category)
    markup = build_order_coming_soon_markup()
    text = f"{_order_flow_header(user_id)}\n\n{ORDER_COMING_SOON_TEXT}"
    if use_edit:
        if await _edit_order_screen(
            bot, state, user_id, text, markup, message=message
        ):
            await _sync_living_nav_anchor(bot, state, user_id, markup)
    elif message is not None:
        await message.answer(text, reply_markup=markup, parse_mode="HTML")


async def _show_sections(
    state: FSMContext,
    platform_key: str,
    *,
    bot: Bot,
    user_id: int,
    message: Message | None = None,
    use_edit: bool = False,
) -> None:
    category = SERVICES.get(platform_key, {})
    sections = category.get("sections") or {}
    warn_markup = build_flow_navigation_keyboard("order:nav:platforms")
    if not sections:
        warn = "⚠️ لا توجد تصنيفات متاحة لهذه المنصة حاليًا."
        if use_edit:
            if await _edit_order_screen(bot, state, user_id, warn, warn_markup, message=message):
                await _sync_living_nav_anchor(bot, state, user_id, warn_markup)
        elif message is not None:
            await message.answer(warn, reply_markup=warn_markup, parse_mode="HTML")
        return
    await state.set_state(OrderFlow.choose_subcategory)
    await state.update_data(platform_key=platform_key, section_key=None, subsection_key=None, service_id=None, parent_section_key=None)
    title = category.get("title", "الخدمات")
    trail = _trail_from_order_context(platform_key)
    short_body = f"<b>{title}</b>\n\nاختر التصنيف:"
    full_text = _order_pre_service_full(
        short_body, *trail, platform_key=platform_key, user_id=user_id
    )
    has_photo = await _living_has_photo(state, user_id)
    text = _order_caption_text(
        full_text,
        short_body,
        *trail,
        has_photo=has_photo,
        platform_key=platform_key,
        user_id=user_id,
    )
    markup = build_sections_menu(platform_key)
    if use_edit:
        if await _edit_order_screen(bot, state, user_id, text, markup, message=message):
            await _sync_living_nav_anchor(bot, state, user_id, markup)
    elif message is not None:
        await message.answer(text, reply_markup=markup, parse_mode="HTML")


async def _show_services(
    state: FSMContext,
    platform_key: str | None = None,
    section_key: str | None = None,
    subsection_key: str | None = None,
    *,
    bot: Bot,
    user_id: int,
    message: Message | None = None,
    use_edit: bool = False,
) -> None:
    data = await state.get_data()
    platform_key = platform_key or data.get("platform_key")
    if section_key is None:
        section_key = data.get("section_key")
    if subsection_key is None:
        subsection_key = data.get("subsection_key")

    if not platform_key or str(platform_key).lower() == "none":
        await _show_platforms(
            state, bot=bot, user_id=user_id, message=message, use_edit=use_edit
        )
        return

    category = SERVICES.get(platform_key, {})
    is_direct = section_key is None or str(section_key).lower() in {"none", "direct", ""}

    trail = _trail_from_order_context(
        platform_key,
        None if is_direct else section_key,
        subsection_key,
    )
    has_photo = await _living_has_photo(state, user_id)
    subscription_notes_text = ""
    split_subscription_notes = False
    section_title = ""
    if is_direct:
        reply_markup = build_services_menu(platform_key, None, None)
        title = category.get("title", "الخدمات")
        await state.update_data(platform_key=platform_key, section_key=None)
        short_body = f"<b>{title}</b>\n\nاختر الخدمة:"
        full_text = _order_pre_service_full(
            short_body, *trail, platform_key=platform_key, user_id=user_id
        )
        text = _order_caption_text(
            full_text,
            short_body,
            *trail,
            has_photo=has_photo,
            platform_key=platform_key,
            user_id=user_id,
        )
    else:
        section = category.get("sections", {}).get(section_key, {})
        section_title = section.get("title", "القسم")
        desc = resolve_section_notice(section, platform_key=platform_key)
        reply_markup = build_services_menu(platform_key, section_key, subsection_key)
        await state.update_data(platform_key=platform_key, section_key=section_key)
        split_subscription_notes = (
            platform_key == "subscriptions"
            and bool(desc)
            and has_photo
        )
        if split_subscription_notes:
            short_body = _subscription_section_main_body(section_title, notes_above=True)
        else:
            short_body = _subscription_section_living_body(
                section_title, with_notes=bool(desc), desc=desc
            )
        full_text = _order_pre_service_full(
            short_body, *trail, platform_key=platform_key, user_id=user_id
        )
        text = _order_caption_text(
            full_text,
            short_body,
            *trail,
            has_photo=has_photo,
            platform_key=platform_key,
            user_id=user_id,
        )
        if split_subscription_notes:
            subscription_notes_text = _order_pre_service_full(
                f"<b>{section_title}</b>\n\n{desc}",
                *trail,
                platform_key=platform_key,
                user_id=user_id,
            )

    await state.set_state(OrderFlow.choose_service)

    if split_subscription_notes and subscription_notes_text:
        chat_id = await _order_chat_id(state, user_id, message)
        if chat_id is not None:
            await _send_subscription_section_notes(
                bot,
                state,
                chat_id,
                subscription_notes_text,
                persist_for_panel=section_key == "iptv_panel",
            )
            main_text = _order_pre_service_full(
                _subscription_section_main_body(section_title, notes_above=True),
                *trail,
                platform_key=platform_key,
                user_id=user_id,
            )
            old_chat, old_id, _ = await get_living_ui(state, user_id)
            if old_chat and old_id:
                await strip_message_reply_markup(bot, old_chat, old_id)
            try:
                sent = await bot.send_message(
                    chat_id=chat_id,
                    text=main_text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
                await register_living_ui_message(state, sent, user_id=user_id)
                await _sync_living_nav_anchor(bot, state, user_id, reply_markup)
            except TelegramBadRequest:
                if message is not None:
                    await message.answer(
                        main_text, reply_markup=reply_markup, parse_mode="HTML"
                    )
        return

    if use_edit:
        if await _edit_order_screen(
            bot, state, user_id, text, reply_markup, message=message
        ):
            await _sync_living_nav_anchor(bot, state, user_id, reply_markup)
    elif message is not None:
        await message.answer(text, reply_markup=reply_markup, parse_mode="HTML")


async def _show_subsections(
    state: FSMContext,
    platform_key: str,
    section_key: str,
    parent_section_key: str | None = None,
    *,
    bot: Bot,
    user_id: int,
    message: Message | None = None,
    use_edit: bool = False,
) -> None:
    category = SERVICES.get(platform_key, {})
    sections = category.get("sections") or {}
    section = sections.get(section_key) or {}
    if not section.get("subsections"):
        await _show_services(
            state,
            platform_key,
            section_key,
            bot=bot,
            user_id=user_id,
            message=message,
            use_edit=use_edit,
        )
        return
    await state.set_state(OrderFlow.choose_subcategory)
    await state.update_data(
        platform_key=platform_key,
        section_key=section_key,
        subsection_key=None,
        parent_section_key=parent_section_key,
    )
    subsections_text = build_subsections_preview(section)
    title = section.get("title", "الخدمات")
    trail = _trail_from_order_context(platform_key, section_key)
    preview = subsections_text if len(subsections_text) <= 500 else subsections_text[:497] + "…"
    short_body = f"<b>{title}</b>\nاختر النوع:\n{preview}"
    full_text = _order_pre_service_full(
        f"<b>{title}</b>\nاختر النوع:\n{subsections_text}",
        *trail,
        platform_key=platform_key,
        user_id=user_id,
    )
    has_photo = await _living_has_photo(state, user_id)
    text = _order_caption_text(
        full_text,
        short_body,
        *trail,
        has_photo=has_photo,
        platform_key=platform_key,
        user_id=user_id,
    )
    markup = build_subsections_menu(platform_key, section_key)
    if use_edit:
        if await _edit_order_screen(bot, state, user_id, text, markup, message=message):
            await _sync_living_nav_anchor(bot, state, user_id, markup)
    elif message is not None:
        await message.answer(text, reply_markup=markup, parse_mode="HTML")


async def _edit_message_to_link_entry_prompt(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
    platform_key: str,
    section_key: str | None,
    subsection_key: str | None = None,
    *,
    message: Message | None = None,
) -> None:
    """استعادة ملخص الخدمة في الرسالة الحية + رسالة خطوة لطلب الرابط."""
    await state.set_state(OrderFlow.enter_link)
    data = await state.get_data()
    service_id = str(data.get("service_id", ""))
    located = await _sync_service_context_in_state(state, service_id)
    if not located:
        return
    service, platform_key, section_key, subsection_key = located
    section_key = section_key or None
    subsection_key = subsection_key or None
    has_photo = await _living_has_photo(state, user_id)
    summary_text = await _build_service_summary_for_state(
        service, state, user_id=user_id, has_photo=has_photo
    )
    await delete_flow_step_prompt(bot, state, chat_id)
    if await _edit_order_screen(
        bot, state, user_id, summary_text, None, message=message
    ):
        await _sync_living_nav_anchor(bot, state, user_id, None)
    await _send_order_link_step_prompt(
        bot,
        state,
        user_id,
        chat_id,
        platform_key=platform_key,
        section_key=section_key,
        subsection_key=subsection_key,
        service=service,
    )
    await state.update_data(last_prompt_id=None)


@router.callback_query(F.data == ORDER_UI_DISMISS)
async def order_ui_dismiss_callback(callback: CallbackQuery) -> None:
    if callback.message:
        try:
            await callback.message.delete()
        except TelegramBadRequest:
            pass
    await callback.answer()


@router.callback_query(F.data == "order:critical")
async def order_critical_points_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """عرض النقاط الهامة الكاملة داخل تدفق طلب خدمة مع إمكانية العودة."""
    if not callback.from_user or not callback.message:
        return
    await _prepare_order_nav(callback, state, bot)
    text = build_critical_points_html(
        "الخدمات والأسعار",
        balance=_user_balance_amount(callback.from_user.id),
        currency_display=CURRENCY_DISPLAY,
    )
    markup = build_order_critical_points_markup()
    if await _edit_order_screen(
        bot,
        state,
        callback.from_user.id,
        text,
        markup,
        message=callback.message,
    ):
        await _sync_living_nav_anchor(bot, state, callback.from_user.id, markup)
    await callback.answer()


@router.callback_query(F.data == "menu:order")
async def order_start_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    await _clear_awaiting_prompt(callback, state, bot=bot)
    await state.clear()
    await reset_flow_transcript(state)
    add_user(callback.from_user.id)
    await register_living_ui_message(state, callback.message, user_id=callback.from_user.id)
    await _show_platforms(
        state,
        bot=bot,
        user_id=callback.from_user.id,
        message=callback.message,
        use_edit=True,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("order:platform:"))
async def handle_platform_selection(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    user_id = await _prepare_order_nav(callback, state, bot, purge_transcript=True)
    if user_id is None:
        return
    platform_key = callback.data.split(":")[-1]
    category = SERVICES.get(platform_key, {})
    await state.update_data(platform_key=platform_key)
    if not category.get("sections") and category.get("direct_items"):
        await _show_services(
            state,
            platform_key,
            section_key="direct",
            bot=bot,
            user_id=user_id,
            message=callback.message,
            use_edit=True,
        )
    else:
        await _show_sections(
            state,
            platform_key,
            bot=bot,
            user_id=user_id,
            message=callback.message,
            use_edit=True,
        )
    await callback.answer()


@router.callback_query(F.data == "order:back:platforms")
async def back_platforms_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """توافق مع لوحات قديمة — نفس order:nav:platforms."""
    await order_nav_platforms(callback, state, bot)


@router.callback_query(F.data.regexp(r"^order:section:[^:]+:[^:]+$"))
async def order_choose_section_callback(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if not callback.from_user or not callback.message:
        return
    user_id = await _prepare_order_nav(callback, state, bot, purge_transcript=True)
    if user_id is None:
        return
    parts = callback.data.split(":", maxsplit=3)
    if len(parts) < 4:
        await callback.answer("تصنيف غير صالح", show_alert=True)
        return
    _, _, platform_key, section_key = parts
    sections = (SERVICES.get(platform_key, {}) or {}).get("sections") or {}
    if section_key not in sections:
        await callback.answer("تصنيف غير صالح", show_alert=True)
        return

    section = sections[section_key]
    if section.get("subsections"):
        await _show_subsections(
            state,
            platform_key,
            section_key,
            bot=bot,
            user_id=user_id,
            message=callback.message,
            use_edit=True,
        )
    else:
        await _show_services(
            state,
            platform_key,
            section_key,
            bot=bot,
            user_id=user_id,
            message=callback.message,
            use_edit=True,
        )
    await callback.answer()


@router.callback_query(F.data == "order:section:back")
async def section_back_to_sections(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """توافق مع لوحات قديمة — نفس order:nav:back."""
    await order_nav_back(callback, state, bot)


@router.callback_query(F.data.regexp(r"^(order:subsection|o:ss):[^:]+:[^:]+:[^:]+$"))
async def order_choose_subsection_callback(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if not callback.from_user or not callback.message:
        return
    user_id = await _prepare_order_nav(callback, state, bot, purge_transcript=True)
    if user_id is None:
        return
    parsed = parse_subsection_callback(callback.data)
    if not parsed:
        await callback.answer("تصنيف فرعي غير صالح", show_alert=True)
        return
    platform_key, section_key, subsection_key = parsed
    section = ((SERVICES.get(platform_key, {}) or {}).get("sections") or {}).get(section_key) or {}
    subsections = section.get("subsections") or {}
    if subsection_key not in subsections:
        await callback.answer("تصنيف فرعي غير صالح", show_alert=True)
        return
    redirect_section_key = str(subsections[subsection_key].get("redirect_section", "")).strip()
    if redirect_section_key:
        redirect_section = ((SERVICES.get(platform_key, {}) or {}).get("sections") or {}).get(redirect_section_key) or {}
        if not redirect_section:
            await callback.answer("التصنيف غير متاح حالياً", show_alert=True)
            return
        await _show_subsections(
            state,
            platform_key,
            redirect_section_key,
            parent_section_key=section_key,
            bot=bot,
            user_id=user_id,
            message=callback.message,
            use_edit=True,
        )
        await callback.answer()
        return
    await _show_services(
        state,
        platform_key,
        section_key,
        subsection_key,
        bot=bot,
        user_id=user_id,
        message=callback.message,
        use_edit=True,
    )
    await callback.answer()


@router.callback_query(F.data == "order:subsection:back")
async def subsection_back_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """توافق مع لوحات قديمة — نفس order:nav:back."""
    await order_nav_back(callback, state, bot)


@router.callback_query(F.data.regexp(r"^os:[^:]+:[^:]+:[^:]+$"))
async def legacy_subsection_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """توافق مع لوحات قديمة — نفس order_choose_subsection_callback."""
    if not callback.from_user or not callback.message:
        return
    user_id = await _prepare_order_nav(callback, state, bot, purge_transcript=True)
    if user_id is None:
        return
    _, platform_key, section_key, subsection_key = callback.data.split(":")
    section = ((SERVICES.get(platform_key, {}) or {}).get("sections") or {}).get(section_key) or {}
    subsections = section.get("subsections") or {}
    subsection = subsections.get(subsection_key) or {}
    if not subsection:
        await callback.answer("تصنيف فرعي غير صالح", show_alert=True)
        return

    redirect_section_key = str(subsection.get("redirect_section", "")).strip()
    if redirect_section_key:
        await _show_subsections(
            state,
            platform_key,
            redirect_section_key,
            parent_section_key=section_key,
            bot=bot,
            user_id=user_id,
            message=callback.message,
            use_edit=True,
        )
    else:
        await _show_services(
            state,
            platform_key,
            section_key,
            subsection_key,
            bot=bot,
            user_id=user_id,
            message=callback.message,
            use_edit=True,
        )
    await callback.answer()


@router.callback_query(F.data.regexp(r"^os:[^:]+:main$"))
async def legacy_back_to_platform_handler(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """توافق مع لوحات قديمة — نفس اختيار المنصة."""
    if not callback.from_user or not callback.message:
        return
    user_id = await _prepare_order_nav(callback, state, bot, purge_transcript=True)
    if user_id is None:
        return
    platform_key = callback.data.split(":")[1].strip()
    if platform_key in SERVICES:
        await _show_sections(
            state,
            platform_key,
            bot=bot,
            user_id=user_id,
            message=callback.message,
            use_edit=True,
        )
    else:
        await _show_platforms(
            state,
            bot=bot,
            user_id=user_id,
            message=callback.message,
            use_edit=True,
        )
    await callback.answer()


@router.callback_query(F.data.startswith("order:service:"))
async def order_choose_service_callback(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if not callback.from_user or not callback.message:
        return
    user_id = await _prepare_order_nav(callback, state, bot, purge_transcript=False)
    if user_id is None:
        return
    service_id = callback.data.split(":", maxsplit=2)[2]
    located = await _sync_service_context_in_state(state, service_id)
    if not located:
        await callback.answer("الخدمة غير موجودة", show_alert=True)
        return
    service, platform_key, section_key, subsection_key = located
    if section_key != "iptv_panel":
        await _clear_iptv_panel_notes_message(
            bot, state, callback.message.chat.id
        )
        await purge_flow_transcript(
            bot, state, user_id, callback.message.chat.id
        )
    await _refresh_provider_limits_cache()

    is_auto_interaction = (
        service.get("auto_quantity") is not None and platform_key != "subscriptions"
    )
    breadcrumb_line = _order_flow_header(
        user_id,
        *_trail_from_order_context(
            platform_key,
            section_key,
            subsection_key,
            service_name=str(service["name"]),
            step_label="تنبيه الخدمة",
        ),
    )
    if is_auto_interaction:
        disclaimer_text = (
            f"{breadcrumb_line}\n\n"
            "<b>تنبيه مهم قبل متابعة هذه الخدمة</b>\n\n"
            "<b>الرابط المطلوب:</b> أرسل رابط آخر منشور موجود حالياً في الحساب. هذا المنشور يستخدم فقط لتحديد الحساب، "
            "ولن تصله المشاهدات من هذا الطلب.\n\n"
            "<b>كيف تعمل الخدمة:</b> بعد التأكيد، ستبدأ المشاهدات والتفاعلات في الوصول تلقائياً للمنشورات الجديدة القادمة فقط.\n\n"
            "<b>تجنب ضياع الطلب:</b> لا تنشئ أكثر من طلب لنفس الرابط في نفس الوقت حتى لا تتداخل الطلبات.\n\n"
            "<b>ما الذي ستحصل عليه:</b> 1000 مشاهدة مع التفاعلات التي اخترتها على كل منشور جديد."
        )
        edited = await _edit_order_screen(
            bot,
            state,
            user_id,
            disclaimer_text,
            build_auto_interactions_disclaimer_keyboard(),
            message=callback.message,
        )
        if not edited and allow_new_message_fallback(callback.message):
            sent = await callback.message.answer(
                disclaimer_text,
                reply_markup=build_auto_interactions_disclaimer_keyboard(),
                parse_mode="HTML",
            )
            await register_living_ui_message(state, sent, user_id=user_id)
        else:
            await register_living_ui_message(state, callback.message, user_id=user_id)
        await state.update_data(last_prompt_id=None)
        await callback.answer()
        return

    await state.set_state(OrderFlow.enter_link)
    section_key = section_key or None
    if section_key != "iptv_panel":
        await reset_flow_transcript(state)
    has_photo = await _living_has_photo(state, user_id)
    summary_text = await _build_service_summary_for_state(
        service,
        state,
        user_id=user_id,
        has_photo=has_photo,
    )
    edited = await _edit_order_screen(
        bot,
        state,
        user_id,
        summary_text,
        None,
        message=callback.message,
    )
    if not edited and allow_new_message_fallback(callback.message):
        sent = await callback.message.answer(
            summary_text,
            parse_mode="HTML",
        )
        await register_living_ui_message(state, sent, user_id=user_id)
        await _sync_living_nav_anchor(bot, state, user_id, None)
    else:
        await register_living_ui_message(state, callback.message, user_id=user_id)
        if edited:
            await _sync_living_nav_anchor(bot, state, user_id, None)
    await state.update_data(last_prompt_id=None)
    await _send_order_link_step_prompt(
        bot,
        state,
        user_id,
        callback.message.chat.id,
        platform_key=platform_key,
        section_key=section_key,
        subsection_key=subsection_key or None,
        service=service,
    )
    await callback.answer()


@router.message(OrderFlow.enter_link)
async def order_enter_link_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user or not message.bot:
        return
    data = await state.get_data()
    service_id = str(data.get("service_id", ""))
    user_id = message.from_user.id
    located = await _sync_service_context_in_state(state, service_id)
    if not located:
        await _finish_order_flow(bot, state, user_id, message.chat.id)
        await _edit_order_living_ui(
            bot, state, user_id, "⚠️ انتهت الجلسة.", _home(user_id)
        )
        return
    service, platform_key, section_key, subsection_key = located
    section_key = section_key or None
    subsection_key = subsection_key or None

    link = (message.text or "").strip()
    is_valid_link, link_error = _validate_order_link(
        link, platform_key, section_key, subsection_key, service
    )
    if not is_valid_link:
        await _flash_order_input_error(message, state, bot, link_error)
        return

    await track_transcript_user_message(state, message)
    await state.update_data(link=link, last_prompt_id=None)

    auto_quantity = service.get("auto_quantity")
    if auto_quantity:
        quantity = auto_quantity
        total_price = order_total_price_dh(service, quantity)
        await state.update_data(confirm_quantity=quantity, confirm_total=total_price)
        await state.set_state(OrderFlow.confirm_order)
        confirm_bc = await _order_breadcrumb_from_state(
            state,
            user_id=user_id,
            service_name=str(service["name"]),
            step_label="تأكيد الطلب",
        )
        invoice_text = build_invoice_text(
            service,
            CURRENCY_DISPLAY,
            quantity,
            total_price,
            link,
            is_fixed_quantity=True,
            breadcrumb_line=confirm_bc,
        )
        confirm_kb = build_order_confirm_keyboard()
        await delete_flow_step_prompt(bot, state, message.chat.id)
        await _edit_order_living_ui(
            bot,
            state,
            user_id,
            invoice_text,
            confirm_kb,
        )
        await acknowledge_then_focus_living_ui(
            bot,
            state,
            user_id,
            message.chat.id,
            ack_text="✅ تم حفظ معلومات الطلب — راجع التفاصيل في الرسالة أعلاه واضغط تأكيد",
            reply_to_message_id=message.message_id,
        )
        await _sync_living_nav_anchor(bot, state, user_id, confirm_kb)
    else:
        await state.set_state(OrderFlow.enter_quantity)
        await _send_order_quantity_step_prompt(
            bot,
            state,
            user_id,
            message.chat.id,
            service=service,
            link=link,
        )


@router.message(OrderFlow.enter_quantity)
async def order_enter_quantity_handler(message: Message, state: FSMContext, bot: Bot) -> None:
    if not message.from_user:
        return
    try:
        quantity = int((message.text or "").strip())
    except ValueError:
        await _flash_order_input_error(
            message, state, bot, "الكمية يجب أن تكون رقماً فقط بدون كلمات أو رموز. مثال: <code>1000</code>."
        )
        return

    data = await state.get_data()
    service_id = str(data.get("service_id", ""))
    link = str(data.get("link", ""))
    user_id = message.from_user.id
    located = await _sync_service_context_in_state(state, service_id)
    if not located or not link:
        await _finish_order_flow(bot, state, user_id, message.chat.id)
        await _edit_order_living_ui(
            bot, state, user_id, "⚠️ انتهت جلسة الطلب.", _home(user_id)
        )
        return
    service, platform_key, section_key, subsection_key = located
    section_key = section_key or None
    subsection_key = subsection_key or None

    is_valid_link, link_error = _validate_order_link(
        link, platform_key, section_key, subsection_key, service
    )
    if not is_valid_link:
        await _flash_order_input_error(message, state, bot, link_error)
        return

    mn, mx = await _effective_limits(service)
    if quantity < mn or quantity > mx:
        await _flash_order_input_error(
            message,
            state,
            bot,
            "الكمية التي كتبتها غير مقبولة لهذه الخدمة.\n"
            f"{build_quantity_limits_text(mn, mx)}\n"
            "اكتب كمية داخل هذه الحدود ثم أرسلها من جديد.",
        )
        return

    await track_transcript_user_message(state, message)

    total_price = order_total_price_dh(service, quantity)
    await state.update_data(confirm_quantity=quantity, confirm_total=total_price)
    await state.set_state(OrderFlow.confirm_order)
    confirm_bc = await _order_breadcrumb_from_state(
        state,
        user_id=user_id,
        service_name=str(service["name"]),
        step_label="تأكيد الطلب",
    )
    invoice_text = build_invoice_text(
        service,
        CURRENCY_DISPLAY,
        quantity,
        total_price,
        link,
        breadcrumb_line=confirm_bc,
    )
    confirm_kb = build_order_confirm_keyboard()
    await delete_flow_step_prompt(bot, state, message.chat.id)
    await _edit_order_living_ui(
        bot,
        state,
        user_id,
        invoice_text,
        confirm_kb,
    )
    await acknowledge_then_focus_living_ui(
        bot,
        state,
        user_id,
        message.chat.id,
        ack_text="✅ تم حفظ معلومات الطلب — راجع التفاصيل في الرسالة أعلاه واضغط تأكيد",
        reply_to_message_id=message.message_id,
    )
    await _sync_living_nav_anchor(bot, state, user_id, confirm_kb)


@router.callback_query(F.data == CB_ORDER_OTHER_SERVICES)
async def order_coming_soon_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    user_id = await _prepare_order_nav(callback, state, bot, purge_transcript=True)
    if user_id is None:
        return
    await _show_order_coming_soon(
        state,
        bot=bot,
        user_id=user_id,
        message=callback.message,
        use_edit=True,
    )
    await callback.answer()


@router.callback_query(F.data == "order:nav:platforms")
async def order_nav_platforms(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    user_id = await _prepare_order_nav(callback, state, bot, purge_transcript=True)
    if user_id is None:
        return
    await _show_platforms(
        state,
        bot=bot,
        user_id=user_id,
        message=callback.message,
        use_edit=True,
    )
    await callback.answer()


@router.callback_query(F.data == "order:nav:home")
async def order_nav_home(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """الرجوع للرئيسية من مسار الطلبات (order:nav:home للرسائل القديمة، menu:home للجديدة)."""
    if not callback.from_user or not callback.message:
        return
    await _clear_awaiting_prompt(callback, state, bot=bot)
    try:
        await navigate_to_main_home(
            callback,
            state,
            bot,
            user_id=callback.from_user.id,
            is_admin=callback.from_user.id == ADMIN_ID,
        )
    except Exception:
        logger.exception("order home navigation failed")
        await callback.answer("تعذر تحديث القائمة الرئيسية", show_alert=True)
        return
    await callback.answer()


def _platform_landing_is_sections(platform_key: str) -> bool:
    """المنصة التي تملك أصنافاً تكون شاشة هبوطها قائمة الأصناف (التي تتضمن الخدمات المباشرة)."""
    return bool((SERVICES.get(platform_key, {}) or {}).get("sections"))


async def _show_platform_landing(
    state: FSMContext,
    platform_key: str,
    *,
    bot: Bot,
    user_id: int,
    message: Message | None,
) -> None:
    """شاشة هبوط المنصة عند الرجوع من خدمة مباشرة: الأصناف إن وُجدت، وإلا قائمة الخدمات المباشرة."""
    if _platform_landing_is_sections(platform_key):
        await _show_sections(
            state, platform_key, bot=bot, user_id=user_id, message=message, use_edit=True
        )
    else:
        await _show_services(
            state, platform_key, "direct", bot=bot, user_id=user_id, message=message, use_edit=True
        )


async def _handle_back_navigation(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    current_state = await state.get_state()
    data = await state.get_data()
    platform_key = str(data.get("platform_key", "")).strip()
    section_key = str(data.get("section_key", "") or "").strip()
    subsection_key = str(data.get("subsection_key", "") or "").strip()
    parent_section_key = str(data.get("parent_section_key", "") or "").strip() or None
    msg = callback.message

    if current_state == OrderFlow.confirm_order.state:
        service_id = str(data.get("service_id", ""))
        located = await _sync_service_context_in_state(state, service_id)
        if not located:
            await _show_home(callback, bot, clear_state=state)
            return
        service, platform_key, section_key, subsection_key = located
        section_key = section_key or None
        subsection_key = subsection_key or None
        if service.get("auto_quantity") is not None:
            await _edit_message_to_link_entry_prompt(
                bot,
                state,
                user_id,
                chat_id,
                platform_key,
                section_key,
                subsection_key,
                message=msg,
            )
        else:
            await state.set_state(OrderFlow.enter_quantity)
            has_photo = await _living_has_photo(state, user_id)
            summary_text = await _build_service_summary_for_state(
                service,
                state,
                user_id=user_id,
                has_photo=has_photo,
            )
            await delete_flow_step_prompt(bot, state, chat_id)
            if await _edit_order_screen(
                bot, state, user_id, summary_text, None, message=msg
            ):
                await _sync_living_nav_anchor(bot, state, user_id, None)
            link_saved = str(data.get("link", ""))
            await _send_order_quantity_step_prompt(
                bot,
                state,
                user_id,
                chat_id,
                service=service,
                link=link_saved,
            )
        return

    if current_state == OrderFlow.enter_quantity.state:
        service_id = str(data.get("service_id", ""))
        located = await _sync_service_context_in_state(state, service_id)
        if located:
            _, platform_key, section_key, subsection_key = located
        await _edit_message_to_link_entry_prompt(
            bot,
            state,
            user_id,
            chat_id,
            platform_key,
            section_key or None,
            (subsection_key if located else str(data.get("subsection_key", "")) or None),
            message=msg,
        )
        return

    if current_state == OrderFlow.enter_link.state:
        await purge_flow_transcript(bot, state, user_id, chat_id)
        has_real_section = section_key.lower() not in {"", "none", "direct"}
        if subsection_key:
            await _show_services(
                state,
                platform_key,
                section_key,
                subsection_key,
                bot=bot,
                user_id=user_id,
                message=msg,
                use_edit=True,
            )
            return
        if has_real_section:
            section = ((SERVICES.get(platform_key, {}) or {}).get("sections") or {}).get(section_key) or {}
            if section.get("subsections"):
                await _show_subsections(
                    state,
                    platform_key,
                    section_key,
                    bot=bot,
                    user_id=user_id,
                    message=msg,
                    use_edit=True,
                )
                return
            await _show_services(
                state,
                platform_key,
                section_key,
                bot=bot,
                user_id=user_id,
                message=msg,
                use_edit=True,
            )
            return
        await _show_platform_landing(
            state, platform_key, bot=bot, user_id=user_id, message=msg
        )
        return

    if current_state == OrderFlow.choose_service.state:
        await delete_flow_step_prompt(bot, state, chat_id)
        has_real_section = section_key.lower() not in {"", "none", "direct"}
        section = ((SERVICES.get(platform_key, {}) or {}).get("sections") or {}).get(section_key) if has_real_section else {}
        if has_real_section and (subsection_key or section.get("subsections")):
            await _show_subsections(
                state,
                platform_key,
                section_key,
                parent_section_key=parent_section_key,
                bot=bot,
                user_id=user_id,
                message=msg,
                use_edit=True,
            )
            return
        if has_real_section:
            await _show_sections(
                state, platform_key, bot=bot, user_id=user_id, message=msg, use_edit=True
            )
            return
        if platform_key and _platform_landing_is_sections(platform_key):
            await _show_sections(
                state, platform_key, bot=bot, user_id=user_id, message=msg, use_edit=True
            )
            return
        await _show_platforms(
            state, bot=bot, user_id=user_id, message=msg, use_edit=True
        )
        return

    if current_state == OrderFlow.choose_subcategory.state:
        await delete_flow_step_prompt(bot, state, chat_id)
        if section_key and platform_key:
            if parent_section_key:
                await _show_subsections(
                    state,
                    platform_key,
                    parent_section_key,
                    bot=bot,
                    user_id=user_id,
                    message=msg,
                    use_edit=True,
                )
            else:
                await _show_sections(
                    state, platform_key, bot=bot, user_id=user_id, message=msg, use_edit=True
                )
            return
        await _show_platforms(
            state, bot=bot, user_id=user_id, message=msg, use_edit=True
        )
        return

    await _show_home(callback, bot, clear_state=state)


@router.callback_query(F.data == "order:nav:back")
async def order_nav_back(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    await clear_last_prompt(callback.message, state, bot=bot)
    await _handle_back_navigation(callback, state, bot)
    await callback.answer()


@router.callback_query(F.data == "order:auto_disclaimer:accept")
async def auto_disclaimer_accept_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    user_id = callback.from_user.id
    await _clear_awaiting_prompt(callback, state, bot=bot)
    data = await state.get_data()
    service_id = str(data.get("service_id", ""))
    located = await _sync_service_context_in_state(state, service_id)
    if not located:
        await callback.answer("الخدمة غير موجودة", show_alert=True)
        return
    service, platform_key, section_key, subsection_key = located

    await state.set_state(OrderFlow.enter_link)
    section_key = section_key or None
    await reset_flow_transcript(state)
    has_photo = await _living_has_photo(state, user_id)
    summary_text = await _build_service_summary_for_state(
        service,
        state,
        user_id=user_id,
        has_photo=has_photo,
    )
    if await _edit_order_screen(
        bot,
        state,
        user_id,
        summary_text,
        None,
        message=callback.message,
    ):
        await register_living_ui_message(state, callback.message, user_id=user_id)
        await _sync_living_nav_anchor(bot, state, user_id, None)
    await _send_order_link_step_prompt(
        bot,
        state,
        user_id,
        callback.message.chat.id,
        platform_key=platform_key,
        section_key=section_key,
        subsection_key=subsection_key or None,
        service=service,
    )
    await state.update_data(last_prompt_id=None)
    await callback.answer()


@router.callback_query(F.data == "order:auto_disclaimer:cancel")
async def auto_disclaimer_cancel_callback(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    user_id = await _prepare_order_nav(callback, state, bot, purge_transcript=True)
    if user_id is None:
        return
    data = await state.get_data()
    platform_key = str(data.get("platform_key", ""))
    section_key = str(data.get("section_key", ""))
    if platform_key in SERVICES and section_key:
        await _show_subsections(
            state,
            platform_key,
            section_key,
            bot=bot,
            user_id=user_id,
            message=callback.message,
            use_edit=True,
        )
    else:
        if callback.from_user and callback.message:
            await _finish_order_flow(
                bot, state, callback.from_user.id, callback.message.chat.id
            )
        else:
            await state.clear()
        if callback.from_user:
            await _show_home(callback, bot)
    await callback.answer()


@router.callback_query(OrderFlow.confirm_order, F.data == "order:confirm:yes")
async def order_confirm_yes(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    if not callback.from_user or not callback.message:
        return
    await _clear_awaiting_prompt(callback, state, bot=bot)
    user_id = callback.from_user.id
    data = await state.get_data()
    service_id = str(data.get("service_id", ""))
    located = await _sync_service_context_in_state(state, service_id)
    if not located:
        await _finish_order_flow(bot, state, user_id, callback.message.chat.id)
        await _edit_order_result(
            callback,
            state,
            bot,
            user_id,
            "الخدمة غير موجودة. ابدأ طلباً جديداً.",
            _home(user_id),
        )
        await callback.answer()
        return
    service, platform_key, section_key, subsection_key = located
    section_key = section_key or None
    subsection_key = subsection_key or None
    link = str(data.get("link", ""))

    auto_qty = service.get("auto_quantity")
    if auto_qty is not None:
        quantity = int(auto_qty)
    else:
        try:
            quantity = int(data.get("confirm_quantity", 0))
        except (TypeError, ValueError):
            quantity = 0
    total_price = to_decimal(data.get("confirm_total", 0))

    if not link or quantity <= 0 or total_price <= to_decimal(0):
        await _finish_order_flow(bot, state, user_id, callback.message.chat.id)
        await _edit_order_result(
            callback,
            state,
            bot,
            user_id,
            "بيانات الطلب غير مكتملة. ابدأ طلباً جديداً وتأكد من اختيار الخدمة وإرسال الرابط والكمية.",
            _home(user_id),
        )
        await callback.answer()
        return

    is_valid_link, link_error = _validate_order_link(
        link, platform_key, section_key, subsection_key, service
    )
    if not is_valid_link:
        await _finish_order_flow(bot, state, user_id, callback.message.chat.id)
        await _edit_order_result(
            callback,
            state,
            bot,
            user_id,
            link_error,
            _home(user_id),
        )
        await callback.answer()
        return

    requires_admin = service_requires_admin(service)

    if auto_qty is not None and not requires_admin:
        qty_ok, qty_error = await _auto_quantity_provider_check(service)
        if not qty_ok:
            await _finish_order_flow(bot, state, user_id, callback.message.chat.id)
            await _edit_order_result(
                callback,
                state,
                bot,
                user_id,
                qty_error,
                _home(user_id),
            )
            await callback.answer()
            return

    if auto_qty is None:
        mn, mx = await _effective_limits(service)
        if quantity < mn or quantity > mx:
            await _finish_order_flow(bot, state, user_id, callback.message.chat.id)
            await _edit_order_result(
                callback,
                state,
                bot,
                user_id,
                "الكمية خارج الحدود المسموحة لهذه الخدمة. ابدأ الطلب من جديد واختر كمية داخل الحد الأدنى والحد الأقصى.",
                _home(user_id),
            )
            await callback.answer()
            return

    expected_total = order_total_price_dh(service, quantity)
    if expected_total != total_price:
        await _finish_order_flow(bot, state, user_id, callback.message.chat.id)
        await _edit_order_result(
            callback,
            state,
            bot,
            user_id,
            "تغير سعر الخدمة أثناء إعداد الطلب. لم يتم تنفيذ الطلب، يرجى إنشاء الطلب من جديد لعرض السعر الصحيح.",
            _home(user_id),
        )
        await callback.answer()
        return

    service_category = " ".join(
        _trail_from_order_context(platform_key, section_key, subsection_key)
    )
    try:
        provider_creds = get_provider_credentials(
            service_category, str(service["name"])
        )
    except RuntimeError as exc:
        await _finish_order_flow(bot, state, user_id, callback.message.chat.id)
        await _edit_order_result(
            callback,
            state,
            bot,
            user_id,
            "<b>تعذر تنفيذ الطلب:</b> إعداد مفاتيح المزود غير مكتمل.\n"
            f"{exc}",
            _home(user_id),
        )
        await callback.answer()
        return

    api_account = provider_creds["account_type"]

    if requires_admin:
        order_id = create_order_with_balance_hold(
            user_id=user_id,
            service_name=str(service["name"]),
            service_id=str(service["id"]),
            link=link,
            quantity=quantity,
            amount=to_float(total_price),
            api_account=api_account,
            initial_status="pending_admin",
            fulfillment_mode=FULFILLMENT_ADMIN,
        )
    else:
        order_id = create_order_with_balance_hold(
            user_id=user_id,
            service_name=str(service["name"]),
            service_id=str(service["id"]),
            link=link,
            quantity=quantity,
            amount=to_float(total_price),
            api_account=api_account,
        )
    if not order_id:
        header = await _order_breadcrumb_from_state(
            state,
            user_id=user_id,
            service_name=str(service["name"]),
            step_label="تأكيد الطلب",
        )
        await _edit_order_result(
            callback,
            state,
            bot,
            user_id,
            f"{header}\n\n"
            f"<b>الرصيد غير كافٍ لإكمال الطلب.</b>\n"
            f"المبلغ المطلوب: <b>{format_amount_2(total_price)} {CURRENCY_DISPLAY}</b>",
            build_order_insufficient_balance_keyboard(),
        )
        await callback.answer()
        return

    try:
        provider_order_id = await _submit_order_to_provider(
            service,
            link,
            quantity,
            api_account,
        )
        if requires_admin:
            assign_provider_order_id(order_id, provider_order_id)
        else:
            set_provider_order_id(order_id, provider_order_id)
    except RuntimeError as exc:
        error_text = str(exc).strip().lower()
        if "min_quantity" in error_text or "min quantity" in error_text:
            if auto_qty is not None:
                limits = await _provider_limits_for_service(service)
                if limits:
                    actual_min, actual_max = limits
                    limit_hint = (
                        f"أقل كمية يقبلها المزود: <b>{actual_min}</b> — "
                        f"أقصى كمية: <b>{actual_max}</b>."
                    )
                else:
                    limit_hint = "تعذر جلب حدود المزود حالياً."
            else:
                actual_min, _ = await _effective_limits(service)
                limit_hint = f"أقل كمية يقبلها المزود حالياً: <b>{actual_min}</b>."
            refund_order(order_id)
            await _finish_order_flow(bot, state, user_id, callback.message.chat.id)
            await _edit_order_result(
                callback,
                state,
                bot,
                user_id,
                "<b>تعذر تنفيذ الطلب لأن الكمية خارج حدود المزود الحالية.</b>\n"
                f"{limit_hint}\n"
                "تم إرجاع المبلغ إلى رصيدك.",
                _home(user_id),
            )
            await callback.answer()
            return
        logger.exception("Provider API failed before provider_id assignment: %s", exc)
        refund_order(order_id)
        await _finish_order_flow(bot, state, user_id, callback.message.chat.id)
        await _edit_order_result(
            callback,
            state,
            bot,
            user_id,
            "<b>تعذر تنفيذ الطلب حالياً بسبب مشكلة عند المزود.</b>\n"
            "لم يضيع رصيدك، وتم إرجاع المبلغ إلى حسابك داخل البوت.",
            _home(user_id),
        )
        await callback.answer()
        return
    except ProviderAuthError as exc:
        logger.exception("Provider API auth failed for account %s: %s", api_account, exc)
        refund_order(order_id)
        await _finish_order_flow(bot, state, user_id, callback.message.chat.id)
        await _edit_order_result(
            callback,
            state,
            bot,
            user_id,
            "<b>تعذر تنفيذ الطلب:</b> مفتاح المزود لهذه المنصة غير صالح.\n"
            "تم إرجاع المبلغ إلى رصيدك. تواصل مع الدعم.",
            _home(user_id),
        )
        await callback.answer()
        return
    except Exception as exc:
        logger.exception("Provider API failed before provider_id assignment: %s", exc)
        refund_order(order_id)
        await _finish_order_flow(bot, state, user_id, callback.message.chat.id)
        await _edit_order_result(
            callback,
            state,
            bot,
            user_id,
            "<b>تعذر تنفيذ الطلب حالياً بسبب مشكلة تقنية.</b>\n"
            "لم يضيع رصيدك، وتم إرجاع المبلغ إلى حسابك داخل البوت.",
            _home(user_id),
        )
        await callback.answer()
        return

    if requires_admin:
        user_record = get_user(user_id)
        trail = _trail_from_order_context(platform_key, section_key, subsection_key)
        order_type_label = " › ".join(trail) if trail else str(service["name"])
        await notify_admin_new_manual_order(
            bot,
            order_id=order_id,
            provider_order_id=provider_order_id,
            user_id=user_id,
            telegram_name=(user_record or {}).get("telegram_name"),
            service_name=str(service["name"]),
            order_type_label=order_type_label,
            link=link,
            quantity=quantity,
            amount=to_float(total_price),
        )

    success_bc = await _order_breadcrumb_from_state(
        state,
        user_id=user_id,
        service_name=str(service["name"]),
        step_label="تم الطلب",
    )
    await _finish_order_flow(bot, state, user_id, callback.message.chat.id)
    receipt = _build_order_success_receipt_html(
        provider_order_id,
        link,
        quantity,
        format_amount_2(total_price),
        breadcrumb_line=success_bc,
        admin_fulfillment=requires_admin,
    )
    success_nav = build_order_success_nav_keyboard()
    await _edit_order_result(callback, state, bot, user_id, receipt, success_nav)
    await _sync_living_nav_anchor(bot, state, user_id, success_nav)
    await callback.answer()
