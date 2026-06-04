# -*- coding: utf-8 -*-
"""لوحات اختيار شبكة USDT للسحب."""

from __future__ import annotations

from typing import Literal

from aiogram.types import InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from keyboards.nav_labels import BTN_BACK_STEP
from utils.crypto_usdt import crypto_withdraw_network_button_text, iter_crypto_withdraw_options

WithdrawFlowPrefix = Literal["withdraw", "referral:withdraw"]


def build_crypto_withdraw_network_menu(
    flow_prefix: WithdrawFlowPrefix,
    *,
    back_callback: str,
    binance_pay_id: str,
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for option in iter_crypto_withdraw_options():
        builder.button(
            text=crypto_withdraw_network_button_text(
                option,
                binance_pay_id=binance_pay_id,
            ),
            callback_data=f"{flow_prefix}:crypto:net:{option.key}",
        )
    builder.button(text=BTN_BACK_STEP, callback_data=back_callback)
    builder.adjust(1)
    return builder.as_markup()
