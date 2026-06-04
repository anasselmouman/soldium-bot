from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.nav_labels import BTN_BACK_STEP, BTN_MAIN_HOME


def build_admin_menu() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💰 الرصيد التقني", callback_data="admin:system_balance")
    builder.button(text="📊 الإحصائيات", callback_data="admin:stats")
    builder.button(text="📢 إعلان", callback_data="admin:broadcast")
    builder.button(text="🧾 آخر 10 طلبات", callback_data="admin:last_orders")
    builder.button(text="🛠 تعديل حالة طلب", callback_data="admin:edit_order_status")
    builder.button(text="🎖️ تعيين شريك", callback_data="admin:assign_partner")
    builder.button(text="💸 معالجة طلبات السحب", callback_data="admin:withdrawals")
    builder.button(text=BTN_MAIN_HOME, callback_data="menu:home")
    builder.adjust(2, 2, 2, 1)
    return builder.as_markup()


def build_admin_withdrawal_actions(withdrawal_id: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(
        text="❌ رفض الطلب",
        callback_data=f"admin:withdraw:reject:{withdrawal_id}",
    )
    builder.button(
        text="✅ تم معالجة السحب",
        callback_data=f"admin:withdraw:approve:{withdrawal_id}",
    )
    builder.button(text=BTN_BACK_STEP, callback_data="menu:admin")
    builder.adjust(2, 1)
    return builder.as_markup()
