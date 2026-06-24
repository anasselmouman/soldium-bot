# -*- coding: utf-8 -*-
"""Schedule and process automatic Telegram message deletions (shared DB)."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from database import get_connection

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 5.0
BATCH_SIZE = 50


def _utc_delete_at_str(seconds_from_now: int) -> str:
    dt = datetime.now(timezone.utc) + timedelta(seconds=seconds_from_now)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def schedule_message_deletion(chat_id: int, message_id: int, seconds: int) -> None:
    delete_at = _utc_delete_at_str(seconds)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO scheduled_message_deletions (chat_id, message_id, delete_at)
            VALUES (?, ?, ?)
            """,
            (chat_id, message_id, delete_at),
        )
        connection.commit()


def _fetch_due_deletions(limit: int) -> list[tuple[int, int, int]]:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, chat_id, message_id
            FROM scheduled_message_deletions
            WHERE delete_at <= ?
            ORDER BY delete_at ASC
            LIMIT ?
            """,
            (now, limit),
        ).fetchall()
    return [(int(row["id"]), int(row["chat_id"]), int(row["message_id"])) for row in rows]


def _remove_deletion(row_id: int) -> None:
    with get_connection() as connection:
        connection.execute(
            "DELETE FROM scheduled_message_deletions WHERE id = ?",
            (row_id,),
        )
        connection.commit()


async def process_due_deletions_once(bot: Bot) -> int:
    due = _fetch_due_deletions(BATCH_SIZE)
    processed = 0
    for row_id, chat_id, message_id in due:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except TelegramBadRequest:
            pass
        _remove_deletion(row_id)
        processed += 1
    return processed


async def run_deletion_worker(bot: Bot) -> None:
    logger.info("Message deletion worker started")
    while True:
        try:
            count = await process_due_deletions_once(bot)
            if count:
                logger.info("Auto-deleted %s scheduled message(s)", count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Message deletion worker error: %s", exc)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
