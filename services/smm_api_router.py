# -*- coding: utf-8 -*-
"""اختيار مفتاح API للمزود حسب منصة الخدمة (إنستغرام / فيسبوك / تيك توك / افتراضي)."""

from __future__ import annotations

from config import api_key_for_account
from smm_api import SMMManager

_ACCOUNT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "instagram",
        ("instagram", "انستقرام", "انستغرام", "إنستقرام", "إنستغرام", "ig"),
    ),
    ("facebook", ("facebook", "فيسبوك", "فيس بوك", "fb")),
    ("tiktok", ("tiktok", "تيك توك", "تيكتوك")),
)


def _normalize_haystack(service_category: str, service_name: str) -> str:
    return f"{service_category or ''} {service_name or ''}".strip().lower()


def get_provider_credentials(service_category: str, service_name: str) -> dict[str, str]:
    """
    يحدد حساب المزود من نص الفئة واسم الخدمة (عربي/إنجليزي).
    يرجع: {"api_key": str, "account_type": str}
    """
    haystack = _normalize_haystack(service_category, service_name)
    account_type = "default"
    for candidate, keywords in _ACCOUNT_RULES:
        if any(kw in haystack for kw in keywords):
            account_type = candidate
            break
    return {
        "api_key": api_key_for_account(account_type),
        "account_type": account_type,
    }


_VALID_ACCOUNTS = frozenset({"instagram", "facebook", "tiktok", "default"})
_manager_cache: dict[str, SMMManager] = {}


def _normalize_account_type(account_type: str) -> str:
    key = str(account_type or "").strip().lower()
    if key not in _VALID_ACCOUNTS:
        key = "default"
    return key


def smm_manager_for_account(account_type: str) -> SMMManager:
    """يرجع مدير SMM واحد لكل حساب (singleton) لتجنب إنشاء جلسات HTTP متكررة."""
    key = _normalize_account_type(account_type)
    cached = _manager_cache.get(key)
    if cached is not None:
        return cached
    manager = SMMManager(api_key=api_key_for_account(key))
    _manager_cache[key] = manager
    return manager


def clear_smm_manager_cache() -> None:
    """يفرغ ذاكرة المديرين — للاختبارات فقط."""
    _manager_cache.clear()
