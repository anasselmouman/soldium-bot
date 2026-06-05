# -*- coding: utf-8 -*-
"""إشعار الأدمن بطلب خدمة يحتاج تنفيذاً يدوياً."""

from __future__ import annotations

import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from config import ADMIN_ID
from keyboards.admin import build_admin_manual_order_actions
from utils.money import format_dh

logger = logging.getLogger(__name__)


def _customer_contact_html(user_id: int, telegram_name: str | None) -> str:
    display = str(telegram_name or "").strip()
    if not display:
        display = f"مستخدم {user_id}"
    return f'<a href="tg://user?id={user_id}">{escape(display)}</a>'


async def notify_admin_new_manual_order(
    bot: Bot,
    *,
    order_id: int,
    provider_order_id: str,
    user_id: int,
    telegram_name: str | None,
    service_name: str,
    order_type_label: str,
    link: str,
    quantity: int,
    amount: float,
) -> None:
    order_ref = escape(str(provider_order_id).strip()) or "—"
    lines = [
        "<b>📋 طلب جديد — تنفيذ يدوي</b>",
        f"رقم الطلب: <code>#{order_ref}</code>",
        f"نوع الطلب: <b>{escape(order_type_label)}</b>",
        f"الخدمة: <b>{escape(service_name)}</b>",
        "",
        "<b>العميل</b>",
        f"• {_customer_contact_html(user_id, telegram_name)}",
        f"• المعرف: <code>{user_id}</code>",
        "",
        f"• الرابط / البيانات: <code>{escape(link)}</code>",
        f"• الكمية: <code>{quantity}</code>",
        f"• المبلغ: <b>{format_dh(amount)}</b>",
        "",
        "اضغط على اسم العميل للتواصل معه، أو استخدم الأزرار أدناه.",
    ]
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text="\n".join(lines),
            parse_mode="HTML",
            reply_markup=build_admin_manual_order_actions(order_id),
            disable_web_page_preview=True,
        )
    except TelegramBadRequest as exc:
        logger.warning("Failed to notify admin for manual order %s: %s", order_id, exc)
