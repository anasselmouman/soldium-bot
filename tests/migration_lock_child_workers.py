"""
Child-process workers for migration lock tests.

Kept separate from test_migration_lock.py so Windows ``spawn`` does not re-import
the parent test module (which imports ``database`` / may inherit a polluted
``sys.path`` that prefers soldium-dashboard ``config``).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_BOT_ROOT = Path(__file__).resolve().parents[1]
_DASHBOARD_ROOT = _BOT_ROOT.parent / "soldium-dashboard"


def _isolate_bot_import_path() -> None:
    """Prefer soldium-bot on ``sys.path``; drop sibling dashboard if present."""
    bot = str(_BOT_ROOT.resolve())
    dash = str(_DASHBOARD_ROOT.resolve()) if _DASHBOARD_ROOT.is_dir() else None
    cleaned: list[str] = []
    for entry in sys.path:
        try:
            resolved = str(Path(entry).resolve())
        except OSError:
            cleaned.append(entry)
            continue
        if dash is not None and resolved == dash:
            continue
        if resolved == bot:
            continue
        cleaned.append(entry)
    sys.path[:] = [bot, *cleaned]


_isolate_bot_import_path()


def child_hold_lock(db_path: str, ready_file: str, release_file: str) -> None:
    _isolate_bot_import_path()
    from migration_lock import migration_lock

    with migration_lock(db_path=db_path, holder="child", timeout_seconds=5.0):
        Path(ready_file).write_text("ready", encoding="utf-8")
        deadline = time.time() + 30
        while time.time() < deadline:
            if Path(release_file).exists():
                break
            time.sleep(0.05)


def child_hold_until_exit(db_path: str, ready_file: str) -> None:
    _isolate_bot_import_path()
    from migration_lock import migration_lock

    with migration_lock(db_path=db_path, holder="doomed", timeout_seconds=5.0):
        Path(ready_file).write_text("ready", encoding="utf-8")
        while True:
            time.sleep(1.0)
