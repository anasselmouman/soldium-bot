# -*- coding: utf-8 -*-
"""اختيار مزوّد SMM وحساب API حسب بيانات الخدمة أو نص الفئة."""

from __future__ import annotations

from typing import Any

from services.provider_registry import (
    ProviderRoute,
    clear_provider_caches,
    get_default_provider_slug,
    get_manager,
    resolve_service_route,
    smm_manager_for_order,
)
from smm_api import SMMManager

_ACCOUNT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "instagram",
        ("instagram", "انستقرام", "انستغرام", "إنستقرام", "إنستغرام", "ig"),
    ),
    ("facebook", ("facebook", "فيسبوك", "فيس بوك", "fb")),
    ("tiktok", ("tiktok", "تيك توك", "تيكتوك")),
)

_VALID_ACCOUNTS = frozenset({"instagram", "facebook", "tiktok", "default"})
_manager_cache: dict[tuple[str, str], SMMManager] = {}


def _normalize_haystack(service_category: str, service_name: str) -> str:
    return f"{service_category or ''} {service_name or ''}".strip().lower()


def infer_account_from_text(service_category: str, service_name: str) -> str:
    """يستنتج حساب API من نص الفئة واسم الخدمة (عربي/إنجليزي)."""
    haystack = _normalize_haystack(service_category, service_name)
    for candidate, keywords in _ACCOUNT_RULES:
        if any(kw in haystack for kw in keywords):
            return candidate
    return "default"


def _normalize_account_type(account_type: str) -> str:
    key = str(account_type or "").strip().lower()
    if key not in _VALID_ACCOUNTS:
        key = "default"
    return key


def _route_to_credentials(route: ProviderRoute) -> dict[str, str]:
    return {
        "api_key": route.api_key,
        "account_type": route.account_key,
        "provider_slug": route.provider_slug,
    }


def get_provider_credentials(
    service_category: str,
    service_name: str,
    *,
    provider_slug: str | None = None,
    provider_account: str | None = None,
) -> dict[str, str]:
    route = resolve_service_route(
        provider_slug=provider_slug,
        provider_account=provider_account,
        service_category=service_category,
        service_name=service_name,
    )
    return _route_to_credentials(route)


def get_provider_credentials_for_service(
    service: dict[str, Any],
    service_category: str,
) -> dict[str, str]:
    provider_account = service.get("provider_account") or service.get("provider_api_account")
    slug = service.get("provider_slug") or get_default_provider_slug()
    return get_provider_credentials(
        service_category,
        str(service.get("name") or ""),
        provider_slug=str(slug),
        provider_account=str(provider_account) if provider_account else None,
    )


def smm_manager_for_account(
    account_type: str,
    provider_slug: str | None = None,
) -> SMMManager:
    slug = str(provider_slug or get_default_provider_slug()).strip().lower()
    account = _normalize_account_type(account_type)
    cache_key = (slug, account)
    cached = _manager_cache.get(cache_key)
    if cached is not None:
        return cached
    manager = get_manager(slug, account)
    _manager_cache[cache_key] = manager
    return manager


def clear_smm_manager_cache() -> None:
    _manager_cache.clear()
    clear_provider_caches()


__all__ = [
    "clear_smm_manager_cache",
    "get_default_provider_slug",
    "get_provider_credentials",
    "get_provider_credentials_for_service",
    "infer_account_from_text",
    "smm_manager_for_account",
    "smm_manager_for_order",
]
