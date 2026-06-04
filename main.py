"""
نقطة تشغيل بوت SOLDIUM. رسائل تحديث الطلب للمستخدم تعرض «رقم الطلب» (مرجع التنفيذ من الواجهة الخارجية) فقط.
تناغم رسائل طلب الإدخال: utils/fsm_prompt_cleanup.py — يُستدعى من handlers.
"""
import asyncio
import json
import logging
import os
import sys
import time
from collections import deque
from html import escape
from pathlib import Path

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import CallbackQuery, Update
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError
from aiogram.fsm.storage.memory import MemoryStorage

# رصيد المستخدمين والطلبات بالدرهم المغربي (CURRENCY_DISPLAY في config.py)
from config import ADMIN_ID, BOT_TOKEN
from database import get_trackable_orders, init_db
from services.order_provider_sync import apply_provider_status_to_order
from services.referral import flush_pending_referral_level_upgrade_notifications
from utils.order_status_ar import normalize_order_status_key
from handlers.admin import router as admin_router
from handlers.orders import router as orders_router
from handlers.crypto_withdraw import router as crypto_withdraw_router
from handlers.payment import router as payment_router
from handlers.referrals import router as referrals_router
from handlers.start import router as start_router
from utils.smart_notification_middleware import SmartNotificationActivityMiddleware
from utils.smart_notifications import router as smart_notifications_router, send_smart_notification
from utils.home_screen import ensure_welcome_upload_jpeg
from services.smm_api_router import smm_manager_for_account
from smm_api import ProviderAuthError, SMMManager
from utils.single_instance import acquire_bot_instance_lock

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)
smm_manager = SMMManager()
MAX_START_RETRIES = 10
BASE_RETRY_DELAY_SECONDS = 5
TRACKING_INTERVAL_SECONDS = 60
_agent_debug_log_write_warned = False
_DEBUG68_LOG_PATH = Path(__file__).resolve().with_name("debug-68c0f1.log")


