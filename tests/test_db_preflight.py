"""Focused tests for SQLite integrity preflight before init_db migrations."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import database as db


def test_preflight_succeeds_for_healthy_database(tmp_path: Path) -> None:
    path = tmp_path / "healthy.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY)")
        connection.commit()

    db.preflight_db_integrity(db_path=path)


def test_preflight_skips_when_database_missing(tmp_path: Path) -> None:
    path = tmp_path / "does_not_exist.db"
    assert not path.exists()
    db.preflight_db_integrity(db_path=path)


def test_init_db_creates_database_when_missing(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "first_run.db"
    assert not db.DB_PATH.exists()
    db.init_db()
    assert db.DB_PATH.exists()
    with sqlite3.connect(db.DB_PATH) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "users" in tables


def test_preflight_blocks_when_integrity_check_not_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "marked_bad.db"
    path.write_bytes(b"placeholder")

    fake_connection = MagicMock()
    fake_connection.execute.return_value.fetchone.return_value = (
        "*** in database main ***",
    )
    monkeypatch.setattr(sqlite3, "connect", MagicMock(return_value=fake_connection))

    with pytest.raises(db.DatabaseIntegrityError, match="integrity_check"):
        db.preflight_db_integrity(db_path=path)

    fake_connection.close.assert_called_once()


def test_preflight_blocks_on_sqlite_database_error(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"this is not a sqlite database file")

    with pytest.raises(db.DatabaseIntegrityError, match="startup stopped for safety"):
        db.preflight_db_integrity(db_path=path)


def test_init_db_does_not_run_migrations_when_preflight_fails(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"this is not a sqlite database file")
    db.DB_PATH = path

    with pytest.raises(db.DatabaseIntegrityError):
        db.init_db()

    # File must remain the same garbage bytes — no recreate/repair.
    assert path.read_bytes() == b"this is not a sqlite database file"
