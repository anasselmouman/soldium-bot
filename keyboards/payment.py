# -*- coding: utf-8 -*-
"""لوحات مفاتيح بوابة شحن الرصيد."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.nav_labels import BTN_BACK, BTN_BACK_STEP, BTN_MY_ACCOUNT, attach_cross_area_nav
from keyboards.orders import build_flow_navigation_keyboard
from config import SUPPORT_LINK, WHATSAPP_SUPPORT_LINK
from utils.payment_banks import PAYMENT_METHODS, PaymentMethod


def build_deposit_history_menu(*, show_all_button: bool = True) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if show_all_button:
        builder.button(
            text="📋 إظهار كل سجل الشحن",
            callback_data="account:finance:log_deposit:all",
        )
    builder.button(text="💳 إضافة رصيد", callback_data="account:finance:deposit")
    builder.button(text=BTN_BACK, callback_data="menu:account")
    if show_all_button:
        builder.adjust(1, 2)
    else:
        builder.adjust(2)
    return builder.as_markup()


def build_deposit_nav_footer() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    attach_cross_area_nav(builder)
    return builder.as_markup()


def build_payment_methods_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for method in PAYMENT_METHODS:
        builder.button(text=method.button_label, callback_data=f"deposit:bank:{method.key}")
    builder.button(
        text="📱 بطاقات التعبئة (يصلك 70% فقط) 📱",
        callback_data="deposit:recharge",
    )
    builder.button(
        text="💬 أملك طريقة دفع أخرى",
        callback_data="deposit:other_method",
    )
    builder.adjust(1)
    nav = InlineKeyboardBuilder()
    attach_cross_area_nav(nav)
    builder.attach(nav)
    return builder.as_markup()


def build_deposit_other_payment_menu() -> InlineKeyboardMarkup:
    """تواصل مع الدعم عند امتلاك طريقة دفع غير مدرجة في القائمة."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 تواصل مع خدمة العملاء 💬", url=SUPPORT_LINK)
    builder.button(text="📱 تواصل عبر الواتساب 📱", url=WHATSAPP_SUPPORT_LINK)
    builder.button(text=BTN_BACK_STEP, callback_data="deposit:back")
    builder.adjust(1)
    nav = InlineKeyboardBuilder()
    attach_cross_area_nav(nav)
    builder.attach(nav)
    return builder.as_markup()


def build_bank_detail_living_menu() -> InlineKeyboardMarkup:
    """الرسالة الحية أثناء رفع الإيصال — بدون رئيسية/حساب (على رسالة الخطوة)."""
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_BACK_STEP, callback_data="deposit:back")
    builder.adjust(1)
    return builder.as_markup()


def build_bank_step_nav() -> InlineKeyboardMarkup:
    """أزرار التنقل على برومبت الإيصال."""
    return build_flow_navigation_keyboard("deposit:back")


def build_bank_detail_menu() -> InlineKeyboardMarkup:
    """توافق — نفس أزرار الخطوة."""
    return build_bank_step_nav()


def build_admin_deposit_actions(deposit_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ تأكيد الشحن", callback_data=f"admin:deposit:approve:{deposit_id}")
    builder.button(text="❌ رفض الطلب", callback_data=f"admin:deposit:reject:{deposit_id}")
    builder.adjust(1)
    return builder.as_markup()


def build_recharge_telecom_menu() -> InlineKeyboardMarkup:
    from utils.recharge_telecom import TELECOM_OPERATORS

    builder = InlineKeyboardBuilder()
    builder.button(text=TELECOM_OPERATORS[0].button_label, callback_data="deposit:recharge:orange")
    builder.button(text=TELECOM_OPERATORS[1].button_label, callback_data="deposit:recharge:inwi")
    builder.adjust(2)
    builder.button(text=TELECOM_OPERATORS[2].button_label, callback_data="deposit:recharge:iam")
    builder.adjust(1)
    builder.button(text=BTN_BACK_STEP, callback_data="deposit:recharge:back")
    builder.adjust(1)
    nav = InlineKeyboardBuilder()
    attach_cross_area_nav(nav)
    builder.attach(nav)
    return builder.as_markup()


def build_recharge_face_value_menu() -> InlineKeyboardMarkup:
    from utils.recharge_telecom import RECHARGE_FACE_VALUES_DH

    builder = InlineKeyboardBuilder()
    for value in RECHARGE_FACE_VALUES_DH:
        builder.button(text=f"{value} DH", callback_data=f"deposit:recharge:val:{value}")
    builder.adjust(2, 2, 2)
    builder.button(text=BTN_BACK_STEP, callback_data="deposit:recharge:telecom_menu")
    builder.adjust(2, 2, 2, 1)
    nav = InlineKeyboardBuilder()
    attach_cross_area_nav(nav)
    builder.attach(nav)
    return builder.as_markup()


def build_recharge_code_living_menu() -> InlineKeyboardMarkup:
    """الرسالة الحية أثناء إدخال رمز التعبئة."""
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_BACK_STEP, callback_data="deposit:recharge:back_face")
    builder.adjust(1)
    return builder.as_markup()


