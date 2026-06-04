# -*- coding: utf-8 -*-
"""ترجمة حالات الطلب للعربية واستخراج بيانات التنفيذ من استجابة واجهة الحالة."""


def format_order_status_ar(status: str) -> str:
    """مسميات الحالات للمستخدم (عربية فصحى، دون إشارة لجهات خارجية)."""
    normalized = (status or "").strip().lower().replace("_", " ")
    mapping = {
        "submitted": "⏳ قيد الانتظار",
        "pending": "⏳ قيد الانتظار",
        "in progress": "🚀 قيد التنفيذ",
        "processing": "🚀 قيد التنفيذ",
        "completed": "✅ مكتمل",
        "canceled": "🚫 ملغي",
        "cancelled": "🚫 ملغي",
        "partial": "🟡 مكتمل جزئياً مع استرداد",
        "refunded": "💰 مسترد",
        "failed": "❌ فشل التنفيذ",
    }
    return mapping.get(normalized, "🔄 قيد المعالجة")


def normalize_order_status_key(raw_status: object) -> str:
    status = str(raw_status or "").strip().lower().replace("_", " ")
    aliases = {
        "cancelled": "canceled",
        "ملغي": "canceled",
        "جزئي": "partial",
    }
    return aliases.get(status, status)


def is_order_in_execution_status(status: str) -> bool:
    s = (status or "").strip().lower().replace("_", " ")
    return s in {"in progress", "processing"}


def extract_start_count_from_status_payload(data: object) -> int | None:
    """يستخرج عداد البداية من حقول شائعة في استجابات واجهات SMM."""
    if not isinstance(data, dict):
        return None
    for key in ("start_count", "start", "starts"):
        val = data.get(key)
        if val is None or val == "":
            continue
        try:
            return int(float(str(val)))
        except (TypeError, ValueError):
            continue
    return None
