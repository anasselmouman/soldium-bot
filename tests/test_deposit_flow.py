"""اختبارات منطق الشحن والاعتماد الذرّي."""

from __future__ import annotations

from pathlib import Path

import database as db
from utils.money import recharge_face_value_to_credit
from utils.recharge_telecom import RECHARGE_FACE_VALUES_DH


def _use_temp_db(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "test_users.db"
    db.init_db()


def test_finalize_approved_deposit_is_atomic(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 9001
    db.add_user(uid)
    dep_id = db.create_deposit(uid, 0.0, "CashPlus", "file_abc")
    assert dep_id is not None

    assert db.finalize_approved_deposit(dep_id, uid, 50.0, "CashPlus") is True
    user = db.get_user(uid)
    assert user is not None
    assert float(user["balance"]) == 50.0

    assert db.finalize_approved_deposit(dep_id, uid, 50.0, "CashPlus") is False
    user = db.get_user(uid)
    assert float(user["balance"]) == 50.0


def test_recharge_code_duplicate_blocked(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    code = "12345678901234"
    assert db.recharge_code_in_use(code) is False

    dep1 = db.create_deposit(1, 10.0, "Orange", code)
    assert dep1 is not None
    assert db.recharge_code_in_use(code) is True
    assert db.recharge_code_in_use(code, exclude_deposit_id=dep1) is False

    dep_dup = db.create_deposit(2, 10.0, "Orange", code)
    assert dep_dup is None

    db.finalize_approved_deposit(dep1, 1, 7.0, "Orange")
    assert db.recharge_code_in_use(code) is True

    dep2 = db.create_deposit(2, 10.0, "Orange", code)
    assert dep2 is None


def test_pending_deposit_limit_count(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    uid = 42
    db.add_user(uid)
    assert db.count_user_pending_deposits(uid) == 0
    assert db.create_deposit(uid, 0.0, "CIH", "proof1") is not None
    assert db.create_deposit(uid, 0.0, "CIH", "proof2") is not None
    assert db.count_user_pending_deposits(uid) == 2


def test_duplicate_receipt_proof_blocked_while_pending(tmp_path: Path) -> None:
    _use_temp_db(tmp_path)
    db.add_user(100)
    proof = "AgACAgIAAxkBAAI_same_receipt"
    first = db.create_deposit(100, 0.0, "CIH", proof)
    second = db.create_deposit(100, 0.0, "CIH", proof)
    assert first is not None
    assert second is None


def test_recharge_face_credit_ratio() -> None:
    assert recharge_face_value_to_credit(10, 0.70) == 7.0
    assert int(10) in RECHARGE_FACE_VALUES_DH
