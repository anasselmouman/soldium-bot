# -*- coding: utf-8 -*-
"""اختبارات شبكات سحب USDT."""

from __future__ import annotations

from config import MIN_CRYPTO_WITHDRAW_DH, USDT_TO_DH_RATE
from keyboards.crypto_withdraw import build_crypto_withdraw_network_menu
from utils.crypto_usdt import (
    BINANCE_PAY_WITHDRAW_KEY,
    build_crypto_withdraw_details_json,
    crypto_network_fee_warning,
    crypto_withdraw_network_button_text,
    format_crypto_step_label,
    format_crypto_withdraw_picker_html,
    get_crypto_withdraw_option,
    iter_crypto_withdraw_options,
    parse_crypto_withdraw_destination,
)
from utils.money import balance_dh_to_usdt, format_usdt
from utils.withdraw_details import (
    format_crypto_withdraw_details_html,
    format_withdraw_details_admin_lines,
    is_crypto_withdraw_details,
)


def test_withdraw_options_include_binance_and_five_chains() -> None:
    options = iter_crypto_withdraw_options()
    assert len(options) == 6
    assert options[0].key == BINANCE_PAY_WITHDRAW_KEY


def test_binance_button_hides_merchant_pay_id() -> None:
    option = get_crypto_withdraw_option(BINANCE_PAY_WITHDRAW_KEY)
    assert option is not None
    label = crypto_withdraw_network_button_text(option, binance_pay_id="784548487")
    assert "784548487" not in label
    assert "Binance Pay" in label
    assert "بدون رسوم" in label


def test_chain_button_shows_fee() -> None:
    option = get_crypto_withdraw_option("bsc")
    assert option is not None
    label = crypto_withdraw_network_button_text(option, binance_pay_id="1")
    assert "0.01" in label
    assert "BSC" in label


def test_picker_html_is_short_and_no_pay_id() -> None:
    html = format_crypto_withdraw_picker_html()
    assert len(html) < 400
    assert "784548487" not in html
    assert "اختر من الأزرار" in html
    assert "رسوم الشبكة" in html


def test_network_menu_one_button_per_row() -> None:
    markup = build_crypto_withdraw_network_menu(
        "withdraw",
        back_callback="account:finance:withdraw",
        binance_pay_id="123",
    )
    assert len(markup.inline_keyboard) == 7
    for row in markup.inline_keyboard:
        assert len(row) == 1


def test_parse_binance_pay_id_digits_only() -> None:
    option = get_crypto_withdraw_option(BINANCE_PAY_WITHDRAW_KEY)
    assert option is not None
    dest, err = parse_crypto_withdraw_destination(option, " 784548487 ")
    assert err == ""
    assert dest == "784548487"


def test_parse_trc20_valid_and_invalid() -> None:
    option = get_crypto_withdraw_option("tron")
    assert option is not None
    valid, err = parse_crypto_withdraw_destination(option, "TNVUjeJLFdexp6P3V5TQFsmVfvozcsKzQ4")
    assert err == ""
    assert valid is not None
    invalid, err = parse_crypto_withdraw_destination(option, "0xdeadbeef")
    assert invalid is None
    assert err


def test_parse_bsc_evm_address() -> None:
    option = get_crypto_withdraw_option("bsc")
    assert option is not None
    addr = "0xfee61cdf284269bd5733befcac8bb8b85a4e8d99"
    dest, err = parse_crypto_withdraw_destination(option, addr)
    assert err == ""
    assert dest == addr
    bad, err = parse_crypto_withdraw_destination(option, "TNVUjeJLFdexp6P3V5TQFsmVfvozcsKzQ4")
    assert bad is None


def test_parse_solana_address() -> None:
    option = get_crypto_withdraw_option("sol")
    assert option is not None
    addr = "5rGrB6KZwooCchMUewqSKFSueSC4mReEiAkayZRXdRzY"
    dest, err = parse_crypto_withdraw_destination(option, addr)
    assert err == ""
    assert dest == addr


def test_build_details_json_includes_network_and_fee() -> None:
    option = get_crypto_withdraw_option("tron")
    assert option is not None
    details = build_crypto_withdraw_details_json(option, "TNVUjeJLFdexp6P3V5TQFsmVfvozcsKzQ4")
    assert details["crypto_network_label"] == "Tron (TRX - TRC20)"
    assert details["network_fee_usdt"] == "1.3"
    assert details["destination"].startswith("TNV")
    assert is_crypto_withdraw_details(details)


def test_balance_dh_to_usdt() -> None:
    assert balance_dh_to_usdt(100, USDT_TO_DH_RATE) == 10.0
    assert format_usdt(10) == "10 USDT"


def test_min_crypto_withdraw_dh_matches_deposit() -> None:
    assert MIN_CRYPTO_WITHDRAW_DH == 100.0


def test_crypto_network_fee_warning_when_too_small() -> None:
    warning = crypto_network_fee_warning(10, "1.3")
    assert warning is not None
    assert "تنبيه" in warning


def test_format_crypto_withdraw_details_html() -> None:
    option = get_crypto_withdraw_option("bsc")
    assert option is not None
    details = build_crypto_withdraw_details_json(
        option, "0xfee61cdf284269bd5733befcac8bb8b85a4e8d99"
    )
    html = format_crypto_withdraw_details_html(details)
    assert "شبكة USDT" in html
    assert "تأكد" in html


def test_admin_lines_include_usdt_estimate_for_crypto() -> None:
    option = get_crypto_withdraw_option("bsc")
    assert option is not None
    details = build_crypto_withdraw_details_json(
        option, "0xfee61cdf284269bd5733befcac8bb8b85a4e8d99"
    )
    lines = format_withdraw_details_admin_lines(details, amount_dh=100.0)
    joined = "\n".join(lines)
    assert "USDT" in joined


def test_crypto_step_label() -> None:
    assert "2 من 3" in format_crypto_step_label(2, "عنوان المحفظة")
