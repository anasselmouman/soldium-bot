# -*- coding: utf-8 -*-
"""
سجل مركزي لمزوّدي خدمات SMM وحسابات API.

كل عمليات التشغيل (طلبات، مزامنة، أرصدة) تمر عبر هذه الطبقة.
مفاتيح API تُقرأ من .env عبر أسماء المتغيرات في provider_accounts.api_key_env فقط.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from smm_api import SMMManager

logger = logging.getLogger(__name__)

GOZIBRA_ADAPTER = "gozibra_v2"
LEGACY_GOZIBRA_SLUG = "gozibra"

DB_PATH = Path(__file__).resolve().parent.parent / "users.db"

_manager_cache: dict[tuple[str, str], SMMManager] = {}


@dataclass(frozen=True)
class ProviderRecord:
    slug: str
    name: str
    api_base_url: str
    adapter_type: str
    is_active: bool


DEFAULT_ACCOUNT_DISPLAY_NAMES: dict[str, str] = {
    "tiktok": "تيك توك",
    "instagram": "انستغرام",
    "facebook": "فيسبوك",
    "default": "افتراضي",
    "youtube": "يوتيوب",
    "telegram": "تيليغرام",
    "x": "إكس",
}


def default_display_name_for_account(account_key: str) -> str:
    key = str(account_key or "").strip().lower() or "default"
    return DEFAULT_ACCOUNT_DISPLAY_NAMES.get(key, key)


@dataclass(frozen=True)
class ProviderAccountRecord:
    provider_slug: str
    account_key: str
    api_key_env: str
    is_active: bool
    display_name: str = ""


@dataclass(frozen=True)
class ProviderRoute:
    provider_slug: str
    account_key: str
    api_key: str
    api_base_url: str


@dataclass(frozen=True)
class ProviderBalanceSnapshot:
    provider_slug: str
    account_key: str
    balance_usd: float
    currency: str
    ok: bool
    error: str | None = None


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def _normalize_account_key(account_key: str) -> str:
    key = str(account_key or "").strip().lower()
    return key or "default"


def _normalize_provider_slug(slug: str | None) -> str:
    value = str(slug or "").strip().lower()
    if value:
        return value
    return get_default_provider_slug()


def _api_key_from_env(env_name: str) -> str:
    name = str(env_name or "").strip()
    if not name:
        raise RuntimeError("اسم متغير البيئة لمفتاح API غير معرّف.")
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"المتغير {name} غير معرّف. أنشئ ملف .env من .env.example واملأ القيم المطلوبة."
        )
    return value


@lru_cache(maxsize=1)
def get_default_provider_slug() -> str:
    """أول مزوّد نشط في قاعدة البيانات (ترتيب أبجدي)."""
    try:
        with _get_connection() as conn:
            row = conn.execute(
                """
                SELECT slug FROM providers
                WHERE is_active = 1
                ORDER BY slug ASC
                LIMIT 1
                """
            ).fetchone()
    except sqlite3.OperationalError:
        row = None
    if row is not None:
        return str(row["slug"])
    return LEGACY_GOZIBRA_SLUG


def clear_default_provider_cache() -> None:
    get_default_provider_slug.cache_clear()


@lru_cache(maxsize=64)
def get_provider_record(slug: str) -> ProviderRecord | None:
    normalized = _normalize_provider_slug(slug)
    try:
        with _get_connection() as conn:
            row = conn.execute(
                """
                SELECT slug, name, api_base_url, adapter_type, is_active
                FROM providers
                WHERE slug = ?
                """,
                (normalized,),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    base_url = str(row["api_base_url"] or "").strip()
    if not base_url:
        return None
    return ProviderRecord(
        slug=str(row["slug"]),
        name=str(row["name"] or row["slug"]),
        api_base_url=base_url,
        adapter_type=str(row["adapter_type"] or GOZIBRA_ADAPTER),
        is_active=bool(int(row["is_active"] or 0)),
    )


@lru_cache(maxsize=128)
def get_provider_account_record(provider_slug: str, account_key: str) -> ProviderAccountRecord | None:
    slug = _normalize_provider_slug(provider_slug)
    account = _normalize_account_key(account_key)
    try:
        with _get_connection() as conn:
            row = conn.execute(
                """
                SELECT provider_slug, account_key, api_key_env, is_active, display_name
                FROM provider_accounts
                WHERE provider_slug = ? AND account_key = ?
                """,
                (slug, account),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None:
        return None
    display = str(row["display_name"] or "").strip()
    if not display:
        display = default_display_name_for_account(str(row["account_key"]))
    return ProviderAccountRecord(
        provider_slug=str(row["provider_slug"]),
        account_key=str(row["account_key"]),
        api_key_env=str(row["api_key_env"] or ""),
        is_active=bool(int(row["is_active"] or 0)),
        display_name=display,
    )


def resolve_api_key(provider_slug: str, account_key: str) -> str:
    slug = _normalize_provider_slug(provider_slug)
    account = _normalize_account_key(account_key)
    record = get_provider_account_record(slug, account)
    if record is None or not record.is_active or not record.api_key_env:
        raise RuntimeError(
            f"لا يوجد حساب API نشط للمزوّد «{slug}» والحساب «{account}». "
            "راجع جدول provider_accounts وملف .env."
        )
    return _api_key_from_env(record.api_key_env)


def get_manager(provider_slug: str | None = None, account_key: str = "default") -> SMMManager:
    slug = _normalize_provider_slug(provider_slug)
    account = _normalize_account_key(account_key)
    cache_key = (slug, account)
    cached = _manager_cache.get(cache_key)
    if cached is not None:
        return cached
    provider = get_provider_record(slug)
    if provider is None or not provider.is_active:
        raise RuntimeError(f"المزوّد «{slug}» غير موجود أو غير مفعّل.")
    api_key = resolve_api_key(slug, account)
    manager = SMMManager(api_key=api_key, api_url=provider.api_base_url)
    _manager_cache[cache_key] = manager
    return manager


def smm_manager_for_order(order: dict) -> SMMManager:
    slug = _normalize_provider_slug(order.get("provider_slug"))
    account = _normalize_account_key(str(order.get("api_account") or "default"))
    return get_manager(slug, account)


def list_active_providers() -> list[ProviderRecord]:
    records: list[ProviderRecord] = []
    try:
        with _get_connection() as conn:
            rows = conn.execute(
                """
                SELECT slug FROM providers
                WHERE is_active = 1
                ORDER BY slug ASC
                """
            ).fetchall()
    except sqlite3.OperationalError:
        return records
    for row in rows:
        record = get_provider_record(str(row["slug"]))
        if record is not None and record.is_active:
            records.append(record)
    return records


def list_active_provider_accounts(
    provider_slug: str | None = None,
) -> list[tuple[str, str]]:
    slug_filter = str(provider_slug or "").strip().lower() or None
    pairs: list[tuple[str, str]] = []
    try:
        with _get_connection() as conn:
            if slug_filter:
                rows = conn.execute(
                    """
                    SELECT pa.provider_slug, pa.account_key
                    FROM provider_accounts pa
                    JOIN providers p ON p.slug = pa.provider_slug
                    WHERE p.is_active = 1 AND pa.is_active = 1
                      AND pa.provider_slug = ?
                    ORDER BY pa.provider_slug, pa.account_key
                    """,
                    (slug_filter,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT pa.provider_slug, pa.account_key
                    FROM provider_accounts pa
                    JOIN providers p ON p.slug = pa.provider_slug
                    WHERE p.is_active = 1 AND pa.is_active = 1
                    ORDER BY pa.provider_slug, pa.account_key
                    """
                ).fetchall()
            for row in rows:
                pairs.append((str(row["provider_slug"]), str(row["account_key"])))
    except sqlite3.OperationalError:
        return []
    return pairs


