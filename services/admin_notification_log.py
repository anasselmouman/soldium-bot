# -*- coding: utf-8 -*-
"""سجل إشعارات الأدمن في قاعدة البيانات المشتركة (للوحة التحكم)."""

from __future__ import annotations

import json
import logging
import re
from html import unescape
from typing import Any

from database import get_connection

logger = logging.getLogger(__name__)

_STRIP_TAGS = re.compile(r"<[^>]+>")


def strip_html_to_plain(html_text: str) -> str:
    text = _STRIP_TAGS.sub(" ", html_text or "")
    return " ".join(unescape(text).split())


def log_admin_notification(
    *,
    category: str,
    title: str,
    body_html: str,
    severity: str = "info",
    entity_type: str | None = None,
    entity_id: str | None = None,
    user_id: int | None = None,
    source: str = "bot",
    channel: str = "telegram",
    telegram_sent: bool = False,
    telegram_error: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int | None:
    """Insert an admin notification row; returns new id or None on failure."""
    cat = (category or "general").strip().lower()
    title_text = (title or "").strip() or "إشعار"
    html_body = (body_html or "").strip()
    plain = strip_html_to_plain(html_body) or title_text
    payload_json = json.dumps(payload, ensure_ascii=False) if payload else None

    try:
        with get_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO admin_notifications (
                    category, severity, title, body_html, body_plain,
                    entity_type, entity_id, user_id, source, channel,
                    telegram_sent, telegram_error, payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cat,
                    (severity or "info").strip().lower(),
                    title_text,
                    html_body,
                    plain,
                    entity_type,
                    str(entity_id) if entity_id is not None else None,
                    int(user_id) if user_id is not None else None,
                    (source or "bot").strip().lower(),
                    (channel or "telegram").strip().lower(),
                    1 if telegram_sent else 0,
                    telegram_error,
                    payload_json,
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)
    except Exception as exc:
        logger.warning("Failed to log admin notification category=%s: %s", cat, exc)
        return None
