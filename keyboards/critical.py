# -*- coding: utf-8 -*-
"""أزرار قسم «نقاط هامة»."""

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.nav_labels import BTN_MAIN_HOME


def build_critical_points_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_MAIN_HOME, callback_data="menu:home")
    builder.button(text="🛒 الخدمات والأسعار", callback_data="menu:order")
    builder.adjust(2)
    return builder.as_markup()
