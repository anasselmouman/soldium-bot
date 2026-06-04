# -*- coding: utf-8 -*-
"""اختيار شبكة USDT للسحب — رصيد عادي وأرباح الإحالة."""

from __future__ import annotations

import json
import logging
from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import MIN_CRYPTO_WITHDRAW_DH, MIN_REFERRAL_WITHDRAW_DH
from database import get_user
from keyboards.crypto_withdraw import build_crypto_withdraw_network_menu
from keyboards.payment import build_withdraw_step_nav
from keyboards.referrals import build_referral_withdraw_step_nav
from utils.crypto_usdt import (
    CryptoWithdrawOption,
    build_crypto_withdraw_details_json,
    format_crypto_step_label,
    format_crypto_withdraw_address_prompt,
    format_crypto_withdraw_amount_prompt,
    format_crypto_withdraw_picker_html,
    get_crypto_withdraw_option,
    parse_crypto_withdraw_destination,
)
from utils.flow_transcript import (
    discard_ephemeral_flow_messages,
    track_transcript_user_message,
)
from utils.fsm_prompt_cleanup import clear_last_prompt
from utils.money import format_dh
from utils.payment_banks import PAYMENT_BY_KEY, PaymentMethod
from utils.states import ReferralFlow, WithdrawFlow

logger = logging.getLogger(__name__)
router = Router()

_BACK_ADDRESS_NORMAL = "withdraw:crypto:back_address"
_BACK_ADDRESS_REFERRAL = "referral:withdraw:crypto:back_address"


def _flow_kind_from_callback(data: str) -> str:
    return "referral" if data.startswith("referral:") else "normal"


def _format_crypto_withdraw_picker_living(
    flow_kind: str,
    user_id: int,
    method: PaymentMethod,
) -> str:
    picker = format_crypto_withdraw_picker_html()
    step_header = format_crypto_step_label(1, "اختر شبكة الاستلام")
    if flow_kind == "referral":
        from handlers.referrals import format_breadcrumb

        user = get_user(user_id)
        referral_balance = float((user or {}).get("referral_balance") or 0.0)
        return "\n".join(
            [
                format_breadcrumb("إربح المال", "سحب أرباح الإحالة"),
                "",
                f"<b>طريقة:</b> {escape(method.breadcrumb_label)}",
                f"المتاح: <b>{format_dh(referral_balance)}</b>",
                "",
                step_header,
                "",
                picker,
            ]
        )
    from handlers.payment import (
        _withdraw_main_breadcrumb,
        _withdrawable_amount_for_method,
    )

    user = get_user(user_id)
    balance = format_dh(user["balance"]) if user else format_dh(0)
    method_available = _withdrawable_amount_for_method(user_id, method)
    return "\n".join(
        [
            _withdraw_main_breadcrumb(),
            "",
            f"<b>طريقة:</b> {escape(method.breadcrumb_label)}",
            f"رصيدك: <b>{balance}</b> · المتاح للسحب: <b>{format_dh(method_available)}</b>",
            "",
            step_header,
            "",
            picker,
        ]
    )


def _format_crypto_withdraw_living_summary(
    flow_kind: str,
    user_id: int,
    method: PaymentMethod,
    option: CryptoWithdrawOption,
    *,
    destination: str | None = None,
) -> str:
    if flow_kind == "referral":
        from handlers.referrals import format_breadcrumb

        user = get_user(user_id)
        referral_balance = float((user or {}).get("referral_balance") or 0.0)
        header = format_breadcrumb("إربح المال", "سحب أرباح الإحالة")
        balance_line = f"المتاح: <b>{format_dh(referral_balance)}</b>"
    else:
        from handlers.payment import (
            _withdraw_main_breadcrumb,
            _withdrawable_amount_for_method,
        )

        user = get_user(user_id)
        balance = format_dh(user["balance"]) if user else format_dh(0)
        method_available = _withdrawable_amount_for_method(user_id, method)
        header = _withdraw_main_breadcrumb()
        balance_line = (
            f"رصيدك: <b>{balance}</b> · المتاح للسحب: "
            f"<b>{format_dh(method_available)}</b>"
        )

    lines = [
        header,
        "",
        f"<b>طريقة:</b> {escape(method.breadcrumb_label)}",
        balance_line,
        "",
        f"<b>شبكة الاستلام:</b> {escape(option.title)}",
    ]
    if destination:
        label = "Pay ID" if option.is_binance_pay else "الوجهة"
        lines.append(f"<b>{label}:</b> <code>{escape(destination)}</code>")
    return "\n".join(lines)


