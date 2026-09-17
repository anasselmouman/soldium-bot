"""Focused tests for consistent SQLite backup (WAL-safe Connection.backup)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import database as db


def _create_wal_db_with_users(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute(
            """
            CREATE TABLE users (
                user_id INTEGER PRIMARY KEY,
                balance REAL NOT NULL DEFAULT 0.0,
                telegram_name TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO users (user_id, balance, telegram_name) VALUES (?, ?, ?)",
            (42, 125.5, "soldium_user"),
        )
        connection.execute(
            "INSERT INTO users (user_id, balance, telegram_name) VALUES (?, ?, ?)",
            (99, 0.0, "zero_balance"),
        )
        connection.commit()


def test_backup_healthy_database_is_readable(tmp_path: Path) -> None:
    source = tmp_path / "users.db"
    _create_wal_db_with_users(source)
    destination = tmp_path / "users.db.backup_test"

    result = db.backup_database(source_path=source, backup_path=destination)

    assert result == destination
    assert destination.exists()
    with sqlite3.connect(destination) as connection:
        rows = connection.execute(
            "SELECT user_id, balance, telegram_name FROM users ORDER BY user_id"
        ).fetchall()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    assert integrity == "ok"
    assert rows == [(42, 125.5, "soldium_user"), (99, 0.0, "zero_balance")]


def test_backup_includes_representative_data_under_wal(tmp_path: Path) -> None:
    source = tmp_path / "users.db"
    _create_wal_db_with_users(source)
    assert (tmp_path / "users.db-wal").exists()

    destination = tmp_path / "consistent.backup.db"
    db.backup_database(source_path=source, backup_path=destination)

    # Backup is a standalone SQLite file; companion wal/shm copies are not required.
    assert not Path(str(destination) + "-wal").exists()
    assert not Path(str(destination) + "-shm").exists()

    with sqlite3.connect(destination) as connection:
        balance = connection.execute(
            "SELECT balance FROM users WHERE user_id = 42"
        ).fetchone()[0]
    assert balance == 125.5


def test_backup_is_independent_from_original(tmp_path: Path) -> None:
    source = tmp_path / "users.db"
    _create_wal_db_with_users(source)
    destination = tmp_path / "users.db.backup_independent"

    db.backup_database(source_path=source, backup_path=destination)

    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE users SET balance = 999.0 WHERE user_id = 42")
        connection.commit()

    with sqlite3.connect(destination) as connection:
        balance = connection.execute(
            "SELECT balance FROM users WHERE user_id = 42"
        ).fetchone()[0]
    assert balance == 125.5


def test_backup_fails_clearly_when_source_missing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(db.DatabaseBackupError, match="missing SQLite database"):
        db.backup_database(source_path=missing, backup_path=tmp_path / "out.db")


def test_backup_fails_clearly_when_destination_exists(tmp_path: Path) -> None:
    source = tmp_path / "users.db"
    _create_wal_db_with_users(source)
    destination = tmp_path / "already.db"
    destination.write_bytes(b"occupied")

    with pytest.raises(db.DatabaseBackupError, match="already exists"):
        db.backup_database(source_path=source, backup_path=destination)

    assert destination.read_bytes() == b"occupied"


def test_backup_fails_clearly_on_sqlite_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "users.db"
    _create_wal_db_with_users(source)
    destination = tmp_path / "users.db.backup_fail"

    fake_source = MagicMock()
    fake_source.backup.side_effect = sqlite3.DatabaseError("backup exploded")
    fake_dest = MagicMock()

    monkeypatch.setattr(
        sqlite3,
        "connect",
        MagicMock(side_effect=[fake_source, fake_dest]),
    )

    with pytest.raises(db.DatabaseBackupError, match="SQLite backup failed"):
        db.backup_database(source_path=source, backup_path=destination)

    assert not destination.exists()
