# -*- coding: utf-8 -*-
"""مراقبة نشاط المستخدم لتشغيل مؤقت حذف الإشعارات المعلقة."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.types import CallbackQuery, Message, TelegramObject

from utils.smart_notifications import on_user_activity


class SmartNotificationActivityMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        bot: Bot | None = data.get("bot")
        user_id: int | None = None

        if isinstance(event, Message) and event.from_user and not event.from_user.is_bot:
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery) and event.from_user:
            user_id = event.from_user.id

        if user_id is not None and bot is not None:
            await on_user_activity(bot, user_id)

        return await handler(event, data)
