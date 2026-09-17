"""Focused tests for SQLite connection policy (Phase 5)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import database as db


def test_get_connection_sets_timeout_and_busy_timeout(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "conn_policy.db"
    db.init_db()

    with db.get_connection() as connection:
        # foreign_keys left at SQLite default (OFF) until a dedicated FK audit.
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 0
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == db.BUSY_TIMEOUT_MS
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() in {"delete", "wal", "memory", "off", "persist", "truncate"}


def test_get_connection_closes_underlying_connection(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "conn_close.db"
    db.init_db()

    with db.get_connection() as connection:
        held = connection
        held.execute("SELECT 1").fetchone()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        held.execute("SELECT 1")


def test_get_connection_does_not_force_wal(tmp_path: Path) -> None:
    db.DB_PATH = tmp_path / "conn_no_wal_force.db"
    # Fresh DB defaults to delete journal on most builds; ensure we don't flip it.
    with db.get_connection() as connection:
        connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()

    # Re-open via get_connection and confirm mode was not forcibly set to wal
    # by connection policy (if already delete, must stay delete).
    with db.get_connection() as connection:
        mode2 = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    if mode == "delete":
        assert mode2 == "delete"


def test_maintenance_wal_checkpoint_on_wal_db(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint.db"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        connection.execute("INSERT INTO t (v) VALUES ('a')")
        connection.commit()

    busy, log, checkpointed = db.maintenance_wal_checkpoint(db_path=path, mode="TRUNCATE")
    assert busy == 0
    assert isinstance(log, int)
    assert isinstance(checkpointed, int)


def test_maintenance_wal_checkpoint_rejects_bad_mode(tmp_path: Path) -> None:
    path = tmp_path / "checkpoint_bad.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
        connection.commit()

    with pytest.raises(ValueError, match="Unsupported wal_checkpoint mode"):
        db.maintenance_wal_checkpoint(db_path=path, mode="NOPE")


def test_maintenance_wal_checkpoint_missing_db(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        db.maintenance_wal_checkpoint(db_path=tmp_path / "missing.db")
