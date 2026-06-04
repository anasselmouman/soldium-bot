# -*- coding: utf-8 -*-
"""
تناغم واجهة FSM: حذف آخر رسالة طلب إدخال نصي عند التنقل عبر الأزرار.
يُستدعى من handlers (مسار الطلب، الشحن، القائمة)؛ يُذكر في main.py ضمن نقطة التشغيل.
"""
from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

LAST_PROMPT_KEY = "last_prompt_id"

try:
    from utils.living_ui import LIVING_UI_MESSAGE_KEY
except ImportError:
    LIVING_UI_MESSAGE_KEY = "living_ui_message_id"


async def clear_last_prompt(message: Message, state: FSMContext, *, bot: Bot | None = None) -> None:
    """
    يحذف رسالة البرومبت المحفوظة في الحالة (إن وُجدت) ويمسح المعرّف من الـ FSM.
    لا يحذف الرسالة الحالية إذا كان المستخدم يضغط من لوحة مفاتيحها (نفس message_id).
    """
    data = await state.get_data()
    mid = data.get(LAST_PROMPT_KEY)
    user_id = message.from_user.id if message.from_user else None
    if not isinstance(mid, int):
        await state.update_data(last_prompt_id=None)
        return
    if user_id is not None:
        from utils.living_ui import is_living_ui_message_id

        if is_living_ui_message_id(user_id, mid):
            await state.update_data(last_prompt_id=None)
            return
    living = data.get(LIVING_UI_MESSAGE_KEY)
    if isinstance(living, int) and mid == living:
        await state.update_data(last_prompt_id=None)
        return
    if message.message_id is not None and mid == message.message_id:
        await state.update_data(last_prompt_id=None)
        return
    b = bot or message.bot
    chat = message.chat
    if not b or not chat:
        await state.update_data(last_prompt_id=None)
        return
    from utils.living_ui import delete_chat_message

    await delete_chat_message(b, chat.id, mid)
    await state.update_data(last_prompt_id=None)
