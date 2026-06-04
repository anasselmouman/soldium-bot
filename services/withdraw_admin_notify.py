# -*- coding: utf-8 -*-
"""إشعار الأدمن بطلب سحب جديد."""

from __future__ import annotations

import logging
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from config import ADMIN_ID
from keyboards.admin import build_admin_withdrawal_actions
from utils.money import format_dh
from utils.withdraw_details import format_withdraw_details_admin_lines, safe_withdraw_details

logger = logging.getLogger(__name__)


async def notify_admin_new_withdrawal(
    bot: Bot,
    *,
    withdrawal_id: int,
    user_id: int,
    telegram_name: str | None,
    amount: float,
    method_label: str,
    details_json: str,
    withdrawal_type: str = "normal",
) -> None:
    details = safe_withdraw_details(details_json)
    detail_lines = format_withdraw_details_admin_lines(details, amount_dh=amount)
    is_referral = withdrawal_type == "referral"
    title = "🎁 طلب سحب إحالة جديد" if is_referral else "💸 طلب سحب جديد"
    lines = [
        f"<b>{title}</b>",
        f"رقم الطلب: <code>#{withdrawal_id}</code>",
        f"معرف المستخدم: <code>{user_id}</code>",
    ]
    if telegram_name:
        lines.append(f"الاسم: <code>{escape(telegram_name)}</code>")
    lines.append(f"المبلغ: <b>{format_dh(amount)}</b>")
    lines.append(f"الطريقة: <b>{escape(method_label)}</b>")
    if detail_lines:
        lines.extend(["", "<b>بيانات الاستلام</b>", *detail_lines])
    lines.append("", "راجع الطلب من لوحة السحب أو الأزرار أدناه.")
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text="\n".join(lines),
            parse_mode="HTML",
            reply_markup=build_admin_withdrawal_actions(withdrawal_id),
        )
    except TelegramBadRequest as exc:
        logger.warning("Failed to notify admin for withdrawal %s: %s", withdrawal_id, exc)
