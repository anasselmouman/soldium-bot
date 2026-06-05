# -*- coding: utf-8 -*-
"""Post-entry delivery — timed announcements and smart notifications after main UI."""

from __future__ import annotations

import logging

from aiogram import Bot

from utils.smart_notifications import on_user_activity
from utils.timed_announcements import deliver_timed_announcements_on_entry

logger = logging.getLogger(__name__)


async def deliver_post_entry_messages(bot: Bot, user_id: int) -> int:
    """
    Run after welcome + main home are shown.
    Order: timed announcements first, then pending smart-notification timers.
    """
    delivered = 0
    try:
        delivered = await deliver_timed_announcements_on_entry(bot, user_id)
        if delivered:
            logger.info(
                "Delivered %s timed announcement(s) after entry UI for user_id=%s",
                delivered,
                user_id,
            )
    except Exception:
        logger.exception(
            "Failed to deliver timed announcements after entry UI user_id=%s",
            user_id,
        )

    try:
        await on_user_activity(bot, user_id)
    except Exception:
        logger.exception(
            "Failed to schedule smart notifications after entry UI user_id=%s",
            user_id,
        )

    return delivered
