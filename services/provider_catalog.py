# -*- coding: utf-8 -*-
"""ذاكرة موحّدة لحدود الكمية (min/max) من قائمة خدمات المورد."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smm_api import SMMManager

logger = logging.getLogger(__name__)

CATALOG_TTL_SECONDS = 300

_LIMITS: dict[int, tuple[int, int]] = {}
_UPDATED_AT: float = 0.0


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def get_provider_limits(provider_id: int) -> tuple[int, int] | None:
    cached = _LIMITS.get(provider_id)
    if cached is not None:
        return cached
    try:
        from services_catalog_db import get_provider_limits_from_db

        return get_provider_limits_from_db(provider_id)
    except Exception:
        return None


def catalog_age_seconds() -> float:
    if _UPDATED_AT <= 0:
        return float("inf")
    return time.time() - _UPDATED_AT


async def refresh_provider_catalog(api: SMMManager, *, force: bool = False) -> bool:
    """يجلب action=services مرة واحدة ويحدّث حدود min/max."""
    global _UPDATED_AT
    now = time.time()
    if not force and _LIMITS and (now - _UPDATED_AT) < CATALOG_TTL_SECONDS:
        return True
    try:
        services = await api.get_services()
    except Exception as exc:
        logger.warning("Provider catalog refresh failed: %s", exc)
        return False
    if not isinstance(services, list):
        logger.warning("Provider catalog: unexpected services payload type %s", type(services))
        return False

    fresh_limits: dict[int, tuple[int, int]] = {}
    for entry in services:
        if not isinstance(entry, dict):
            continue
        service_id = _safe_int(entry.get("service"), default=0)
        if service_id <= 0:
            continue
        min_qty = _safe_int(entry.get("min"), default=1)
        max_qty = _safe_int(entry.get("max"), default=1_000_000_000)
        if min_qty <= 0:
            min_qty = 1
        if max_qty < min_qty:
            max_qty = min_qty
        fresh_limits[service_id] = (min_qty, max_qty)

    if not fresh_limits:
        logger.warning("Provider catalog refresh returned no services")
        return False

    _LIMITS.clear()
    _LIMITS.update(fresh_limits)
    _UPDATED_AT = now
    return True
