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
from storefront import navigation_tree
from utils.money import format_amount

# --- 1. الدوال المساعدة (Internal Helpers) ---

def _services() -> dict:
    """Storefront-backed Legacy-shaped tree (Phase 9Q)."""
    return navigation_tree()


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

CB_ORDER_BACK_SERVICES = "order:nav:back_services"
BTN_BACK_SERVICES = "🔙 العودة لقائمة الخدمات"


def build_flow_navigation_keyboard(back_callback: str = "order:nav:back") -> InlineKeyboardMarkup:
    """زر رجوع بسيط للتنقل بين الخطوات الخطية"""
    builder = InlineKeyboardBuilder()
    _append_standard_controls(builder, back_callback)
    builder.adjust(2)
    return builder.as_markup()

def build_order_success_nav_keyboard() -> InlineKeyboardMarkup:
    """تنقل بعد إتمام الطلب — العودة للخدمات ثم الرئيسية وحسابي."""
    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_BACK_SERVICES, callback_data=CB_ORDER_BACK_SERVICES)
    builder.button(text=BTN_MAIN_HOME, callback_data=CB_MENU_HOME)
    builder.button(text="👤 حسابي وطلباتي", callback_data="menu:account")
    builder.adjust(1, 2)
    return builder.as_markup()


def build_order_insufficient_balance_keyboard() -> InlineKeyboardMarkup:
    """خيارات عند عدم كفاية الرصيد (سباق نادر بعد بوابة التمويل)."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 تمويل الطلب", callback_data="order_fund:open")
    builder.button(text=BTN_MAIN_HOME, callback_data=CB_MENU_HOME)
    builder.button(text=BTN_BACK_STEP, callback_data="order:nav:back")
    builder.adjust(1, 2)
    return builder.as_markup()


def build_order_funding_methods_keyboard() -> InlineKeyboardMarkup:
    """وسائل الدفع داخل سياق تمويل الطلب (ليس menu:deposit)."""
    from utils.payment_banks import PAYMENT_METHODS

    builder = InlineKeyboardBuilder()
    for method in PAYMENT_METHODS:
        builder.button(
            text=method.button_label,
            callback_data=f"order_fund:bank:{method.key}",
        )
    builder.button(
        text="📱 بطاقات التعبئة (يصلك 70% فقط) 📱",
        callback_data="order_fund:recharge",
    )
    builder.button(
        text="💬 أملك طريقة دفع أخرى",
        callback_data="order_fund:other_method",
    )
    builder.adjust(1)
    _append_standard_controls(builder, "order_fund:back")
    builder.adjust(*([1] * (len(PAYMENT_METHODS) + 2)), 2)
    return builder.as_markup()


def build_order_funding_pending_short_keyboard() -> InlineKeyboardMarkup:
    """بعد شحن جزئي — متابعة التمويل أو إلغاء الطلب المعلق."""
    builder = InlineKeyboardBuilder()
    builder.button(text="💳 متابعة إضافة رصيد", callback_data="order_fund:open")
    builder.button(text="❌ إلغاء الطلب المعلق", callback_data="order_fund:cancel")
    builder.button(text=BTN_MAIN_HOME, callback_data=CB_MENU_HOME)
    builder.adjust(1, 1, 1)
    return builder.as_markup()


def build_order_funding_resume_confirm_keyboard() -> InlineKeyboardMarkup:
    """تأكيد بعد تمويل كافٍ (نفس مسار التأكيد مع رجوع خطوة)."""
    return build_order_confirm_keyboard(
        confirm_callback="order:confirm:yes",
        cancel_callback="order:nav:back",
    )


def build_order_active_link_occupied_keyboard() -> InlineKeyboardMarkup:
    """تنقل عند رفض الطلب لأن الرابط مشغول بطلب نشط."""
    builder = InlineKeyboardBuilder()
    _append_standard_controls(builder, "order:nav:back")
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

_LEGACY_PLATFORM_LABELS = {
    "instagram": "📸 إنستغرام 📸",
    "facebook": "🔵 فيسبوك 🔵",
    "tiktok": "🎵 تيك توك 🎵",
    "youtube": "🔴 يوتيوب 🔴",
    "telegram": "✈️ تيليجرام ✈️",
    "x": "𝕏 تويتر",
}


def _is_catalog_tree() -> bool:
    """True when Telegram is using Catalog-entry navigation keys (ent_* / _root)."""
    try:
        from storefront import get_storefront

        return get_storefront().backend_name == "catalog"
    except Exception:
        return False


def build_platforms_menu() -> InlineKeyboardMarkup:
    """قائمة المنصات / جذور كتالوج الخدمات."""
    builder = InlineKeyboardBuilder()
    builder.button(text=CRITICAL_POINTS_BUTTON_TEXT, callback_data="order:critical")
    tree = _services()
    if _is_catalog_tree():
        # Catalog SoT: root Catalog nodes from entries tree (arbitrary titles).
        for key, cat in tree.items():
            title = str((cat or {}).get("title") or key)
            builder.button(text=title, callback_data=f"order:platform:{key}")
        builder.button(text=BTN_OTHER_SERVICES, callback_data=CB_ORDER_OTHER_SERVICES)
        builder.button(text=BTN_BACK_STEP, callback_data="order:nav:home")
        n = max(len(tree), 1)
        # critical + platforms (pairs) + other + back
        rows = [1]
        remaining = n
        while remaining > 0:
            take = 2 if remaining >= 2 else 1
            rows.append(take)
            remaining -= take
        rows.extend([1, 1])
        builder.adjust(*rows)
        return builder.as_markup()

    for key, text in _LEGACY_PLATFORM_LABELS.items():
        builder.button(text=text, callback_data=f"order:platform:{key}")
    builder.button(text=BTN_SUBSCRIPTIONS, callback_data=CB_ORDER_SUBSCRIPTIONS)
    builder.button(text=BTN_OTHER_SERVICES, callback_data=CB_ORDER_OTHER_SERVICES)
    builder.button(text=BTN_BACK_STEP, callback_data="order:nav:home")
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


def _child_nodes(bucket: dict) -> dict:
    """Child navigable nodes: prefer sections; Catalog may nest deeper in sections."""
    return dict(bucket.get("sections") or {})


def _nested_nodes(bucket: dict) -> dict:
    """Deeper children under a section (subsections or nested Catalog sections)."""
    nested = dict(bucket.get("subsections") or {})
    if nested:
        return nested
    # Catalog entries tree stores deeper nodes under sections recursively.
    return dict(bucket.get("sections") or {})


def build_sections_menu(platform_key: str) -> InlineKeyboardMarkup:
    """قائمة الأقسام الرئيسية داخل منصة / جذر كتالوج."""
    builder = InlineKeyboardBuilder()
    category = _services().get(platform_key, {})
    sections = _child_nodes(category)

    sections_count = 0
    for section_key, section in sections.items():
        if platform_key == "telegram" and section_key == "automatic_interactions":
            continue
        builder.button(
            text=str(section.get("title", "قسم")),
            callback_data=f"order:section:{platform_key}:{section_key}",
        )
        sections_count += 1

    direct_items = list(category.get("direct_items") or [])
    # Catalog root may also place services in items
    if not direct_items and _is_catalog_tree():
        direct_items = list(category.get("items") or [])
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
    """قائمة الأقسام الفرعية أو الخدمات داخل القسم (تدعم عمق كتالوج إضافي)."""
    builder = InlineKeyboardBuilder()
    platform_data = _services().get(platform_key, {})
    section = (_child_nodes(platform_data) or {}).get(section_key, {})

    items = list(section.get("items") or [])
    for item in items:
        builder.button(
            text=_service_button_text(item),
            callback_data=f"order:service:{item.get('id')}",
        )

    subsections = _nested_nodes(section)
    for sub_key, sub in subsections.items():
        # Avoid treating the same dict as both items-host and nested self
        if sub_key == section_key:
            continue
        callback_data = f"o:ss:{platform_key}:{section_key}:{sub_key}"
        builder.button(
            text=str(sub.get("title", "فرعي")),
            callback_data=callback_data,
        )

    back_cb = f"order:platform:{platform_key}"
    _append_standard_controls(builder, back_cb)

    total_btns = len(items) + len(subsections)
    builder.adjust(*([1] * total_btns), order_nav_controls_count(back_cb))
    return builder.as_markup()


def build_services_menu(
    platform_key: str, section_key: str | None, subsection_key: str | None = None
) -> InlineKeyboardMarkup:
    """القائمة النهائية لعرض الخدمات للاختيار (عمق كتالوج إضافي عبر order:node)."""
    builder = InlineKeyboardBuilder()
    category = _services().get(platform_key, {})
    sections = _child_nodes(category)
    section = sections.get(section_key) if section_key else {}

    if subsection_key:
        nested = _nested_nodes(section or {})
        sub = nested.get(subsection_key, {})
        items = list(sub.get("items") or [])
        # Deeper Catalog nodes under this subsection
        deeper = _child_nodes(sub)
        for deep_key, deep in deeper.items():
            builder.button(
                text=str(deep.get("title", "قسم")),
                callback_data=f"order:node:{deep_key}",
            )
        back_callback = f"order:section:{platform_key}:{section_key}"
    elif not section_key or str(section_key).lower() in {"none", "direct"}:
        items = list(category.get("direct_items") or [])
        if not items and _is_catalog_tree():
            items = list(category.get("items") or [])
        back_callback = "order:nav:platforms"
    else:
        items = list((section or {}).get("items") or [])
        back_callback = f"order:platform:{platform_key}"

    for item in items:
        builder.button(
            text=_service_button_text(item),
            callback_data=f"order:service:{item.get('id')}",
        )

    _append_standard_controls(builder, back_callback)
    nav_n = order_nav_controls_count(back_callback)
    extra = 0
    if subsection_key:
        nested = _nested_nodes(section or {})
        sub = nested.get(subsection_key, {})
        extra = len(_child_nodes(sub))
    total = len(items) + extra
    if total:
        builder.adjust(*([1] * total), nav_n)
    else:
        builder.adjust(nav_n)
    return builder.as_markup()


def build_catalog_node_menu(entry_id: str, *, back_callback: str) -> InlineKeyboardMarkup:
    """Arbitrary-depth Catalog node: list child nodes + services under entry_id."""
    builder = InlineKeyboardBuilder()
    # Walk tree for entry_id
    node = _find_catalog_node(entry_id)
    if not node:
        _append_standard_controls(builder, back_callback)
        builder.adjust(order_nav_controls_count(back_callback))
        return builder.as_markup()

    for child_key, child in _child_nodes(node).items():
        builder.button(
            text=str(child.get("title", "قسم")),
            callback_data=f"order:node:{child_key}",
        )
    for item in list(node.get("items") or []) + list(node.get("direct_items") or []):
        builder.button(
            text=_service_button_text(item),
            callback_data=f"order:service:{item.get('id')}",
        )
    _append_standard_controls(builder, back_callback)
    total = len(_child_nodes(node)) + len(node.get("items") or []) + len(
        node.get("direct_items") or []
    )
    nav_n = order_nav_controls_count(back_callback)
    if total:
        builder.adjust(*([1] * total), nav_n)
    else:
        builder.adjust(nav_n)
    return builder.as_markup()


def _find_catalog_node(entry_id: str) -> dict | None:
    eid = str(entry_id or "").strip()
    if not eid:
        return None

    def walk(bucket: dict) -> dict | None:
        if str(bucket.get("entry_id") or "") == eid:
            return bucket
        for child in _child_nodes(bucket).values():
            found = walk(child)
            if found is not None:
                return found
        for child in (bucket.get("subsections") or {}).values():
            found = walk(child)
            if found is not None:
                return found
        return None

    for root in _services().values():
        found = walk(root or {})
        if found is not None:
            return found
    return None


def build_auto_interactions_disclaimer_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="✅ قرأت الشروط وأوافق", callback_data="order:auto_disclaimer:accept")
    builder.button(text="❌ إلغاء", callback_data="order:auto_disclaimer:cancel")
    builder.adjust(1)
    return builder.as_markup()
