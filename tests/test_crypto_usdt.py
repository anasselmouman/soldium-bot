# -*- coding: utf-8 -*-
"""اختبارات رسالة إيداع USDT الموحدة."""

from __future__ import annotations

from utils.crypto_usdt import (
    TELEGRAM_PHOTO_CAPTION_MAX,
    USDT_NETWORKS,
    format_crypto_deposit_html,
)


def test_five_networks_no_aptos() -> None:
    assert len(USDT_NETWORKS) == 5
    assert all(n.key != "apt" for n in USDT_NETWORKS)


def test_single_message_has_pay_id_and_all_wallets() -> None:
    html = format_crypto_deposit_html(binance_pay_id="784548487")
    assert "<code>784548487</code>" in html
    assert "1️⃣ Binance Pay" in html
    assert "رسوم: 0 USDT" in html
    assert html.count("الحد الأدنى") == 1
    assert "بعد التحويل" in html
    assert "سعر الصرف" in html
    for network in USDT_NETWORKS:
        assert f"<code>{network.wallet_address}</code>" in html
        assert network.network_fee_usdt in html


def test_solana_case_sensitive_note() -> None:
    html = format_crypto_deposit_html(binance_pay_id="1")
    assert "سولانا" in html


def test_deposit_html_fits_photo_caption_limit() -> None:
    html = format_crypto_deposit_html(binance_pay_id="784548487")
    assert len(html) <= TELEGRAM_PHOTO_CAPTION_MAX


def test_crypto_deposit_living_text_fits_photo_caption_with_breadcrumb() -> None:
    from handlers.payment import _format_payment_detail_text
    from utils.payment_banks import PAYMENT_METHODS

    crypto = next(m for m in PAYMENT_METHODS if m.key == "crypto")
    text = _format_payment_detail_text(crypto)
    assert len(text) <= TELEGRAM_PHOTO_CAPTION_MAX
    assert text.count("<code>") == 6
