# -*- coding: utf-8 -*-
"""إشعار الأدمن بطلب خدمة يحتاج تنفيذاً يدوياً."""

from __future__ import annotations

import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from config import ADMIN_ID
from keyboards.admin import build_admin_manual_order_actions
from services.admin_notification_log import log_admin_notification
from utils.money import format_dh
from utils.order_admin_format import order_refs_block_html, platform_label, provider_label

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
    platform_key: str | None = None,
    platform_title: str | None = None,
    provider_slug: str | None = None,
    api_account: str | None = None,
) -> None:
    platform = platform_label(platform_title, platform_key)
    provider = provider_label(provider_slug, api_account)
    lines = [
        "<b>📋 طلب جديد — تنفيذ يدوي</b>",
        order_refs_block_html(provider_order_id, order_id),
        f"المنصة: <b>{escape(platform)}</b>",
        f"المزوّد: <b>{escape(provider)}</b>",
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
    body = "\n".join(lines)
    telegram_sent = False
    telegram_error: str | None = None
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=body,
            parse_mode="HTML",
            reply_markup=build_admin_manual_order_actions(order_id),
            disable_web_page_preview=True,
        )
        telegram_sent = True
    except TelegramBadRequest as exc:
        telegram_error = str(exc)
        logger.warning("Failed to notify admin for manual order %s: %s", order_id, exc)
    log_admin_notification(
        category="manual_order",
        title="طلب جديد — تنفيذ يدوي",
        body_html=body,
        severity="warning",
        entity_type="order",
        entity_id=str(order_id),
        user_id=user_id,
        telegram_sent=telegram_sent,
        telegram_error=telegram_error,
        payload={
            "order_id": order_id,
            "provider_order_id": provider_order_id,
            "service_name": service_name,
            "order_type_label": order_type_label,
            "platform": platform,
            "provider": provider,
            "quantity": quantity,
            "amount": amount,
        },
    )