def _crypto_amount_prompt_text(
    flow_kind: str,
    user_id: int,
    method: PaymentMethod,
    option: CryptoWithdrawOption,
) -> str:
    if flow_kind == "referral":
        user = get_user(user_id)
        available = float((user or {}).get("referral_balance") or 0.0)
        min_dh = MIN_REFERRAL_WITHDRAW_DH
    else:
        from handlers.payment import _withdrawable_amount_for_method

        available = _withdrawable_amount_for_method(user_id, method)
        min_dh = MIN_CRYPTO_WITHDRAW_DH
    return format_crypto_withdraw_amount_prompt(
        method_label=method.breadcrumb_label,
        available_dh=available,
        min_dh=min_dh,
        option=option,
    )


async def _send_crypto_step_prompt(
    bot: Bot,
    state: FSMContext,
    user_id: int,
    chat_id: int,
    text: str,
    back_callback: str,
    *,
    flow_kind: str,
) -> int | None:
    if flow_kind == "referral":
        from handlers.referrals import _send_referral_step_prompt

        return await _send_referral_step_prompt(
            bot, state, user_id, chat_id, text, back_callback
        )
    from handlers.payment import _send_payment_step_prompt

    return await _send_payment_step_prompt(
        bot, state, user_id, chat_id, text, back_callback
    )


async def _restore_crypto_address_step(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    *,
    flow_kind: str,
) -> None:
    if not callback.message or not callback.from_user:
        return
    data = await state.get_data()
    network_key = str(data.get("withdraw_crypto_network_key", ""))
    option = get_crypto_withdraw_option(network_key)
    method = PAYMENT_BY_KEY.get("crypto")
    if option is None or method is None:
        if flow_kind == "referral":
            from handlers.referrals import referral_withdraw

            await referral_withdraw(callback, state, bot)
        else:
            from handlers.payment import withdraw_gateway_handler

            await withdraw_gateway_handler(callback, state, bot)
        return

    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    back_cb = (
        "referral:withdraw:crypto:nets"
        if flow_kind == "referral"
        else "withdraw:crypto:nets"
    )
    living_text = _format_crypto_withdraw_living_summary(
        flow_kind, user_id, method, option
    )
    if flow_kind == "referral":
        await state.set_state(ReferralFlow.withdraw_crypto_address)
        from handlers.referrals import _edit_referral_from_callback, _sync_referral_nav_anchor

        await _edit_referral_from_callback(
            callback, state, bot, text=living_text, reply_markup=None, sync_nav=False
        )
        await _sync_referral_nav_anchor(bot, state, user_id, None)
    else:
        await state.set_state(WithdrawFlow.enter_crypto_address)
        from handlers.payment import (
            _edit_payment_from_callback,
            _sync_payment_nav_anchor,
        )

        await _edit_payment_from_callback(
            callback, state, bot, text=living_text, reply_markup=None, sync_nav=False
        )
        await _sync_payment_nav_anchor(bot, state, user_id, None)

    await _send_crypto_step_prompt(
        bot,
        state,
        user_id,
        chat_id,
        format_crypto_withdraw_address_prompt(option),
        back_cb,
        flow_kind=flow_kind,
    )


