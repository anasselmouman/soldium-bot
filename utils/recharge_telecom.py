# -*- coding: utf-8 -*-
"""شركات اتصالات — شحن عبر بطاقات التعبئة."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TelecomOperator:
    key: str
    button_label: str
    ledger_name: str
    display_name: str


TELECOM_OPERATORS: tuple[TelecomOperator, ...] = (
    TelecomOperator(
        key="orange",
        button_label="🟠 أورنج (Orange)",
        ledger_name="Orange",
        display_name="Orange",
    ),
    TelecomOperator(
        key="inwi",
        button_label="🟣 إنوي (Inwi)",
        ledger_name="Inwi",
        display_name="Inwi",
    ),
    TelecomOperator(
        key="iam",
        button_label="🔴 اتصالات المغرب (IAM)",
        ledger_name="IAM",
        display_name="اتصالات المغرب (IAM)",
    ),
)

TELECOM_BY_KEY: dict[str, TelecomOperator] = {t.key: t for t in TELECOM_OPERATORS}
RECHARGE_LEDGER_NAMES: frozenset[str] = frozenset(t.ledger_name for t in TELECOM_OPERATORS)
RECHARGE_FACE_VALUES_DH: tuple[int, ...] = (5, 10, 20, 50, 100, 200)


def is_recharge_ledger(method_name: str) -> bool:
    return method_name.strip() in RECHARGE_LEDGER_NAMES