def _debug68_log(run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # region agent log
    try:
        payload = {
            "sessionId": "68c0f1",
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with _DEBUG68_LOG_PATH.open("a", encoding="utf-8") as _f:
            _f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass
    # endregion


def _boot_witness_json(stage: str, main_path: Path, mtime_ns: int, **extra: object) -> None:
    # region agent log
    payload = {
        "sessionId": "8857df",
        "hypothesisId": "H-boot",
        "location": "main.py:_boot_witness_json",
        "message": stage,
        "data": {
            "main_path": str(main_path),
            "mtime_ns": mtime_ns,
            "pid": os.getpid(),
            **extra,
        },
        "timestamp": int(time.time() * 1000),
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    for target in (
        main_path.with_name("soldium_last_boot.json"),
        Path(os.environ.get("TEMP") or ".") / "soldium_last_boot.json",
    ):
        try:
            target.write_text(text, encoding="utf-8")
        except OSError:
            continue
    # endregion


class _TelegramConflictStormHandler(logging.Handler):
    """If getUpdates conflicts flood the log, exit so the user restarts a single poller or rotates the token."""

    def __init__(self) -> None:
        super().__init__(level=logging.ERROR)
        # ~1 conflict / 10s from aiogram backoff → need a low threshold in a 2‑minute window
        self._window_s = 120.0
        self._max_in_window = 12
        self._hits: deque[float] = deque()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:
            return
        if "TelegramConflictError" not in msg:
            return
        now = time.monotonic()
        self._hits.append(now)
        while self._hits and self._hits[0] < now - self._window_s:
            self._hits.popleft()
        if len(self._hits) < self._max_in_window:
            return
        self._fatal_exit()

    def _fatal_exit(self) -> None:
        try:
            main_p = Path(__file__).resolve()
            st = main_p.stat()
            _boot_witness_json(
                "telegram_conflict_storm_exit",
                main_p,
                st.st_mtime_ns,
                conflict_error_logs=len(self._hits),
                window_sec=self._window_s,
            )
            _agent_debug_log(
                "H-conflict",
                "main.py:_TelegramConflictStormHandler",
                "storm_exit",
                {
                    "n": len(self._hits),
                    "window_s": self._window_s,
                    "pid": os.getpid(),
                },
            )
        except Exception:
            pass
        print(
            "[SOLDIUM] Too many Telegram getUpdates conflicts in a short time — exiting. "
            "Stop every other bot using this token (other PC, VPS, second terminal) or revoke the token in BotFather.",
            file=sys.stderr,
            flush=True,
        )
        print(
            "[SOLDIUM] توجد تعارضات متكررة مع getUpdates — تم إيقاف العملية. أوقف أي نسخة أخرى أو غيّر التوكن.",
            file=sys.stderr,
            flush=True,
        )
        os._exit(2)


def _install_telegram_conflict_storm_guard() -> None:
    lg = logging.getLogger("aiogram.dispatcher")
    if getattr(lg, "_soldium_conflict_storm_guard", False):
        return
    setattr(lg, "_soldium_conflict_storm_guard", True)
    lg.addHandler(_TelegramConflictStormHandler())
    # Small marker file (not overwritten by later _boot_witness_json) — proves this code ran after restart.
    try:
        Path(__file__).resolve().with_name("soldium_guard_installed.flag").write_text(
            f"pid={os.getpid()}\niso={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    # region agent log
    _agent_debug_log(
        "H-guard",
        "main.py:_install_telegram_conflict_storm_guard",
        "storm_guard_attached",
        {"pid": os.getpid(), "logger": "aiogram.dispatcher"},
    )
    # endregion


def _agent_debug_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # region agent log
    from config import DEBUG_AGENT_LOGS

    if not DEBUG_AGENT_LOGS:
        return
    global _agent_debug_log_write_warned
    try:
        import json
        import time
        from pathlib import Path

        payload = {
            "sessionId": "8857df",
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(Path(__file__).resolve().with_name("debug-8857df.log"), "a", encoding="utf-8") as _f:
            _f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as _dbg_w_err:
        if not _agent_debug_log_write_warned:
            _agent_debug_log_write_warned = True
            logger.warning("Could not write debug-8857df.log (NDJSON): %s", _dbg_w_err)
    # endregion


def _looks_like_dns_or_connectivity_error(exc: BaseException) -> bool:
    cur: BaseException | None = exc
    for _ in range(12):
        if cur is None:
            break
        name = type(cur).__name__
        text = str(cur).lower()
        if "getaddrinfo" in text or "ClientConnectorDNSError" in name or "gaierror" in name:
            return True
        cur = getattr(cur, "__cause__", None)
    return False


async def _track_orders_and_refund(bot: Bot) -> None:
    while True:
        try:
            orders = get_trackable_orders(limit=200)
            for order in orders:
                provider_order_id = order["provider_order_id"]
                if not provider_order_id:
                    continue
                try:
                    account = str(order.get("api_account") or "default")
                    status_data = await smm_manager_for_account(account).get_order_status(
                        provider_order_id
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to fetch provider status for %s: %s",
                        provider_order_id,
                        exc,
                    )
                    continue

                provider_status_lower = normalize_order_status_key(status_data.get("status", ""))
                if not provider_status_lower:
                    continue

                await apply_provider_status_to_order(order, status_data, bot, notify=True)
        except Exception as exc:
            logger.exception("Order tracking loop failed: %s", exc)

        try:
            await flush_pending_referral_level_upgrade_notifications(bot)
        except Exception as exc:
            logger.warning("Referral upgrade notification flush failed: %s", exc)

        await asyncio.sleep(TRACKING_INTERVAL_SECONDS)


async def main() -> None:
    # region agent log
    from pathlib import Path as _Path_main

    _main_fp = _Path_main(__file__).resolve()
    # region agent log
    _debug68_log(
        "pre-fix",
        "H-runtime-fresh",
        "main.py:main:entry",
        "process_started_with_latest_source",
        {"pid": os.getpid(), "main_path": str(_main_fp)},
    )
    # endregion
    _agent_debug_log(
        "H0",
        "main.py:main:entry",
        "main_entered",
        {
            "main_path": str(_main_fp),
            "log_path": str(_main_fp.with_name("debug-8857df.log")),
            "token_ok": bool(BOT_TOKEN and "PASTE" not in BOT_TOKEN),
        },
    )
    # endregion

    if not BOT_TOKEN or "PASTE" in BOT_TOKEN:
        raise ValueError("يرجى ضبط BOT_TOKEN داخل config.py قبل التشغيل.")

    init_db()
    try:
        from services_config import reload_services

        reload_services()
        logger.info("Services catalog reloaded from services.json")
    except Exception as exc:
        logger.warning("Services catalog reload at startup failed: %s", exc)
    ensure_welcome_upload_jpeg()
    # region agent log
    try:
        _boot7 = _main_fp.with_name("debug-7d38a8.log")
        with _boot7.open("a", encoding="utf-8") as _bf:
            _bf.write(
                json.dumps(
                    {
                        "sessionId": "7d38a8",
                        "runId": "post-fix",
                        "hypothesisId": "H0",
                        "location": "main.py:main:boot",
                        "message": "bot_started",
                        "data": {"pid": os.getpid(), "living_ui": True},
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # endregion
    # region agent log
    try:
        _boot_e94964 = _main_fp.with_name("debug-e94964.log")
        with _boot_e94964.open("a", encoding="utf-8") as _bf:
            _bf.write(
                json.dumps(
                    {
                        "sessionId": "e94964",
                        "runId": "post-fix",
                        "hypothesisId": "H-boot",
                        "location": "main.py:main:boot",
                        "message": "withdraw_fix_build_loaded",
                        "data": {"pid": os.getpid()},
                        "timestamp": int(time.time() * 1000),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    except OSError:
        pass
    # endregion
    logger.info("SOLDIUM home UI: bot.edit_message_media v2 active")
    dp = Dispatcher(storage=MemoryStorage())
    dp.update.outer_middleware(SmartNotificationActivityMiddleware())

    # region agent log
    class _DebugAnyUpdateLogMiddleware(BaseMiddleware):
        async def __call__(
            self,
            handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
            event: Update,
            data: dict[str, Any],
        ) -> Any:
            # region agent log
            _debug68_log(
                "pre-fix",
                "H-update-stream",
                "main.py:_DebugAnyUpdateLogMiddleware",
                "update_received",
                {
                    "update_id": getattr(event, "update_id", None),
                    "has_message": bool(getattr(event, "message", None)),
                    "has_callback_query": bool(getattr(event, "callback_query", None)),
                    "has_inline_query": bool(getattr(event, "inline_query", None)),
                },
            )
            # endregion
            return await handler(event, data)

    dp.update.outer_middleware(_DebugAnyUpdateLogMiddleware())

    # region agent log
    class _DebugCallbackLogMiddleware(BaseMiddleware):
        async def __call__(
            self,
            handler: Callable[[CallbackQuery, dict[str, Any]], Awaitable[Any]],
            event: CallbackQuery,
            data: dict[str, Any],
        ) -> Any:
            # region agent log
            _debug68_log(
                "pre-fix",
                "H-callback-stream",
                "main.py:_DebugCallbackLogMiddleware",
                "callback_received",
                {
                    "callback_data": event.data,
                    "chat_id": event.message.chat.id if event.message else None,
                    "message_id": event.message.message_id if event.message else None,
                    "from_user_id": event.from_user.id if event.from_user else None,
                },
            )
            # endregion
            try:
                _cb_log = _main_fp.with_name("debug-7d38a8.log")
                with _cb_log.open("a", encoding="utf-8") as _cf:
                    _cf.write(
                        json.dumps(
                            {
                                "sessionId": "7d38a8",
                                "runId": "post-fix",
                                "hypothesisId": "H-cb",
                                "location": "main.py:callback_middleware",
                                "message": "callback_received",
                                "data": {
                                    "callback_data": event.data,
                                    "message_id": event.message.message_id if event.message else None,
                                    "has_photo": bool(event.message.photo) if event.message else None,
                                },
                                "timestamp": int(time.time() * 1000),
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            except OSError:
                pass
            return await handler(event, data)

    dp.callback_query.middleware(_DebugCallbackLogMiddleware())
    # endregion

    dp.include_router(smart_notifications_router)
    dp.include_router(payment_router)
    dp.include_router(crypto_withdraw_router)
    dp.include_router(start_router)
    dp.include_router(referrals_router)
    dp.include_router(orders_router)
    dp.include_router(admin_router)

    @dp.errors()
    async def global_error_handler(event) -> None:
        logging.exception("Unhandled error: %s", event.exception)

    # مهلة أطول لرفع صورة الترحيب (~1.8MB) على شبكات بطيئة (الافتراضي 60ث يفشل).
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=AiohttpSession(timeout=180),
    )
    # region agent log
    import socket as _sock

    try:
        _addrs = _sock.getaddrinfo("api.telegram.org", 443, type=_sock.SOCK_STREAM)
        _agent_debug_log(
            "H1",
            "main.py:main:before_poll",
            "dns_getaddrinfo_ok",
            {"naddrs": len(_addrs), "sample": str(_addrs[0][4]) if _addrs else None},
        )
    except OSError as _dns_e:
        _agent_debug_log(
            "H1",
            "main.py:main:before_poll",
            "dns_getaddrinfo_fail",
            {"errno": getattr(_dns_e, "errno", None), "err": str(_dns_e)[:500]},
        )
    # endregion
    try:
        balance = await smm_manager.get_balance()
        logger.info("SMM provider API credentials verified (balance=%s).", balance.get("balance"))
        from services.provider_catalog import refresh_provider_catalog

        if await refresh_provider_catalog(smm_manager, force=True):
            logger.info("Provider service catalog (limits) cached at startup.")
    except ProviderAuthError as exc:
        logger.critical("SMM provider API key is invalid: %s", exc)
        try:
            await send_smart_notification(
                bot,
                ADMIN_ID,
                "<b>⚠️ مفتاح API للمزود غير صالح</b>\n\n"
                f"السبب: {escape(str(exc))}\n\n"
                "حدّث مفاتيح <code>SMM_KEY_*</code> في ملف <code>.env</code> من لوحة "
                "<a href=\"https://gozibra.com\">gozibra.com</a> ثم أعد تشغيل البوت.\n"
                "لن تُنفَّذ الطلبات حتى تصحيح المفتاح.",
            )
        except Exception:
            logger.exception("Failed to notify admin about invalid SMM API key")
    except Exception as exc:
        logger.warning("Could not verify SMM provider credentials at startup: %s", exc)

    tracker_task = asyncio.create_task(_track_orders_and_refund(bot))
    try:
        wh = await bot.get_webhook_info()
        logger.info(
            "Telegram webhook before polling: configured=%s pending_updates=%s",
            bool(getattr(wh, "url", None) or ""),
            int(getattr(wh, "pending_update_count", 0) or 0),
        )
    except Exception as exc:
        logger.warning("Could not read Telegram webhook info: %s", exc)
    try:
        webhook_dropped = await bot.delete_webhook(drop_pending_updates=False)
        logger.info("delete_webhook before polling: ok=%s", bool(webhook_dropped))
    except Exception as exc:
        logger.warning("delete_webhook failed (continuing): %s", exc)
    try:
        for attempt in range(1, MAX_START_RETRIES + 1):
            try:
                await dp.start_polling(bot)
                break
            except TelegramNetworkError as exc:
                # region agent log
                _c1 = getattr(exc, "__cause__", None)
                _agent_debug_log(
                    "H2",
                    "main.py:main:start_polling",
                    "polling_TelegramNetworkError",
                    {
                        "attempt": attempt,
                        "exc_type": type(exc).__name__,
                        "exc_str": str(exc)[:500],
                        "cause_type": type(_c1).__name__ if _c1 else None,
                        "cause_str": str(_c1)[:500] if _c1 else None,
                    },
                )
                # endregion
                if attempt == MAX_START_RETRIES:
                    raise
                delay = BASE_RETRY_DELAY_SECONDS * attempt
                logger.warning(
                    "Network error during polling startup (attempt %s/%s): %s. Retrying in %ss.",
                    attempt,
                    MAX_START_RETRIES,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
    finally:
        tracker_task.cancel()
        await asyncio.gather(tracker_task, return_exceptions=True)


if __name__ == "__main__":
    # Tiny proof file (overwritten each run) — if this never updates, this main.py is not what you are executing.
    try:
        Path(__file__).resolve().with_name("soldium_last_run_path.txt").write_text(
            f"{Path(__file__).resolve()}\npid={os.getpid()}\n{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    # Attach before asyncio.run so dispatcher ERROR logs hit the handler immediately.
    _install_telegram_conflict_storm_guard()
    _boot_main = Path(__file__).resolve()
    _boot_stat = _boot_main.stat()
    _boot_msg = (
        f"[SOLDIUM] boot file={_boot_main} mtime={_boot_stat.st_mtime_ns} pid={os.getpid()}"
    )
    print(_boot_msg, flush=True)
    print(_boot_msg, file=sys.stderr, flush=True)
    _boot_witness_json("pre_lock", _boot_main, _boot_stat.st_mtime_ns)
    # region agent log
    _agent_debug_log(
        "H-1",
        "main.py:__main__",
        "script_started",
        {
            "argv0": sys.argv[0] if sys.argv else None,
            "main_path": str(_boot_main),
            "mtime_ns": _boot_stat.st_mtime_ns,
            "pid": os.getpid(),
        },
    )
    # endregion
    acquire_bot_instance_lock(BOT_TOKEN)
    _boot_witness_json(
        "post_single_instance_lock",
        _boot_main,
        _boot_stat.st_mtime_ns,
        single_instance_ok=True,
    )
    logger.info("Single-instance guard acquired for pid=%s (no second local main.py for this token).", os.getpid())
    # region agent log
    _agent_debug_log(
        "H5",
        "main.py:__main__",
        "single_instance_lock_acquired",
        {"pid": os.getpid()},
    )
    # endregion
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
    except Exception as exc:
        logger.exception("Fatal startup error: %s", exc)
        # region agent log
        _agent_debug_log(
            "H3",
            "main.py:__main__",
            "fatal_exit",
            {"exc_type": type(exc).__name__, "exc_str": str(exc)[:500]},
        )
        # endregion
        if _looks_like_dns_or_connectivity_error(exc):
            logger.error(
                "تعذّر الاتصال بـ api.telegram.org (DNS أو الشبكة). جرّب تعيين DNS عام (مثل 1.1.1.1 أو 8.8.8.8)، "
                "تعطيل VPN أو البروكسي، أو السماح بالخروج إلى المنفذ 443 في الجدار الناري."
            )
