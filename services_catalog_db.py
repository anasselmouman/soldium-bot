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
    row_keys = row.keys()
    catalog_id = str(row["catalog_id"]) if "catalog_id" in row_keys else str(row["service_id"])
    external_id_raw = ""
    if "external_service_id" in row_keys and row["external_service_id"]:
        external_id_raw = str(row["external_service_id"]).strip()
    else:
        external_id_raw = str(row["service_id"]).strip()
    local_id = str(row["local_item_id"] or catalog_id)
    try:
        provider_external_id = int(external_id_raw)
    except (TypeError, ValueError):
        provider_external_id = 0
    item: dict[str, Any] = {
        "id": local_id,
        "catalog_id": catalog_id,
        "name": str(row["name_ar"] or ""),
        "price": float(row["local_price_dh"] or 0),
        "min": int(row["min_qty"] or 1),
        "max": int(row["max_qty"] or 0),
        "provider_id": provider_external_id,
        "external_service_id": provider_external_id,
        "provider_rate_usd": float(row["provider_price_usd"] or 0),
    }
    if str(row["category"] or "") == "per_unit":
        item["price_per_unit"] = True
        item["auto_quantity"] = 1
        provider_usd = float(row["provider_price_usd"] or 0)
        local_dh = float(row["local_price_dh"] or 0)
        if local_dh > 0:
            item["price"] = local_dh
        elif provider_usd > 0:
            item["price"] = round(provider_usd * SERVICE_USD_TO_DH_MULTIPLIER, 2)
    row_keys = row.keys()
    from services.provider_registry import get_default_provider_slug

    provider_slug = get_default_provider_slug()
    if "provider_slug" in row_keys and row["provider_slug"]:
        provider_slug = str(row["provider_slug"]).strip().lower() or provider_slug
    item["provider_slug"] = provider_slug
    if "provider_api_account" in row_keys and row["provider_api_account"]:
        item["provider_account"] = str(row["provider_api_account"]).strip()
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
    "instagram": ["likes", "views", "followers", "interaction"],
    "facebook": ["reactions", "video_reels_views", "followers_members", "live_stream_views"],
    "tiktok": ["likes", "views", "followers"],
    "telegram": [
        "post_interactions",
        "post_views",
        "channel_members",
        "post_share",
        "start_bot",
        "automatic_interactions",
    ],
}

# عناوين عرض الأقسام (إيموجي في البداية والنهاية) — تُطبَّق فوق قاعدة البيانات.
PLATFORM_SECTION_TITLES: dict[str, dict[str, str]] = {
    "telegram": {
        "post_interactions": "⚡ تفاعلات المنشورات 👍❤️🔥 ⚡",
        "post_views": "👁️ مشاهدة منشور 👁️",
        "channel_members": "👥 أعضاء القنوات والمجموعات 👥",
        "post_share": "📤 مشاركة المنشور (Share) 📤",
        "start_bot": "🤖 بدء البوت (Start Bot) 🤖",
        "automatic_interactions": "🔄 تفاعلات تلقائية للمنشورات القادمة 🔄",
    },
}

PLATFORM_SUBSECTION_TITLES: dict[str, dict[tuple[str, str], str]] = {
    "telegram": {
        ("channel_members", "global_members"): "👥 أعضاء عالمي (اقتصادي/عالي الجودة) 👥",
        ("channel_members", "targeted_members"): "🌍 أعضاء مستهدفين (دول) 🌍",
        ("channel_members", "premium_members"): "⭐ أعضاء بريميوم (لدعم الرانك) ⭐",
        ("channel_members", "member_bundles"): "📦 باقات متكاملة (أعضاء + مشاهدات) 📦",
        ("channel_members", "arab_mix_members"): "🇲🇦 أعضاء عرب (Mix) 🇲🇦",
        ("post_views", "past_posts"): "🔙 منشورات سابقة (Auto) 🔙",
        ("post_views", "future_posts"): "🔮 منشورات قادمة (Future) 🔮",
        ("post_views", "premium_views"): "🌟 مشاهدات بريميوم (تلقائية) 🌟",
        ("post_interactions", "normal_interactions"): "⚡ تفاعلات حسابات عادية (الفورية) ⚡",
        ("post_interactions", "premium_interactions"): "🌟 تفاعلات حسابات بريميوم (الفورية) 🌟",
        ("post_interactions", "automatic_interactions"): "🔄 تفاعلات تلقائية للمنشورات القادمة 🔄",
        ("automatic_interactions", "positive_mix"): "✨ ميكس تفاعلات إيجابي (👍❤️🔥🥰👏🎉💯) ✨",
        ("automatic_interactions", "negative_mix"): "💀 ميكس تفاعلات سلبي (👎💔🤨🙄🤬🖕💩🤡🤮) 💀",
        ("automatic_interactions", "specific_emoji"): "🎯 تفاعل محدد (إيموجي واحد) 🎯",
    },
}

