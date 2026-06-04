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
    return {
        "id": local_id,
        "name": str(row["name_ar"] or ""),
        "price": float(row["local_price_dh"] or 0),
        "min": int(row["min_qty"] or 1),
        "max": int(row["max_qty"] or 0),
        "provider_id": int(service_id),
        "provider_rate_usd": float(row["provider_price_usd"] or 0),
    }


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


def build_services_dict_from_db() -> dict[str, Any]:
    rows = _fetch_active_rows()
    if not rows:
        return _fallback_embedded()

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
