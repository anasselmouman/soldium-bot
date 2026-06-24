"""
Migrate production data from users server.db into users.db.

Keeps the updated smm_services catalog from users.db (current schema).
Replaces all user/transactional data with server production data.
"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURRENT_DB = ROOT / "users.db"
SERVER_DB = ROOT / "users server.db"

# Tables copied from server (production data). smm_services stays from current DB.
DATA_TABLES = [
    "users",
    "deposits",
    "deposit_transactions",
    "orders",
    "withdrawals",
    "pending_notifications",
    "pending_referral_level_upgrades",
    "refund_audit_log",
    "timed_announcements",
    "timed_announcement_dismissals",
]

PRESERVE_TABLE = "smm_services"


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info([{table}])").fetchall()]


def common_columns(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> list[str]:
    src_cols = set(table_columns(src, table))
    dst_cols = set(table_columns(dst, table))
    shared = sorted(src_cols & dst_cols, key=lambda c: table_columns(dst, table).index(c))
    only_src = src_cols - dst_cols
    only_dst = dst_cols - src_cols
    if only_src:
        print(f"  [{table}] columns only in server (skipped): {sorted(only_src)}")
    if only_dst:
        print(f"  [{table}] columns only in current (default/null): {sorted(only_dst)}")
    return shared


def copy_table(
    src: sqlite3.Connection,
    dst: sqlite3.Connection,
    table: str,
) -> int:
    cols = common_columns(src, dst, table)
    if not cols:
        raise RuntimeError(f"No shared columns for table {table}")
    col_list = ", ".join(f"[{c}]" for c in cols)
    placeholders = ", ".join("?" for _ in cols)
    rows = src.execute(f"SELECT {col_list} FROM [{table}]").fetchall()
    dst.executemany(
        f"INSERT INTO [{table}] ({col_list}) VALUES ({placeholders})",
        rows,
    )
    return len(rows)


def reset_autoincrement(dst: sqlite3.Connection, table: str, id_col: str = "id") -> None:
    max_id = dst.execute(f"SELECT COALESCE(MAX([{id_col}]), 0) FROM [{table}]").fetchone()[0]
    dst.execute(
        "INSERT OR REPLACE INTO sqlite_sequence (name, seq) VALUES (?, ?)",
        (table, max_id),
    )


def verify_counts(src: sqlite3.Connection, dst: sqlite3.Connection) -> list[str]:
    errors: list[str] = []
    for table in DATA_TABLES + [PRESERVE_TABLE]:
        src_count = src.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
        dst_count = dst.execute(f"SELECT COUNT(*) FROM [{table}]").fetchone()[0]
        expected = src_count if table != PRESERVE_TABLE else dst_count  # smm checked separately
        if table == PRESERVE_TABLE:
            current_smm = dst_count
            if current_smm < 2000:
                errors.append(f"{table}: expected large catalog, got {current_smm}")
        elif dst_count != src_count:
            errors.append(f"{table}: server={src_count}, current={dst_count}")
        else:
            print(f"  OK {table}: {dst_count} rows")
    return errors


def main() -> None:
    if not SERVER_DB.exists():
        raise SystemExit(f"Server DB not found: {SERVER_DB}")
    if not CURRENT_DB.exists():
        raise SystemExit(f"Current DB not found: {CURRENT_DB}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = ROOT / f"users.db.backup_{timestamp}"
    print(f"Creating backup: {backup_path.name}")
    shutil.copy2(CURRENT_DB, backup_path)

    smm_backup_count: int
    with sqlite3.connect(CURRENT_DB) as cur_conn:
        smm_backup_count = cur_conn.execute(
            f"SELECT COUNT(*) FROM [{PRESERVE_TABLE}]"
        ).fetchone()[0]
    print(f"Preserving {PRESERVE_TABLE}: {smm_backup_count} rows from current DB")

    with sqlite3.connect(SERVER_DB) as src, sqlite3.connect(CURRENT_DB) as dst:
        dst.execute("PRAGMA foreign_keys = OFF")
        dst.execute("BEGIN")

        for table in DATA_TABLES:
            deleted = dst.execute(f"DELETE FROM [{table}]").rowcount
            print(f"Cleared [{table}]: {deleted} old rows")

        for table in DATA_TABLES:
            inserted = copy_table(src, dst, table)
            print(f"Copied [{table}]: {inserted} rows from server")

        for table in ("deposits", "deposit_transactions", "orders", "withdrawals", "refund_audit_log", "timed_announcements"):
            reset_autoincrement(dst, table)

        dst.execute("COMMIT")
        dst.execute("PRAGMA foreign_keys = ON")

        print("\nVerification:")
        errors = verify_counts(src, dst)

        # smm_services must remain from current
        smm_now = dst.execute(f"SELECT COUNT(*) FROM [{PRESERVE_TABLE}]").fetchone()[0]
        if smm_now != smm_backup_count:
            errors.append(
                f"{PRESERVE_TABLE}: count changed {smm_backup_count} -> {smm_now}"
            )
        else:
            print(f"  OK {PRESERVE_TABLE}: {smm_now} rows (preserved from current)")

        # Referential integrity spot-checks
        orphan_orders = dst.execute(
            """
            SELECT COUNT(*) FROM orders o
            LEFT JOIN users u ON u.user_id = o.user_id
            WHERE u.user_id IS NULL
            """
        ).fetchone()[0]
        if orphan_orders:
            errors.append(f"orphan orders: {orphan_orders}")

        orphan_deposits = dst.execute(
            """
            SELECT COUNT(*) FROM deposits d
            LEFT JOIN users u ON u.user_id = d.user_id
            WHERE u.user_id IS NULL
            """
        ).fetchone()[0]
        if orphan_deposits:
            errors.append(f"orphan deposits: {orphan_deposits}")

    if errors:
        print("\nMIGRATION ERRORS:")
        for err in errors:
            print(f"  - {err}")
        print(f"\nBackup kept at: {backup_path}")
        raise SystemExit(1)

    print(f"\nMigration successful. Backup: {backup_path.name}")


if __name__ == "__main__":
    main()
