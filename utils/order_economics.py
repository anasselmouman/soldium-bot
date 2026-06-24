# -*- coding: utf-8 -*-
"""حساب تكلفة المورد بالدرهم — مشترك بين الطلبات والتحليلات المالية."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from config import SERVICE_USD_TO_DH_MULTIPLIER
from utils.money import MONEY_STEP, to_decimal

# يطابق DEFAULT_MARKUP_MULTIPLIER في soldium-dashboard عند تقدير التكلفة بدون سعر مورد
DEFAULT_MARKUP_FALLBACK = 15.0


def compute_provider_cost_dh(
    quantity: int,
    *,
    provider_price_usd: float,
    local_price_dh: float = 0.0,
    price_per_unit: bool = False,
    usd_to_dh: float | None = None,
    markup_fallback: float = DEFAULT_MARKUP_FALLBACK,
) -> float:
    """
    تكلفة المورد بالدرهم من سعر API (USD لكل 1000 أو لكل وحدة).

    إن لم يتوفر provider_price_usd يُقدَّر من سعر البيع ÷ هامش افتراضي.
    """
    mult = to_decimal(usd_to_dh if usd_to_dh is not None else SERVICE_USD_TO_DH_MULTIPLIER)
    qty = to_decimal(max(int(quantity), 0))
    rate = to_decimal(provider_price_usd)
    local = to_decimal(local_price_dh)
    markup = to_decimal(markup_fallback)
    if markup <= 0:
        markup = to_decimal(DEFAULT_MARKUP_FALLBACK)

    if price_per_unit:
        if rate > 0:
            cost = qty * rate * mult
        elif local > 0:
            cost = qty * local * (mult / markup)
        else:
            cost = to_decimal(0)
    else:
        thousand = to_decimal(1000)
        if rate > 0:
            cost = (qty / thousand) * rate * mult
        elif local > 0:
            cost = (qty / thousand) * (local / markup) * mult
        else:
            cost = to_decimal(0)

    return float(cost.quantize(MONEY_STEP, rounding=ROUND_HALF_UP))


def provider_cost_dh_from_service(service: dict, quantity: int) -> float:
    """يحسب تكلفة المورد من عنصر كتالوج SERVICES."""
    return compute_provider_cost_dh(
        quantity,
        provider_price_usd=float(service.get("provider_rate_usd") or 0),
        local_price_dh=float(service.get("price") or 0),
        price_per_unit=bool(service.get("price_per_unit")),
    )
