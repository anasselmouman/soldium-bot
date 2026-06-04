# -*- coding: utf-8 -*-
"""عرض معلومات السحب من details_json."""

from __future__ import annotations

import json
from html import escape

from config import USDT_TO_DH_RATE
from utils.money import balance_dh_to_usdt, format_usdt

_DETAIL_LABELS = {
    "name": "الاسم",
    "account": "رقم الحساب",
    "phone": "رقم الهاتف",
    "destination": "وجهة الاستلام",
    "email": "إيميل PayPal",
    "details": "المعلومات",
    "crypto_network_label": "شبكة USDT",
    "network_fee_usdt": "رسوم الشبكة",
    "payout_type": "نوع الاستلام",
}

_DETAIL_ORDER = (
    "crypto_network_label",
    "network_fee_usdt",
    "payout_type",
    "destination",
    "name",
    "account",
    "phone",
    "email",
    "details",
)


def safe_withdraw_details(raw: object) -> dict[str, str]:
    try:
        data = json.loads(str(raw or "{}"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items()}


def is_crypto_withdraw_details(details: dict[str, str]) -> bool:
    return bool(
        str(details.get("crypto_network_key", "") or "").strip()
        or str(details.get("crypto_network_label", "") or "").strip()
    )


_SKIP_DISPLAY_KEYS = frozenset({"payout_type", "crypto_network_key", "reference_deposit_address"})


def format_crypto_withdraw_details_html(details: dict[str, str]) -> str:
    network = str(details.get("crypto_network_label", "") or "").strip()
    fee = str(details.get("network_fee_usdt", "") or "").strip()
    destination = str(details.get("destination", "") or "").strip()
    payout_type = str(details.get("payout_type", "") or "").strip()
    lines: list[str] = []
    if network:
        lines.append(f"• شبكة USDT: <b>{escape(network)}</b>")
    if fee and payout_type != "binance_pay":
        lines.append(f"• رسوم الشبكة: <b>{escape(fee)} USDT</b> (تُخصم من USDT المُرسَل)")
    elif payout_type == "binance_pay":
        lines.append("• نوع الاستلام: <b>Binance Pay</b> (بدون رسوم شبكة)")
    if destination:
        label = "Pay ID" if payout_type == "binance_pay" else "الوجهة"
        lines.append(f"• {label}: <code>{escape(destination)}</code>")
    lines.append(
        "⚠️ <b>تأكد أن شبكة المحفظة = شبكة الاستلام المختارة.</b>"
    )
    return "\n".join(lines)


def format_withdraw_details_display(details: dict[str, str]) -> str:
    if is_crypto_withdraw_details(details):
        plain = format_crypto_withdraw_details_html(details)
        return plain.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", "")
    parts: list[str] = []
    for key in _DETAIL_ORDER:
        if key in _SKIP_DISPLAY_KEYS:
            continue
        value = str(details.get(key, "") or "").strip()
        if value:
            parts.append(f"{_DETAIL_LABELS.get(key, key)}: {value}")
    for key, value in details.items():
        if key in _DETAIL_ORDER or key in _SKIP_DISPLAY_KEYS:
            continue
        value = str(value or "").strip()
        if value:
            parts.append(f"{_DETAIL_LABELS.get(key, key)}: {value}")
    return " | ".join(parts)


def format_withdraw_details_admin_lines(
    details: dict[str, str],
    *,
    amount_dh: float | None = None,
    rate: float = USDT_TO_DH_RATE,
) -> list[str]:
    """سطر HTML لكل حقل — كل قيمة داخل <code> للنسخ المنفصل في تيليجرام."""
    if is_crypto_withdraw_details(details):
        lines: list[str] = []
        for line in format_crypto_withdraw_details_html(details).splitlines():
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
        if amount_dh is not None and amount_dh > 0:
            usdt = balance_dh_to_usdt(amount_dh, rate)
            lines.append(f"• USDT التقريبي: <b>≈ {escape(format_usdt(usdt))}</b>")
        return lines

    lines: list[str] = []
    seen: set[str] = set()
    for key in _DETAIL_ORDER:
        value = str(details.get(key, "") or "").strip()
        if not value:
            continue
        seen.add(key)
        label = _DETAIL_LABELS.get(key, key)
        lines.append(f"• {label}: <code>{escape(value)}</code>")
    for key, value in details.items():
        if key in seen:
            continue
        value = str(value or "").strip()
        if not value:
            continue
        label = _DETAIL_LABELS.get(key, key)
        lines.append(f"• {label}: <code>{escape(value)}</code>")
    return lines
