# -*- coding: utf-8 -*-
from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from keyboards.nav_labels import (
    BTN_BACK_STEP,
    BTN_MAIN_HOME,
    CB_MENU_HOME,
    _HOME_EQUIVALENT_BACK,
    order_back_button_label,
    order_nav_controls_count,
)
from services_config import SERVICES
from utils.money import format_amount

# --- 1. الدوال المساعدة (Internal Helpers) ---

def _service_button_text(item: dict) -> str:
    """تنسيق نص زر الخدمة مع السعر بوضوح"""
    name = item.get('name', 'خدمة غير معروفة')
    price = item.get('price', 0)
    unit_label = "1" if item.get("price_per_unit") else "1000"
    return f"{name}\n[ {format_amount(price)} DH لكل {unit_label} ]"

def _append_standard_controls(builder: InlineKeyboardBuilder, back_callback: str) -> None:
    """الرئيسية أولاً ثم الرجوع، فيظهر زر الرجوع على اليمين؛ الرجوع فقط إن لم يكن تكراراً للرئيسية."""
    builder.button(text=BTN_MAIN_HOME, callback_data=CB_MENU_HOME)
    if back_callback not in _HOME_EQUIVALENT_BACK:
        builder.button(
            text=order_back_button_label(back_callback),
            callback_data=back_callback,
        )

# --- 2. أزرار التحكم في التدفق (Flow Control) ---

def build_flow_navigation_keyboard(back_callback: str = "order:nav:back") -> InlineKeyboardMarkup:
    """زر رجوع بسيط للتنقل بين الخطوات الخطية"""
    builder = InlineKeyboardBuilder()
    _append_standard_controls(builder, back_callback)
    builder.adjust(2)
    return builder.as_markup()

def build_order_success_nav_keyboard() -> InlineKeyboardMarkup:
    """تنقل بعد إتمام الطلب — الرئيسية وحسابي فقط."""
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_MAIN_HOME, callback_data=CB_MENU_HOME)
    builder.button(text="👤 حسابي وطلباتي", callback_data="menu:account")
    builder.adjust(2)
    return builder.as_markup()


def build_order_confirm_keyboard(
    confirm_callback: str = "order:confirm:yes",
    cancel_callback: str = "order:nav:back",
) -> InlineKeyboardMarkup:
    """قائمة تأكيد الطلب النهائي"""
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ تأكيد الطلب", callback_data=confirm_callback)
    _append_standard_controls(builder, cancel_callback)
    builder.adjust(1, 2)
    return builder.as_markup()

# --- 3. دوال بناء القوائم الديناميكية (Dynamic Menus) ---

CRITICAL_POINTS_BUTTON_TEXT = "⚠️ 📜 ━━━ اقرأ قبل الشراء ━━━"
BTN_OTHER_SERVICES = "📦 خدمات أخرى"
BTN_SUBSCRIPTIONS = "🔄 إشتراكات"
CB_ORDER_OTHER_SERVICES = "order:coming:other_services"
CB_ORDER_SUBSCRIPTIONS = "order:platform:subscriptions"


def build_platforms_menu() -> InlineKeyboardMarkup:
    """قائمة المنصات الاجتماعية"""
    builder = InlineKeyboardBuilder()
    builder.button(text=CRITICAL_POINTS_BUTTON_TEXT, callback_data="order:critical")
    platforms = {
        "instagram": "📸 إنستغرام 📸",
        "facebook": "🔵 فيسبوك 🔵",
        "tiktok": "🎵 تيك توك 🎵",
        "youtube": "🔴 يوتيوب 🔴",
        "telegram": "✈️ تيليجرام ✈️",
        "x": "𝕏 تويتر"
    }
    for key, text in platforms.items():
        builder.button(text=text, callback_data=f"order:platform:{key}")
    builder.button(text=BTN_SUBSCRIPTIONS, callback_data=CB_ORDER_SUBSCRIPTIONS)
    builder.button(text=BTN_OTHER_SERVICES, callback_data=CB_ORDER_OTHER_SERVICES)
    builder.button(text=BTN_BACK_STEP, callback_data="order:nav:home")
    # اقرأ قبل الشراء + الرجوع: سطر كامل لكل منهما؛ باقي الأزرار: زران في كل سطر.
    builder.adjust(1, 2, 2, 2, 2, 1)
    return builder.as_markup()


