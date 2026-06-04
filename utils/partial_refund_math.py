# -*- coding: utf-8 -*-
"""حساب الاسترداد الجزئي من استجابة المزوّد: تكلفة USD الفعلية × 22 = سعر العميل النهائي للمنفّذ."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from utils.money import MONEY_STEP, to_decimal

# سعر التحويل الثابت لحالة Partial (درهم لكل دولار تكلفة مزوّد)
PARTIAL_PROVIDER_USD_TO_DH = Decimal("22")


def _parse_positive_decimal_from_keys(data: dict, keys: tuple[str, ...]) -> Decimal | None:
    for key in keys:
        raw = data.get(key)
        if raw is None or raw == "":
            continue
        try:
            val = to_decimal(raw)
            if val > 0:
                return val
        except Exception:
            continue
    return None


def _parse_remains_quantity(raw: object) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        return int(Decimal(str(raw)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except Exception:
        try:
            return int(float(str(raw)))
        except Exception:
            return None


def compute_partial_refund_from_status(
    original_price_paid_dh: object,
    quantity: int,
    status_data: dict,
) -> tuple[Decimal, Decimal, Decimal, Literal["cost_or_charge_usd", "remains_scaled"]] | None:
    """
    يحسب (refund_dh, actual_provider_usd, final_customer_price_dh, method).

    - إن وُجدت تكلفة USD من المزوّد (cost أو charge…): Final = USD × 22، Refund = الأصلي − Final.
    - وإلا مع remains صالحة: يُقدَّر USD المنفّذ بنسبة (منفّذ/الكل) من (الأصلي/22) ثم يُطبَّق ×22
      (يعادل الاسترداد النسبي للجزء غير المنفّذ عند غياب حقل USD في الاستجابة).
    """
    orig = to_decimal(original_price_paid_dh)
    if orig <= 0:
        return None

    q = max(int(quantity), 1)

    actual_usd = _parse_positive_decimal_from_keys(
        status_data,
        ("cost", "provider_cost", "charge", "order_charge"),
    )
    method: Literal["cost_or_charge_usd", "remains_scaled"] = "cost_or_charge_usd"

    if actual_usd is None:
        remains_n = _parse_remains_quantity(status_data.get("remains"))
        if remains_n is None:
            return None
        remains_clamped = max(0, min(remains_n, q))
        delivered = q - remains_clamped
        if delivered <= 0:
            return None
        full_order_usd_ref = (orig / PARTIAL_PROVIDER_USD_TO_DH).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
        actual_usd = (
            (Decimal(delivered) / Decimal(q)) * full_order_usd_ref
        ).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
        method = "remains_scaled"

    final_customer = (actual_usd * PARTIAL_PROVIDER_USD_TO_DH).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    refund = (orig - final_customer).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    if refund <= 0:
        return None
    if refund > orig:
        refund = orig
    return (refund, actual_usd, final_customer, method)
