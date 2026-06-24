# -*- coding: utf-8 -*-
"""
مزامنة أسعار المورد (USD لكل 1000) وحدود الكمية من API المزوّدين إلى جدول smm_services.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from services.provider_registry import get_default_provider_slug, list_active_provider_accounts
from services.smm_api_router import get_provider_credentials, smm_manager_for_account

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "users.db"

PLATFORM_TO_ACCOUNT: dict[str, str] = {
    "instagram": "instagram",
    "facebook": "facebook",
    "tiktok": "tiktok",
    "telegram": "default",
    "x": "default",
    "youtube": "default",
    "subscriptions": "default",
}

_ACCOUNT_FALLBACK_ORDER: tuple[str, ...] = ("default", "instagram", "facebook", "tiktok")

ProviderCatalogs = dict[str, dict[str, dict[int, "ProviderServiceEntry"]]]


@dataclass
class ProviderServiceEntry:
    service_id: int
    rate_usd: float
    min_qty: int
    max_qty: int
    api_account: str
    provider_slug: str


@dataclass
class ProviderPriceSyncResult:
    fetched_accounts: int = 0
    catalog_entries: int = 0
    rows_scanned: int = 0
    updated: int = 0
    unchanged: int = 0
    missing_in_api: int = 0
    skipped_admin: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return not self.errors and self.fetched_accounts > 0


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _normalize_slug(slug: str | None) -> str:
    return str(slug or get_default_provider_slug()).strip().lower()


def resolve_api_account(
    *,
    platform_key: str,
    category: str,
    name_ar: str,
    provider_account: str | None = None,
) -> str:
    stored = str(provider_account or "").strip().lower()
    if stored:
        return stored
    pk = str(platform_key or "").strip().lower()
    if pk in PLATFORM_TO_ACCOUNT:
        return PLATFORM_TO_ACCOUNT[pk]
    creds = get_provider_credentials(str(category or ""), str(name_ar or ""))
    return str(creds.get("account_type") or "default")


def _parse_catalog_entry(
    entry: dict,
    api_account: str,
    provider_slug: str,
) -> ProviderServiceEntry | None:
    if not isinstance(entry, dict):
        return None
    service_id = _safe_int(entry.get("service"), default=0)
    if service_id <= 0:
        return None
    min_qty = _safe_int(entry.get("min"), default=1)
    max_qty = _safe_int(entry.get("max"), default=1_000_000)
    if min_qty <= 0:
        min_qty = 1
    if max_qty < min_qty:
        max_qty = min_qty
    rate_usd = _safe_float(entry.get("rate"), default=0.0)
    return ProviderServiceEntry(
        service_id=service_id,
        rate_usd=rate_usd,
        min_qty=min_qty,
        max_qty=max_qty,
        api_account=api_account,
        provider_slug=provider_slug,
    )


async def fetch_provider_catalogs(
    *,
    provider_slug: str | None = None,
    accounts: tuple[str, ...] | None = None,
) -> ProviderCatalogs:
    if accounts:
        slug = _normalize_slug(provider_slug)
        pairs = [(slug, account) for account in accounts]
    else:
        pairs = list_active_provider_accounts(provider_slug)

    catalogs: ProviderCatalogs = {}
    for slug, account in pairs:
        slug = _normalize_slug(slug)
        try:
            manager = smm_manager_for_account(account, slug)
            raw_services = await manager.get_services()
        except Exception as exc:
            logger.warning(
                "Provider catalog fetch failed for %s/%s: %s",
                slug,
                account,
                exc,
            )
            continue
        if not isinstance(raw_services, list):
            logger.warning(
                "Unexpected services payload for %s/%s: %r",
                slug,
                account,
                type(raw_services),
            )
            continue
        by_id: dict[int, ProviderServiceEntry] = {}
        for raw in raw_services:
            parsed = _parse_catalog_entry(raw, account, slug)
            if parsed is not None:
                by_id[parsed.service_id] = parsed
        catalogs.setdefault(slug, {})[account] = by_id
        logger.info(
            "Fetched %s provider services for provider=%s account=%s",
            len(by_id),
            slug,
            account,
        )
    return catalogs


def _lookup_provider_entry(
    external_service_id: int,
    provider_slug: str,
    preferred_account: str,
    catalogs: ProviderCatalogs,
) -> ProviderServiceEntry | None:
    slug = _normalize_slug(provider_slug)
    provider_catalog = catalogs.get(slug, {})
    preferred = provider_catalog.get(preferred_account, {})
    hit = preferred.get(external_service_id)
    if hit is not None:
        return hit
    for account in _ACCOUNT_FALLBACK_ORDER:
        hit = provider_catalog.get(account, {}).get(external_service_id)
        if hit is not None:
            return hit
    for account, by_id in provider_catalog.items():
        hit = by_id.get(external_service_id)
        if hit is not None:
            return hit
    return None


def _get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def _external_id_from_row(row: sqlite3.Row) -> int | None:
    keys = row.keys()
    raw = ""
    if "external_service_id" in keys and row["external_service_id"]:
        raw = str(row["external_service_id"]).strip()
    elif "service_id" in keys and row["service_id"]:
        raw = str(row["service_id"]).strip()
    if not raw.isdigit():
        return None
    return int(raw)


def sync_provider_prices_to_db(
    catalogs: ProviderCatalogs,
    *,
    active_only: bool = False,
    db_path: Path | None = None,
) -> ProviderPriceSyncResult:
    account_count = sum(len(accounts) for accounts in catalogs.values())
    result = ProviderPriceSyncResult(
        fetched_accounts=account_count,
        catalog_entries=sum(
            len(by_id) for accounts in catalogs.values() for by_id in accounts.values()
        ),
    )
    if not catalogs:
        result.errors.append("No provider catalogs fetched")
        return result

    now = _utc_now_iso()
    where_clause = "WHERE is_active = 1 AND platform_key != ''" if active_only else ""
    with _get_connection(db_path) as conn:
        rows = conn.execute(
            f"""
            SELECT catalog_id, external_service_id, service_id, platform_key, category,
                   name_ar, provider_price_usd, min_qty, max_qty, fulfillment_mode,
                   provider_slug, provider_api_account
            FROM smm_services
            {where_clause}
            """
        ).fetchall()
        result.rows_scanned = len(rows)

        for row in rows:
            external_id = _external_id_from_row(row)
            if external_id is None:
                result.missing_in_api += 1
                continue
            fulfillment = str(row["fulfillment_mode"] or "auto").strip().lower()
            if fulfillment == "admin":
                result.skipped_admin += 1
                continue

            provider_slug = _normalize_slug(row["provider_slug"])
            preferred_account = resolve_api_account(
                platform_key=str(row["platform_key"] or ""),
                category=str(row["category"] or ""),
                name_ar=str(row["name_ar"] or ""),
                provider_account=str(row["provider_api_account"] or "") or None,
            )
            entry = _lookup_provider_entry(
                external_id,
                provider_slug,
                preferred_account,
                catalogs,
            )
            if entry is None:
                result.missing_in_api += 1
                continue

            old_rate = _safe_float(row["provider_price_usd"])
            old_min = _safe_int(row["min_qty"], default=1)
            old_max = _safe_int(row["max_qty"], default=0)
            price_changed = (
                abs(old_rate - entry.rate_usd) > 0.000001
                or old_min != entry.min_qty
                or old_max != entry.max_qty
            )
            if price_changed:
                result.updated += 1
            else:
                result.unchanged += 1

            conn.execute(
                """
                UPDATE smm_services
                SET provider_price_usd = ?,
                    min_qty = ?,
                    max_qty = ?,
                    provider_api_account = ?,
                    provider_price_updated_at = ?
                WHERE provider_slug = ? AND external_service_id = ?
                """,
                (
                    entry.rate_usd,
                    entry.min_qty,
                    entry.max_qty,
                    entry.api_account,
                    now,
                    provider_slug,
                    str(external_id),
                ),
            )

        conn.commit()

    return result


async def refresh_provider_prices(
    *,
    active_only: bool = False,
    provider_slug: str | None = None,
    accounts: tuple[str, ...] | None = None,
    db_path: Path | None = None,
) -> ProviderPriceSyncResult:
    catalogs = await fetch_provider_catalogs(
        provider_slug=provider_slug,
        accounts=accounts,
    )
    return sync_provider_prices_to_db(
        catalogs,
        active_only=active_only,
        db_path=db_path,
    )
