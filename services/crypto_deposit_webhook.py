# -*- coding: utf-8 -*-
"""إشعار خدمة خارجية عند تأكيد المستخدم لدفع USDT (اختياري)."""

from __future__ import annotations

import logging
import time

from aiohttp import ClientSession, ClientTimeout

from config import CRYPTO_DEPOSIT_WEBHOOK_URL

logger = logging.getLogger(__name__)


async def notify_crypto_deposit_pending(
    *,
    user_id: int,
    network_key: str,
    network_label: str,
    username: str | None = None,
) -> bool:
    """
    يرسل طلب POST إلى CRYPTO_DEPOSIT_WEBHOOK_URL إن وُجد.
    يُرجع True عند نجاح الإرسال (2xx)، وFalse إن لم يُضبط الرابط أو فشل الطلب.
    """
    url = CRYPTO_DEPOSIT_WEBHOOK_URL
    if not url:
        return False
    payload = {
        "event": "crypto_deposit_pending",
        "user_id": user_id,
        "network_key": network_key,
        "network_label": network_label,
        "username": username or "",
        "timestamp": int(time.time()),
    }
    try:
        timeout = ClientTimeout(total=8)
        async with ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as response:
                if 200 <= response.status < 300:
                    return True
                body = await response.text()
                logger.warning(
                    "crypto deposit webhook HTTP %s: %s",
                    response.status,
                    body[:500],
                )
    except Exception as exc:
        logger.warning("crypto deposit webhook failed: %s", exc)
    return False