def resolve_service_route(
    *,
    provider_slug: str | None = None,
    provider_account: str | None = None,
    service_category: str = "",
    service_name: str = "",
) -> ProviderRoute:
    from services.smm_api_router import infer_account_from_text

    slug = _normalize_provider_slug(provider_slug)
    account = str(provider_account or "").strip().lower()
    if not account:
        account = infer_account_from_text(service_category, service_name)
    account = _normalize_account_key(account)
    provider = get_provider_record(slug)
    if provider is None or not provider.is_active:
        raise RuntimeError(f"المزوّد «{slug}» غير موجود أو غير مفعّل.")
    api_key = resolve_api_key(slug, account)
    return ProviderRoute(
        provider_slug=slug,
        account_key=account,
        api_key=api_key,
        api_base_url=provider.api_base_url,
    )


async def fetch_account_balance(provider_slug: str, account_key: str) -> ProviderBalanceSnapshot:
    slug = _normalize_provider_slug(provider_slug)
    account = _normalize_account_key(account_key)
    try:
        data = await get_manager(slug, account).get_balance()
        balance = float(data.get("balance", 0) or 0)
        currency = str(data.get("currency") or "USD")
        return ProviderBalanceSnapshot(
            provider_slug=slug,
            account_key=account,
            balance_usd=balance,
            currency=currency,
            ok=True,
        )
    except Exception as exc:
        logger.warning("Balance fetch failed for %s/%s: %s", slug, account, exc)
        return ProviderBalanceSnapshot(
            provider_slug=slug,
            account_key=account,
            balance_usd=0.0,
            currency="USD",
            ok=False,
            error=str(exc),
        )