# أقسام فرعية فارغة تُوجّه لقسم آخر — لا تُخزَّن في smm_services.
TELEGRAM_REDIRECT_SUBSECTIONS: dict[str, dict[str, dict[str, Any]]] = {
    "post_interactions": {
        "automatic_interactions": {
            "redirect_section": "automatic_interactions",
        },
    },
}

SERVICE_ITEM_META_KEYS: tuple[str, ...] = (
    "auto_quantity",
    "note",
    "link_prompt_key",
    "notice_key",
)
SECTION_META_KEYS: tuple[str, ...] = ("section_notice_key", "note")
SUBSECTION_META_KEYS: tuple[str, ...] = ("note",)

_embedded_indexes_cache: tuple[
    dict[str, dict[str, Any]],
    dict[tuple[str, str], dict[str, Any]],
    dict[tuple[str, str, str], dict[str, Any]],
    list[tuple[str, str, str, dict[str, Any]]],
] | None = None

TELEGRAM_POST_INTERACTIONS_SUBSECTION_ORDER: list[str] = [
    "normal_interactions",
    "premium_interactions",
    "automatic_interactions",
]

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


def _apply_platform_display_titles(platform_key: str, platform: dict[str, Any]) -> None:
    section_titles = PLATFORM_SECTION_TITLES.get(platform_key) or {}
    subsection_titles = PLATFORM_SUBSECTION_TITLES.get(platform_key) or {}
    sections = platform.get("sections") or {}
    for section_key, section in sections.items():
        if not isinstance(section, dict):
            continue
        title = section_titles.get(str(section_key))
        if title:
            section["title"] = title
        for subsection_key, subsection in (section.get("subsections") or {}).items():
            if not isinstance(subsection, dict):
                continue
            sub_title = subsection_titles.get((str(section_key), str(subsection_key)))
            if sub_title:
                subsection["title"] = sub_title


def _sort_services_catalog(services: dict[str, Any]) -> None:
    for platform_key, platform in services.items():
        if not isinstance(platform, dict):
            continue
        pk = str(platform_key)
        _reorder_platform_sections(pk, platform)
        _apply_platform_display_titles(pk, platform)
        _sort_platform_catalog(pk, platform)


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


def _section_has_catalog_items(section: dict[str, Any]) -> bool:
    if section.get("items"):
        return True
    for subsection in (section.get("subsections") or {}).values():
        if isinstance(subsection, dict) and subsection.get("items"):
            return True
    return False


def _reorder_named_subsections(section: dict[str, Any], order: list[str]) -> None:
    subsections = section.get("subsections") or {}
    if not subsections:
        return
    ordered: dict[str, Any] = {}
    for key in order:
        if key in subsections:
            ordered[key] = subsections[key]
    for key, value in subsections.items():
        if key not in ordered:
            ordered[key] = value
    section["subsections"] = ordered


def _embedded_services_source() -> dict[str, Any]:
    try:
        from services_config_embedded import SERVICES

        return SERVICES
    except Exception as exc:
        logger.debug("Embedded catalog unavailable for metadata merge: %s", exc)
        return {}


def _service_index_keys(item: dict[str, Any]) -> list[str]:
    keys: list[str] = []
    for field in ("provider_id", "id"):
        value = item.get(field)
        if value is not None and str(value).strip():
            keys.append(str(value).strip())
    return keys


