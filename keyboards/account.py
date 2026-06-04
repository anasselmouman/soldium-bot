# -*- coding: utf-8 -*-

"""لوحة «حسابي» — أزرار التنقل الحي داخل تلغرام."""



from aiogram.types import InlineKeyboardMarkup

from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.nav_labels import BTN_BACK, CB_MENU_HOME, CB_MENU_ACCOUNT





def build_account_dashboard_markup() -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()

    builder.button(text="📦 طلباتي السابقة", callback_data="account:orders")

    builder.adjust(1)

    builder.button(text="💳 إضافة رصيد", callback_data="account:finance:deposit")

    builder.button(text="💸 سحب الرصيد", callback_data="account:finance:withdraw")

    builder.adjust(2)

    builder.button(text="📥 سجل الشحن", callback_data="account:finance:log_deposit")

    builder.button(text="📤 سجل السحب", callback_data="account:finance:log_withdraw")

    builder.adjust(2)

    builder.button(text=BTN_BACK, callback_data=CB_MENU_HOME)

    builder.adjust(1)

    return builder.as_markup()





def build_account_orders_markup(*, show_all_button: bool = True, include_search: bool = True) -> InlineKeyboardMarkup:

    builder = InlineKeyboardBuilder()

    if show_all_button:

        builder.button(text="📋 أظهر كل الطلبات", callback_data="account:orders:all")

    if include_search:

        builder.button(text="🔎 البحث عن طلب", callback_data="account:orders:search")

    builder.button(text=BTN_BACK, callback_data=CB_MENU_ACCOUNT)

    if show_all_button and include_search:

        builder.adjust(1, 1, 1)

    elif show_all_button or include_search:

        builder.adjust(1, 1)

    else:

        builder.adjust(1)

    return builder.as_markup()

