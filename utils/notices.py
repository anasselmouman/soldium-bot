# -*- coding: utf-8 -*-
"""ملاحظات موحّدة للخدمات + إرشادات الرابط في خطوة الإدخال."""

from __future__ import annotations

import re
from html import escape

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_BOLD_MD_RE = re.compile(r"\*\*(.+?)\*\*")

GLOBAL_SERVICE_NOTES_HTML = (
    "<b>ملاحظات:</b>\n"
    "- يُرجى التأكد من أن الحساب عام وليس خاصًا.\n"
    "- في حال وجود ضغط كبير على الخدمة، قد يحدث تغيير في السرعة ووقت البدء.\n"
    "- يُرجى عدم إنشاء طلب ثانٍ لنفس الرابط قبل اكتمال الطلب الحالي داخل النظام."
)

_LINK_PROMPTS: dict[str, tuple[str, bool]] = {
    "x_live_broadcast": (
        "يرجى إرسال رابط البث المباشر (Live Link) الجاري حالياً.",
        False,
    ),
    "x_direct_messages": (
        "أرسل نص الطلب على سطرين:\n\n"
        "السطر 1 (LINK): رابط حسابك على X أو @yourusername\n\n"
        "السطر 2 (USERNAMES): من 5 إلى 10 يوزرات مفصولة بفواصل. مثال:\n"
        "<code>elonmusk, dogecoin, Nike, tesla</code>\n\n"
        "تجنّب الحسابات المقفلة خصوصياً (Private).",
        False,
    ),
    "x_spaces": (
        "أرسل رابط السبيس فقط. يجب أن يحتوي الرابط على كلمة <code>spaces</code>.",
        False,
    ),
}


def format_notice_html(note: str) -> str:
    """عرض ملاحظة بأمان في parse_mode=HTML."""
    text = str(note or "").strip()
    if not text:
        return ""
    if _HTML_TAG_RE.search(text):
        return text
    escaped = escape(text)
    return _BOLD_MD_RE.sub(r"<b>\1</b>", escaped)


def resolve_service_notice(service: dict, *, short: bool = False) -> str:
    """ملاحظات موحّدة لجميع الخدمات (short يُتجاهل — القصّ في order_flow)."""
    del service, short
    return GLOBAL_SERVICE_NOTES_HTML


def resolve_section_notice(section: dict) -> str:
    """نفس الملاحظات الموحّدة في شاشة اختيار الخدمات داخل القسم."""
    del section
    return GLOBAL_SERVICE_NOTES_HTML


def resolve_link_prompt(
    platform_key: str,
    section_key: str | None,
    subsection_key: str | None = None,
    *,
    service: dict | None = None,
) -> tuple[str | None, bool]:
    """نص إرشاد الرابط + هل يُقبل @username."""
    svc = service or {}
    key = str(svc.get("link_prompt_key") or "").strip()
    if key and key in _LINK_PROMPTS:
        text, allow_username = _LINK_PROMPTS[key]
        return text, allow_username

    sk = str(section_key or "").strip() or None
    ssk = str(subsection_key or "").strip() or None
    pk = str(platform_key or "").strip()

    if pk == "telegram" and ssk == "future_posts":
        return (
            "أرسل رابط <b>آخر منشور</b> في القناة (مثال: https://t.me/username/123). "
            "لن تُطبَّق المشاهدات على هذا المنشور، بل على المنشورات القادمة فقط.",
            False,
        )
    if pk == "telegram" and ssk == "past_posts":
        return (
            "أرسل رابط <b>القناة العام</b> فقط (مثال: https://t.me/username أو @username). "
            "لا تضع رابط منشور فردي.",
            True,
        )
    if pk == "telegram" and sk in {"members", "channel_members"}:
        return "أرسل رابط القناة أو المجموعة التي تريد إضافة الأعضاء إليها.", True
    if pk == "telegram" and sk == "post_share":
        return "أرسل رابط المنشور الذي تريد زيادة المشاركات عليه.", False
    if pk == "telegram" and sk == "start_bot":
        return "أرسل رابط البوت الذي تريد تشغيل الخدمة عليه.", False
    if pk == "telegram" and sk == "automatic_interactions":
        return (
            "أرسل رابط آخر منشور في القناة (مثال: https://t.me/username/123) لتحديد الحساب. "
            "لن تُطبَّق المشاهدات على هذا المنشور، بل على المنشورات الجديدة فقط.",
            True,
        )
    if pk == "x" and sk == "followers":
        return "أرسل رابط حسابك على X (تويتر) أو المعرف الخاص بك.", True
    if pk == "x" and sk == "likes":
        return "أرسل رابط التغريدة التي تريد زيادة الإعجابات عليها.", False
    if pk == "x" and sk in {"views", "video_views"}:
        return "أرسل رابط التغريدة التي تحتوي على الفيديو المطلوب زيادة مشاهداته.", False
    if pk == "x" and sk == "mentions":
        return (
            "أرسل رابط التغريدة لتنفيذ خدمة المنشن عليها. "
            "يجب أن يحتوي الرابط على <code>/status/</code>."
        ), False
    if pk == "x" and sk == "direct_messages":
        text, allow = _LINK_PROMPTS["x_direct_messages"]
        return text, allow
    if pk == "x" and sk == "spaces":
        text, allow = _LINK_PROMPTS["x_spaces"]
        return text, allow
    if pk == "x" and sk == "live_broadcast":
        text, allow = _LINK_PROMPTS["x_live_broadcast"]
        return text, allow

    return None, False
