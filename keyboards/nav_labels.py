# -*- coding: utf-8 -*-
"""تسميات موحّدة لأزرار التنقل — عودة حقيقية مقابل انتقال لمنطقة ثابتة."""

from aiogram.utils.keyboard import InlineKeyboardBuilder

BTN_MAIN_HOME = "🏠 الصفحة الرئيسية"
BTN_MY_ACCOUNT = "👤 حسابي وطلباتي"
BTN_BACK = "🔙 رجوع"
BTN_BACK_STEP = "🔙 رجوع"
CB_MENU_HOME = "menu:home"
CB_MENU_ACCOUNT = "menu:account"

# رجوع يعيد نفس الشاشة الرئيسية — لا نكرّر زرّين
_HOME_EQUIVALENT_BACK = frozenset({CB_MENU_HOME, "order:nav:home"})


def attach_cross_area_nav(builder: InlineKeyboardBuilder) -> None:
    """انتقال ثابت للرئيسية أو حسابي (ليس رجوعاً لخطوة سابقة في المكدس)."""
    builder.button(text=BTN_MAIN_HOME, callback_data=CB_MENU_HOME)
    builder.button(text=BTN_MY_ACCOUNT, callback_data=CB_MENU_ACCOUNT)
    builder.adjust(2)


def order_back_button_label(back_callback: str) -> str:
    """تسمية موحّدة لزر الرجوع داخل مسار الطلبات (منطقة سابقة وحيدة)."""
    return BTN_BACK_STEP


def order_nav_controls_count(back_callback: str) -> int:
    """عدد أزرار شريط التنقل السفلي (رئيسية + رجوع إن وُجد)."""
    return 1 if back_callback in _HOME_EQUIVALENT_BACK else 2
