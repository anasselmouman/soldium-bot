# -*- coding: utf-8 -*-
"""تنسيق رسائل إشعار الأدمن للطلبات (تيليغرام)."""

from __future__ import annotations

import html

PLATFORM_LABELS: dict[str, str] = {
    "instagram": "إنستغرام",
    "tiktok": "تيك توك",
    "facebook": "فيسبوك",
    "youtube": "يوتيوب",
    "telegram": "تيليغرام",
    "twitter": "تويتر",
    "snapchat": "سناب شات",
    "subscriptions": "اشتراكات",
}


def platform_label(platform_title: str | None, platform_key: str | None) -> str:
    title = str(platform_title or "").strip()
    if title:
        return title
    key = str(platform_key or "").strip().lower()
    if not key:
        return "—"
    return PLATFORM_LABELS.get(key, key)


def provider_label(
    provider_slug: str | None,
    api_account: str | None = None,
    provider_name: str | None = None,
) -> str:
    name = str(provider_name or "").strip()
    slug = str(provider_slug or "").strip().lower()
    account = str(api_account or "").strip().lower()
    if name and slug:
        base = f"{name} ({slug})"
    elif slug:
        base = slug
    elif name:
        base = name
    else:
        base = "—"
    if account and account not in {"", "default"}:
        return f"{base} · حساب {account}"
    return base


def order_refs_block_html(
    provider_order_id: str | None,
    internal_order_id: int,
) -> str:
    provider_ref = str(provider_order_id or "").strip()
    lines = []
    if provider_ref:
        lines.append(f'مرجع المزوّد: <code>{html.escape(provider_ref)}</code>')
    lines.append(f"المعرّف الداخلي: <code>{internal_order_id}</code>")
    return "\n".join(lines)
