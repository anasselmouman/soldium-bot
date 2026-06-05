# -*- coding: utf-8 -*-
"""مزامنة حالة الطلب من المورد مع الاسترداد وعمولة الإحالة (مسار مشترك لـ main وطلباتي)."""

from __future__ import annotations

import json
import logging
from html import escape
from typing import TypedDict

from aiogram import Bot

from database import apply_partial_or_full_refund, get_connection, refund_order, update_order_status
from services.referral import flush_pending_referral_level_upgrade_notifications
from utils.money import format_amount, format_dh
from utils.order_status_ar import (
    extract_start_count_from_status_payload,
    format_order_status_ar,
    is_order_in_execution_status,
    normalize_order_status_key,
)
from utils.partial_refund_math import compute_partial_refund_from_status
from utils.smart_notifications import send_smart_notification

logger = logging.getLogger(__name__)


class OrderSyncSnapshot(TypedDict):
    status_key: str
    start_count: int | None
    status_note: str | None


def user_visible_order_ref(order: dict) -> str:
    """مرجع الطلب المعروض للعميل (رقم الطلب لدى الموزّع فقط)."""
    provider_ref = str(order.get("provider_order_id") or "").strip()
    return provider_ref or "—"


def load_order_sync_snapshot(order_id: int) -> OrderSyncSnapshot:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT status, start_count, status_note
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
        ).fetchone()
    if row is None:
        return {"status_key": "", "start_count": None, "status_note": None}
    status_key = normalize_order_status_key(str(row["status"] or ""))
    sc = row["start_count"]
    note = row["status_note"]
    return {
        "status_key": status_key,
        "start_count": int(sc) if sc is not None else None,
        "status_note": str(note) if note is not None else None,
    }


async def apply_provider_status_to_order(
    order: dict,
    status_data: dict,
    bot: Bot | None,
    *,
    notify: bool = True,
) -> OrderSyncSnapshot:
    """
    يطبّق حالة المورد على الطلب: استرداد المدعو ودفع عمولة المُحيل عند الحاجة.
    نفس منطق الحلقة الخلفية في main.py.
    """
    order_id = int(order["id"])
    provider_status_lower = normalize_order_status_key(status_data.get("status", ""))
    if not provider_status_lower:
        return load_order_sync_snapshot(order_id)

    user_id = int(order["user_id"])
    provider_order_id = str(order.get("provider_order_id") or "").strip()

    if provider_status_lower == "canceled":
        refunded = apply_partial_or_full_refund(
            order_id=order_id,
            refund_amount=float(order["amount"]),
            next_status="canceled",
        )
        if refunded and notify and bot is not None:
            ref = escape(user_visible_order_ref(order))
            await send_smart_notification(
                bot,
                user_id,
                f"🔔 تحديث الطلب <code>{ref}</code>\n"
                f"الحالة: <b>{format_order_status_ar('canceled')}</b>\n"
                f"تم إعادة <b>{format_dh(order['amount'])}</b> إلى رصيدك.",
            )
        return load_order_sync_snapshot(order_id)

    if provider_status_lower == "refunded":
        refunded = apply_partial_or_full_refund(
            order_id=order_id,
            refund_amount=float(order["amount"]),
            next_status="refunded",
        )
        if refunded and notify and bot is not None:
            ref = escape(user_visible_order_ref(order))
            await send_smart_notification(
                bot,
                user_id,
                f"🔔 تحديث الطلب <code>{ref}</code>\n"
                f"الحالة: <b>{format_order_status_ar('refunded')}</b>\n"
                f"تمت إضافة <b>{format_dh(order['amount'])}</b> إلى رصيدك.",
            )
        return load_order_sync_snapshot(order_id)

    if provider_status_lower == "partial":
        computed = compute_partial_refund_from_status(
            order["amount"],
            int(order["quantity"]),
            status_data,
        )
        if computed is None:
            logger.warning(
                "Partial order %s (provider %s): could not compute refund from API payload %s",
                order_id,
                provider_order_id,
                status_data,
            )
            return load_order_sync_snapshot(order_id)

        refund_dec, actual_usd, final_customer_dh, calc_method = computed
        refund_amount = float(refund_dec)
        note = f"تم إعادة مبلغ {format_amount(refund_dec)} DH إلى رصيدك مقابل الجزء غير المنفذ."
        audit_payload = json.dumps(
            {
                "calc_method": calc_method,
                "provider_status": status_data.get("status"),
                "cost": status_data.get("cost"),
                "charge": status_data.get("charge"),
                "remains": status_data.get("remains"),
            },
            ensure_ascii=False,
        )
        refunded = apply_partial_or_full_refund(
            order_id=order_id,
            refund_amount=refund_amount,
            next_status="partial",
            status_note=note,
            actual_provider_usd=float(actual_usd),
            final_customer_price_dh=float(final_customer_dh),
            audit_payload_json=audit_payload,
        )
        if refunded and notify and bot is not None:
            ref = escape(user_visible_order_ref(order))
            x_dh = format_dh(refund_amount)
            await send_smart_notification(
                bot,
                user_id,
                "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬\n"
                "💰 <b>تنبيه استرداد مالي:</b>\n"
                f"طلبك رقم <code>{ref}</code> اكتمل بشكل جزئي من المصدر.\n"
                f"الحالة: <b>{format_order_status_ar('partial')}</b>\n"
                "• تم احتساب تكلفة ما تم تنفيذه فقط.\n"
                f"• تم إعادة مبلغ <b>{x_dh}</b> إلى رصيدك بنجاح.\n"
                "▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬",
            )
        return load_order_sync_snapshot(order_id)

    if provider_status_lower == "failed":
        refunded = refund_order(order_id)
        if refunded and notify and bot is not None:
            ref = escape(user_visible_order_ref(order))
            await send_smart_notification(
                bot,
                user_id,
                f"🔔 تحديث الطلب <code>{ref}</code>\n"
                f"الحالة: <b>{format_order_status_ar('failed')}</b>\n"
                f"تم إعادة <b>{format_dh(order['amount'])}</b> إلى رصيدك.",
            )
        return load_order_sync_snapshot(order_id)

    previous_status = str(order.get("status") or "").strip().lower()
    normalized_previous = previous_status.replace("_", " ")
    normalized_current = provider_status_lower.replace("_", " ")
    sc_api = (
        extract_start_count_from_status_payload(status_data)
        if is_order_in_execution_status(normalized_current)
        else None
    )
    prev_sc = order.get("start_count")
    if prev_sc is not None:
        prev_sc = int(prev_sc)

    status_changed = normalized_current != normalized_previous
    sc_changed = (
        is_order_in_execution_status(normalized_current)
        and sc_api is not None
        and sc_api != prev_sc
    )

    if status_changed:
        update_order_status(
            order_id,
            provider_status_lower,
            start_count=sc_api if is_order_in_execution_status(normalized_current) else None,
        )
        if notify and bot is not None:
            await send_smart_notification(
                bot,
                user_id,
                f"🔔 تحديث الطلب <code>{escape(user_visible_order_ref(order))}</code>\n"
                f"الحالة الحالية: <b>{format_order_status_ar(provider_status_lower)}</b>",
            )
    elif sc_changed:
        update_order_status(order_id, provider_status_lower, start_count=sc_api)

    if bot is not None:
        try:
            await flush_pending_referral_level_upgrade_notifications(bot)
        except Exception as exc:
            logger.warning("Referral upgrade notify flush failed: %s", exc)

    return load_order_sync_snapshot(order_id)
