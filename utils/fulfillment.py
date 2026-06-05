# -*- coding: utf-8 -*-
"""أنماط تنفيذ الخدمات: تلقائي عبر المزود أو يدوي عبر الأدمن."""

from __future__ import annotations

from typing import Any

FULFILLMENT_AUTO = "auto"
FULFILLMENT_ADMIN = "admin"

ORDER_STATUS_PENDING_ADMIN = "pending_admin"


def normalize_fulfillment_mode(value: object) -> str:
    mode = str(value or FULFILLMENT_AUTO).strip().lower()
    return FULFILLMENT_ADMIN if mode == FULFILLMENT_ADMIN else FULFILLMENT_AUTO


def service_requires_admin(service: dict[str, Any] | None) -> bool:
    if not service:
        return False
    return normalize_fulfillment_mode(service.get("fulfillment_mode")) == FULFILLMENT_ADMIN
