# -*- coding: utf-8 -*-
"""
حساب الاسترداد الجزئي من استجابة المزوّد.

المسار الدقيق (عند توفر snapshot تكلفة المورد):
  ما يُحسب منفّذاً = amount × (تكلفة USD المنفّذ × مضاعف التحويل ÷ provider_cost_dh)

الاحتياطي (legacy): تكلفة USD × 22 — عند غياب snapshot أو فشل الحساب الدقيق.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from config import SERVICE_USD_TO_DH_MULTIPLIER
from utils.money import MONEY_STEP, to_decimal

# سعر التحويل للمسار الاحتياطي legacy (درهم لكل دولار تكلفة مزوّد)
PARTIAL_PROVIDER_USD_TO_DH = Decimal("22")

PartialRefundMethod = Literal[
    "margin_proportional",
    "quantity_proportional",
    "legacy_cost_or_charge_usd",
    "legacy_remains_scaled",
]

LegacyPartialMethod = Literal["cost_or_charge_usd", "remains_scaled"]


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


def _finalize_partial_result(
    orig: Decimal,
    refund: Decimal,
    final_customer: Decimal,
    actual_usd: Decimal,
    method: PartialRefundMethod,
) -> tuple[Decimal, Decimal, Decimal, PartialRefundMethod] | None:
    refund = refund.quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    final_customer = final_customer.quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    if refund <= 0:
        return None
    if refund > orig:
        refund = orig
        final_customer = Decimal(0)
    if final_customer > orig:
        final_customer = orig
    if final_customer < 0:
        final_customer = Decimal(0)
    return (refund, actual_usd, final_customer, method)


def compute_partial_refund(
    *,
    amount_paid_dh: object,
    quantity: int,
    status_data: dict,
    provider_cost_dh: float = 0.0,
    usd_to_dh: float | None = None,
) -> tuple[Decimal, Decimal, Decimal, PartialRefundMethod] | None:
    """
    يحسب (refund_dh, actual_provider_usd, final_customer_price_dh, calc_method).

    الأولوية:
    1. margin_proportional — snapshot + USD من المورد
    2. quantity_proportional — snapshot + remains
    3. legacy ×22 — compute_partial_refund_from_status
    """
    orig = to_decimal(amount_paid_dh)
    if orig <= 0:
        return None

    q = max(int(quantity), 1)
    cost_snapshot = to_decimal(provider_cost_dh)
    mult = to_decimal(usd_to_dh if usd_to_dh is not None else SERVICE_USD_TO_DH_MULTIPLIER)

    actual_usd = _parse_positive_decimal_from_keys(
        status_data,
        ("cost", "provider_cost", "charge", "order_charge"),
    )

    if cost_snapshot > 0 and actual_usd is not None:
        try:
            executed_cost_dh = (actual_usd * mult).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
            final_customer = (orig * executed_cost_dh / cost_snapshot).quantize(
                MONEY_STEP, rounding=ROUND_HALF_UP
            )
            if final_customer > orig:
                final_customer = orig
            refund = orig - final_customer
            result = _finalize_partial_result(
                orig, refund, final_customer, actual_usd, "margin_proportional"
            )
            if result is not None:
                return result
        except Exception:
            pass

    if cost_snapshot > 0:
        remains_n = _parse_remains_quantity(status_data.get("remains"))
        if remains_n is not None:
            remains_clamped = max(0, min(remains_n, q))
            if remains_clamped > 0:
                try:
                    refund = orig * Decimal(remains_clamped) / Decimal(q)
                    final_customer = orig - refund
                    result = _finalize_partial_result(
                        orig, refund, final_customer, Decimal(0), "quantity_proportional"
                    )
                    if result is not None:
                        return result
                except Exception:
                    pass

    legacy = compute_partial_refund_from_status(orig, q, status_data)
    if legacy is None:
        return None
    refund, legacy_usd, final_customer, legacy_method = legacy
    mapped: PartialRefundMethod = (
        "legacy_cost_or_charge_usd"
        if legacy_method == "cost_or_charge_usd"
        else "legacy_remains_scaled"
    )
    return (refund, legacy_usd, final_customer, mapped)


def compute_partial_refund_from_status(
    original_price_paid_dh: object,
    quantity: int,
    status_data: dict,
) -> tuple[Decimal, Decimal, Decimal, LegacyPartialMethod] | None:
    """
    مسار legacy: تكلفة USD × 22 (أو remains نسبي على ×22).

    يُستخدم كاحتياطي عند غياب provider_cost_dh أو فشل المسار الدقيق.
    """
    orig = to_decimal(original_price_paid_dh)
    if orig <= 0:
        return None

    q = max(int(quantity), 1)

    actual_usd = _parse_positive_decimal_from_keys(
        status_data,
        ("cost", "provider_cost", "charge", "order_charge"),
    )
    method: LegacyPartialMethod = "cost_or_charge_usd"

    if actual_usd is None:
        remains_n = _parse_remains_quantity(status_data.get("remains"))
        if remains_n is None:
            return None
        remains_clamped = max(0, min(remains_n, q))
        delivered = q - remains_clamped
        if delivered <= 0:
            return None
        full_order_usd_ref = (orig / PARTIAL_PROVIDER_USD_TO_DH).quantize(
            MONEY_STEP, rounding=ROUND_HALF_UP
        )
        actual_usd = (
            (Decimal(delivered) / Decimal(q)) * full_order_usd_ref
        ).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
        method = "remains_scaled"

    final_customer = (actual_usd * PARTIAL_PROVIDER_USD_TO_DH).quantize(
        MONEY_STEP, rounding=ROUND_HALF_UP
    )
    refund = (orig - final_customer).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    if refund <= 0:
        return None
    if refund > orig:
        refund = orig
    return (refund, actual_usd, final_customer, method)
