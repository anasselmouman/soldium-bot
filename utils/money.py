from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

MONEY_STEP = Decimal("0.000001")


def to_decimal(value: object) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def to_float(value: object) -> float:
    return float(to_decimal(value))


def format_amount(value: object) -> str:
    normalized = to_decimal(value).normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_amount_2(value: object) -> str:
    """عرض المبالغ برقمين بعد الفاصلة للواجهات."""
    return f"{to_decimal(value):.2f}"


def format_dh(value: object) -> str:
    return f"{format_amount(value)} DH"


def usdt_to_balance_dh(usdt: object, rate: float) -> float:
    """تحويل USDT المؤكَّد من الأدمن إلى مبلغ يُضاف لرصيد المستخدم (DH)."""
    return to_float(to_decimal(usdt) * to_decimal(rate))


def balance_dh_to_usdt(dh: object, rate: float) -> float:
    """تحويل مبلغ DH إلى USDT تقريبي (للعرض عند السحب بالكريبتو)."""
    rate_dec = to_decimal(rate)
    if rate_dec <= 0:
        return 0.0
    return to_float(to_decimal(dh) / rate_dec)


def format_usdt(value: object) -> str:
    return f"{format_amount(value)} USDT"


def recharge_face_value_to_credit(face_value: object, ratio: float = 0.70) -> float:
    """قيمة التعبئة الأصلية × النسبة (افتراضياً 70%) → رصيد يُضاف للمستخدم."""
    return to_float(to_decimal(face_value) * to_decimal(ratio))
