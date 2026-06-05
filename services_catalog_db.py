# -*- coding: utf-8 -*-
"""
بناء كتالوج SERVICES من جدول smm_services (قاعدة users.db المشتركة).
"""
from __future__ import annotations

import copy
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from config import SERVICE_USD_TO_DH_MULTIPLIER

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).with_name("users.db")
_READ_RETRIES = 5
_READ_RETRY_DELAY = 0.06


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def _fetch_active_rows() -> list[sqlite3.Row]:
    last_error: Exception | None = None
    for attempt in range(_READ_RETRIES):
        try:
            with _get_connection() as conn:
                cursor = conn.execute(
                    """
                    SELECT *
                    FROM smm_services
                    WHERE is_active = 1
                      AND platform_key != ''
                    ORDER BY platform_key, section_key, subsection_key, name_ar
                    """,
                )
                return list(cursor.fetchall())
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "no such table" in str(exc).lower():
                return []
            if attempt + 1 < _READ_RETRIES:
                time.sleep(_READ_RETRY_DELAY)
        except Exception as exc:
            last_error = exc
            if attempt + 1 < _READ_RETRIES:
                time.sleep(_READ_RETRY_DELAY)
    if last_error:
        logger.error("Failed to load smm_services: %s", last_error)
    return []


def _item_from_row(row: sqlite3.Row) -> dict[str, Any]:
    service_id = str(row["service_id"])
    local_id = str(row["local_item_id"] or service_id)
    item: dict[str, Any] = {
        "id": local_id,
        "name": str(row["name_ar"] or ""),
        "price": float(row["local_price_dh"] or 0),
        "min": int(row["min_qty"] or 1),
        "max": int(row["max_qty"] or 0),
        "provider_id": int(service_id),
        "provider_rate_usd": float(row["provider_price_usd"] or 0),
    }
    if str(row["category"] or "") == "per_unit":
        item["price_per_unit"] = True
        item["auto_quantity"] = 1
        provider_usd = float(row["provider_price_usd"] or 0)
        if provider_usd > 0:
            item["price"] = round(provider_usd * SERVICE_USD_TO_DH_MULTIPLIER, 2)
    row_keys = row.keys()
    fulfillment_mode = "auto"
    if "fulfillment_mode" in row_keys:
        fulfillment_mode = str(row["fulfillment_mode"] or "auto").strip().lower()
    item["fulfillment_mode"] = "admin" if fulfillment_mode == "admin" else "auto"
    section_key = row["section_key"]
    if section_key is not None and str(section_key).strip():
        item["section_key"] = str(section_key)
    return item


def _ensure_platform(services: dict[str, Any], row: sqlite3.Row) -> dict[str, Any]:
    pk = str(row["platform_key"])
    if pk not in services:
        services[pk] = {
            "title": str(row["platform_title"] or pk),
            "sections": {},
        }
    return services[pk]


def _ensure_section(platform: dict[str, Any], row: sqlite3.Row) -> dict[str, Any]:
    sk = row["section_key"]
    if sk is None or sk == "":
        return platform
    sk = str(sk)
    sections = platform.setdefault("sections", {})
    if sk not in sections:
        sections[sk] = {
            "title": str(row["section_title"] or sk),
            "items": [],
            "subsections": {},
        }
    return sections[sk]


PLATFORM_SECTION_ORDER: dict[str, list[str]] = {
    "instagram": ["followers", "views", "likes", "interaction"],
}

# ترتيب ثانوي للعناصر بنفس السعر (مثلاً استهداف جغرافي في المشاهدات).
SECTION_ITEM_ORDER: dict[tuple[str, str], list[str]] = {
    ("instagram", "views"): ["4205", "4222", "3732", "3728", "3729"],
    ("instagram", "followers"): ["4823", "4459", "3956", "2128"],
    ("instagram", "likes"): ["4832", "4496"],
    ("instagram", "interaction"): ["3168", "1154", "3537", "1173", "4644"],
}


def _item_sort_key(
    item: dict[str, Any],
    *,
    platform_key: str,
    section_key: str | None,
) -> tuple[float, int]:
    price = float(item.get("price") or 0)
    item_id = str(item.get("id") or "")
    order_ids = (
        SECTION_ITEM_ORDER.get((platform_key, section_key or "")) or []
    )
    try:
        rank = order_ids.index(item_id)
    except ValueError:
        rank = len(order_ids)
    return (price, rank)


def _sort_items_list(
    items: list[dict[str, Any]],
    *,
    platform_key: str,
    section_key: str | None = None,
) -> None:
    if len(items) < 2:
        return
    items.sort(
        key=lambda item: _item_sort_key(
            item,
            platform_key=platform_key,
            section_key=section_key,
        ),
    )


def _sort_platform_catalog(platform_key: str, platform: dict[str, Any]) -> None:
    _sort_items_list(platform.get("items") or [], platform_key=platform_key)
    _sort_items_list(platform.get("direct_items") or [], platform_key=platform_key)

    sections = platform.get("sections") or {}
    for section_key, section in sections.items():
        if not isinstance(section, dict):
            continue
        _sort_items_list(
            section.get("items") or [],
            platform_key=platform_key,
            section_key=str(section_key),
        )
        for subsection in (section.get("subsections") or {}).values():
            if not isinstance(subsection, dict):
                continue
            _sort_items_list(
                subsection.get("items") or [],
                platform_key=platform_key,
                section_key=str(section_key),
            )


