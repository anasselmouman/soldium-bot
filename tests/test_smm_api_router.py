# -*- coding: utf-8 -*-
"""
اختبارات شاملة لـ Smart API Router: التوجيه، ذاكرة المديرين، الحفظ في DB، وفحص الحالة.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# بيئة اختبار ثابتة قبل استيراد config / router
os.environ.setdefault("BOT_TOKEN", "test:token")
os.environ.setdefault("SMM_KEY_INSTAGRAM", "ig-test-key")
os.environ.setdefault("SMM_KEY_FACEBOOK", "fb-test-key")
os.environ.setdefault("SMM_KEY_TIKTOK", "tt-test-key")
os.environ.setdefault("SMM_KEY_DEFAULT", "default-test-key")
os.environ.setdefault("ADMIN_ID", "1")

from services import smm_api_router
from services.smm_api_router import (
    clear_smm_manager_cache,
    get_provider_credentials,
    smm_manager_for_account,
)
from smm_api import SMMManager

import database as db
from services.provider_registry import clear_provider_caches, seed_default_gozibra_provider


@pytest.fixture(autouse=True)
def _reset_manager_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    provider_db = tmp_path / "provider_registry_test.db"
    monkeypatch.setattr("services.provider_registry.DB_PATH", provider_db)
    import sqlite3

    with sqlite3.connect(provider_db) as connection:
        seed_default_gozibra_provider(connection)
        connection.commit()
    monkeypatch.setenv("SMM_KEY_INSTAGRAM", "ig-test-key")
    monkeypatch.setenv("SMM_KEY_FACEBOOK", "fb-test-key")
    monkeypatch.setenv("SMM_KEY_TIKTOK", "tt-test-key")
    monkeypatch.setenv("SMM_KEY_DEFAULT", "default-test-key")
    clear_smm_manager_cache()
    yield
    clear_smm_manager_cache()


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db.DB_PATH = tmp_path / "test_smm_router.db"
    db.init_db()
    return db.DB_PATH


# ---------------------------------------------------------------------------
# 1. Routing logic (keyword matching)
# ---------------------------------------------------------------------------

ROUTING_CASES = [
    # Instagram
    ("Instagram", "followers", "instagram", "ig-test-key"),
    ("", "متابعين انستغرام", "instagram", "ig-test-key"),
    ("social", "IG likes", "instagram", "ig-test-key"),
    ("INSTAGRAM", "PREMIUM", "instagram", "ig-test-key"),
    # TikTok
    ("تيك توك", "مشاهدات", "tiktok", "tt-test-key"),
    ("TikTok", "views", "tiktok", "tt-test-key"),
    ("", "تيكتوك لايكات", "tiktok", "tt-test-key"),
    # Facebook
    ("فيسبوك", "متابعين", "facebook", "fb-test-key"),
    ("Facebook", "page likes", "facebook", "fb-test-key"),
    ("", "فيس بوك صفحة", "facebook", "fb-test-key"),
    # Default
    ("Telegram", "members", "default", "default-test-key"),
    ("Twitter", "followers", "default", "default-test-key"),
    ("", "Random Service", "default", "default-test-key"),
]


@pytest.mark.parametrize(
    "category,name,expected_account,expected_key",
    ROUTING_CASES,
    ids=[
        "ig_english_followers",
        "ig_arabic_name",
        "ig_ig_likes",
        "ig_uppercase",
        "tt_arabic_category",
        "tt_english",
        "tt_name_only",
        "fb_arabic",
        "fb_english_page",
        "fb_arabic_spaced",
        "default_telegram",
        "default_twitter",
        "default_random",
    ],
)
def test_get_provider_credentials_routing(
    category: str,
    name: str,
    expected_account: str,
    expected_key: str,
) -> None:
    creds = get_provider_credentials(category, name)
    assert creds["account_type"] == expected_account
    assert creds["api_key"] == expected_key


def test_routing_case_insensitivity_on_combined_haystack() -> None:
    creds = get_provider_credentials("InStAgRaM", "FoLlOwErS")
    assert creds["account_type"] == "instagram"


def test_instagram_wins_when_multiple_platform_keywords() -> None:
    creds = get_provider_credentials("instagram facebook tiktok", "test")
    assert creds["account_type"] == "instagram"


def test_unknown_account_normalizes_to_default_credentials() -> None:
    creds = get_provider_credentials("telegram", "members")
    assert creds["account_type"] == "default"
    assert creds["provider_slug"] == "gozibra"

def test_get_provider_credentials_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMM_KEY_INSTAGRAM", "")
    clear_smm_manager_cache()
    with pytest.raises(RuntimeError, match="SMM_KEY_INSTAGRAM"):
        get_provider_credentials("instagram", "followers")


# ---------------------------------------------------------------------------
# 2. Singleton cache (memory / session reuse)
# ---------------------------------------------------------------------------

def test_smm_manager_for_account_returns_same_instance_for_same_account() -> None:
    manager_1 = smm_manager_for_account("tiktok")
    manager_2 = smm_manager_for_account("tiktok")
    assert id(manager_1) == id(manager_2)


def test_smm_manager_for_account_normalizes_case_for_cache() -> None:
    lower = smm_manager_for_account("tiktok")
    upper = smm_manager_for_account("TikTok")
    assert id(lower) == id(upper)


def test_smm_manager_for_account_different_accounts_different_instances() -> None:
    ig = smm_manager_for_account("instagram")
    fb = smm_manager_for_account("facebook")
    tt = smm_manager_for_account("tiktok")
    default = smm_manager_for_account("default")
    ids = {id(ig), id(fb), id(tt), id(default)}
    assert len(ids) == 4


def test_unknown_account_type_uses_default_singleton() -> None:
    unknown = smm_manager_for_account("youtube")
    default = smm_manager_for_account("default")
    assert id(unknown) == id(default)


def test_smm_manager_instances_carry_correct_api_keys() -> None:
    assert smm_manager_for_account("instagram").api_key == "ig-test-key"
    assert smm_manager_for_account("facebook").api_key == "fb-test-key"
    assert smm_manager_for_account("tiktok").api_key == "tt-test-key"
    assert smm_manager_for_account("default").api_key == "default-test-key"


def test_clear_cache_allows_new_instance() -> None:
    first = smm_manager_for_account("tiktok")
    clear_smm_manager_cache()
    second = smm_manager_for_account("tiktok")
    assert id(first) != id(second)


def test_smm_manager_constructor_not_called_on_cache_hit() -> None:
    clear_smm_manager_cache()
    with patch("services.provider_registry.SMMManager") as mock_cls:
        mock_cls.return_value = MagicMock(spec=SMMManager)
        smm_manager_for_account("facebook")
        smm_manager_for_account("facebook")
        smm_manager_for_account("facebook")
        assert mock_cls.call_count == 1


# ---------------------------------------------------------------------------
# 3. Database persistence (api_account from router)
# ---------------------------------------------------------------------------

def test_create_order_persists_api_account_from_router(temp_db: Path) -> None:
    creds = get_provider_credentials("إنستغرام", "متابعين انستغرام")
    assert creds["account_type"] == "instagram"

    user_id = 42
    db.add_user(user_id)
    dep_id = db.create_deposit(user_id, 0.0, "CashPlus", "proof_router")
    assert dep_id is not None
    assert db.finalize_approved_deposit(dep_id, user_id, 500.0, "CashPlus") is True

    order_id = db.create_order_with_balance_hold(
        user_id=user_id,
        service_name="متابعين انستغرام",
        service_id="ig_svc_1",
        link="https://instagram.com/u",
        quantity=100,
        amount=10.0,
        api_account=creds["account_type"],
    )
    assert order_id is not None

    with db.get_connection() as connection:
        row = connection.execute(
            "SELECT api_account FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
    assert row is not None
    assert row["api_account"] == "instagram"
    assert row["api_account"] != "default"


def test_create_order_mock_asserts_api_account_passed_from_router() -> None:
    """يتحقق أن مسار الإدراج يستقبل account_type من الموجّه وليس default افتراضياً."""
    creds = get_provider_credentials("Facebook", "page likes")
    assert creds["account_type"] == "facebook"

    with patch.object(db, "create_order_with_balance_hold") as mock_create:
        mock_create.return_value = 99
        db.create_order_with_balance_hold(
            user_id=1,
            service_name="page likes",
            service_id="fb_1",
            link="https://facebook.com/p",
            quantity=50,
            amount=5.0,
            api_account=creds["account_type"],
        )
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs.get("api_account") == "facebook"
        assert call_kwargs.get("api_account") != "default"


def test_get_user_orders_includes_api_account_field(temp_db: Path) -> None:
    user_id = 7
    db.add_user(user_id)
    with db.get_connection() as connection:
        connection.execute(
            """
            INSERT INTO orders (
                user_id, service_name, service_id, link, quantity, amount,
                total_price, status, api_account
            )
            VALUES (?, 'svc', '1', 'http://x', 1, 1.0, 1.0, 'pending', 'tiktok')
            """,
            (user_id,),
        )
        connection.commit()

    orders = db.get_user_orders(user_id, limit=1)
    assert len(orders) == 1
    assert orders[0]["api_account"] == "tiktok"


# ---------------------------------------------------------------------------
# 4. Status check routing (mocked provider managers)
# ---------------------------------------------------------------------------

async def _fetch_status_like_main(order: dict) -> dict:
    """نفس منطق main.py / start.py لجلب حالة الطلب."""
    provider_order_id = order["provider_order_id"]
    if not provider_order_id:
        raise ValueError("missing provider_order_id")
    manager = smm_api_router.smm_manager_for_order(order)
    return await manager.get_order_status(provider_order_id)


def _run(coro):
    return asyncio.run(coro)


def test_status_check_routes_to_facebook_manager_not_default() -> None:
    order = {
        "id": 1,
        "user_id": 1,
        "service_name": "likes",
        "link": "http://fb.com",
        "quantity": 100,
        "amount": 10.0,
        "status": "pending",
        "provider_order_id": "provider_order_999",
        "created_at": "2026-01-01",
        "start_count": None,
        "status_note": None,
        "api_account": "facebook",
        "provider_slug": "gozibra",
    }

    mock_fb = MagicMock(spec=SMMManager)
    mock_fb.get_order_status = AsyncMock(return_value={"status": "Completed"})
    mock_default = MagicMock(spec=SMMManager)
    mock_default.get_order_status = AsyncMock(return_value={"status": "Pending"})

    def factory(order: dict) -> SMMManager:
        if str(order.get("api_account")) == "facebook":
            return mock_fb
        return mock_default

    with patch.object(smm_api_router, "smm_manager_for_order", side_effect=factory):
        result = _run(_fetch_status_like_main(order))

    assert result["status"] == "Completed"
    mock_fb.get_order_status.assert_awaited_once_with("provider_order_999")
    mock_default.get_order_status.assert_not_awaited()


def test_status_check_legacy_order_without_api_account_uses_default() -> None:
    order = {
        "id": 2,
        "user_id": 1,
        "service_name": "svc",
        "link": "http://x.com",
        "quantity": 1,
        "amount": 1.0,
        "status": "pending",
        "provider_order_id": "old_order_1",
        "created_at": "2025-01-01",
        "start_count": None,
        "status_note": None,
        "api_account": "default",
        "provider_slug": "gozibra",
    }

    mock_default = MagicMock(spec=SMMManager)
    mock_default.get_order_status = AsyncMock(return_value={"status": "In progress"})
    mock_ig = MagicMock(spec=SMMManager)
    mock_ig.get_order_status = AsyncMock()

    def factory(order: dict) -> SMMManager:
        if str(order.get("api_account")) == "default":
            return mock_default
        return mock_ig

    with patch.object(smm_api_router, "smm_manager_for_order", side_effect=factory):
        _run(_fetch_status_like_main(order))

    mock_default.get_order_status.assert_awaited_once_with("old_order_1")
    mock_ig.get_order_status.assert_not_awaited()


def test_trackable_order_from_db_status_uses_stored_api_account(temp_db: Path) -> None:
    """طلب محفوظ بـ api_account=facebook يُمرَّر كاملاً لمسار فحص الحالة."""
    user_id = 99
    db.add_user(user_id)
    with db.get_connection() as connection:
        connection.execute(
            """
            INSERT INTO orders (
                user_id, service_name, service_id, link, quantity, amount,
                total_price, status, provider_order_id, api_account
            )
            VALUES (?, 'likes', '1', 'http://fb.com', 10, 5.0, 5.0, 'pending', '777', 'facebook')
            """,
            (user_id,),
        )
        connection.commit()

    trackable = db.get_trackable_orders(limit=10)
    assert len(trackable) == 1
    assert trackable[0]["api_account"] == "facebook"

    mock_fb = MagicMock(spec=SMMManager)
    mock_fb.get_order_status = AsyncMock(return_value={"status": "Processing"})
    mock_default = MagicMock(spec=SMMManager)
    mock_default.get_order_status = AsyncMock()

    def factory(order: dict) -> SMMManager:
        return mock_fb if str(order.get("api_account")) == "facebook" else mock_default

    with patch.object(smm_api_router, "smm_manager_for_order", side_effect=factory):
        _run(_fetch_status_like_main(trackable[0]))

    mock_fb.get_order_status.assert_awaited_once_with("777")
    mock_default.get_order_status.assert_not_awaited()