async def show_crypto_withdraw_network_picker(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    *,
    method: PaymentMethod,
    flow_kind: str,
    back_callback: str,
) -> None:
    if not callback.message or not callback.from_user:
        return
    prefix = "referral:withdraw" if flow_kind == "referral" else "withdraw"
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    await clear_last_prompt(callback.message, state)
    await discard_ephemeral_flow_messages(bot, state, user_id, chat_id)
    # البيانات في FSM data؛ الحالة تُضبط عند اختيار الشبكة
    await state.set_state(None)
    state_payload: dict[str, object] = {
        "withdraw_method_key": method.key,
        "withdraw_method_label": method.ledger_name,
        "withdraw_flow_kind": flow_kind,
        "withdraw_crypto_network_key": None,
        "withdraw_crypto_details_json": None,
    }
    if flow_kind == "referral":
        state_payload["referral_withdraw_method_key"] = method.key
    await state.update_data(**state_payload)
    text = _format_crypto_withdraw_picker_living(flow_kind, user_id, method)
    pay_id = method.binance_pay_id or ""
    markup = build_crypto_withdraw_network_menu(
        prefix,
        back_callback=back_callback,
        binance_pay_id=pay_id,
    )
    if flow_kind == "referral":
        from handlers.referrals import _edit_referral_from_callback

        await _edit_referral_from_callback(
            callback, state, bot, text=text, reply_markup=markup, sync_nav=True
        )
    else:
        from handlers.payment import _edit_payment_from_callback

        await _edit_payment_from_callback(
            callback,
            state,
            bot,
            text=text,
            reply_markup=markup,
            sync_nav=True,
        )


