"""Phase 12: cross-process migration lock."""
from __future__ import annotations

import multiprocessing
import sys
import time
from pathlib import Path

import pytest

import database as db
from migration_lock import (
    MigrationLockTimeout,
    migration_lock,
    migration_lock_path,
)

# Spawn children import this sibling module by name; keep tests/ on path.
_TESTS_DIR = str(Path(__file__).resolve().parent)
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from migration_lock_child_workers import child_hold_lock, child_hold_until_exit  # noqa: E402


def _pollute_with_dashboard_path() -> str | None:
    """Insert soldium-dashboard at sys.path[0]; return path string if inserted."""
    bot_root = Path(__file__).resolve().parents[1]
    dash_root = bot_root.parent / "soldium-dashboard"
    if not dash_root.is_dir():
        return None
    path = str(dash_root)
    sys.path.insert(0, path)
    return path


def _clear_dashboard_path(path: str | None) -> None:
    if not path:
        return
    try:
        sys.path.remove(path)
    except ValueError:
        pass
    # Drop cached dashboard modules that may have been resolved during pollution.
    for name in list(sys.modules):
        if name == "config" or name == "services" or name.startswith("services."):
            mod = sys.modules.get(name)
            file_path = getattr(mod, "__file__", "") or ""
            if "soldium-dashboard" in file_path.replace("\\", "/"):
                sys.modules.pop(name, None)


def test_migration_lock_acquire_release(tmp_path: Path) -> None:
    db_path = tmp_path / "users.db"
    lock_path = migration_lock_path(db_path)
    with migration_lock(db_path=db_path, holder="test") as held:
        assert held == lock_path
        assert lock_path.is_file()
    # File may remain; advisory lock is released (presence ≠ locked).
    assert lock_path.is_file()


def test_migration_lock_same_process_nesting(tmp_path: Path) -> None:
    db_path = tmp_path / "users.db"
    with migration_lock(db_path=db_path, holder="outer"):
        with migration_lock(db_path=db_path, holder="inner"):
            pass


def test_migration_lock_released_on_exception(tmp_path: Path) -> None:
    db_path = tmp_path / "users.db"
    with pytest.raises(RuntimeError, match="boom"):
        with migration_lock(db_path=db_path, holder="boom"):
            raise RuntimeError("boom")
    # Second acquire must succeed immediately after exception unwind.
    with migration_lock(db_path=db_path, holder="after", timeout_seconds=1.0):
        pass


def test_migration_lock_process_safe_and_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "users.db"
    ready = tmp_path / "ready.txt"
    release = tmp_path / "release.txt"
    polluted = _pollute_with_dashboard_path()

    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(
        target=child_hold_lock,
        args=(str(db_path), str(ready), str(release)),
    )
    proc.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not ready.exists():
            time.sleep(0.05)
        assert ready.exists(), "child never acquired lock"

        with pytest.raises(MigrationLockTimeout):
            with migration_lock(
                db_path=db_path,
                holder="parent-timeout",
                timeout_seconds=0.3,
            ):
                pass

        release.write_text("go", encoding="utf-8")
        proc.join(timeout=10)
        assert proc.exitcode == 0

        with migration_lock(db_path=db_path, holder="parent-after", timeout_seconds=2.0):
            pass
    finally:
        _clear_dashboard_path(polluted)
        if proc.is_alive():
            release.write_text("go", encoding="utf-8")
            proc.join(timeout=5)
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)


def test_init_db_uses_migration_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "bot.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)
    calls: list[str] = []
    real = migration_lock

    def _tracking(*, db_path, holder, timeout_seconds=None):
        calls.append(holder)
        return real(db_path=db_path, holder=holder, timeout_seconds=timeout_seconds)

    monkeypatch.setattr("migration_lock.migration_lock", _tracking)
    import migration_lock as ml

    monkeypatch.setattr(ml, "migration_lock", _tracking)
    db.init_db()
    assert calls == ["soldium-bot"]
    assert db_path.is_file()


def test_second_process_skips_duplicate_migration_after_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-check after lock: first migrator wins; second must not backup again."""
    db_path = tmp_path / "race.db"
    monkeypatch.setattr(db, "DB_PATH", db_path)

    db.init_db()
    assert db.pending_init_db_migrations(db_path=db_path) == []

    backups: list[str] = []

    def _no_backup(**_kwargs):
        backups.append("backup")
        raise AssertionError("backup must not run when nothing is pending")

    monkeypatch.setattr(db, "backup_database", _no_backup)
    db.init_db()
    assert backups == []


def test_crash_style_release_allows_next_holder(tmp_path: Path) -> None:
    """Simulate abrupt unlock by closing the lock from a child process exit."""
    polluted = _pollute_with_dashboard_path()
    db_path = tmp_path / "crash.db"
    ready = tmp_path / "ready.txt"
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(
        target=child_hold_until_exit,
        args=(str(db_path), str(ready)),
    )
    proc.start()
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not ready.exists():
            time.sleep(0.05)
        assert ready.exists()
        proc.terminate()
        proc.join(timeout=10)
        with migration_lock(db_path=db_path, holder="after-crash", timeout_seconds=5.0):
            pass
    finally:
        _clear_dashboard_path(polluted)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
