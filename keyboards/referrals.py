# -*- coding: utf-8 -*-

"""Inline keyboards for the referral «living» UI."""



from __future__ import annotations



from aiogram.types import InlineKeyboardMarkup

from aiogram.utils.keyboard import InlineKeyboardBuilder



from keyboards.nav_labels import BTN_BACK_STEP, BTN_MAIN_HOME, CB_MENU_HOME
from keyboards.orders import build_flow_navigation_keyboard
from services.referral import MAX_REFERRAL_LEVEL
from utils.payment_banks import PAYMENT_METHODS, PaymentMethod


def build_referral_step_nav(back_callback: str = "referral:menu") -> InlineKeyboardMarkup:
    return build_flow_navigation_keyboard(back_callback)


def build_referral_withdraw_step_nav() -> InlineKeyboardMarkup:
    return build_referral_step_nav("referral:withdraw")





def build_referral_main_menu(*, referral_level: int) -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()

    if referral_level < MAX_REFERRAL_LEVEL:

        builder.button(text="🚀 ترقية مستواي", callback_data="referral:upgrade")

    builder.button(text="👥 المدعوين", callback_data="referral_list:0")

    builder.button(text="🔁 تحويل للرصيد", callback_data="referral:transfer")

    builder.button(text="💳 سحب الأرباح", callback_data="referral:withdraw")

    builder.button(text=BTN_MAIN_HOME, callback_data=CB_MENU_HOME)

    if referral_level < MAX_REFERRAL_LEVEL:

        builder.adjust(1, 2, 1, 1)

    else:

        builder.adjust(2, 1, 1)

    return builder.as_markup()





def build_referral_upgrade_menu() -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()

    builder.button(text=BTN_BACK_STEP, callback_data="referral:menu")

    builder.adjust(1)

    return builder.as_markup()





def build_referral_list_menu(*, page: int, has_next: bool) -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()

    if has_next:

        builder.button(text="⬅️ الصفحة التالية", callback_data=f"referral_list:{page + 1}")

    builder.button(text=BTN_BACK_STEP, callback_data="referral_main")

    if has_next:

        builder.adjust(1, 1)

    else:

        builder.adjust(1)

    return builder.as_markup()





def build_referral_back_menu() -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()

    builder.button(text=BTN_BACK_STEP, callback_data="referral:menu")

    builder.adjust(1)

    return builder.as_markup()





def build_referral_withdraw_methods_menu(

    methods: tuple[PaymentMethod, ...] = PAYMENT_METHODS,

) -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()

    for method in methods:

        builder.button(
            text=method.menu_button_label(withdraw=True),
            callback_data=f"referral:withdraw:method:{method.key}",
        )

    builder.button(text=BTN_BACK_STEP, callback_data="referral:menu")

    builder.adjust(*([1] * len(methods)), 1)

    return builder.as_markup()





def build_referral_withdraw_confirm_menu() -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()

    builder.button(text="✅ تأكيد سحب أرباح الإحالة", callback_data="referral:withdraw:confirm")

    builder.button(text="🔙 تعديل البيانات", callback_data="referral:withdraw:back_details")

    builder.button(text="❌ إلغاء", callback_data="referral:menu")

    builder.adjust(1, 1, 1)

    return builder.as_markup()





def build_referral_withdraw_pending_menu(pending_withdrawal_id: int) -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()

    builder.button(

        text="❌ إلغاء سحب الإحالة المعلق",

        callback_data=f"referral:withdraw:cancel:{pending_withdrawal_id}",

    )

    builder.button(text=BTN_BACK_STEP, callback_data="referral:menu")

    builder.adjust(1, 1)

    return builder.as_markup()

