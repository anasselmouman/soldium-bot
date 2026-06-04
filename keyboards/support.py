# -*- coding: utf-8 -*-
"""أزرار قسم الدعم الفني."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config import SUPPORT_LINK
from keyboards.nav_labels import BTN_MAIN_HOME


def build_support_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 تواصل مع خدمة العملاء 💬", url=SUPPORT_LINK)
    builder.adjust(1)
    builder.button(text=BTN_MAIN_HOME, callback_data="menu:home")
    builder.adjust(1)
    return builder.as_markup()
