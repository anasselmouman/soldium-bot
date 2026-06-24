"""Tests for provider cost calculation."""
from __future__ import annotations

import os

os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("SMM_KEY_DEFAULT", "k")
os.environ.setdefault("ADMIN_ID", "1")

from utils.order_economics import compute_provider_cost_dh, provider_cost_dh_from_service


def test_standard_service_cost_per_1000() -> None:
    # 1000 qty, $1/1000, x14 = 14 DH
    assert compute_provider_cost_dh(1000, provider_price_usd=1.0) == 14.0


def test_per_unit_cost() -> None:
    assert compute_provider_cost_dh(3, provider_price_usd=2.0, price_per_unit=True) == 84.0


def test_from_service_dict() -> None:
    service = {
        "provider_rate_usd": 0.5,
        "price": 10.0,
        "price_per_unit": False,
    }
    assert provider_cost_dh_from_service(service, 2000) == 14.0