@router.callback_query(
    (F.data.startswith("withdraw:crypto:net:"))
    | (F.data.startswith("referral:withdraw:crypto:net:"))
)
async def crypto_withdraw_network_handler(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if not callback.message or not callback.from_user:
        return
    flow_kind = _flow_kind_from_callback(callback.data or "")
    network_key = (callback.data or "").split(":")[-1]
    option = get_crypto_withdraw_option(network_key)
    if option is None:
        await callback.answer("شبكة غير معروفة", show_alert=True)
        return
    method = PAYMENT_BY_KEY.get("crypto")
    if method is None:
        await callback.answer("غير متاح", show_alert=True)
        return
    await clear_last_prompt(callback.message, state)
    await state.update_data(withdraw_crypto_network_key=option.key)
    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    back_cb = (
        "referral:withdraw:crypto:nets"
        if flow_kind == "referral"
        else "withdraw:crypto:nets"
    )
    living_text = _format_crypto_withdraw_living_summary(
        flow_kind, user_id, method, option
    )
    if flow_kind == "referral":
        await state.set_state(ReferralFlow.withdraw_crypto_address)
        from handlers.referrals import _edit_referral_from_callback, _sync_referral_nav_anchor

        await _edit_referral_from_callback(
            callback,
            state,
            bot,
            text=living_text,
            reply_markup=None,
            sync_nav=False,
        )
        await _sync_referral_nav_anchor(bot, state, user_id, None)
    else:
        await state.set_state(WithdrawFlow.enter_crypto_address)
        from handlers.payment import (
            _edit_payment_from_callback,
            _sync_payment_nav_anchor,
        )

        await _edit_payment_from_callback(
            callback,
            state,
            bot,
            text=living_text,
            reply_markup=None,
            sync_nav=False,
        )
        await _sync_payment_nav_anchor(bot, state, user_id, None)

    await _send_crypto_step_prompt(
        bot,
        state,
        user_id,
        chat_id,
        format_crypto_withdraw_address_prompt(option),
        back_cb,
        flow_kind=flow_kind,
    )
    await callback.answer()


@router.callback_query(F.data == "withdraw:crypto:nets")
async def withdraw_crypto_back_nets_handler(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if not callback.message or not callback.from_user:
        return
    method = PAYMENT_BY_KEY.get("crypto")
    if method is None:
        await callback.answer("غير متاح", show_alert=True)
        return
    await show_crypto_withdraw_network_picker(
        callback,
        state,
        bot,
        method=method,
        flow_kind="normal",
        back_callback="account:finance:withdraw",
    )
    await callback.answer()


@router.callback_query(F.data == "referral:withdraw:crypto:nets")
async def referral_crypto_back_nets_handler(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    if not callback.message or not callback.from_user:
        return
    method = PAYMENT_BY_KEY.get("crypto")
    if method is None:
        await callback.answer("غير متاح", show_alert=True)
        return
    await show_crypto_withdraw_network_picker(
        callback,
        state,
        bot,
        method=method,
        flow_kind="referral",
        back_callback="referral:withdraw",
    )
    await callback.answer()


@router.callback_query(F.data == _BACK_ADDRESS_NORMAL)
async def withdraw_crypto_back_address_handler(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    await _restore_crypto_address_step(callback, state, bot, flow_kind="normal")
    await callback.answer()


@router.callback_query(F.data == _BACK_ADDRESS_REFERRAL)
async def referral_crypto_back_address_handler(
    callback: CallbackQuery, state: FSMContext, bot: Bot
) -> None:
    await _restore_crypto_address_step(callback, state, bot, flow_kind="referral")
    await callback.answer()


async def _crypto_address_to_amount_step(
    message: Message,
    state: FSMContext,
    bot: Bot,
    *,
    flow_kind: str,
) -> None:
    if not message.from_user:
        return
    data = await state.get_data()
    network_key = str(data.get("withdraw_crypto_network_key", ""))
    option = get_crypto_withdraw_option(network_key)
    if option is None:
        return
    destination, error = parse_crypto_withdraw_destination(option, message.text or "")
    nav = (
        build_referral_withdraw_step_nav()
        if flow_kind == "referral"
        else build_withdraw_step_nav()
    )
    if destination is None:
        if flow_kind == "referral":
            from handlers.referrals import _flash_referral_input_error

            await _flash_referral_input_error(
                message, state, bot, error, restore_markup=nav
            )
        else:
            from handlers.payment import _flash_deposit_input_error

            await _flash_deposit_input_error(
                message, state, bot, error, restore_markup=nav
            )
        return

    details = build_crypto_withdraw_details_json(option, destination)
    await track_transcript_user_message(state, message)
    await state.update_data(
        withdraw_crypto_details_json=json.dumps(details, ensure_ascii=False),
    )
    user_id = message.from_user.id
    chat_id = message.chat.id
    method = PAYMENT_BY_KEY.get("crypto")
    if method is None:
        return

    summary = _format_crypto_withdraw_living_summary(
        flow_kind, user_id, method, option, destination=destination
    )
    amount_prompt = _crypto_amount_prompt_text(flow_kind, user_id, method, option)
    back_cb = (
        _BACK_ADDRESS_REFERRAL if flow_kind == "referral" else _BACK_ADDRESS_NORMAL
    )

    if flow_kind == "referral":
        await state.set_state(ReferralFlow.withdraw_amount)
        from handlers.referrals import _edit_referral_screen, _sync_referral_nav_anchor

        await _edit_referral_screen(bot, state, user_id, summary, None)
        await _sync_referral_nav_anchor(bot, state, user_id, None)
    else:
        await state.set_state(WithdrawFlow.enter_amount)
        from handlers.payment import _edit_payment_living, _sync_payment_nav_anchor

        await _edit_payment_living(bot, state, user_id, summary, None)
        await _sync_payment_nav_anchor(bot, state, user_id, None)

    await _send_crypto_step_prompt(
        bot, state, user_id, chat_id, amount_prompt, back_cb, flow_kind=flow_kind
    )


@router.message(StateFilter(WithdrawFlow.enter_crypto_address), F.text)
async def withdraw_crypto_address_handler(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    await _crypto_address_to_amount_step(message, state, bot, flow_kind="normal")


@router.message(StateFilter(ReferralFlow.withdraw_crypto_address), F.text)
async def referral_crypto_address_handler(
    message: Message, state: FSMContext, bot: Bot
) -> None:
    await _crypto_address_to_amount_step(message, state, bot, flow_kind="referral")
