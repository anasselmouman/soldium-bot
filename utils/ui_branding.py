# -*- coding: utf-8 -*-
"""هوية الواجهة الحية: فتات الخبز واللمسة البصرية (أزرق كهربائي عبر 💠)."""

from __future__ import annotations

from html import escape

ELECTRIC_ACCENT = "💠"


def format_breadcrumb(*segments: str) -> str:
    """سطر المسار — مثال: format_breadcrumb('نقاط هامة') → 🏠 الرئيسية > نقاط هامة"""
    if not segments:
        return "🏠 الرئيسية"
    trail = " &gt; ".join(f"<b>{escape(s)}</b>" for s in segments)
    return f"🏠 الرئيسية &gt; {trail}"


def brand_title(subtitle: str | None = None) -> str:
    line = f"{ELECTRIC_ACCENT} <b>SOLDIUM</b> {ELECTRIC_ACCENT}"
    if subtitle:
        return f"{line}\n<i>{escape(subtitle)}</i>"
    return line


def screen_body(breadcrumb_line: str, *paragraphs: str) -> str:
    parts = [breadcrumb_line, ""]
    parts.extend(paragraphs)
    return "\n".join(parts)


ACCOUNT_PERSISTENCE_HTML = (
    "🔒 <b>حفظ بياناتك:</b> حتى لو حذفت المحادثة أو أرشفتها أو أعدت تثبيت تطبيق تيليجرام، "
    "يبقى رصيدك وبياناتك محفوظين طالما تستخدم <b>نفس حسابك</b> على تيليجرام."
)