def _reorder_platform_sections(platform_key: str, platform: dict[str, Any]) -> None:
    preferred = PLATFORM_SECTION_ORDER.get(platform_key)
    sections = platform.get("sections") or {}
    if not preferred or not sections:
        return
    ordered: dict[str, Any] = {}
    for section_key in preferred:
        if section_key in sections:
            ordered[section_key] = sections[section_key]
    for section_key, section in sections.items():
        if section_key not in ordered:
            ordered[section_key] = section
    platform["sections"] = ordered


def _sort_services_catalog(services: dict[str, Any]) -> None:
    for platform_key, platform in services.items():
        if not isinstance(platform, dict):
            continue
        _reorder_platform_sections(str(platform_key), platform)
        _sort_platform_catalog(str(platform_key), platform)


SUBSCRIPTIONS_PLATFORM: dict[str, Any] = {
    "title": "🔄 إشتراكات",
    "sections": {
        "iptv_wc2026": {
            "title": "⚽🌍 حسابات ايبتيفي - كأس العالم 2026",
            "section_notice_key": "iptv_wc2026",
            "items": [],
        },
        "iptv_panel": {
            "title": "لوحة - ايبتيفي بانل [ إربح من بيع حسابات ايبتيفي]",
            "section_notice_key": "iptv_panel",
            "items": [],
        },
    },
}


def _sort_subscription_section_items(services: dict[str, Any], section_key: str) -> None:
    section = (
        (services.get("subscriptions") or {}).get("sections") or {}
    ).get(section_key)
    items = (section or {}).get("items") or []
    if len(items) > 1:
        items.sort(key=lambda item: int(item.get("provider_id") or item.get("id") or 0))


def _sort_iptv_wc2026_items(services: dict[str, Any]) -> None:
    _sort_subscription_section_items(services, "iptv_wc2026")


def _merge_subscriptions_platform(services: dict[str, Any]) -> None:
    """دمج تصنيفات الإشتراكات الثابتة مع ما يُحمَّل من قاعدة البيانات."""
    existing = services.get("subscriptions")
    if existing is None:
        services["subscriptions"] = copy.deepcopy(SUBSCRIPTIONS_PLATFORM)
        return
    if not existing.get("title"):
        existing["title"] = SUBSCRIPTIONS_PLATFORM["title"]
    sections = existing.setdefault("sections", {})
    for section_key, section in SUBSCRIPTIONS_PLATFORM["sections"].items():
        if section_key not in sections:
            sections[section_key] = copy.deepcopy(section)
            continue
        existing_section = sections[section_key]
        if section.get("section_notice_key"):
            existing_section["section_notice_key"] = section["section_notice_key"]
        if not existing_section.get("title"):
            existing_section["title"] = section.get("title", section_key)


def build_services_dict_from_db() -> dict[str, Any]:
    rows = _fetch_active_rows()
    if not rows:
        services = _fallback_embedded()
        _merge_subscriptions_platform(services)
        _sort_iptv_wc2026_items(services)
        _sort_subscription_section_items(services, "iptv_panel")
        _sort_services_catalog(services)
        return services

    services: dict[str, Any] = {}
    for row in rows:
        item = _item_from_row(row)
        platform = _ensure_platform(services, row)
        section_key = row["section_key"]
        subsection_key = row["subsection_key"]

        if section_key is None or section_key == "":
            platform.setdefault("items", []).append(item)
            continue

        section_key = str(section_key)
        if section_key == "direct":
            platform.setdefault("direct_items", []).append(item)
            continue

        section = _ensure_section(platform, row)
        if subsection_key is None or subsection_key == "":
            section.setdefault("items", []).append(item)
        else:
            sub_key = str(subsection_key)
            subsections = section.setdefault("subsections", {})
            if sub_key not in subsections:
                subsections[sub_key] = {
                    "title": str(row["subsection_title"] or sub_key),
                    "items": [],
                }
            subsections[sub_key]["items"].append(item)

    _merge_subscriptions_platform(services)
    _sort_iptv_wc2026_items(services)
    _sort_subscription_section_items(services, "iptv_panel")
    _sort_services_catalog(services)
    return services


def _fallback_embedded() -> dict[str, Any]:
    try:
        from services_config_embedded import SERVICES

        logger.warning("smm_services empty or missing — using embedded catalog fallback")
        return copy.deepcopy(SERVICES)
    except Exception as exc:
        logger.error("Embedded catalog fallback failed: %s", exc)
        return {}


def load_services_dict() -> dict[str, Any]:
    return build_services_dict_from_db()


def reload_services_mapping(target: dict[str, Any]) -> None:
    fresh = build_services_dict_from_db()
    target.clear()
    target.update(fresh)


def get_provider_limits_from_db(provider_id: int) -> tuple[int, int] | None:
    try:
        with _get_connection() as conn:
            row = conn.execute(
                "SELECT min_qty, max_qty FROM smm_services WHERE service_id = ?",
                (str(provider_id),),
            ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    min_qty = int(row["min_qty"] or 1)
    max_qty = int(row["max_qty"] or 0)
    if max_qty < min_qty:
        max_qty = min_qty
    return min_qty, max_qty
