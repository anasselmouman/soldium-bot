from __future__ import annotations

import asyncio
from types import SimpleNamespace

import handlers.admin as admin


class _FakeMessage:
    def __init__(self) -> None:
        self.edited_texts: list[str] = []

    async def edit_text(self, text: str, **kwargs) -> None:
        self.edited_texts.append(text)


class _FakeCallback:
    def __init__(self, data: str, user_id: int) -> None:
        self.data = data
        self.message = _FakeMessage()
        self.from_user = SimpleNamespace(id=user_id)
        self.answers: list[tuple[str | None, bool]] = []

    async def answer(self, text: str | None = None, show_alert: bool = False) -> None:
        self.answers.append((text, show_alert))


def test_admin_approve_survives_notification_failure(monkeypatch) -> None:
    admin_id = 999001
    callback = _FakeCallback("admin:withdraw:approve:44", admin_id)

    monkeypatch.setattr(admin, "ADMIN_ID", admin_id)
    monkeypatch.setattr(admin, "get_withdrawal", lambda _: {"status": "pending"})
    monkeypatch.setattr(
        admin,
        "approve_withdrawal_by_admin",
        lambda _: {
            "user_id": 777,
            "amount": 125.0,
            "method": "CashPlus",
            "withdrawal_type": "normal",
        },
    )

    async def _raise_notification(*args, **kwargs):
        raise RuntimeError("network down")

    notices: list[str | None] = []

    async def _capture_queue(message, *, notice=None):
        notices.append(notice)

    monkeypatch.setattr(admin, "send_smart_notification", _raise_notification)
    monkeypatch.setattr(admin, "_render_admin_withdrawal_queue", _capture_queue)

    asyncio.run(admin.admin_withdraw_approve_handler(callback, bot=object()))

    assert callback.answers[-1] == ("تم اعتماد السحب", False)
    assert notices, "Queue renderer should be called"
    assert "تعذر إرسال إشعار للمستخدم" in (notices[-1] or "")


def test_admin_reject_survives_notification_failure(monkeypatch) -> None:
    admin_id = 999002
    callback = _FakeCallback("admin:withdraw:reject:55", admin_id)

    monkeypatch.setattr(admin, "ADMIN_ID", admin_id)
    monkeypatch.setattr(admin, "get_withdrawal", lambda _: {"status": "pending"})
    monkeypatch.setattr(
        admin,
        "reject_withdrawal_by_admin",
        lambda _: {
            "user_id": 778,
            "amount": 99.0,
            "method": "CIH",
            "withdrawal_type": "referral",
        },
    )

    async def _raise_notification(*args, **kwargs):
        raise RuntimeError("telegram timeout")

    notices: list[str | None] = []

    async def _capture_queue(message, *, notice=None):
        notices.append(notice)

    monkeypatch.setattr(admin, "send_smart_notification", _raise_notification)
    monkeypatch.setattr(admin, "_render_admin_withdrawal_queue", _capture_queue)

    asyncio.run(admin.admin_withdraw_reject_handler(callback, bot=object()))

    assert callback.answers[-1] == ("تم رفض الطلب وإرجاع الرصيد", True)
    assert notices, "Queue renderer should be called"
    assert "تعذر إرسال إشعار للمستخدم" in (notices[-1] or "")
