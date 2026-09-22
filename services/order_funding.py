# -*- coding: utf-8 -*-
"""Pending order funding helpers — durable intent resume (identity-safe)."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot

from config import CURRENCY_DISPLAY
from database import (
    PendingOrderFundingRecord,
    delete_pending_order_funding_by_intent,
    get_pending_order_funding,
    get_pending_order_funding_by_intent,
    get_user,
)
from utils.money import format_amount_2, to_decimal, to_float
from utils.services import find_service_location, order_total_price_dh
from utils.smart_notifications import send_smart_notification

logger = logging.getLogger(__name__)

PENDING_FUNDING_INTENT_ID_KEY = "pending_funding_intent_id"
ORDER_FUND_CONTEXT_KEY = "order_fund_context"


def format_funding_amounts(
    *,
    order_total: float,
    balance: float,
) -> tuple[str, str, str]:
    total_s = format_amount_2(order_total)
    bal_s = format_amount_2(balance)
    remaining = max(0.0, to_float(to_decimal(order_total) - to_decimal(balance)))
    rem_s = format_amount_2(remaining)
    return total_s, bal_s, rem_s


def build_order_funding_screen_html(
    *,
    order_total: float,
    balance: float,
    service_name: str,
) -> str:
    total_s, bal_s, rem_s = format_funding_amounts(
        order_total=order_total, balance=balance
    )
    return (
        "🏠 <b>طلب جديد</b> &gt; <b>تمويل الطلب</b>\n\n"
        f"الخدمة: <b>{service_name}</b>\n"
        f"إجمالي الطلب: <b>{total_s} {CURRENCY_DISPLAY}</b>\n"
        f"رصيدك الحالي: <b>{bal_s} {CURRENCY_DISPLAY}</b>\n"
        f"المبلغ المتبقي لإتمام الطلب: <b>{rem_s} {CURRENCY_DISPLAY}</b>\n\n"
        "رصيدك غير كافٍ حالياً. اختر وسيلة الدفع لإضافة رصيد إلى حسابك "
        "ثم تابع تأكيد هذا الطلب.\n"
        "<i>يُضاف المبلغ إلى رصيدك العادي بعد اعتماد الإدارة — "
        "ولن يُنفَّذ الطلب تلقائياً.</i>"
    )


def build_pending_still_short_html(
    *,
    order_total: float,
    balance: float,
    service_name: str,
) -> str:
    total_s, bal_s, rem_s = format_funding_amounts(
        order_total=order_total, balance=balance
    )
    return (
        "🏠 <b>طلب معلق</b>\n\n"
        f"الخدمة: <b>{service_name}</b>\n"
        f"إجمالي الطلب: <b>{total_s} {CURRENCY_DISPLAY}</b>\n"
        f"رصيدك الحالي: <b>{bal_s} {CURRENCY_DISPLAY}</b>\n"
        f"المبلغ المتبقي: <b>{rem_s} {CURRENCY_DISPLAY}</b>\n\n"
        "تمت إضافة رصيد، لكنه ما زال غير كافٍ لإتمام هذا الطلب.\n"
        "يمكنك متابعة إضافة رصيد أو إلغاء الطلب المعلق "
        "(الرصيد المضاف يبقى في حسابك)."
    )


def build_pending_invalidated_html() -> str:
    return (
        "<b>تعذر متابعة الطلب السابق</b>\n"
        "تغيّرت بيانات الخدمة أو السعر أو لم يعد الطلب صالحاً.\n"
        "تم إلغاء الطلب المعلق. يمكنك بدء طلب جديد."
    )


def intent_price_matches_live(intent: PendingOrderFundingRecord, live_total: object) -> bool:
    return to_decimal(intent["amount_dh"]) == to_decimal(live_total)


def resolve_live_total_for_intent(
    intent: PendingOrderFundingRecord,
) -> tuple[dict[str, Any], object] | None:
    """Return (service, live_total) or None if service missing."""
    located = find_service_location(str(intent["service_id"]))
    if not located:
        return None
    service, _platform, _section, _sub = located
    qty = int(intent["quantity"])
    auto = bool(int(intent.get("auto_quantity") or 0))
    if auto and service.get("auto_quantity") is not None:
        qty = int(service["auto_quantity"])
    live = order_total_price_dh(service, qty)
    return service, live


async def resume_pending_order_after_deposit(
    bot: Bot,
    user_id: int,
    *,
    storage=None,
) -> None:
    """After deposit credit: resume confirmation or still-short UI (intent_id-safe)."""
    from keyboards.orders import build_order_funding_pending_short_keyboard
    from storefront import get_storefront

    intent = get_pending_order_funding(user_id)
    if intent is None:
        return

    intent_id = int(intent["intent_id"])

    located = find_service_location(str(intent["service_id"]))
    if located is None:
        delete_pending_order_funding_by_intent(intent_id, user_id)
        await send_smart_notification(bot, user_id, build_pending_invalidated_html())
        return

    service, _platform, _section, _sub = located
    qty = int(intent["quantity"])
    auto = bool(int(intent.get("auto_quantity") or 0))
    if auto and service.get("auto_quantity") is not None:
        qty = int(service["auto_quantity"])
    link = str(intent["link"])

    # Same price authority as order_confirm_yes / restore_confirm_from_funding_intent.
    storefront = get_storefront()
    use_catalog_contract = storefront.backend_name == "catalog" or (
        hasattr(storefront, "uses_catalog_order_contract")
        and storefront.uses_catalog_order_contract(str(service["id"]))
    )
    live_total: object
    if use_catalog_contract:
        from catalog_core.storefront_adapter import StorefrontAdapterError
        from storefront import order_intent_to_create_bridge

        try:
            catalog_intent = storefront.resolve_order_intent(
                str(service["id"]), qty, target=link
            )
            conn = getattr(storefront, "_connection", None)
            bridge = order_intent_to_create_bridge(
                catalog_intent, user_id=user_id, connection=conn
            )
            live_total = to_decimal(bridge.amount_dh)
        except StorefrontAdapterError:
            delete_pending_order_funding_by_intent(intent_id, user_id)
            await send_smart_notification(bot, user_id, build_pending_invalidated_html())
            return
    else:
        live_total = order_total_price_dh(service, qty)

    if not intent_price_matches_live(intent, live_total):
        delete_pending_order_funding_by_intent(intent_id, user_id)
        await send_smart_notification(bot, user_id, build_pending_invalidated_html())
        return

    if get_pending_order_funding_by_intent(intent_id, user_id) is None:
        return

    user = get_user(user_id)
    balance = float((user or {}).get("balance") or 0.0)
    live_f = to_float(live_total)

    if balance < live_f:
        if get_pending_order_funding_by_intent(intent_id, user_id) is None:
            return
        text = build_pending_still_short_html(
            order_total=live_f,
            balance=balance,
            service_name=str(intent["service_name"]),
        )
        kb = build_order_funding_pending_short_keyboard()
        living_updated = False
        try:
            from utils.living_ui import edit_living_ui_message, get_user_living_ui

            lc, lm, hp = get_user_living_ui(user_id)
            if lc is not None and lm is not None:
                await edit_living_ui_message(
                    bot,
                    lc,
                    lm,
                    text,
                    kb,
                    has_photo=hp,
                )
                living_updated = True
        except Exception:
            logger.debug("still-short living UI update skipped", exc_info=True)
        if living_updated:
            await send_smart_notification(bot, user_id, text)
        else:
            try:
                await bot.send_message(
                    chat_id=user_id,
                    text=text,
                    reply_markup=kb,
                    parse_mode="HTML",
                )
            except Exception:
                await send_smart_notification(bot, user_id, text)
        return

    if get_pending_order_funding_by_intent(intent_id, user_id) is None:
        return

    from handlers.orders import restore_confirm_from_funding_intent

    try:
        await restore_confirm_from_funding_intent(
            bot, user_id, intent_id, storage=storage
        )
    except Exception:
        logger.exception(
            "resume_pending_order_after_deposit failed for user=%s intent=%s",
            user_id,
            intent_id,
        )
        await send_smart_notification(
            bot,
            user_id,
            (
                f"<b>تم شحن رصيدك بنجاح</b>\n"
                f"رصيدك كافٍ الآن لطلب «{intent['service_name']}».\n"
                "افتح <b>طلب جديد</b> لمتابعة تأكيد الطلب المعلق."
            ),
        )
