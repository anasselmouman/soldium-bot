"""One local process per bot token — avoids TelegramConflictError from duplicate getUpdates."""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import sys
import time
from pathlib import Path


def _debug_8857df(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # region agent log
    try:
        log_path = Path(__file__).resolve().parents[1] / "debug-8857df.log"
        payload = {
            "sessionId": "8857df",
            "runId": "pre-fix",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # endregion


class _SingleInstanceGuard:
    def __init__(self) -> None:
        self._windows: tuple[object, int] | None = None
        self._lock_file: object | None = None

    def acquire_for_token(self, token: str) -> None:
        key = hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
        name = f"soldium_bot_{key}"
        if sys.platform == "win32":
            self._acquire_windows(name)
        else:
            self._acquire_posix(name)

    def _acquire_windows(self, name: str) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = wintypes.DWORD
        ERROR_ALREADY_EXISTS = 183
        mutex_name = "Local\\" + name.replace("/", "_")
        ctypes.windll.kernel32.SetLastError(0)
        handle = kernel32.CreateMutexW(None, False, mutex_name)
        last_err = kernel32.GetLastError()
        if not handle:
            raise ctypes.WinError(last_err)
        if last_err == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            raise RuntimeError("already_running")
        self._windows = (kernel32, handle)

        def _close() -> None:
            if self._windows:
                k, h = self._windows
                k.CloseHandle(h)
                self._windows = None

        atexit.register(_close)

    def _acquire_posix(self, name: str) -> None:
        import fcntl
        from pathlib import Path

        base = os.environ.get("XDG_RUNTIME_DIR") or os.environ.get("TMPDIR") or "/tmp"
        lock_path = Path(base) / f".{name}.lock"
        self._lock_file = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self._lock_file.close()
            self._lock_file = None
            raise RuntimeError("already_running") from exc

        def _unlock() -> None:
            if self._lock_file:
                try:
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                try:
                    self._lock_file.close()
                except OSError:
                    pass
                self._lock_file = None

        atexit.register(_unlock)


_guard = _SingleInstanceGuard()


def acquire_bot_instance_lock(token: str) -> None:
    """Exit with code 1 if another process already holds the lock for this token."""
    try:
        _guard.acquire_for_token(token)
    except RuntimeError:
        _debug_8857df(
            "H-dup",
            "utils/single_instance.py:acquire_bot_instance_lock",
            "second_instance_exit",
            {"pid": os.getpid()},
        )
        print(
            "Another bot process for this token is already running on this machine "
            "(second copy causes TelegramConflictError). Stop the other `python main.py` first.",
            file=sys.stderr,
        )
        print(
            "توجد نسخة أخرى من البوت تعمل بنفس التوكن على هذا الجهاز — أوقفها ثم أعد التشغيل.",
            file=sys.stderr,
        )
        sys.exit(1)