def _pick_meta(source: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {key: source[key] for key in fields if source.get(key) is not None}


def _merge_missing_fields(target: dict[str, Any], meta: dict[str, Any]) -> None:
    for key, value in meta.items():
        if target.get(key) is None:
            target[key] = value


def _build_embedded_metadata_indexes(
    embedded: dict[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[tuple[str, str], dict[str, Any]],
    dict[tuple[str, str, str], dict[str, Any]],
    list[tuple[str, str, str, dict[str, Any]]],
]:
    service_meta: dict[str, dict[str, Any]] = {}
    section_meta: dict[tuple[str, str], dict[str, Any]] = {}
    subsection_meta: dict[tuple[str, str, str], dict[str, Any]] = {}
    redirect_links: list[tuple[str, str, str, dict[str, Any]]] = []

    def _index_service_item(item: dict[str, Any]) -> None:
        meta = _pick_meta(item, SERVICE_ITEM_META_KEYS)
        if not meta:
            return
        for key in _service_index_keys(item):
            bucket = service_meta.setdefault(key, {})
            for field, value in meta.items():
                bucket.setdefault(field, value)

    for platform_key, category in embedded.items():
        if not isinstance(category, dict):
            continue
        pk = str(platform_key)
        for item in category.get("items") or []:
            if isinstance(item, dict):
                _index_service_item(item)
        for item in category.get("direct_items") or []:
            if isinstance(item, dict):
                _index_service_item(item)

        for section_key, section in (category.get("sections") or {}).items():
            if not isinstance(section, dict):
                continue
            sk = str(section_key)
            sec_meta = _pick_meta(section, SECTION_META_KEYS)
            if sec_meta:
                bucket = section_meta.setdefault((pk, sk), {})
                for field, value in sec_meta.items():
                    bucket.setdefault(field, value)

            for item in section.get("items") or []:
                if isinstance(item, dict):
                    _index_service_item(item)

            for subsection_key, subsection in (section.get("subsections") or {}).items():
                if not isinstance(subsection, dict):
                    continue
                ssk = str(subsection_key)
                sub_meta = _pick_meta(subsection, SUBSECTION_META_KEYS)
                if sub_meta:
                    bucket = subsection_meta.setdefault((pk, sk, ssk), {})
                    for field, value in sub_meta.items():
                        bucket.setdefault(field, value)

                redirect_target = str(subsection.get("redirect_section") or "").strip()
                if redirect_target and not subsection.get("items"):
                    redirect_links.append(
                        (
                            pk,
                            sk,
                            ssk,
                            {
                                "redirect_section": redirect_target,
                                "title": str(subsection.get("title") or ssk),
                            },
                        )
                    )

                for item in subsection.get("items") or []:
                    if isinstance(item, dict):
                        _index_service_item(item)

    return service_meta, section_meta, subsection_meta, redirect_links


def _get_embedded_metadata_indexes() -> tuple[
    dict[str, dict[str, Any]],
    dict[tuple[str, str], dict[str, Any]],
    dict[tuple[str, str, str], dict[str, Any]],
    list[tuple[str, str, str, dict[str, Any]]],
]:
    global _embedded_indexes_cache
    if _embedded_indexes_cache is None:
        _embedded_indexes_cache = _build_embedded_metadata_indexes(_embedded_services_source())
    return _embedded_indexes_cache


def _infer_auto_quantity(
    item: dict[str, Any],
    *,
    section_key: str | None,
) -> None:
    if item.get("auto_quantity") is not None:
        return
    sk = str(section_key or "").strip()
    if sk != "automatic_interactions":
        return
    min_qty = int(item.get("min") or 0)
    max_qty = int(item.get("max") or 0)
    if min_qty > 0 and min_qty == max_qty:
        item["auto_quantity"] = min_qty


def _apply_redirect_subsections(
    services: dict[str, Any],
    redirect_links: list[tuple[str, str, str, dict[str, Any]]],
) -> None:
    subsection_titles = PLATFORM_SUBSECTION_TITLES.get("telegram") or {}
    for platform_key, parent_section_key, subsection_key, meta in redirect_links:
        platform = services.get(platform_key)
        if not isinstance(platform, dict):
            continue
        sections = platform.get("sections") or {}
        redirect_target = str(meta.get("redirect_section") or "").strip()
        target_section = sections.get(redirect_target)
        if not redirect_target or not isinstance(target_section, dict):
            continue
        if not _section_has_catalog_items(target_section):
            continue

        parent_section = sections.setdefault(
            parent_section_key,
            {"title": parent_section_key, "items": [], "subsections": {}},
        )
        subsections = parent_section.setdefault("subsections", {})
        title = str(
            meta.get("title")
            or subsection_titles.get((parent_section_key, subsection_key), subsection_key)
        )
        existing = subsections.get(subsection_key)
        if isinstance(existing, dict):
            if not existing.get("redirect_section") and not existing.get("items"):
                existing["redirect_section"] = redirect_target
            if not existing.get("title"):
                existing["title"] = title
            continue
        subsections[subsection_key] = {
            "title": title,
            "redirect_section": redirect_target,
            "items": [],
        }


def _telegram_static_redirect_links() -> list[tuple[str, str, str, dict[str, Any]]]:
    subsection_titles = PLATFORM_SUBSECTION_TITLES.get("telegram") or {}
    links: list[tuple[str, str, str, dict[str, Any]]] = []
    for parent_sk, subs in TELEGRAM_REDIRECT_SUBSECTIONS.items():
        for sub_sk, meta in subs.items():
            links.append(
                (
                    "telegram",
                    parent_sk,
                    sub_sk,
                    {
                        "redirect_section": str(meta.get("redirect_section") or ""),
                        "title": subsection_titles.get((parent_sk, sub_sk), sub_sk),
                    },
                )
            )
    return links


def _merge_catalog_metadata_from_embedded(services: dict[str, Any]) -> None:
    """يُكمّل metadata غير المخزّن في smm_services (تنويهات، redirect، auto_quantity…)."""
    (
        service_meta,
        section_meta,
        subsection_meta,
        redirect_links,
    ) = _get_embedded_metadata_indexes()

    for platform_key, category in services.items():
        if not isinstance(category, dict):
            continue
        pk = str(platform_key)

        def _enrich_item(item: dict[str, Any], section_key: str | None) -> None:
            for key in _service_index_keys(item):
                meta = service_meta.get(key)
                if meta:
                    _merge_missing_fields(item, meta)
            _infer_auto_quantity(item, section_key=section_key)

        for item in category.get("items") or []:
            if isinstance(item, dict):
                _enrich_item(item, None)
        for item in category.get("direct_items") or []:
            if isinstance(item, dict):
                _enrich_item(item, "direct")

        for section_key, section in (category.get("sections") or {}).items():
            if not isinstance(section, dict):
                continue
            sk = str(section_key)
            sec_extra = section_meta.get((pk, sk))
            if sec_extra:
                _merge_missing_fields(section, sec_extra)

            for item in section.get("items") or []:
                if isinstance(item, dict):
                    _enrich_item(item, sk)

            for subsection_key, subsection in (section.get("subsections") or {}).items():
                if not isinstance(subsection, dict):
                    continue
                ssk = str(subsection_key)
                sub_extra = subsection_meta.get((pk, sk, ssk))
                if sub_extra:
                    _merge_missing_fields(subsection, sub_extra)
                for item in subsection.get("items") or []:
                    if isinstance(item, dict):
                        _enrich_item(item, sk)

    _apply_redirect_subsections(services, redirect_links)
    _apply_redirect_subsections(services, _telegram_static_redirect_links())

    telegram = services.get("telegram")
    if isinstance(telegram, dict):
        post_section = (telegram.get("sections") or {}).get("post_interactions")
        if isinstance(post_section, dict):
            _reorder_named_subsections(post_section, TELEGRAM_POST_INTERACTIONS_SUBSECTION_ORDER)


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
        _merge_catalog_metadata_from_embedded(services)
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
    _merge_catalog_metadata_from_embedded(services)
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


def get_provider_limits_from_db(
    provider_slug: str,
    external_service_id: int,
) -> tuple[int, int] | None:
    try:
        with _get_connection() as conn:
            row = conn.execute(
                """
                SELECT min_qty, max_qty FROM smm_services
                WHERE provider_slug = ? AND external_service_id = ?
                """,
                (str(provider_slug).strip().lower(), str(external_service_id)),
            ).fetchone()
    except sqlite3.OperationalError:
        try:
            with _get_connection() as conn:
                row = conn.execute(
                    "SELECT min_qty, max_qty FROM smm_services WHERE service_id = ?",
                    (str(external_service_id),),
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