def build_order_coming_soon_markup() -> InlineKeyboardMarkup:
    """رجوع لقائمة المنصات من صفحة «قريباً»."""
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_BACK_STEP, callback_data="order:nav:platforms")
    builder.adjust(1)
    return builder.as_markup()


def build_order_critical_points_markup() -> InlineKeyboardMarkup:
    """العودة من «نقاط هامة» أثناء تدفق طلب خدمة."""
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_MAIN_HOME, callback_data=CB_MENU_HOME)
    builder.button(text=BTN_BACK_STEP, callback_data="order:nav:platforms")
    builder.adjust(2)
    return builder.as_markup()

def build_sections_menu(platform_key: str) -> InlineKeyboardMarkup:
    """قائمة الأقسام الرئيسية داخل منصة معينة"""
    builder = InlineKeyboardBuilder()
    category = SERVICES.get(platform_key, {})
    sections = category.get("sections") or {}
    
    # إضافة الأقسام
    sections_count = 0
    for section_key, section in sections.items():
        if platform_key == "telegram" and section_key == "automatic_interactions":
            continue
        builder.button(
            text=str(section.get("title", "قسم")),
            callback_data=f"order:section:{platform_key}:{section_key}",
        )
        sections_count += 1
    
    # إضافة الخدمات المباشرة (إن وجدت)
    direct_items = category.get("direct_items") or []
    for item in direct_items:
        builder.button(
            text=_service_button_text(item),
            callback_data=f"order:service:{item.get('id')}",
        )
    
    back_cb = "order:nav:platforms"
    _append_standard_controls(builder, back_cb)

    total_custom_btns = sections_count + len(direct_items)
    builder.adjust(*([1] * total_custom_btns), order_nav_controls_count(back_cb))
    return builder.as_markup()

def build_subsections_menu(platform_key: str, section_key: str) -> InlineKeyboardMarkup:
    """قائمة الأقسام الفرعية أو الخدمات داخل القسم"""
    builder = InlineKeyboardBuilder()
    platform_data = SERVICES.get(platform_key, {})
    section = platform_data.get("sections", {}).get(section_key, {})
    
    # 1. الخدمات المباشرة في هذا القسم
    items = section.get("items") or []
    for item in items:
        builder.button(text=_service_button_text(item), callback_data=f"order:service:{item.get('id')}")
    
    # 2. الأقسام الفرعية (Sub-sections)
    subsections = section.get("subsections") or {}
    for sub_key, sub in subsections.items():
        callback_data = f"o:ss:{platform_key}:{section_key}:{sub_key}"
        builder.button(
            text=str(sub.get("title", "فرعي")), 
            callback_data=callback_data
        )
    
    back_cb = f"order:platform:{platform_key}"
    _append_standard_controls(builder, back_cb)

    total_btns = len(items) + len(subsections)
    builder.adjust(*([1] * total_btns), order_nav_controls_count(back_cb))
    return builder.as_markup()

def build_services_menu(platform_key: str, section_key: str | None, subsection_key: str | None = None) -> InlineKeyboardMarkup:
    """القائمة النهائية لعرض الخدمات للاختيار"""
    builder = InlineKeyboardBuilder()
    category = SERVICES.get(platform_key, {})
    sections = category.get("sections") or {}
    section = sections.get(section_key) if section_key else {}
    
    if subsection_key:
        # إذا كنا داخل قسم فرعي
        items = section.get("subsections", {}).get(subsection_key, {}).get("items") or []
        back_callback = f"order:section:{platform_key}:{section_key}"
    elif not section_key or str(section_key).lower() in {"none", "direct"}:
        # مسار الخدمات المباشرة على مستوى المنصة
        items = category.get("direct_items") or []
        back_callback = "order:nav:platforms"
    else:
        # إذا كنا داخل قسم رئيسي مباشرة
        items = section.get("items") or []
        back_callback = f"order:platform:{platform_key}"

    for item in items:
        builder.button(text=_service_button_text(item), callback_data=f"order:service:{item.get('id')}")
    
    _append_standard_controls(builder, back_callback)
    nav_n = order_nav_controls_count(back_callback)
    if items:
        builder.adjust(*([1] * len(items)), nav_n)
    else:
        builder.adjust(nav_n)
    return builder.as_markup()

def build_auto_interactions_disclaimer_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ قرأت الشروط وأوافق", callback_data="order:auto_disclaimer:accept")
    builder.button(text="❌ إلغاء", callback_data="order:auto_disclaimer:cancel")
    builder.adjust(1)
    return builder.as_markup()