# -*- coding: utf-8 -*-
"""
كتالوج خدمات SOLDIUM — يُحمَّل من جدول smm_services في users.db.

للتحرير استخدم لوحة soldium-dashboard (أسعار الخدمات).
النسخة الاحتياطية: services_config_embedded.py
"""
from __future__ import annotations

from services_catalog_db import load_services_dict, reload_services_mapping

SERVICES: dict = load_services_dict()


def reload_services() -> None:
    """إعادة تحميل الكتالوج من قاعدة البيانات (آمن أثناء التحديث من اللوحة)."""
    reload_services_mapping(SERVICES)
