"""Phase 8: pre-migration backup + final integrity wiring in init_db."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import database as db


def test_fully_migrated_db_skips_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db.DB_PATH = tmp_path / "migrated.db"
    db.init_db()
    assert db.pending_init_db_migrations(db_path=db.DB_PATH) == []

    backup_calls: list[object] = []

    def fail_if_called(*_a, **_k):
        backup_calls.append(True)
        raise AssertionError("backup_database must not run when nothing is pending")

    monkeypatch.setattr(db, "backup_database", fail_if_called)
    db.init_db()
    assert backup_calls == []


def test_pending_migration_creates_backup_then_migrates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "needs_mig.db"
    db.DB_PATH = path
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0);
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service_name TEXT NOT NULL DEFAULT '',
                service_id TEXT NOT NULL,
                link TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                amount REAL NOT NULL DEFAULT 0.0,
                total_price REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                provider_order_id TEXT,
                api_account TEXT NOT NULL DEFAULT 'default',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO orders (
                user_id, service_name, service_id, link, quantity, amount, total_price, status
            ) VALUES (1, 'a', '1', 'https://example.com/x', 1, 0, 25, 'completed');
            """
        )
        connection.commit()

    pending_before = db.pending_init_db_migrations(db_path=path)
    assert pending_before
    assert "orders_active_link_guard" in pending_before or any(
        p.startswith("orders_add_column:") for p in pending_before
    )

    created: list[Path] = []
    real_backup = db.backup_database

    def tracking_backup(**kwargs):
        result = real_backup(**kwargs)
        created.append(result)
        return result

    monkeypatch.setattr(db, "backup_database", tracking_backup)
    db.init_db()

    assert len(created) == 1
    assert created[0].exists()
    with db.get_connection() as connection:
        assert db._orders_active_link_index_exists(connection)
        amount = connection.execute(
            "SELECT amount FROM orders WHERE id = 1"
        ).fetchone()[0]
    assert float(amount) == 25.0
    assert db.pending_init_db_migrations(db_path=path) == []


def test_backup_failure_aborts_before_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "backup_fail.db"
    db.DB_PATH = path
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0)"
        )
        connection.execute(
            """
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service_id TEXT NOT NULL,
                link TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                amount REAL NOT NULL DEFAULT 0,
                total_price REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
        connection.commit()

    assert db.pending_init_db_migrations(db_path=path)

    def boom(**_kwargs):
        raise db.DatabaseBackupError("simulated backup failure")

    migrate_calls: list[object] = []

    def no_migrate():
        migrate_calls.append(True)
        raise AssertionError("migrations must not run after backup failure")

    monkeypatch.setattr(db, "backup_database", boom)
    monkeypatch.setattr(db, "_apply_init_db_schema_and_migrations", no_migrate)

    with pytest.raises(db.DatabaseBackupError, match="simulated backup failure"):
        db.init_db()
    assert migrate_calls == []


def test_corrupt_db_blocks_before_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a sqlite database")
    db.DB_PATH = path

    backup_calls: list[object] = []
    migrate_calls: list[object] = []

    monkeypatch.setattr(
        db,
        "backup_database",
        lambda **_k: backup_calls.append(True) or path,
    )
    monkeypatch.setattr(
        db,
        "_apply_init_db_schema_and_migrations",
        lambda: migrate_calls.append(True),
    )

    with pytest.raises(db.DatabaseIntegrityError):
        db.init_db()
    assert backup_calls == []
    assert migrate_calls == []


def test_migration_failure_preserves_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "mig_fail.db"
    db.DB_PATH = path
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0)"
        )
        connection.execute(
            """
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service_id TEXT NOT NULL DEFAULT '',
                link TEXT NOT NULL DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                amount REAL NOT NULL DEFAULT 0,
                total_price REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
        connection.commit()

    backup_file = tmp_path / "preserved.backup.db"
    backup_file.write_bytes(b"sqlite-backup-placeholder")

    monkeypatch.setattr(db, "backup_database", lambda **_k: backup_file)

    def explode():
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr(db, "_apply_init_db_schema_and_migrations", explode)

    with pytest.raises(RuntimeError, match="simulated migration failure"):
        db.init_db()
    assert backup_file.exists()
    assert backup_file.read_bytes() == b"sqlite-backup-placeholder"


def test_fresh_db_skips_backup_and_initializes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.DB_PATH = tmp_path / "brand_new.db"
    assert not db.DB_PATH.exists()

    backup_calls: list[object] = []
    monkeypatch.setattr(
        db,
        "backup_database",
        lambda **_k: backup_calls.append(True),
    )
    db.init_db()
    assert backup_calls == []
    assert db.DB_PATH.exists()
    with db.get_connection() as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "users" in tables
    assert "orders" in tables


def test_final_integrity_failure_fails_init(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.DB_PATH = tmp_path / "final_bad.db"
    assert not db.DB_PATH.exists()

    real_apply = db._apply_init_db_schema_and_migrations
    checks: list[str] = []

    def apply_then_corrupt():
        real_apply()
        # Replace DB bytes so the post-migration integrity check fails.
        db.DB_PATH.write_bytes(b"corrupted-after-migrate")

    def tracking_preflight(*, db_path=None):
        path = Path(db_path) if db_path is not None else Path(db.DB_PATH)
        if not path.exists():
            checks.append("skip-missing")
            return
        checks.append("check")
        # Delegate to real implementation for the actual check.
        uri = path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            row = connection.execute("PRAGMA integrity_check").fetchone()
            result = str(row[0]) if row is not None else ""
            if result != "ok":
                raise db.DatabaseIntegrityError(
                    f"SQLite database failed integrity_check ({result!r}); "
                    f"startup stopped for safety: {path}"
                )
        except db.DatabaseIntegrityError:
            raise
        except sqlite3.DatabaseError as exc:
            raise db.DatabaseIntegrityError(
                f"SQLite database error during integrity_check; "
                f"startup stopped for safety: {path}"
            ) from exc
        finally:
            connection.close()

    monkeypatch.setattr(db, "_apply_init_db_schema_and_migrations", apply_then_corrupt)
    monkeypatch.setattr(db, "preflight_db_integrity", tracking_preflight)

    with pytest.raises(db.DatabaseIntegrityError):
        db.init_db()
    # Fresh DB: no opening preflight file check beyond skip; final check runs.
    assert "check" in checks
