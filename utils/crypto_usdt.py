# -*- coding: utf-8 -*-
"""شبكات إيداع USDT — عناوين، رسوم، ورسالة واحدة مدمجة."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from html import escape

from config import MIN_CRYPTO_DEPOSIT_USDT, USDT_TO_DH_RATE
from utils.money import balance_dh_to_usdt, format_dh, format_usdt, to_float

def _pay_env(key: str, default: str) -> str:
    value = os.environ.get(key, "").strip()
    return value if value else default


BINANCE_PAY_WITHDRAW_KEY = "binance_pay"
CRYPTO_WITHDRAW_STEP_TOTAL = 3

_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_TRC20_ADDRESS_RE = re.compile(r"^T[1-9A-HJ-NP-Za-km-z]{33}$")
_SOL_ADDRESS_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
_BASE58_CHARS = set("123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz")

CRYPTO_WITHDRAW_EXCHANGE_NOTICE = (
    "💵 <b>سعر الصرف:</b> يُحوَّل المبلغ إلى USDT حسب سعر البيع في Binance "
    "لحظة معالجة السحب يوم الجمعة."
)


@dataclass(frozen=True, slots=True)
class UsdtNetwork:
    key: str
    title: str
    network_fee_usdt: str
    wallet_address: str
    special_alert_html: str | None = None


@dataclass(frozen=True, slots=True)
class CryptoWithdrawOption:
    key: str
    title: str
    network_fee_usdt: str
    is_binance_pay: bool = False


USDT_NETWORKS: tuple[UsdtNetwork, ...] = (
    UsdtNetwork(
        key="bsc",
        title="BNB Smart Chain (BSC - BEP20)",
        network_fee_usdt="0.01",
        wallet_address=_pay_env(
            "PAY_USDT_BEP20",
            "0xfee61cdf284269bd5733befcac8bb8b85a4e8d99",
        ),
    ),
    UsdtNetwork(
        key="opbnb",
        title="opBNB",
        network_fee_usdt="0.015",
        wallet_address=_pay_env(
            "PAY_USDT_OPBNB",
            "0xfee61cdf284269bd5733befcac8bb8b85a4e8d99",
        ),
    ),
    UsdtNetwork(
        key="tron",
        title="Tron (TRX - TRC20)",
        network_fee_usdt="1.3",
        wallet_address=_pay_env(
            "PAY_USDT_TRC20",
            "TNVUjeJLFdexp6P3V5TQFsmVfvozcsKzQ4",
        ),
    ),
    UsdtNetwork(
        key="eth",
        title="Ethereum (ETH - ERC20)",
        network_fee_usdt="0.4",
        wallet_address=_pay_env(
            "PAY_USDT_ERC20",
            "0xfee61cdf284269bd5733befcac8bb8b85a4e8d99",
        ),
    ),
    UsdtNetwork(
        key="sol",
        title="Solana (SOL)",
        network_fee_usdt="0.3",
        wallet_address=_pay_env(
            "PAY_USDT_SOL",
            "5rGrB6KZwooCchMUewqSKFSueSC4mReEiAkayZRXdRzY",
        ),
        special_alert_html=(
            "🛑 <b>تنبيه:</b> انسخ عنوان سولانا حرفاً بحرف (حساس لحالة الأحرف)."
        ),
    ),
)

USDT_NETWORK_BY_KEY: dict[str, UsdtNetwork] = {n.key: n for n in USDT_NETWORKS}

_BINANCE_PAY_OPTION = CryptoWithdrawOption(
    key=BINANCE_PAY_WITHDRAW_KEY,
    title="Binance Pay",
    network_fee_usdt="0",
    is_binance_pay=True,
)


def iter_crypto_withdraw_options() -> tuple[CryptoWithdrawOption, ...]:
    chain_options = tuple(
        CryptoWithdrawOption(
            key=n.key,
            title=n.title,
            network_fee_usdt=n.network_fee_usdt,
        )
        for n in USDT_NETWORKS
    )
    return (_BINANCE_PAY_OPTION, *chain_options)


CRYPTO_WITHDRAW_OPTION_BY_KEY: dict[str, CryptoWithdrawOption] = {
    o.key: o for o in iter_crypto_withdraw_options()
}


def get_crypto_withdraw_option(key: str) -> CryptoWithdrawOption | None:
    return CRYPTO_WITHDRAW_OPTION_BY_KEY.get(key.strip())


_WITHDRAW_BUTTON_SHORT: dict[str, str] = {
    "bsc": "BSC (BEP20)",
    "opbnb": "opBNB",
    "tron": "Tron (TRC20)",
    "eth": "Ethereum (ERC20)",
    "sol": "Solana (SOL)",
}


def format_crypto_step_label(step: int, title: str, *, total: int = CRYPTO_WITHDRAW_STEP_TOTAL) -> str:
    return f"<b>الخطوة {step} من {total}: {title}</b>"


def crypto_withdraw_network_button_text(
    option: CryptoWithdrawOption,
    *,
    binance_pay_id: str = "",
) -> str:
    del binance_pay_id  # معرّف المنصة للإيداع فقط — لا يُعرض في السحب
    if option.is_binance_pay:
        return "Binance Pay · بدون رسوم"
    short = _WITHDRAW_BUTTON_SHORT.get(option.key, option.title)
    return f"{short} · ~{option.network_fee_usdt} USDT"


def format_crypto_withdraw_picker_html() -> str:
    return "\n".join(
        [
            "<b>💰 سحب USDT — اختر شبكة الاستلام</b>",
            "⚠️ اختر الشبكة المطابقة تماماً — شبكة خاطئة = خسارة دائمة.",
            "ℹ️ رسوم الشبكة تُخصم من USDT المُرسَل إليك (لا من رصيد DH).",
            "",
            "اختر من الأزرار أدناه 👇",
        ]
    )


def format_crypto_withdraw_address_prompt(option: CryptoWithdrawOption) -> str:
    if option.is_binance_pay:
        return (
            f"{format_crypto_step_label(2, 'Binance Pay ID')}\n\n"
            "أرسل <b>Pay ID الخاص بك</b> لاستقبال USDT (أرقام فقط).\n"
            "مثال: <code>784548487</code>"
        )
    network = USDT_NETWORK_BY_KEY.get(option.key)
    extra = ""
    if network and network.special_alert_html:
        extra = f"\n\n{network.special_alert_html}"
    return (
        f"{format_crypto_step_label(2, 'عنوان المحفظة')}\n\n"
        f"الشبكة: <b>{escape(option.title)}</b>\n"
        f"رسوم الشبكة: <b>{escape(option.network_fee_usdt)} USDT</b> "
        "(تُخصم من USDT المُرسَل)\n\n"
        "أرسل عنوان محفظتك في رسالة واحدة (انسخه بدقة)."
        f"{extra}"
    )


def _validate_evm_address(text: str) -> tuple[str | None, str]:
    if not _EVM_ADDRESS_RE.match(text):
        return None, "أرسل عنوان EVM صحيحاً (يبدأ بـ 0x ويليه 40 رمز hex)."
    return text, ""


def _validate_trc20_address(text: str) -> tuple[str | None, str]:
    if not _TRC20_ADDRESS_RE.match(text):
        return None, "أرسل عنوان Tron (TRC20) صحيحاً (يبدأ بـ T و34 حرفاً)."
    return text, ""


def _validate_solana_address(text: str) -> tuple[str | None, str]:
    if not _SOL_ADDRESS_RE.match(text):
        return None, "أرسل عنوان Solana صحيحاً (32–44 حرف base58)."
    if not all(ch in _BASE58_CHARS for ch in text):
        return None, "عنوان Solana يحتوي أحرفاً غير صالحة."
    return text, ""


def parse_crypto_withdraw_destination(
    option: CryptoWithdrawOption,
    raw: str,
) -> tuple[str | None, str]:
    text = raw.strip()
    if not text:
        return None, "أرسل العنوان أو Pay ID ولا تترك الرسالة فارغة."
    if option.is_binance_pay:
        compact = "".join(ch for ch in text if ch.isdigit())
        if len(compact) < 5:
            return None, "أرسل Binance Pay ID صحيحاً (5 أرقام على الأقل)."
        if len(compact) > 15:
            return None, "Binance Pay ID طويل جداً. تحقق وأعد الإرسال."
        return compact, ""
    if option.key in ("bsc", "eth", "opbnb"):
        return _validate_evm_address(text)
    if option.key == "tron":
        return _validate_trc20_address(text)
    if option.key == "sol":
        return _validate_solana_address(text)
    if len(text) < 8:
        return None, "عنوان المحفظة قصير. تحقق وأعد الإرسال."
    return text, ""


def build_crypto_withdraw_details_json(
    option: CryptoWithdrawOption,
    destination: str,
) -> dict[str, str]:
    payload: dict[str, str] = {
        "payout_type": "binance_pay" if option.is_binance_pay else "wallet",
        "crypto_network_key": option.key,
        "crypto_network_label": option.title,
        "network_fee_usdt": option.network_fee_usdt,
        "destination": destination,
    }
    if not option.is_binance_pay:
        network = USDT_NETWORK_BY_KEY.get(option.key)
        if network:
            payload["reference_deposit_address"] = network.wallet_address
    return payload


def crypto_network_fee_warning(
    amount_dh: float,
    fee_usdt: str,
    *,
    rate: float = USDT_TO_DH_RATE,
) -> str | None:
    """تحذير إذا USDT التقريبي أقل من رسوم الشبكة (لا يمنع الإرسال)."""
    try:
        fee = to_float(fee_usdt)
    except Exception:
        return None
    if fee <= 0:
        return None
    usdt_approx = balance_dh_to_usdt(amount_dh, rate)
    if usdt_approx < fee:
        return (
            f"⚠️ <b>تنبيه:</b> المبلغ ≈ <b>{format_usdt(usdt_approx)}</b> "
            f"قد لا يكفي لتغطية رسوم الشبكة (~{format_usdt(fee)}). "
            "فكّر في زيادة المبلغ أو اختيار شبكة برسوم أقل."
        )
    return None


def format_crypto_withdraw_amount_prompt(
    *,
    method_label: str,
    available_dh: float,
    min_dh: float,
    option: CryptoWithdrawOption | None = None,
    rate: float = USDT_TO_DH_RATE,
) -> str:
    lines = [
        format_crypto_step_label(3, "المبلغ"),
        "",
        "أرسل المبلغ الذي تريد سحبه بالأرقام فقط (بالدرهم DH).",
        f"الحد الأدنى: <b>{format_dh(min_dh)}</b> — "
        f"المتاح: <b>{format_dh(available_dh)}</b>.",
        f"طريقة السحب: <b>{escape(method_label)}</b>.",
        "",
        CRYPTO_WITHDRAW_EXCHANGE_NOTICE,
        "ℹ️ رسوم الشبكة تُخصم من USDT المُرسَل (لا من رصيد DH).",
    ]
    if option is not None and not option.is_binance_pay:
        lines.append(
            f"رسوم شبكة <b>{escape(option.title)}</b>: "
            f"<b>{escape(option.network_fee_usdt)} USDT</b>."
        )
    return "\n".join(lines)


def format_crypto_usdt_estimate_line(amount_dh: float, *, rate: float = USDT_TO_DH_RATE) -> str:
    usdt = balance_dh_to_usdt(amount_dh, rate)
    return f"USDT التقريبي: <b>≈ {format_usdt(usdt)}</b> (حسب سعر Binance لحظة المعالجة)"


_DEPOSIT_CHAIN_EMOJI = ("2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣")
# حد تعليق الصورة في تيليجرام — إن تجاوزناه يُزال HTML ولا يعمل النسخ بالضغط
TELEGRAM_PHOTO_CAPTION_MAX = 1024

def format_crypto_deposit_html(*, binance_pay_id: str) -> str:
    """رسالة واحدة: Binance Pay + كل شبكات USDT."""
    pay_id = escape(binance_pay_id.strip())
    min_usdt = int(MIN_CRYPTO_DEPOSIT_USDT)
    lines = [
        "<b>💰 إيداع USDT</b>",
        "",
        "حوّل مبلغ الشحن عبر إحدى الطرق أدناه.",
        "💵 <b>سعر الصرف:</b> سعر البيع في Binance لحظة التأكيد.",
        f"الحد الأدنى: <b>{min_usdt} USDT</b> صافياً بعد رسوم الشبكة.",
        "",
        "⚠️ بعد التحويل أرسل لقطة الشاشة في الرسالة التالية.",
        "ℹ️ اضغط على العنوان لنسخه.",
        "⚠️ الشبكة يجب أن تطابق التحويل — خطأ = خسارة دائمة.",
        "",
        "<b>1️⃣ Binance Pay</b>",
        "رسوم: 0 USDT",
        f"<code>{pay_id}</code>",
        "",
    ]
    for emoji, network in zip(_DEPOSIT_CHAIN_EMOJI, USDT_NETWORKS, strict=True):
        addr = escape(network.wallet_address)
        lines.append(f"<b>{emoji} {escape(network.title)}</b>")
        lines.append(f"رسوم: {escape(network.network_fee_usdt)} USDT")
        lines.append(f"<code>{addr}</code>")
        if network.special_alert_html:
            lines.append(network.special_alert_html)
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)
