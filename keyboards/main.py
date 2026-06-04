from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


def build_main_menu(is_admin: bool) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🛒 الخدمات والأسعار 🛍️", callback_data="menu:order")
    builder.button(text="💳 إضافة رصيد", callback_data="menu:deposit")
    builder.button(text="👤 حسابي وطلباتي", callback_data="menu:account")
    builder.button(text="💰 إربح المال", callback_data="menu:referral")
    builder.button(text="📜 اقرأ قبل الشراء", callback_data="menu:critical")
    builder.button(text="💬 مساعدة ودعم", callback_data="menu:support")
    if is_admin:
        builder.button(text="🛠 لوحة التحكم", callback_data="menu:admin")
        builder.adjust(1, 2, 2, 1, 1)
    else:
        builder.adjust(1, 2, 2, 1)
    return builder.as_markup()
