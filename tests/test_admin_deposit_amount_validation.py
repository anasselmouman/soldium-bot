"""Admin deposit credit amount validation — no method-specific minimum."""

from __future__ import annotations

from pathlib import Path

import pytest

from config import MAX_SINGLE_DEPOSIT_DH
from handlers.payment import _parse_admin_amount, _validate_admin_deposit_amount
from utils.money import to_float
from utils.payment_banks import CRYPTO_LEDGER_NAME, PAYPAL_LEDGER_NAME


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("0.50", 0.5),
        ("1", 1.0),
        ("1.25", 1.25),
        ("4.99", 4.99),
        ("5", 5.0),
        ("7.25", 7.25),
        ("50", 50.0),
        ("100", 100.0),
        ("0,50", 0.5),
    ],
)
def test_parse_admin_amount_accepts_positive(raw: str, expected: float) -> None:
    assert _parse_admin_amount(raw) == expected


@pytest.mark.parametrize("raw", ["0", "0.0", "-1", "-0.5", "abc", "", "  ", "not-a-number"])
def test_parse_admin_amount_rejects_invalid(raw: str) -> None:
    assert _parse_admin_amount(raw) is None


@pytest.mark.parametrize(
    "method,amount",
    [
        ("CashPlus", 0.50),
        ("CashPlus", 1.0),
        ("CashPlus", 4.99),
        ("CashPlus", 5.0),
        ("CIH", 2.50),
        ("Wafacash", 1.0),
        (CRYPTO_LEDGER_NAME, 50.0),
        (CRYPTO_LEDGER_NAME, 75.0),
        (CRYPTO_LEDGER_NAME, 99.0),
        (CRYPTO_LEDGER_NAME, 100.0),
        (PAYPAL_LEDGER_NAME, 25.0),
        (PAYPAL_LEDGER_NAME, 49.99),
        (PAYPAL_LEDGER_NAME, 50.0),
    ],
)
def test_validate_admin_deposit_amount_accepts_below_old_mins(
    method: str, amount: float
) -> None:
    assert _validate_admin_deposit_amount(method, amount) is None


def test_validate_admin_accepts_exact_maximum() -> None:
    max_dh = to_float(MAX_SINGLE_DEPOSIT_DH)
    assert _validate_admin_deposit_amount("CashPlus", max_dh) is None


def test_validate_admin_rejects_above_maximum() -> None:
    max_dh = to_float(MAX_SINGLE_DEPOSIT_DH)
    err = _validate_admin_deposit_amount("CashPlus", max_dh + 0.01)
    assert err is not None
    assert "الحد الأقصى" in err


def test_small_admin_credit_finalizes_balance(tmp_path: Path) -> None:
    """End-to-end: tiny Admin credit still uses unchanged finalize path."""
    import database as db

    db.DB_PATH = tmp_path / "test_admin_small_credit.db"
    db.init_db()
    uid = 91001
    db.add_user(uid)
    dep_id = db.create_deposit(uid, 0.0, "CashPlus", "proof_small_0_50")
    assert dep_id is not None

    amount = 0.50
    assert _validate_admin_deposit_amount("CashPlus", amount) is None
    assert db.finalize_approved_deposit(dep_id, uid, amount, "CashPlus") is True
    user = db.get_user(uid)
    assert user is not None
    assert float(user["balance"]) == 0.50
    # duplicate approval still blocked
    assert db.finalize_approved_deposit(dep_id, uid, amount, "CashPlus") is False
    assert float(db.get_user(uid)["balance"]) == 0.50