async def fetch_all_provider_balances() -> list[ProviderBalanceSnapshot]:
    import asyncio

    pairs = list_active_provider_accounts()
    if not pairs:
        return []
    results = await asyncio.gather(
        *[fetch_account_balance(slug, account) for slug, account in pairs],
        return_exceptions=False,
    )
    return list(results)


def clear_provider_caches() -> None:
    _manager_cache.clear()
    get_provider_record.cache_clear()
    get_provider_account_record.cache_clear()
    clear_default_provider_cache()


def _any_gozibra_env_key_configured() -> bool:
    for name in (
        "SMM_KEY_DEFAULT",
        "SMM_KEY_INSTAGRAM",
        "SMM_KEY_FACEBOOK",
        "SMM_KEY_TIKTOK",
    ):
        if os.environ.get(name, "").strip():
            return True
    return False


def seed_default_gozibra_provider(connection: sqlite3.Connection) -> None:
    """يُنشئ جداول المزوّدين ويُهيّئ Gozibra عند توفر مفاتيح .env (للترحيل فقط)."""
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS providers (
            slug TEXT PRIMARY KEY,
            name TEXT NOT NULL DEFAULT '',
            api_base_url TEXT NOT NULL DEFAULT '',
            adapter_type TEXT NOT NULL DEFAULT 'gozibra_v2',
            is_active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS provider_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider_slug TEXT NOT NULL,
            account_key TEXT NOT NULL DEFAULT 'default',
            api_key_env TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            UNIQUE(provider_slug, account_key),
            FOREIGN KEY(provider_slug) REFERENCES providers(slug)
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_provider_accounts_slug
        ON provider_accounts (provider_slug, is_active)
        """
    )
    if not _any_gozibra_env_key_configured():
        return
    api_url = os.environ.get("API_URL", "https://gozibra.com/api/v2").strip()
    if not api_url:
        api_url = "https://gozibra.com/api/v2"
    connection.execute(
        """
        INSERT OR IGNORE INTO providers (slug, name, api_base_url, adapter_type, is_active)
        VALUES (?, 'Gozibra', ?, ?, 1)
        """,
        (LEGACY_GOZIBRA_SLUG, api_url, GOZIBRA_ADAPTER),
    )
    for account_key, env_name in (
        ("default", "SMM_KEY_DEFAULT"),
        ("instagram", "SMM_KEY_INSTAGRAM"),
        ("facebook", "SMM_KEY_FACEBOOK"),
        ("tiktok", "SMM_KEY_TIKTOK"),
    ):
        if not os.environ.get(env_name, "").strip():
            continue
        connection.execute(
            """
            INSERT OR IGNORE INTO provider_accounts
                (provider_slug, account_key, api_key_env, display_name, is_active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (
                LEGACY_GOZIBRA_SLUG,
                account_key,
                env_name,
                default_display_name_for_account(account_key),
            ),
        )
