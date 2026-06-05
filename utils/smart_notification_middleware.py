# -*- coding: utf-8 -*-
"""مراقبة نشاط المستخدم لتشغيل مؤقت حذف الإشعارات المعلقة."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject

from utils.smart_notifications import on_user_activity


def _is_start_command(message: Message) -> bool:
    text = (message.text or "").strip()
    if not text.startswith("/"):
        return False
    command = text.split(maxsplit=1)[0]
    return command == "/start" or command.startswith("/start@")


class SmartNotificationActivityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot: Bot | None = data.get("bot")
        user_id: int | None = None
        defer_activity = False

        if isinstance(event, Message) and event.from_user and not event.from_user.is_bot:
            user_id = event.from_user.id
            defer_activity = _is_start_command(event)
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        result = await handler(event, data)

        # /start finishes with welcome + home first; entry_delivery handles activity.
        if user_id is not None and bot is not None and not defer_activity:
            await on_user_activity(bot, user_id)

        return result