def build_recharge_code_step_nav() -> InlineKeyboardMarkup:
    return build_flow_navigation_keyboard("deposit:recharge:back_face")


def build_recharge_code_input_menu() -> InlineKeyboardMarkup:
    return build_recharge_code_step_nav()


def build_admin_recharge_actions(deposit_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ تأكيد التعبئة", callback_data=f"admin:recharge:approve:{deposit_id}")
    builder.button(text="❌ رمز خاطئ/مستعمل", callback_data=f"admin:recharge:reject:{deposit_id}")
    builder.adjust(2)
    return builder.as_markup()


def build_withdraw_methods_menu(methods: list[PaymentMethod]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for method in methods:
        builder.button(
            text=method.menu_button_label(withdraw=True),
            callback_data=f"withdraw:method:{method.key}",
        )
    builder.button(text="📤 سجل السحب", callback_data="account:finance:log_withdraw")
    builder.button(text=BTN_BACK, callback_data="menu:account")
    builder.adjust(*([1] * len(methods)), 1, 1)
    return builder.as_markup()


def build_withdraw_living_step_menu() -> InlineKeyboardMarkup:
    """الرسالة الحية أثناء إدخال مبلغ/تفاصيل السحب."""
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_BACK_STEP, callback_data="account:finance:withdraw")
    builder.adjust(1)
    return builder.as_markup()


def build_withdraw_step_nav() -> InlineKeyboardMarkup:
    return build_flow_navigation_keyboard("account:finance:withdraw")


def build_withdraw_nav_menu() -> InlineKeyboardMarkup:
    return build_withdraw_step_nav()


def build_withdraw_confirm_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ تأكيد طلب السحب", callback_data="withdraw:confirm")
    builder.button(text="🔙 تعديل المعلومات", callback_data="withdraw:back:details")
    builder.button(text="❌ إلغاء", callback_data="account:finance:withdraw")
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def build_withdraw_pending_screen_menu(pending_withdrawal_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ إلغاء طلب السحب المعلق",
        callback_data=f"withdraw:cancel:{pending_withdrawal_id}",
    )
    builder.button(text=BTN_MY_ACCOUNT, callback_data="menu:account")
    builder.adjust(1, 1)
    return builder.as_markup()


def build_withdraw_history_menu(
    *,
    pending_withdrawal_id: int | None = None,
    show_all_button: bool = False,
    show_new_withdraw_button: bool = True,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    if pending_withdrawal_id is not None:
        builder.button(
            text="❌ إلغاء طلب السحب المعلق",
            callback_data=f"withdraw:cancel:{pending_withdrawal_id}",
        )
    if show_all_button:
        builder.button(text="📋 إظهار كل سجل السحب", callback_data="account:finance:log_withdraw:all")
    if show_new_withdraw_button:
        builder.button(text="💸 طلب سحب جديد", callback_data="account:finance:withdraw")
    builder.button(text=BTN_BACK, callback_data="menu:account")
    rows = []
    if pending_withdrawal_id is not None:
        rows.append(1)
    if show_all_button:
        rows.append(1)
    if show_new_withdraw_button:
        rows.append(1)
    rows.append(1)
    builder.adjust(*rows)
    return builder.as_markup()
