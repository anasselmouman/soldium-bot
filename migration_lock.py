"""
Cross-process advisory lock for Soldium SQLite schema migrations only.

Protects bot ``init_db()`` / dashboard shared-schema bridge so at most one process
runs migrations against the shared ``users.db`` at a time. Does not lock normal
reads/writes.

Uses OS advisory locks (``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows).
The kernel releases the lock when the holding process exits — no permanent stale
lock from a crash. Lock-file presence alone is never treated as “locked”.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

# Bound wait for the other process to finish backup + migrations.
DEFAULT_TIMEOUT_SECONDS = float(os.environ.get("SOLDIUM_MIGRATE_LOCK_TIMEOUT", "120"))
_POLL_INTERVAL_SECONDS = 0.05

_nesting = threading.local()


class MigrationLockTimeout(TimeoutError):
    """Timed out waiting for another process to finish schema migrations."""


class MigrationLockError(RuntimeError):
    """Failed to acquire or manage the migration lock."""


def migration_lock_path(db_path: Path | str) -> Path:
    """Sidecar lock file next to the shared DB (not inside the SQLite file)."""
    path = Path(db_path).resolve()
    return path.with_name(path.name + ".migrate.lock")


def _ensure_parent(lock_path: Path) -> None:
    lock_path.parent.mkdir(parents=True, exist_ok=True)


def _try_acquire_posix(fd: int) -> bool:
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _release_posix(fd: int) -> None:
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


def _try_acquire_windows(fd: int) -> bool:
    import msvcrt

    try:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _release_windows(fd: int) -> None:
    import msvcrt

    try:
        # Seek to start — msvcrt.locking is relative to current position.
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def _nesting_depth() -> int:
    return int(getattr(_nesting, "depth", 0) or 0)


def _set_nesting_depth(value: int) -> None:
    _nesting.depth = value


@contextmanager
def migration_lock(
    *,
    db_path: Path | str,
    holder: str,
    timeout_seconds: float | None = None,
) -> Iterator[Path]:
    """
    Acquire an exclusive advisory lock for schema migration work.

    Same-process nesting is allowed (dashboard bridge → ``init_db()``) without
    re-taking the OS lock. Cross-process waits up to ``timeout_seconds``.
    """
    timeout = (
        DEFAULT_TIMEOUT_SECONDS if timeout_seconds is None else float(timeout_seconds)
    )
    if timeout < 0:
        raise MigrationLockError("migration lock timeout_seconds must be >= 0")

    depth = _nesting_depth()
    if depth > 0:
        _set_nesting_depth(depth + 1)
        try:
            yield migration_lock_path(db_path)
        finally:
            _set_nesting_depth(_nesting_depth() - 1)
        return

    lock_path = migration_lock_path(db_path)
    _ensure_parent(lock_path)
    # Ensure the file exists and has at least one byte for Windows msvcrt.locking.
    handle = open(lock_path, "a+b")  # noqa: SIM115
    try:
        handle.seek(0, os.SEEK_END)
        if handle.tell() < 1:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)

        fd = handle.fileno()
        deadline = time.monotonic() + timeout
        acquired = False
        while True:
            if sys.platform == "win32":
                acquired = _try_acquire_windows(fd)
            else:
                acquired = _try_acquire_posix(fd)
            if acquired:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error(
                    "Migration lock timeout after %.1fs waiting on %s (waiter=%s)",
                    timeout,
                    lock_path,
                    holder,
                )
                raise MigrationLockTimeout(
                    f"Timed out after {timeout:.0f}s waiting for migration lock "
                    f"{lock_path} (waiter={holder}). Another process may be "
                    f"migrating the shared database; retry after it finishes."
                )
            time.sleep(min(_POLL_INTERVAL_SECONDS, max(remaining, 0.0)))

        logger.info(
            "Migration lock acquired: path=%s holder=%s",
            lock_path,
            holder,
        )
        _set_nesting_depth(1)
        try:
            yield lock_path
        finally:
            _set_nesting_depth(0)
            try:
                if sys.platform == "win32":
                    _release_windows(fd)
                else:
                    _release_posix(fd)
            except OSError as exc:
                logger.warning(
                    "Migration lock release failed for %s (holder=%s): %s",
                    lock_path,
                    holder,
                    exc,
                )
            logger.info(
                "Migration lock released: path=%s holder=%s",
                lock_path,
                holder,
            )
    finally:
        try:
            handle.close()
        except OSError:
            pass
