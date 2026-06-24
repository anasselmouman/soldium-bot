# -*- coding: utf-8 -*-
"""حدود الكمية (min/max) لكل مزوّد — من قاعدة البيانات مع ذاكرة مؤقتة اختيارية."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from smm_api import SMMManager

logger = logging.getLogger(__name__)

CATALOG_TTL_SECONDS = 300

# (provider_slug, external_service_id) -> (min, max)
_LIMITS: dict[tuple[str, int], tuple[int, int]] = {}
_UPDATED_AT: float = 0.0


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _limits_key(provider_slug: str, external_service_id: int) -> tuple[str, int]:
    return str(provider_slug or "").strip().lower(), int(external_service_id)


def get_provider_limits(
    external_service_id: int,
    *,
    provider_slug: str | None = None,
) -> tuple[int, int] | None:
    from services.provider_registry import get_default_provider_slug

    slug = str(provider_slug or get_default_provider_slug()).strip().lower()
    key = _limits_key(slug, external_service_id)
    cached = _LIMITS.get(key)
    if cached is not None:
        return cached
    try:
        from services_catalog_db import get_provider_limits_from_db

        return get_provider_limits_from_db(slug, external_service_id)
    except Exception:
        return None


def catalog_age_seconds() -> float:
    if _UPDATED_AT <= 0:
        return float("inf")
    return time.time() - _UPDATED_AT


async def refresh_provider_catalog(
    api: SMMManager,
    *,
    provider_slug: str,
    force: bool = False,
) -> bool:
    """يجلب action=services ويحدّث حدود min/max للمزوّد المحدّد فقط."""
    global _UPDATED_AT
    slug = str(provider_slug or "").strip().lower()
    if not slug:
        return False
    now = time.time()
    if not force and _LIMITS and (now - _UPDATED_AT) < CATALOG_TTL_SECONDS:
        return True
    try:
        services = await api.get_services()
    except Exception as exc:
        logger.warning("Provider catalog refresh failed for %s: %s", slug, exc)
        return False
    if not isinstance(services, list):
        logger.warning(
            "Provider catalog: unexpected services payload type %s for %s",
            type(services),
            slug,
        )
        return False

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
        _LIMITS[_limits_key(slug, service_id)] = (min_qty, max_qty)

    _UPDATED_AT = now
    return True


async def refresh_all_provider_catalogs(*, force: bool = False) -> int:
    """يحدّث ذاكرة الحدود لكل حساب API نشط. يرجع عدد الحسابات المحدّثة بنجاح."""
    from services.smm_api_router import smm_manager_for_account
    from services.provider_registry import list_active_provider_accounts

    updated = 0
    for slug, account in list_active_provider_accounts():
        try:
            manager = smm_manager_for_account(account, slug)
            if await refresh_provider_catalog(manager, provider_slug=slug, force=force):
                updated += 1
        except Exception as exc:
            logger.warning("Catalog refresh skipped for %s/%s: %s", slug, account, exc)
    return updated


def clear_limits_cache() -> None:
    global _UPDATED_AT
    _LIMITS.clear()
    _UPDATED_AT = 0.0
