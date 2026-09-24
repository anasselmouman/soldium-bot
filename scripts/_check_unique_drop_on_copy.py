# -*- coding: utf-8 -*-
"""Local safety check: migrate a backup copy of users.db (never production)."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import database as db


def main() -> None:
    src = Path(__file__).resolve().parents[1] / "users.db"
    if not src.exists():
        print("NO_LOCAL_USERS_DB")
        return

    td = Path(tempfile.mkdtemp(prefix="smm_unique_drop_"))
    dst = td / "users_copy.db"
    with sqlite3.connect(str(src)) as s, sqlite3.connect(str(dst)) as d:
        s.backup(d)

    db.DB_PATH = dst
    with db.get_connection() as c:
        before_smm = c.execute("SELECT COUNT(*) FROM smm_services").fetchone()[0]
        before_ids = {
            r[0] for r in c.execute("SELECT catalog_id FROM smm_services")
        }
        before_users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        before_orders = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        before_unique = db._smm_provider_external_unique_enforced(c)
        try:
            before_catalog = c.execute(
                "SELECT COUNT(*) FROM soldium_catalog_services"
            ).fetchone()[0]
        except sqlite3.Error:
            before_catalog = None
        try:
            before_exec = c.execute(
                "SELECT COUNT(*) FROM soldium_catalog_execution_sources"
            ).fetchone()[0]
        except sqlite3.Error:
            before_exec = None

    pending = db.pending_init_db_migrations(db_path=dst)
    print(
        "before",
        {
            "smm": before_smm,
            "users": before_users,
            "orders": before_orders,
            "unique": before_unique,
            "catalog": before_catalog,
            "exec": before_exec,
            "pending": pending,
        },
    )

    db.init_db()

    with db.get_connection() as c:
        after_smm = c.execute("SELECT COUNT(*) FROM smm_services").fetchone()[0]
        after_ids = {
            r[0] for r in c.execute("SELECT catalog_id FROM smm_services")
        }
        after_users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        after_orders = c.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        after_unique = db._smm_provider_external_unique_enforced(c)
        idx = c.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='index' AND name='idx_smm_services_provider_external'"
        ).fetchone()
        ddl = c.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='table' AND name='smm_services'"
        ).fetchone()[0]
        try:
            after_catalog = c.execute(
                "SELECT COUNT(*) FROM soldium_catalog_services"
            ).fetchone()[0]
        except sqlite3.Error:
            after_catalog = None
        try:
            after_exec = c.execute(
                "SELECT COUNT(*) FROM soldium_catalog_execution_sources"
            ).fetchone()[0]
        except sqlite3.Error:
            after_exec = None

    compact = "".join(str(ddl).lower().split())
    print(
        "after",
        {
            "smm": after_smm,
            "users": after_users,
            "orders": after_orders,
            "unique": after_unique,
            "catalog": after_catalog,
            "exec": after_exec,
            "ids_equal": before_ids == after_ids,
            "idx_sql": idx[0] if idx else None,
            "ddl_has_unique_pair": "unique(provider_slug,external_service_id)"
            in compact,
            "pending_after": db.pending_init_db_migrations(db_path=dst),
            "copy_path": str(dst),
        },
    )

    # Prove duplicates are insertable after migration (copy only).
    with db.get_connection() as c:
        c.execute(
            """
            INSERT INTO smm_services (
                catalog_id, external_service_id, service_id, provider_slug,
                name_ar, local_item_id
            ) VALUES
            ('dup-test-A', '4210', '4210', 'gozibra', 'A', 'dup-test-A'),
            ('dup-test-B', '4210', '4210', 'gozibra', 'B', 'dup-test-B')
            """
        )
        n = c.execute(
            """
            SELECT COUNT(*) FROM smm_services
            WHERE provider_slug='gozibra' AND external_service_id='4210'
              AND catalog_id IN ('dup-test-A', 'dup-test-B')
            """
        ).fetchone()[0]
        print("duplicate_insert_ok", n == 2, "unique_still", db._smm_provider_external_unique_enforced(c))


if __name__ == "__main__":
    main()
