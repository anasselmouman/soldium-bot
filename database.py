"""
قاعدة البيانات المحلية.
أرصدة المستخدمين والإيداعات والطلبات المسجّلة بالدرهم المغربي (DH / MAD)
حسب واجهة البوت (لا تحويل صرف آللي — المبلغ المخزّن هو نفسه المعروض).

جدول orders: المفتاح الداخلي id للربط الداخلي فقط؛ المعرف المعروض للعميل في الواجهة هو provider_order_id (رقم الطلب).

--- PostgreSQL (إن انتقلت من SQLite لاحقاً) ---
ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by INTEGER REFERENCES users(user_id);
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_level INTEGER NOT NULL DEFAULT 1;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_earned_total DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS referral_balance DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN IF NOT EXISTS telegram_name TEXT;
ALTER TABLE withdrawals ADD COLUMN IF NOT EXISTS withdrawal_type TEXT NOT NULL DEFAULT 'normal';
ALTER TABLE orders ADD COLUMN IF NOT EXISTS refunded_amount DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS referral_payout_done BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS referral_commission_amount DOUBLE PRECISION NOT NULL DEFAULT 0;
"""
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator, TypedDict

from config import MIN_REFERRAL_WITHDRAW_DH
from utils.money import to_decimal, to_float
from utils.order_status_ar import normalize_order_status_key

DB_PATH = Path(__file__).with_name("users.db")
# Align with dashboard database_connector / other bot readers that already wait on locks.
CONNECT_TIMEOUT_SECONDS = 30.0
BUSY_TIMEOUT_MS = 30_000
logger = logging.getLogger(__name__)


class DatabaseIntegrityError(RuntimeError):
    """Raised when the SQLite database fails preflight integrity_check."""


class DatabaseBackupError(RuntimeError):
    """Raised when a consistent SQLite backup cannot be completed."""


def preflight_db_integrity(*, db_path: Path | None = None) -> None:
    """
    Read-only SQLite health check that must run before migrations.

    If the database file does not exist yet, returns without error so first-run
    creation via ``init_db()`` remains unchanged. Never writes, repairs, or
    deletes database files.
    """
    path = Path(db_path) if db_path is not None else Path(DB_PATH)
    if not path.exists():
        return

    connection: sqlite3.Connection | None = None
    try:
        # mode=ro avoids creating journals/side files while checking.
        uri = path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        row = connection.execute("PRAGMA integrity_check").fetchone()
        result = str(row[0]) if row is not None else ""
        if result != "ok":
            logger.error(
                "Database failed integrity_check; startup aborted for safety. "
                "path=%s integrity_check=%s",
                path,
                result,
            )
            raise DatabaseIntegrityError(
                f"SQLite database failed integrity_check ({result!r}); "
                f"startup stopped for safety: {path}"
            )
    except DatabaseIntegrityError:
        raise
    except sqlite3.DatabaseError as exc:
        logger.error(
            "Database failed integrity_check; startup aborted for safety. "
            "path=%s error=%s",
            path,
            exc,
        )
        raise DatabaseIntegrityError(
            f"SQLite database error during integrity_check; "
            f"startup stopped for safety: {path}"
        ) from exc
    finally:
        if connection is not None:
            connection.close()


def backup_database(
    *,
    source_path: Path | None = None,
    backup_path: Path | None = None,
) -> Path:
    """
    Create a consistent SQLite backup using ``sqlite3.Connection.backup()``.

    This is the supported online-backup API: committed pages from an active WAL
    are included in the destination file, so ``*-wal`` / ``*-shm`` do not need
    to be copied separately. Does not delete, replace, or mutate application
    data in the source database, and does not run ``wal_checkpoint``.

    Not called automatically by ``get_connection()``. May be invoked by ``init_db()``
    when one-time migrations are pending on an existing database.

    Returns the path of the written backup file.
    """
    source = Path(source_path) if source_path is not None else Path(DB_PATH)
    if not source.exists():
        raise DatabaseBackupError(f"Cannot backup missing SQLite database: {source}")

    destination = (
        Path(backup_path)
        if backup_path is not None
        else source.with_name(
            f"{source.name}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
    )
    if destination.exists():
        raise DatabaseBackupError(
            f"Backup destination already exists (refusing to overwrite): {destination}"
        )

    destination.parent.mkdir(parents=True, exist_ok=True)

    source_conn: sqlite3.Connection | None = None
    dest_conn: sqlite3.Connection | None = None
    try:
        # Read-only source: include WAL contents via the SQLite pager without
        # writing checkpoints or altering production rows.
        source_uri = source.resolve().as_uri() + "?mode=ro"
        source_conn = sqlite3.connect(source_uri, uri=True)
        dest_conn = sqlite3.connect(destination)
        source_conn.backup(dest_conn)
    except DatabaseBackupError:
        raise
    except sqlite3.Error as exc:
        logger.error(
            "SQLite backup failed; production database was not replaced. "
            "source=%s dest=%s error=%s",
            source,
            destination,
            exc,
        )
        if dest_conn is not None:
            try:
                dest_conn.close()
            except sqlite3.Error:
                pass
            dest_conn = None
        if destination.exists():
            try:
                destination.unlink()
            except OSError:
                logger.warning("Could not remove incomplete backup file: %s", destination)
        raise DatabaseBackupError(
            f"SQLite backup failed for {source} -> {destination}: {exc}"
        ) from exc
    finally:
        if dest_conn is not None:
            dest_conn.close()
        if source_conn is not None:
            source_conn.close()

    logger.info("SQLite backup created: %s -> %s", source, destination)
    return destination


class UserRecord(TypedDict):
    user_id: int
    balance: float
    total_spent: float
    referred_by: int | None
    referral_level: int
    referral_earned_total: float
    referral_balance: float
    telegram_name: str | None


class DepositRecord(TypedDict):
    id: int
    user_id: int
    amount: float
    method: str
    proof_file_id: str
    status: str


class DepositTransactionRecord(TypedDict):
    id: int
    user_id: int
    deposit_method: str
    amount: float
    status: str
    deposit_id: int | None
    created_at: str


class WithdrawalRecord(TypedDict):
    id: int
    user_id: int
    amount: float
    method: str
    details_json: str
    status: str
    withdrawal_type: str
    created_at: str
    updated_at: str | None


class OrderRecord(TypedDict):
    id: int
    user_id: int
    service_name: str
    service_id: str
    quantity: int
    amount: float
    status: str
    provider_order_id: str | None
    created_at: str
    link: str
    start_count: int | None
    status_note: str | None
    api_account: str
    provider_slug: str
    provider_cost_dh: float


class PendingOrderFundingRecord(TypedDict):
    intent_id: int
    user_id: int
    service_id: str
    service_name: str
    platform_key: str
    section_key: str | None
    subsection_key: str | None
    link: str
    quantity: int
    amount_dh: float
    auto_quantity: int
    created_at: str
    updated_at: str


def _order_row_to_record(row: sqlite3.Row) -> OrderRecord:
    keys = row.keys()
    status_note = None
    if "status_note" in keys and row["status_note"] is not None:
        status_note = str(row["status_note"])
    api_account = "default"
    if "api_account" in keys and row["api_account"] is not None:
        api_account = str(row["api_account"]).strip() or "default"
    provider_slug = ""
    if "provider_slug" in keys and row["provider_slug"] is not None:
        provider_slug = str(row["provider_slug"]).strip()
    if not provider_slug:
        try:
            from services.provider_registry import get_default_provider_slug

            provider_slug = get_default_provider_slug()
        except Exception:
            provider_slug = "gozibra"
    service_id = ""
    if "service_id" in keys and row["service_id"] is not None:
        service_id = str(row["service_id"])
    provider_cost_dh = 0.0
    if "provider_cost_dh" in keys and row["provider_cost_dh"] is not None:
        provider_cost_dh = float(row["provider_cost_dh"])
    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]),
        "service_name": str(row["service_name"]),
        "service_id": service_id,
        "link": str(row["link"] or ""),
        "quantity": int(row["quantity"]),
        "amount": float(row["amount"]),
        "status": str(row["status"]),
        "provider_order_id": (
            str(row["provider_order_id"]) if row["provider_order_id"] else None
        ),
        "start_count": int(row["start_count"]) if row["start_count"] is not None else None,
        "created_at": str(row["created_at"]),
        "status_note": status_note,
        "api_account": api_account,
        "provider_slug": provider_slug,
        "provider_cost_dh": provider_cost_dh,
    }


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """
    Open ``users.db`` with shared lock-tolerant settings.

    Configures timeout + busy_timeout (aligned with the dashboard / other bot
    readers) so concurrent access waits instead of failing immediately.

    Does **not** set ``PRAGMA foreign_keys=ON`` yet: the schema declares FKs and
    the dashboard enables them, but the bot historically ran with SQLite's
    default (OFF). Turning enforcement on changes ``IntegrityError`` behavior
    for child inserts and needs a dedicated insert-order audit.

    Does **not** set ``journal_mode``: production WAL is already persisted in the
    database file header; changing journal mode is a durable DB change and is
    left to explicit maintenance, not every connection.

    Used as ``with get_connection() as connection:``. Commits on success, rolls
    back on error (same as sqlite3.Connection context-manager semantics), and
    always closes the underlying connection.
    """
    connection = sqlite3.connect(str(DB_PATH), timeout=CONNECT_TIMEOUT_SECONDS)
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def maintenance_wal_checkpoint(
    *,
    db_path: Path | None = None,
    mode: str = "TRUNCATE",
) -> tuple[int, int, int]:
    """
    Maintenance-only WAL checkpoint helper.

    Not used by ``get_connection()``, ``init_db()``, or normal request paths.
    Intended for controlled deployment/maintenance when a checkpoint is desired
    after backups or before offline file operations.

    ``mode`` must be one of: PASSIVE, FULL, RESTART, TRUNCATE.
    Returns the ``(busy, log, checkpointed)`` triple from SQLite.
    """
    allowed = {"PASSIVE", "FULL", "RESTART", "TRUNCATE"}
    normalized = str(mode or "TRUNCATE").strip().upper()
    if normalized not in allowed:
        raise ValueError(f"Unsupported wal_checkpoint mode: {mode!r}")

    path = Path(db_path) if db_path is not None else Path(DB_PATH)
    if not path.exists():
        raise FileNotFoundError(f"Cannot checkpoint missing SQLite database: {path}")

    connection = sqlite3.connect(str(path), timeout=CONNECT_TIMEOUT_SECONDS)
    try:
        connection.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        row = connection.execute(f"PRAGMA wal_checkpoint({normalized})").fetchone()
        if row is None:
            raise sqlite3.OperationalError("wal_checkpoint returned no result")
        result = (int(row[0]), int(row[1]), int(row[2]))
        logger.info(
            "maintenance_wal_checkpoint mode=%s path=%s result=%s",
            normalized,
            path,
            result,
        )
        return result
    finally:
        connection.close()


def _dedupe_active_deposit_proofs(connection: sqlite3.Connection) -> int:
    """
    Legacy rows may share proof_file_id while pending/approved.
    Keep the newest deposit (max id); mark older duplicates so the partial unique index can be created.
    """
    duplicate_groups = connection.execute(
        """
        SELECT proof_file_id, GROUP_CONCAT(id) AS ids
        FROM deposits
        WHERE status = 'pending' OR status LIKE 'approved:%'
        GROUP BY proof_file_id
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    superseded = 0
    for group in duplicate_groups:
        deposit_ids = [int(part) for part in str(group["ids"]).split(",") if part.strip()]
        if len(deposit_ids) < 2:
            continue
        keep_id = max(deposit_ids)
        for deposit_id in deposit_ids:
            if deposit_id == keep_id:
                continue
            cursor = connection.execute(
                """
                UPDATE deposits
                SET status = 'duplicate_superseded'
                WHERE id = ?
                  AND (status = 'pending' OR status LIKE 'approved:%')
                """,
                (deposit_id,),
            )
            superseded += int(cursor.rowcount or 0)
    return superseded


def _migrate_provider_accounts_display_name(connection: sqlite3.Connection) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(provider_accounts)").fetchall()
    }
    if "display_name" not in columns:
        connection.execute(
            "ALTER TABLE provider_accounts ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
        )
    from services.provider_registry import default_display_name_for_account

    rows = connection.execute(
        """
        SELECT id, account_key, display_name FROM provider_accounts
        WHERE TRIM(COALESCE(display_name, '')) = ''
        """
    ).fetchall()
    for row in rows:
        label = default_display_name_for_account(str(row["account_key"]))
        connection.execute(
            "UPDATE provider_accounts SET display_name = ? WHERE id = ?",
            (label, int(row["id"])),
        )


def _backfill_orders_amount_from_total_price(connection: sqlite3.Connection) -> int:
    """One-time historical backfill: amount=0 rows copy total_price. No-op when none match."""
    needs = connection.execute(
        """
        SELECT 1 FROM orders
        WHERE amount = 0 AND COALESCE(total_price, 0) != 0
        LIMIT 1
        """
    ).fetchone()
    if needs is None:
        return 0
    cursor = connection.execute(
        """
        UPDATE orders
        SET amount = total_price
        WHERE amount = 0 AND COALESCE(total_price, 0) != 0
        """
    )
    return int(cursor.rowcount or 0)


def _backfill_smm_subscription_fulfillment_mode(connection: sqlite3.Connection) -> int:
    """One-time backfill: subscriptions rows still on auto → admin. No-op when none match."""
    needs = connection.execute(
        """
        SELECT 1 FROM smm_services
        WHERE platform_key = 'subscriptions'
          AND COALESCE(fulfillment_mode, 'auto') = 'auto'
        LIMIT 1
        """
    ).fetchone()
    if needs is None:
        return 0
    cursor = connection.execute(
        """
        UPDATE smm_services
        SET fulfillment_mode = 'admin'
        WHERE platform_key = 'subscriptions'
          AND COALESCE(fulfillment_mode, 'auto') = 'auto'
        """
    )
    return int(cursor.rowcount or 0)


def _orders_active_link_index_exists(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'index' AND name = 'idx_orders_active_normalized_link'
        """
    ).fetchone()
    return row is not None


def _orders_active_link_has_conflicts(connection: sqlite3.Connection) -> bool:
    from utils.active_link_guard import active_link_status_sql_predicate

    status_pred = active_link_status_sql_predicate("status")
    row = connection.execute(
        f"""
        SELECT 1
        FROM orders
        WHERE normalized_link IS NOT NULL
          AND TRIM(normalized_link) != ''
          AND {status_pred}
        GROUP BY normalized_link
        HAVING COUNT(*) > 1
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def _orders_active_link_migration_needed(connection: sqlite3.Connection) -> bool:
    """True when column/index is missing or unresolved active-link conflicts remain."""
    order_columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(orders)").fetchall()
    }
    if "normalized_link" not in order_columns:
        return True
    if not _orders_active_link_index_exists(connection):
        return True
    return _orders_active_link_has_conflicts(connection)


def pending_init_db_migrations(*, db_path: Path | None = None) -> list[str]:
    """
    Read-only list of one-time init_db migrations that are not yet applied.

    Empty means no one-time migrations are pending. Harmless ``CREATE IF NOT EXISTS``
    / ``INSERT OR IGNORE`` may still run during ``init_db()``. Not a version registry.
    Does not create backups and is not called automatically by ``init_db()``.
    """
    path = Path(db_path) if db_path is not None else Path(DB_PATH)
    if not path.exists():
        return ["first_run_create_schema"]

    pending: list[str] = []
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "orders" not in tables:
            pending.append("first_run_create_schema")
            return pending

        required_tables = (
            "users",
            "deposits",
            "deposit_transactions",
            "withdrawals",
            "orders",
            "refund_audit_log",
            "pending_notifications",
            "admin_alerts",
            "admin_notifications",
            "pending_referral_level_upgrades",
            "pending_order_funding",
            "smm_services",
            "providers",
            "provider_accounts",
            "timed_announcements",
            "timed_announcement_dismissals",
            "scheduled_message_deletions",
        )
        for table_name in required_tables:
            if table_name not in tables:
                pending.append(f"create_table:{table_name}")

        order_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(orders)").fetchall()
        }
        for col in (
            "service_name",
            "amount",
            "start_count",
            "refunded_amount",
            "referral_payout_done",
            "referral_commission_amount",
            "status_note",
            "api_account",
            "fulfillment_mode",
            "provider_cost_dh",
            "status_changed_at",
            "catalog_id",
            "external_service_id_snapshot",
            "soldium_service_id",
            "normalized_link",
            "provider_slug",
        ):
            if col not in order_columns:
                pending.append(f"orders_add_column:{col}")

        if _orders_active_link_migration_needed(connection):
            pending.append("orders_active_link_guard")

        user_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        for col in (
            "referred_by",
            "partner_status",
            "referral_level",
            "referral_earned_total",
            "referral_balance",
            "telegram_name",
            "living_ui_chat_id",
            "living_ui_message_id",
            "living_ui_has_photo",
        ):
            if col not in user_columns:
                pending.append(f"users_add_column:{col}")

        if "withdrawals" in tables:
            withdrawal_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(withdrawals)").fetchall()
            }
            if "withdrawal_type" not in withdrawal_columns:
                pending.append("withdrawals_add_column:withdrawal_type")

        if "pending_referral_level_upgrades" in tables:
            row = connection.execute(
                """
                SELECT sql FROM sqlite_master
                WHERE type = 'table' AND name = 'pending_referral_level_upgrades'
                """
            ).fetchone()
            ddl = str(row["sql"] or "") if row is not None else ""
            if "AUTOINCREMENT" not in ddl.upper():
                pending.append("pending_referral_level_upgrades_rebuild")

        smm_has_catalog_id = False
        if "smm_services" in tables:
            smm_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(smm_services)").fetchall()
            }
            smm_has_catalog_id = "catalog_id" in smm_columns
            for col in (
                "fulfillment_mode",
                "provider_api_account",
                "provider_price_updated_at",
                "provider_slug",
                "catalog_id",
            ):
                if col not in smm_columns:
                    pending.append(f"smm_services_add_or_rebuild:{col}")
            if "fulfillment_mode" in smm_columns and "platform_key" in smm_columns:
                if connection.execute(
                    """
                    SELECT 1 FROM smm_services
                    WHERE platform_key = 'subscriptions'
                      AND COALESCE(fulfillment_mode, 'auto') = 'auto'
                    LIMIT 1
                    """
                ).fetchone():
                    pending.append("smm_services_subscription_fulfillment_backfill")

        if "timed_announcements" in tables:
            timed_columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(timed_announcements)"
                ).fetchall()
            }
            if "auto_delete_seconds" not in timed_columns:
                pending.append("timed_announcements_add_column:auto_delete_seconds")

        if (
            "amount" in order_columns
            and "total_price" in order_columns
            and connection.execute(
                """
                SELECT 1 FROM orders
                WHERE amount = 0 AND COALESCE(total_price, 0) != 0
                LIMIT 1
                """
            ).fetchone()
        ):
            pending.append("orders_amount_total_price_backfill")

        if "provider_accounts" in tables:
            pacols = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(provider_accounts)"
                ).fetchall()
            }
            if "display_name" not in pacols:
                pending.append("provider_accounts_add_column:display_name")
            elif connection.execute(
                """
                SELECT 1 FROM provider_accounts
                WHERE TRIM(COALESCE(display_name, '')) = ''
                LIMIT 1
                """
            ).fetchone():
                pending.append("provider_accounts_display_name_backfill")

        if "deposits" in tables and connection.execute(
            """
            SELECT 1
            FROM deposits
            WHERE status = 'pending' OR status LIKE 'approved:%'
            GROUP BY proof_file_id
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        ).fetchone():
            pending.append("deposits_active_proof_dedupe")

        index_names = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND name IS NOT NULL"
            ).fetchall()
        }
        if "deposits" in tables and "idx_deposits_active_proof_unique" not in index_names:
            pending.append("create_index:idx_deposits_active_proof_unique")
        # UNIQUE(provider_slug, external_service_id) must be dropped so multiple
        # Soldium/legacy rows may share one provider SKU. Detect table UNIQUE and
        # the named unique index — either means the migration is still pending.
        if smm_has_catalog_id and _smm_provider_external_unique_enforced(connection):
            pending.append("smm_services_drop_provider_external_unique")
    finally:
        connection.close()
    return pending


def _smm_provider_external_unique_enforced(connection: sqlite3.Connection) -> bool:
    """True when smm_services still enforces UNIQUE(provider_slug, external_service_id).

    Covers the table-level UNIQUE (sqlite_autoindex_*) and the named unique index
    ``idx_smm_services_provider_external``.
    """
    try:
        indexes = connection.execute("PRAGMA index_list('smm_services')").fetchall()
    except sqlite3.Error:
        return False
    for idx in indexes:
        # PRAGMA index_list: (seq, name, unique, origin, partial) — Row or tuple
        try:
            is_unique = int(idx["unique"] if hasattr(idx, "keys") else idx[2])
            name = str(idx["name"] if hasattr(idx, "keys") else idx[1] or "")
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        if not is_unique:
            continue
        try:
            cols = [
                str(info["name"] if hasattr(info, "keys") else info[2])
                for info in connection.execute(
                    f"PRAGMA index_info('{name.replace(chr(39), '')}')"
                ).fetchall()
            ]
        except sqlite3.Error:
            continue
        if cols == ["provider_slug", "external_service_id"]:
            return True
    # Fallback: table DDL still declares the UNIQUE clause (before indexes introspected).
    try:
        row = connection.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'smm_services'
            """
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is None:
        return False
    ddl = str(row["sql"] if hasattr(row, "keys") else row[0] or "")
    compact = "".join(ddl.lower().split())
    return "unique(provider_slug,external_service_id)" in compact


_SMM_SERVICES_CATALOG_DDL_NO_PROVIDER_EXTERNAL_UNIQUE = """
CREATE TABLE {table_name} (
    catalog_id TEXT PRIMARY KEY,
    external_service_id TEXT NOT NULL,
    provider_slug TEXT NOT NULL DEFAULT 'gozibra',
    category TEXT NOT NULL DEFAULT '',
    name_ar TEXT NOT NULL DEFAULT '',
    provider_price_usd REAL NOT NULL DEFAULT 0,
    local_price_dh REAL NOT NULL DEFAULT 0,
    min_qty INTEGER NOT NULL DEFAULT 1,
    max_qty INTEGER NOT NULL DEFAULT 1000000,
    is_active INTEGER NOT NULL DEFAULT 1,
    platform_key TEXT NOT NULL DEFAULT '',
    section_key TEXT,
    subsection_key TEXT,
    local_item_id TEXT NOT NULL DEFAULT '',
    platform_title TEXT NOT NULL DEFAULT '',
    section_title TEXT,
    subsection_title TEXT,
    fulfillment_mode TEXT NOT NULL DEFAULT 'auto',
    provider_api_account TEXT,
    provider_price_updated_at TEXT,
    service_id TEXT NOT NULL DEFAULT ''
)
"""


def _recreate_smm_services_nonunique_indexes(connection: sqlite3.Connection) -> None:
    """Recreate smm_services indexes after a table rebuild (no provider/external UNIQUE)."""
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_smm_services_active
        ON smm_services (is_active, platform_key)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_smm_services_fulfillment
        ON smm_services (fulfillment_mode, platform_key)
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_smm_services_provider
        ON smm_services (provider_slug, is_active)
        """
    )
    # Non-unique lookup aid for provider-SKU scans (limits / sync SELECT).
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_smm_services_provider_external
        ON smm_services (provider_slug, external_service_id)
        """
    )


def _migrate_smm_services_drop_provider_external_unique(
    connection: sqlite3.Connection,
) -> None:
    """Remove UNIQUE(provider_slug, external_service_id) from smm_services.

    Idempotent. SQLite cannot DROP a table UNIQUE without recreation, so this
    rebuilds the table when uniqueness is still enforced. Preserves every row
    and column; catalog_id remains the PK.
    """
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(smm_services)").fetchall()
    }
    if "catalog_id" not in columns:
        return
    if not _smm_provider_external_unique_enforced(connection):
        # Ensure the non-unique lookup index exists even when UNIQUE is already gone.
        _recreate_smm_services_nonunique_indexes(connection)
        return

    # Column order for INSERT…SELECT — only columns present on the live table.
    preferred = (
        "catalog_id",
        "external_service_id",
        "provider_slug",
        "category",
        "name_ar",
        "provider_price_usd",
        "local_price_dh",
        "min_qty",
        "max_qty",
        "is_active",
        "platform_key",
        "section_key",
        "subsection_key",
        "local_item_id",
        "platform_title",
        "section_title",
        "subsection_title",
        "fulfillment_mode",
        "provider_api_account",
        "provider_price_updated_at",
        "service_id",
    )
    copy_cols = [c for c in preferred if c in columns]
    extras = [c for c in sorted(columns) if c not in preferred]
    if extras:
        # Unexpected columns: refuse rather than silently drop data.
        raise RuntimeError(
            "smm_services_drop_provider_external_unique: unexpected columns "
            f"{extras}; refusing rebuild"
        )

    col_csv = ", ".join(copy_cols)
    tmp = "smm_services_no_pe_unique"
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute("DROP TABLE IF EXISTS " + tmp)
        connection.execute(
            _SMM_SERVICES_CATALOG_DDL_NO_PROVIDER_EXTERNAL_UNIQUE.format(table_name=tmp)
        )
        connection.execute(
            f"INSERT INTO {tmp} ({col_csv}) SELECT {col_csv} FROM smm_services"
        )
        before = connection.execute("SELECT COUNT(*) FROM smm_services").fetchone()[0]
        after = connection.execute(f"SELECT COUNT(*) FROM {tmp}").fetchone()[0]
        if int(before) != int(after):
            raise RuntimeError(
                f"smm_services unique-drop row count mismatch: before={before} after={after}"
            )
        connection.execute("DROP TABLE smm_services")
        connection.execute(f"ALTER TABLE {tmp} RENAME TO smm_services")
        _recreate_smm_services_nonunique_indexes(connection)
        if _smm_provider_external_unique_enforced(connection):
            raise RuntimeError(
                "smm_services unique-drop failed: UNIQUE(provider_slug, external_service_id) still present"
            )
        connection.commit()
        logger.info(
            "Dropped UNIQUE(provider_slug, external_service_id) on smm_services "
            "(%s rows preserved)",
            after,
        )
    except Exception:
        connection.rollback()
        raise


def _migrate_smm_services_catalog_identity(connection: sqlite3.Connection) -> None:
    """
    يرقّي smm_services إلى هوية مركّبة:
    catalog_id (PK فريد للبوت). Provider SKU (provider_slug, external_service_id)
    may be shared by multiple rows — no UNIQUE on that pair.
    """
    columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(smm_services)").fetchall()
    }
    if "catalog_id" in columns:
        return

    if "external_service_id" not in columns:
        connection.execute(
            "ALTER TABLE smm_services ADD COLUMN external_service_id TEXT NOT NULL DEFAULT ''"
        )
        connection.execute(
            """
            UPDATE smm_services
            SET external_service_id = service_id
            WHERE external_service_id = '' OR external_service_id IS NULL
            """
        )

    # Persist mild ALTER/UPDATE work, then rebuild atomically so an interruption
    # cannot leave smm_services dropped while smm_services_v2 still exists.
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            _SMM_SERVICES_CATALOG_DDL_NO_PROVIDER_EXTERNAL_UNIQUE.format(
                table_name="smm_services_v2"
            )
        )
        connection.execute(
            """
            INSERT INTO smm_services_v2 (
                catalog_id, external_service_id, provider_slug, category, name_ar,
                provider_price_usd, local_price_dh, min_qty, max_qty, is_active,
                platform_key, section_key, subsection_key, local_item_id,
                platform_title, section_title, subsection_title,
                fulfillment_mode, provider_api_account, provider_price_updated_at, service_id
            )
            SELECT
                COALESCE(NULLIF(TRIM(local_item_id), ''), service_id),
                COALESCE(NULLIF(TRIM(external_service_id), ''), service_id),
                COALESCE(NULLIF(TRIM(provider_slug), ''), 'gozibra'),
                category, name_ar, provider_price_usd, local_price_dh,
                min_qty, max_qty, is_active, platform_key, section_key, subsection_key,
                COALESCE(NULLIF(TRIM(local_item_id), ''), service_id),
                platform_title, section_title, subsection_title,
                COALESCE(fulfillment_mode, 'auto'),
                provider_api_account, provider_price_updated_at,
                COALESCE(NULLIF(TRIM(external_service_id), ''), service_id)
            FROM smm_services
            """
        )
        connection.execute("DROP TABLE smm_services")
        connection.execute("ALTER TABLE smm_services_v2 RENAME TO smm_services")
        _recreate_smm_services_nonunique_indexes(connection)
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def init_db() -> None:
    """
    Initialize schema and apply pending one-time migrations.

    Lifecycle for an existing DB:
      integrity preflight → pending migration check → optional WAL-safe backup →
      migrations → final integrity check (when migrations or first-run create ran).

    Fresh installs (missing ``users.db``) skip backup and create the schema normally.

    Cross-process migration coordination (Phase 12): the full critical section runs
    under ``migration_lock`` so bot and dashboard never migrate concurrently.
    """
    from migration_lock import migration_lock

    with migration_lock(db_path=DB_PATH, holder="soldium-bot"):
        _init_db_under_migration_lock()


def _init_db_under_migration_lock() -> None:
    """Migration critical section — caller must hold ``migration_lock`` (or nest)."""
    db_existed = Path(DB_PATH).exists()
    # Fail closed on a corrupted existing DB before any migration/schema writes.
    preflight_db_integrity()

    pending: list[str] = []
    backup_path: Path | None = None
    if db_existed:
        pending = pending_init_db_migrations()
        if pending:
            logger.info(
                "Pending init_db migrations detected (%s); creating pre-migration backup.",
                ", ".join(pending),
            )
            try:
                backup_path = backup_database()
            except DatabaseBackupError:
                logger.error(
                    "Pre-migration backup failed; init_db aborted before applying "
                    "pending migrations: %s",
                    ", ".join(pending),
                )
                raise
            logger.info(
                "Pre-migration backup ready at %s; applying pending migrations: %s",
                backup_path,
                ", ".join(pending),
            )

    try:
        _apply_init_db_schema_and_migrations()
    except Exception:
        if backup_path is not None:
            logger.error(
                "init_db migration failed after successful backup; "
                "backup preserved at %s (no automatic restore).",
                backup_path,
            )
        raise

    # Fresh create or real migrations may have written pages; re-verify.
    # Skip when an existing healthy DB had no pending one-time migrations
    # (preflight already validated; only IF NOT EXISTS / OR IGNORE ran).
    if (not db_existed) or pending:
        preflight_db_integrity()


def _apply_init_db_schema_and_migrations() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance REAL DEFAULT 0.0,
                total_spent REAL DEFAULT 0.0
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                method TEXT NOT NULL,
                proof_file_id TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_deposits_proof_status
            ON deposits(proof_file_id, status)
            """
        )
        if _dedupe_active_deposit_proofs(connection):
            connection.commit()
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_deposits_active_proof_unique
            ON deposits(proof_file_id)
            WHERE status = 'pending' OR status LIKE 'approved:%'
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS deposit_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                deposit_method TEXT NOT NULL,
                amount REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed',
                deposit_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id),
                FOREIGN KEY(deposit_id) REFERENCES deposits(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                method TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                withdrawal_type TEXT NOT NULL DEFAULT 'normal',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
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
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
            """
        )
        # Lightweight migration for older DBs.
        order_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(orders)").fetchall()
        }
        if "service_name" not in order_columns:
            connection.execute("ALTER TABLE orders ADD COLUMN service_name TEXT NOT NULL DEFAULT ''")
        if "amount" not in order_columns:
            connection.execute("ALTER TABLE orders ADD COLUMN amount REAL NOT NULL DEFAULT 0.0")
        if "start_count" not in order_columns:
            connection.execute("ALTER TABLE orders ADD COLUMN start_count INTEGER")
        if "refunded_amount" not in order_columns:
            connection.execute("ALTER TABLE orders ADD COLUMN refunded_amount REAL NOT NULL DEFAULT 0")
        if "referral_payout_done" not in order_columns:
            connection.execute("ALTER TABLE orders ADD COLUMN referral_payout_done INTEGER NOT NULL DEFAULT 0")
        if "referral_commission_amount" not in order_columns:
            connection.execute(
                "ALTER TABLE orders ADD COLUMN referral_commission_amount REAL NOT NULL DEFAULT 0"
            )
        if "status_note" not in order_columns:
            connection.execute("ALTER TABLE orders ADD COLUMN status_note TEXT")
        if "api_account" not in order_columns:
            connection.execute(
                "ALTER TABLE orders ADD COLUMN api_account TEXT NOT NULL DEFAULT 'default'"
            )
        if "fulfillment_mode" not in order_columns:
            connection.execute(
                "ALTER TABLE orders ADD COLUMN fulfillment_mode TEXT NOT NULL DEFAULT 'auto'"
            )
        if "provider_cost_dh" not in order_columns:
            connection.execute(
                "ALTER TABLE orders ADD COLUMN provider_cost_dh REAL NOT NULL DEFAULT 0"
            )
        if "status_changed_at" not in order_columns:
            connection.execute("ALTER TABLE orders ADD COLUMN status_changed_at TEXT")
            connection.execute(
                "UPDATE orders SET status_changed_at = created_at WHERE status_changed_at IS NULL"
            )
        # Phase 8G — Option A execution identity (nullable TEXT; never backfill).
        if "catalog_id" not in order_columns:
            connection.execute("ALTER TABLE orders ADD COLUMN catalog_id TEXT")
        if "external_service_id_snapshot" not in order_columns:
            connection.execute(
                "ALTER TABLE orders ADD COLUMN external_service_id_snapshot TEXT"
            )
        # Catalog SoT — immutable soldium svc_* identity for NEW Catalog orders.
        if "soldium_service_id" not in order_columns:
            connection.execute("ALTER TABLE orders ADD COLUMN soldium_service_id TEXT")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_soldium_service
            ON orders (soldium_service_id)
            """
        )

        _migrate_orders_active_link_guard(connection)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS refund_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                refund_type TEXT NOT NULL,
                refund_amount_dh REAL NOT NULL,
                actual_provider_usd REAL,
                final_customer_price_dh REAL,
                payload_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        user_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        if "referred_by" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        if "partner_status" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN partner_status INTEGER NOT NULL DEFAULT 0")
        if "referral_level" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN referral_level INTEGER NOT NULL DEFAULT 1")
            connection.execute(
                "UPDATE users SET referral_level = 4 WHERE COALESCE(partner_status, 0) = 1"
            )
        if "referral_earned_total" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN referral_earned_total REAL NOT NULL DEFAULT 0")
        if "referral_balance" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN referral_balance REAL NOT NULL DEFAULT 0")
        if "telegram_name" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN telegram_name TEXT")
        if "living_ui_chat_id" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN living_ui_chat_id INTEGER")
        if "living_ui_message_id" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN living_ui_message_id INTEGER")
        if "living_ui_has_photo" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN living_ui_has_photo INTEGER NOT NULL DEFAULT 1"
            )

        withdrawal_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(withdrawals)").fetchall()
        }
        if "withdrawal_type" not in withdrawal_columns:
            connection.execute(
                "ALTER TABLE withdrawals ADD COLUMN withdrawal_type TEXT NOT NULL DEFAULT 'normal'"
            )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_notifications (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, message_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'warning',
                entity_type TEXT NOT NULL,
                entity_id TEXT,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT,
                fingerprint TEXT NOT NULL UNIQUE,
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'open',
                dismissed_at TEXT,
                telegram_notified INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_admin_alerts_status
            ON admin_alerts (status, severity, last_seen_at)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'info',
                title TEXT NOT NULL,
                body_html TEXT NOT NULL DEFAULT '',
                body_plain TEXT NOT NULL DEFAULT '',
                entity_type TEXT,
                entity_id TEXT,
                user_id INTEGER,
                source TEXT NOT NULL DEFAULT 'bot',
                channel TEXT NOT NULL DEFAULT 'telegram',
                telegram_sent INTEGER NOT NULL DEFAULT 0,
                telegram_error TEXT,
                is_read INTEGER NOT NULL DEFAULT 0,
                read_at TEXT,
                payload_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_admin_notifications_created
            ON admin_notifications (created_at DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_admin_notifications_unread
            ON admin_notifications (is_read, created_at DESC)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_admin_notifications_category
            ON admin_notifications (category, created_at DESC)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_referral_level_upgrades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                new_level INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, new_level)
            )
            """
        )
        _migrate_pending_referral_level_upgrades_table(connection)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS smm_services (
                service_id TEXT PRIMARY KEY,
                category TEXT NOT NULL DEFAULT '',
                name_ar TEXT NOT NULL DEFAULT '',
                provider_price_usd REAL NOT NULL DEFAULT 0,
                local_price_dh REAL NOT NULL DEFAULT 0,
                min_qty INTEGER NOT NULL DEFAULT 1,
                max_qty INTEGER NOT NULL DEFAULT 1000000,
                is_active INTEGER NOT NULL DEFAULT 1,
                platform_key TEXT NOT NULL DEFAULT '',
                section_key TEXT,
                subsection_key TEXT,
                local_item_id TEXT NOT NULL DEFAULT '',
                platform_title TEXT NOT NULL DEFAULT '',
                section_title TEXT,
                subsection_title TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_smm_services_active
            ON smm_services (is_active, platform_key)
            """
        )
        smm_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(smm_services)").fetchall()
        }
        if "fulfillment_mode" not in smm_columns:
            connection.execute(
                "ALTER TABLE smm_services ADD COLUMN fulfillment_mode TEXT NOT NULL DEFAULT 'auto'"
            )
        _backfill_smm_subscription_fulfillment_mode(connection)
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_smm_services_fulfillment
            ON smm_services (fulfillment_mode, platform_key)
            """
        )
        if "provider_api_account" not in smm_columns:
            connection.execute(
                "ALTER TABLE smm_services ADD COLUMN provider_api_account TEXT"
            )
        if "provider_price_updated_at" not in smm_columns:
            connection.execute(
                "ALTER TABLE smm_services ADD COLUMN provider_price_updated_at TEXT"
            )
        if "provider_slug" not in smm_columns:
            connection.execute(
                "ALTER TABLE smm_services ADD COLUMN provider_slug TEXT NOT NULL DEFAULT 'gozibra'"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_smm_services_provider
            ON smm_services (provider_slug, is_active)
            """
        )

        from services.provider_registry import seed_default_gozibra_provider

        seed_default_gozibra_provider(connection)

        _migrate_provider_accounts_display_name(connection)

        _migrate_smm_services_catalog_identity(connection)
        _migrate_smm_services_drop_provider_external_unique(connection)

        if "provider_slug" not in order_columns:
            connection.execute(
                "ALTER TABLE orders ADD COLUMN provider_slug TEXT NOT NULL DEFAULT 'gozibra'"
            )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_provider
            ON orders (provider_slug, api_account)
            """
        )

        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_orders_provider_ref
            ON orders (provider_slug, provider_order_id)
            """
        )

        _backfill_orders_amount_from_total_price(connection)

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_order_funding (
                intent_id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL UNIQUE,
                service_id TEXT NOT NULL,
                service_name TEXT NOT NULL,
                platform_key TEXT NOT NULL,
                section_key TEXT,
                subsection_key TEXT,
                link TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                amount_dh REAL NOT NULL,
                auto_quantity INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            )
            """
        )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS timed_announcements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_html TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                launched_at TEXT,
                stopped_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS timed_announcement_dismissals (
                announcement_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                dismissed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (announcement_id, user_id),
                FOREIGN KEY (announcement_id) REFERENCES timed_announcements(id),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_timed_announcements_status_ends
            ON timed_announcements (status, ends_at)
            """
        )

        timed_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(timed_announcements)").fetchall()
        }
        if "auto_delete_seconds" not in timed_columns:
            connection.execute(
                "ALTER TABLE timed_announcements ADD COLUMN auto_delete_seconds INTEGER"
            )

        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduled_message_deletions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                delete_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_scheduled_deletions_delete_at
            ON scheduled_message_deletions (delete_at)
            """
        )

        connection.commit()


def add_user(user_id: int, *, telegram_name: str | None = None) -> None:
    with get_connection() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (user_id,),
        )
        if telegram_name:
            connection.execute(
                "UPDATE users SET telegram_name = ? WHERE user_id = ?",
                (str(telegram_name)[:255], user_id),
            )
        connection.commit()


def upsert_user_on_start(
    user_id: int,
    *,
    telegram_name: str | None,
    referrer_id: int | None,
) -> None:
    """
    يسجّل المستخدم ويحدّث الاسم المعروض؛ يربط المُحيل فقط عند أول ظهور للمستخدم في قاعدة البيانات.
    """
    with get_connection() as connection:
        existed = (
            connection.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
            is not None
        )
        connection.execute(
            "INSERT OR IGNORE INTO users (user_id) VALUES (?)",
            (user_id,),
        )
        if telegram_name:
            connection.execute(
                "UPDATE users SET telegram_name = ? WHERE user_id = ?",
                (str(telegram_name)[:255], user_id),
            )
        if not existed and referrer_id and referrer_id != user_id:
            ref_ok = connection.execute(
                "SELECT 1 FROM users WHERE user_id = ?",
                (referrer_id,),
            ).fetchone()
            if ref_ok:
                connection.execute(
                    """
                    UPDATE users
                    SET referred_by = ?
                    WHERE user_id = ?
                      AND user_id != ?
                      AND referred_by IS NULL
                    """,
                    (referrer_id, user_id, referrer_id),
                )
        connection.commit()


def get_user(user_id: int) -> UserRecord | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT user_id, balance, total_spent, referred_by, referral_level,
                   referral_earned_total, referral_balance, telegram_name
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            return None

        level = int(row["referral_level"] or 1)
        if level < 1:
            level = 1
        if level > 4:
            level = 4

        return {
            "user_id": int(row["user_id"]),
            "balance": float(row["balance"]),
            "total_spent": float(row["total_spent"]),
            "referred_by": int(row["referred_by"]) if row["referred_by"] is not None else None,
            "referral_level": level,
            "referral_earned_total": float(row["referral_earned_total"] or 0.0),
            "referral_balance": float(row["referral_balance"] or 0.0),
            "telegram_name": str(row["telegram_name"]) if row["telegram_name"] else None,
        }


def set_user_living_ui(
    user_id: int,
    *,
    chat_id: int,
    message_id: int,
    has_photo: bool,
) -> None:
    """يحفظ معرّف رسالة الواجهة الحية — لا يُمسح عند state.clear()."""
    add_user(user_id)
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE users
            SET living_ui_chat_id = ?,
                living_ui_message_id = ?,
                living_ui_has_photo = ?
            WHERE user_id = ?
            """,
            (chat_id, message_id, 1 if has_photo else 0, user_id),
        )
        connection.commit()


def get_user_living_ui(user_id: int) -> tuple[int | None, int | None, bool]:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT living_ui_chat_id, living_ui_message_id, living_ui_has_photo
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    if row is None:
        return None, None, True
    chat_id = row["living_ui_chat_id"]
    message_id = row["living_ui_message_id"]
    has_photo = bool(int(row["living_ui_has_photo"] if row["living_ui_has_photo"] is not None else 1))
    return (
        int(chat_id) if chat_id is not None else None,
        int(message_id) if message_id is not None else None,
        has_photo,
    )


def update_balance(user_id: int, amount: float) -> bool:
    amount_money = to_float(amount)
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE users
            SET
                balance = ROUND(balance + ?, 6),
                total_spent = CASE
                    WHEN ? < 0 THEN ROUND(total_spent + ABS(?), 6)
                    ELSE total_spent
                END
            WHERE user_id = ?
              AND (balance + ?) >= 0
            """,
            (amount_money, amount_money, amount_money, user_id, amount_money),
        )
        connection.commit()
        return cursor.rowcount > 0


def transfer_referral_balance_to_main(user_id: int, amount: float) -> bool:
    """تحويل أرباح مؤكدة فقط (referral_balance) — لا يشمل التقدير المعلّق."""
    amount_money = to_float(amount)
    if amount_money <= 0:
        return False
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            """
            UPDATE users
            SET referral_balance = ROUND(COALESCE(referral_balance, 0) - ?, 6),
                balance = ROUND(balance + ?, 6)
            WHERE user_id = ?
              AND COALESCE(referral_balance, 0) >= ?
            """,
            (amount_money, amount_money, user_id, amount_money),
        )
        if cursor.rowcount == 0:
            connection.execute("ROLLBACK")
            return False
        connection.commit()
        return True


def _migrate_orders_active_link_guard(connection: sqlite3.Connection) -> dict[str, object]:
    """Add ``normalized_link`` + partial unique index for active-link protection.

    Existing duplicate active links are preserved: the oldest order (MIN id) keeps
    ``normalized_link``; newer conflicting actives get ``normalized_link`` cleared
    so the unique index can be created without deleting/altering financial rows.
    App-level ``find_active_order_for_link`` still sees leftovers via stored ``link``.

    Fully migrated databases (column + unique index present, no active conflicts)
    are a no-op — the index is not dropped or recreated.
    """
    import logging

    from utils.active_link_guard import (
        active_link_status_sql_predicate,
        normalize_order_link,
    )

    log = logging.getLogger(__name__)
    report: dict[str, object] = {
        "skipped": False,
        "column_added": False,
        "backfilled": 0,
        "conflict_groups": 0,
        "conflict_orders_cleared": 0,
        "index_created": False,
        "conflicts": [],
    }

    if not _orders_active_link_migration_needed(connection):
        report["skipped"] = True
        return report

    # Keep DROP INDEX + backfill + conflict clears + CREATE INDEX atomic so a
    # failed run does not leave the unique index permanently missing.
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        order_columns = {
            str(row["name"]) for row in connection.execute("PRAGMA table_info(orders)").fetchall()
        }
        if "normalized_link" not in order_columns:
            connection.execute("ALTER TABLE orders ADD COLUMN normalized_link TEXT")
            report["column_added"] = True

        # Drop only while performing an incomplete migration: re-running backfill
        # against an existing unique index can fail on leftover NULL duplicates.
        connection.execute("DROP INDEX IF EXISTS idx_orders_active_normalized_link")

        rows = connection.execute(
            "SELECT id, link, status, normalized_link FROM orders"
        ).fetchall()
        backfilled = 0
        for row in rows:
            current = str(row["normalized_link"] or "").strip()
            if current:
                continue
            key = normalize_order_link(row["link"])
            if not key:
                continue
            connection.execute(
                "UPDATE orders SET normalized_link = ? WHERE id = ?",
                (key, int(row["id"])),
            )
            backfilled += 1
        report["backfilled"] = backfilled

        status_pred = active_link_status_sql_predicate("status")
        conflict_rows = connection.execute(
            f"""
            SELECT normalized_link, GROUP_CONCAT(id) AS ids, COUNT(*) AS cnt
            FROM orders
            WHERE normalized_link IS NOT NULL
              AND TRIM(normalized_link) != ''
              AND {status_pred}
            GROUP BY normalized_link
            HAVING COUNT(*) > 1
            """
        ).fetchall()

        conflicts: list[dict[str, object]] = []
        cleared = 0
        for group in conflict_rows:
            key = str(group["normalized_link"])
            ids = sorted(
                int(part) for part in str(group["ids"] or "").split(",") if part.strip().isdigit()
            )
            if len(ids) < 2:
                continue
            keep_id = ids[0]
            drop_ids = ids[1:]
            conflicts.append(
                {
                    "normalized_link": key,
                    "keep_order_id": keep_id,
                    "cleared_order_ids": drop_ids,
                }
            )
            for oid in drop_ids:
                connection.execute(
                    "UPDATE orders SET normalized_link = NULL WHERE id = ?",
                    (oid,),
                )
                cleared += 1
                log.warning(
                    "active_link_guard migration: cleared normalized_link on duplicate "
                    "active order id=%s (kept id=%s key=%r)",
                    oid,
                    keep_id,
                    key,
                )
        report["conflict_groups"] = len(conflicts)
        report["conflict_orders_cleared"] = cleared
        report["conflicts"] = conflicts
        if conflicts:
            log.warning(
                "active_link_guard migration: %s conflict group(s), cleared normalized_link "
                "on %s newer duplicate active order(s); all order rows preserved",
                len(conflicts),
                cleared,
            )

        # Re-verify no active duplicate keys remain before creating the index.
        leftover = connection.execute(
            f"""
            SELECT normalized_link, COUNT(*) AS cnt
            FROM orders
            WHERE normalized_link IS NOT NULL
              AND TRIM(normalized_link) != ''
              AND {status_pred}
            GROUP BY normalized_link
            HAVING COUNT(*) > 1
            """
        ).fetchall()
        if leftover:
            details = ", ".join(
                f"{r['normalized_link']!r}×{r['cnt']}" for r in leftover
            )
            raise RuntimeError(
                "active_link_guard migration aborted: unresolved active-link conflicts "
                f"remain ({details}). No unique index created; financial data untouched."
            )

        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_active_normalized_link
            ON orders (normalized_link)
            WHERE normalized_link IS NOT NULL
              AND TRIM(normalized_link) != ''
              AND LOWER(REPLACE(status, '_', ' ')) IN (
                  'pending', 'pending admin', 'submitted', 'in progress', 'processing'
              )
            """
        )
        report["index_created"] = True
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return report


def create_order_with_balance_hold(
    user_id: int,
    service_name: str,
    service_id: str,
    link: str,
    quantity: int,
    amount: float,
    *,
    api_account: str = "default",
    provider_slug: str | None = None,
    initial_status: str = "pending",
    fulfillment_mode: str = "auto",
    provider_cost_dh: float = 0.0,
    catalog_id: str | None = None,
    external_service_id_snapshot: str | None = None,
    soldium_service_id: str | None = None,
) -> int | None:
    """Create order and debit balance atomically.

    Returns ``None`` when the user/balance is insufficient.
    Raises ``ActiveLinkOccupiedError`` when the target link is already occupied
    by an active order (no debit, no row).
    """
    from utils.active_link_guard import (
        ActiveLinkOccupiedError,
        find_active_order_for_link,
        is_active_link_order_status,
        normalize_order_link,
    )

    amount_money = to_float(amount)
    stored_link = str(link or "")
    normalized = normalize_order_link(stored_link)
    # Empty / blank targets must not enter the unique occupancy index.
    normalized_for_db = normalized or None

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        user_row = connection.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if user_row is None:
            connection.rollback()
            return None

        balance = float(user_row["balance"])
        if balance < amount_money:
            connection.rollback()
            return None

        status = str(initial_status or "pending").strip() or "pending"
        if normalized_for_db and is_active_link_order_status(status):
            occupied = find_active_order_for_link(connection, stored_link)
            if occupied is not None:
                connection.rollback()
                raise ActiveLinkOccupiedError(
                    existing_order_id=int(occupied["id"]),
                    normalized_link=normalized_for_db,
                )

        balance_cursor = connection.execute(
            """
            UPDATE users
            SET
                balance = ROUND(balance - ?, 6),
                total_spent = ROUND(total_spent + ?, 6)
            WHERE user_id = ? AND balance >= ?
            """,
            (amount_money, amount_money, user_id, amount_money),
        )
        if balance_cursor.rowcount == 0:
            connection.rollback()
            return None

        account = str(api_account or "default").strip() or "default"
        slug = str(provider_slug or "").strip().lower()
        if not slug:
            try:
                from services.provider_registry import get_default_provider_slug

                slug = get_default_provider_slug()
            except Exception:
                slug = "gozibra"
        mode = str(fulfillment_mode or "auto").strip().lower() or "auto"
        if mode not in {"auto", "admin"}:
            mode = "auto"
        legacy_catalog_id = str(catalog_id or "").strip() or None
        external_snap = str(external_service_id_snapshot or "").strip() or None
        soldium_id = str(soldium_service_id or "").strip() or None
        order_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(orders)").fetchall()
        }
        has_soldium = "soldium_service_id" in order_columns
        try:
            if has_soldium:
                order_cursor = connection.execute(
                    """
                    INSERT INTO orders (
                        user_id, service_name, service_id, link, quantity, amount, total_price, status,
                        api_account, provider_slug, fulfillment_mode, provider_cost_dh,
                        catalog_id, external_service_id_snapshot, soldium_service_id, normalized_link
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        service_name,
                        service_id,
                        stored_link,
                        quantity,
                        amount_money,
                        amount_money,
                        status,
                        account,
                        slug,
                        mode,
                        round(to_float(provider_cost_dh), 6),
                        legacy_catalog_id,
                        external_snap,
                        soldium_id,
                        normalized_for_db,
                    ),
                )
            else:
                order_cursor = connection.execute(
                    """
                    INSERT INTO orders (
                        user_id, service_name, service_id, link, quantity, amount, total_price, status,
                        api_account, provider_slug, fulfillment_mode, provider_cost_dh,
                        catalog_id, external_service_id_snapshot, normalized_link
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        service_name,
                        service_id,
                        stored_link,
                        quantity,
                        amount_money,
                        amount_money,
                        status,
                        account,
                        slug,
                        mode,
                        round(to_float(provider_cost_dh), 6),
                        legacy_catalog_id,
                        external_snap,
                        normalized_for_db,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            from utils.active_link_guard import is_active_link_unique_violation

            if normalized_for_db and is_active_link_unique_violation(exc):
                occupied = find_active_order_for_link(connection, stored_link)
                raise ActiveLinkOccupiedError(
                    existing_order_id=(
                        int(occupied["id"]) if occupied is not None else None
                    ),
                    normalized_link=normalized_for_db,
                ) from exc
            raise
        connection.commit()
        return int(order_cursor.lastrowid)


def _pending_order_funding_from_row(row: sqlite3.Row) -> PendingOrderFundingRecord:
    section = row["section_key"]
    subsection = row["subsection_key"]
    return {
        "intent_id": int(row["intent_id"]),
        "user_id": int(row["user_id"]),
        "service_id": str(row["service_id"]),
        "service_name": str(row["service_name"]),
        "platform_key": str(row["platform_key"]),
        "section_key": str(section) if section is not None and str(section) != "" else None,
        "subsection_key": (
            str(subsection) if subsection is not None and str(subsection) != "" else None
        ),
        "link": str(row["link"] or ""),
        "quantity": int(row["quantity"]),
        "amount_dh": float(row["amount_dh"]),
        "auto_quantity": int(row["auto_quantity"] or 0),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def upsert_pending_order_funding(
    user_id: int,
    *,
    service_id: str,
    service_name: str,
    platform_key: str,
    section_key: str | None,
    subsection_key: str | None,
    link: str,
    quantity: int,
    amount_dh: float,
    auto_quantity: bool,
) -> int:
    """Replace any existing pending intent for ``user_id`` with a NEW ``intent_id``."""
    amount_money = to_float(amount_dh)
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "DELETE FROM pending_order_funding WHERE user_id = ?",
            (user_id,),
        )
        cursor = connection.execute(
            """
            INSERT INTO pending_order_funding (
                user_id, service_id, service_name, platform_key, section_key, subsection_key,
                link, quantity, amount_dh, auto_quantity, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                user_id,
                str(service_id),
                str(service_name),
                str(platform_key or ""),
                (str(section_key) if section_key else None),
                (str(subsection_key) if subsection_key else None),
                str(link or ""),
                int(quantity),
                amount_money,
                1 if auto_quantity else 0,
            ),
        )
        connection.commit()
        return int(cursor.lastrowid)


def get_pending_order_funding(user_id: int) -> PendingOrderFundingRecord | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT intent_id, user_id, service_id, service_name, platform_key, section_key,
                   subsection_key, link, quantity, amount_dh, auto_quantity, created_at, updated_at
            FROM pending_order_funding
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
    if row is None:
        return None
    return _pending_order_funding_from_row(row)


def get_pending_order_funding_by_intent(
    intent_id: int,
    user_id: int,
) -> PendingOrderFundingRecord | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT intent_id, user_id, service_id, service_name, platform_key, section_key,
                   subsection_key, link, quantity, amount_dh, auto_quantity, created_at, updated_at
            FROM pending_order_funding
            WHERE intent_id = ? AND user_id = ?
            """,
            (int(intent_id), int(user_id)),
        ).fetchone()
    if row is None:
        return None
    return _pending_order_funding_from_row(row)


def delete_pending_order_funding(user_id: int) -> bool:
    """Delete the current pending intent for ``user_id`` (if any)."""
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            "DELETE FROM pending_order_funding WHERE user_id = ?",
            (user_id,),
        )
        connection.commit()
        return cursor.rowcount > 0


def delete_pending_order_funding_by_intent(intent_id: int, user_id: int) -> bool:
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        cursor = connection.execute(
            "DELETE FROM pending_order_funding WHERE intent_id = ? AND user_id = ?",
            (int(intent_id), int(user_id)),
        )
        connection.commit()
        return cursor.rowcount > 0


def create_order_with_balance_hold_consuming_funding_intent(
    intent_id: int,
    user_id: int,
    service_name: str,
    service_id: str,
    link: str,
    quantity: int,
    amount: float,
    *,
    api_account: str = "default",
    provider_slug: str | None = None,
    initial_status: str = "pending",
    fulfillment_mode: str = "auto",
    provider_cost_dh: float = 0.0,
    catalog_id: str | None = None,
    external_service_id_snapshot: str | None = None,
    soldium_service_id: str | None = None,
) -> int | None:
    """Atomic: verify intent snapshot → debit → insert order → delete SAME intent.

    Returns ``None`` when intent missing/mismatched or balance insufficient.
    Raises ``ActiveLinkOccupiedError`` on active-link conflict (intent preserved).
    """
    from utils.active_link_guard import (
        ActiveLinkOccupiedError,
        find_active_order_for_link,
        is_active_link_order_status,
        normalize_order_link,
    )

    amount_money = to_float(amount)
    stored_link = str(link or "")
    normalized = normalize_order_link(stored_link)
    normalized_for_db = normalized or None
    charge_service_id = str(service_id)
    charge_quantity = int(quantity)

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        intent_row = connection.execute(
            """
            SELECT intent_id, user_id, service_id, link, quantity, amount_dh
            FROM pending_order_funding
            WHERE intent_id = ? AND user_id = ?
            """,
            (int(intent_id), int(user_id)),
        ).fetchone()
        if intent_row is None:
            connection.rollback()
            return None

        # Bind charge payload to the durable snapshot (same TX).
        intent_link = str(intent_row["link"] or "")
        if (
            str(intent_row["service_id"]) != charge_service_id
            or int(intent_row["quantity"]) != charge_quantity
            or to_decimal(intent_row["amount_dh"]) != to_decimal(amount_money)
            or intent_link.strip() != stored_link.strip()
        ):
            connection.rollback()
            return None

        user_row = connection.execute(
            "SELECT balance FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if user_row is None:
            connection.rollback()
            return None

        balance = float(user_row["balance"])
        if balance < amount_money:
            connection.rollback()
            return None

        status = str(initial_status or "pending").strip() or "pending"
        if normalized_for_db and is_active_link_order_status(status):
            occupied = find_active_order_for_link(connection, stored_link)
            if occupied is not None:
                connection.rollback()
                raise ActiveLinkOccupiedError(
                    existing_order_id=int(occupied["id"]),
                    normalized_link=normalized_for_db,
                )

        balance_cursor = connection.execute(
            """
            UPDATE users
            SET
                balance = ROUND(balance - ?, 6),
                total_spent = ROUND(total_spent + ?, 6)
            WHERE user_id = ? AND balance >= ?
            """,
            (amount_money, amount_money, user_id, amount_money),
        )
        if balance_cursor.rowcount == 0:
            connection.rollback()
            return None

        account = str(api_account or "default").strip() or "default"
        slug = str(provider_slug or "").strip().lower()
        if not slug:
            try:
                from services.provider_registry import get_default_provider_slug

                slug = get_default_provider_slug()
            except Exception:
                slug = "gozibra"
        mode = str(fulfillment_mode or "auto").strip().lower() or "auto"
        if mode not in {"auto", "admin"}:
            mode = "auto"
        legacy_catalog_id = str(catalog_id or "").strip() or None
        external_snap = str(external_service_id_snapshot or "").strip() or None
        soldium_id = str(soldium_service_id or "").strip() or None
        order_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(orders)").fetchall()
        }
        has_soldium = "soldium_service_id" in order_columns
        try:
            if has_soldium:
                order_cursor = connection.execute(
                    """
                    INSERT INTO orders (
                        user_id, service_name, service_id, link, quantity, amount, total_price, status,
                        api_account, provider_slug, fulfillment_mode, provider_cost_dh,
                        catalog_id, external_service_id_snapshot, soldium_service_id, normalized_link
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        service_name,
                        service_id,
                        stored_link,
                        quantity,
                        amount_money,
                        amount_money,
                        status,
                        account,
                        slug,
                        mode,
                        round(to_float(provider_cost_dh), 6),
                        legacy_catalog_id,
                        external_snap,
                        soldium_id,
                        normalized_for_db,
                    ),
                )
            else:
                order_cursor = connection.execute(
                    """
                    INSERT INTO orders (
                        user_id, service_name, service_id, link, quantity, amount, total_price, status,
                        api_account, provider_slug, fulfillment_mode, provider_cost_dh,
                        catalog_id, external_service_id_snapshot, normalized_link
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        service_name,
                        service_id,
                        stored_link,
                        quantity,
                        amount_money,
                        amount_money,
                        status,
                        account,
                        slug,
                        mode,
                        round(to_float(provider_cost_dh), 6),
                        legacy_catalog_id,
                        external_snap,
                        normalized_for_db,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            from utils.active_link_guard import is_active_link_unique_violation

            if normalized_for_db and is_active_link_unique_violation(exc):
                occupied = find_active_order_for_link(connection, stored_link)
                raise ActiveLinkOccupiedError(
                    existing_order_id=(
                        int(occupied["id"]) if occupied is not None else None
                    ),
                    normalized_link=normalized_for_db,
                ) from exc
            raise

        del_cursor = connection.execute(
            "DELETE FROM pending_order_funding WHERE intent_id = ? AND user_id = ?",
            (int(intent_id), int(user_id)),
        )
        if del_cursor.rowcount == 0:
            connection.rollback()
            return None

        connection.commit()
        return int(order_cursor.lastrowid)


def _is_execution_status(status: str) -> bool:
    """قيد التنفيذ — يُحدَّث معها عداد البداية عند توفره."""
    s = str(status or "").strip().lower().replace("_", " ")
    return s in {"in progress", "processing"}


def _migrate_pending_referral_level_upgrades_table(connection: sqlite3.Connection) -> None:
    """ترقية الجدول القديم (مفتاح user_id فقط) لدعم عدة إشعارات ترقية."""
    row = connection.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table' AND name = 'pending_referral_level_upgrades'
        """
    ).fetchone()
    if row is None:
        return
    ddl = str(row["sql"] or "")
    if "AUTOINCREMENT" in ddl.upper():
        return
    # Atomic rebuild: avoid leaving *_new without the live table name.
    connection.commit()
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_referral_level_upgrades_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                new_level INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, new_level)
            )
            """
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO pending_referral_level_upgrades_new (user_id, new_level, created_at)
            SELECT user_id, new_level, created_at FROM pending_referral_level_upgrades
            """
        )
        connection.execute("DROP TABLE pending_referral_level_upgrades")
        connection.execute(
            "ALTER TABLE pending_referral_level_upgrades_new "
            "RENAME TO pending_referral_level_upgrades"
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise


def count_active_referred_users(referrer_id: int, *, connection: sqlite3.Connection | None = None) -> int:
    """
    مدعوون نشطون: referred_by = referrer ولديهم طلب واحد على الأقل
    بحالة completed أو partial.
    """
    sql = """
        SELECT COUNT(DISTINCT u.user_id) AS c
        FROM users u
        WHERE u.referred_by = ?
          AND EXISTS (
              SELECT 1
              FROM orders o
              WHERE o.user_id = u.user_id
                AND LOWER(REPLACE(o.status, '_', ' ')) IN ('completed', 'partial')
          )
    """
    if connection is not None:
        row = connection.execute(sql, (referrer_id,)).fetchone()
        return int(row["c"]) if row else 0
    with get_connection() as conn:
        row = conn.execute(sql, (referrer_id,)).fetchone()
        return int(row["c"]) if row else 0


def enqueue_referral_level_upgrade(user_id: int, new_level: int) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO pending_referral_level_upgrades (user_id, new_level)
            VALUES (?, ?)
            """,
            (user_id, int(new_level)),
        )
        connection.commit()


def enqueue_referral_level_upgrades(user_id: int, new_levels: list[int]) -> None:
    for level in new_levels:
        enqueue_referral_level_upgrade(user_id, level)


def pop_all_pending_referral_level_upgrades() -> list[tuple[int, int]]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT user_id, new_level
            FROM pending_referral_level_upgrades
            ORDER BY new_level ASC, created_at ASC
            """
        ).fetchall()
        if rows:
            connection.execute("DELETE FROM pending_referral_level_upgrades")
            connection.commit()
    return [(int(r["user_id"]), int(r["new_level"])) for r in rows]


def _check_and_upgrade_referral_level_in_tx(
    connection: sqlite3.Connection,
    user_id: int,
) -> list[int]:
    from services.referral import MAX_REFERRAL_LEVEL, meets_next_level_requirements

    upgraded_levels: list[int] = []
    while True:
        row = connection.execute(
            """
            SELECT referral_level, COALESCE(referral_earned_total, 0) AS referral_earned_total
            FROM users
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        if row is None:
            break
        current_level = int(row["referral_level"] or 1)
        if current_level < 1:
            current_level = 1
        if current_level >= MAX_REFERRAL_LEVEL:
            break
        active_users = count_active_referred_users(user_id, connection=connection)
        earnings = float(row["referral_earned_total"] or 0.0)
        if not meets_next_level_requirements(
            current_level,
            active_users=active_users,
            earnings=earnings,
        ):
            break
        new_level = current_level + 1
        connection.execute(
            "UPDATE users SET referral_level = ? WHERE user_id = ?",
            (new_level, user_id),
        )
        upgraded_levels.append(new_level)
    return upgraded_levels


def check_and_upgrade_referral_level(user_id: int) -> int | None:
    """
    يفحص شروط المستوى التالي ويرقّي عند استيفائها (قد يتخطى أكثر من مستوى دفعة واحدة).
    يُستدعى فقط بعد دفع عمولة إحالة ناجحة.
    """
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        new_levels = _check_and_upgrade_referral_level_in_tx(connection, user_id)
        connection.commit()
    if new_levels:
        enqueue_referral_level_upgrades(user_id, new_levels)
    return new_levels[-1] if new_levels else None


def try_apply_referral_payout_for_order(order_id: int) -> None:
    """
    يضيف عمولة الإحالة إلى referral_balance (أرباح مؤكدة) عند حالة نهائية فقط:
    completed أو partial. العمولة = صافي الإنفاق × نسبة المُحيل لحظة الدفع.
    يُنفَّذ مرة واحدة لكل طلب (referral_payout_done). التقدير المعلّق لا يمر من هنا.
    """
    from services.referral import compute_commission, net_spent_amount

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT o.id, o.amount, o.status,
                   COALESCE(o.refunded_amount, 0) AS refunded_amount,
                   COALESCE(o.referral_payout_done, 0) AS referral_payout_done,
                   u.referred_by AS referred_by
            FROM orders o
            JOIN users u ON u.user_id = o.user_id
            WHERE o.id = ?
            """,
            (order_id,),
        ).fetchone()
        if row is None:
            connection.rollback()
            return
        if int(row["referral_payout_done"] or 0):
            connection.commit()
            return

        status_key = normalize_order_status_key(row["status"])
        if status_key not in {"completed", "partial"}:
            connection.rollback()
            return

        net = net_spent_amount(float(row["amount"]), float(row["refunded_amount"] or 0))
        referred_by = row["referred_by"]

        if net <= 0:
            connection.execute(
                """
                UPDATE orders
                SET referral_payout_done = 1,
                    referral_commission_amount = 0
                WHERE id = ?
                """,
                (order_id,),
            )
            connection.commit()
            return

        if referred_by is None:
            connection.execute(
                """
                UPDATE orders
                SET referral_payout_done = 1,
                    referral_commission_amount = 0
                WHERE id = ?
                """,
                (order_id,),
            )
            connection.commit()
            return

        ref = connection.execute(
            "SELECT referral_level FROM users WHERE user_id = ?",
            (int(referred_by),),
        ).fetchone()
        if ref is None:
            connection.execute(
                """
                UPDATE orders
                SET referral_payout_done = 1,
                    referral_commission_amount = 0
                WHERE id = ?
                """,
                (order_id,),
            )
            connection.commit()
            return

        referrer_level = int(ref["referral_level"] or 1)
        if referrer_level < 1:
            referrer_level = 1
        commission = compute_commission(net, referrer_level)
        if commission <= 0:
            connection.execute(
                """
                UPDATE orders
                SET referral_payout_done = 1,
                    referral_commission_amount = 0
                WHERE id = ?
                """,
                (order_id,),
            )
            connection.commit()
            return

        referrer_id = int(referred_by)
        connection.execute(
            """
            UPDATE users
            SET referral_balance = ROUND(COALESCE(referral_balance, 0) + ?, 6),
                referral_earned_total = ROUND(COALESCE(referral_earned_total, 0) + ?, 6)
            WHERE user_id = ?
            """,
            (commission, commission, referrer_id),
        )
        connection.execute(
            """
            UPDATE orders
            SET referral_payout_done = 1,
                referral_commission_amount = ?
            WHERE id = ?
            """,
            (commission, order_id),
        )
        new_levels = _check_and_upgrade_referral_level_in_tx(connection, referrer_id)
        connection.commit()
        if new_levels:
            enqueue_referral_level_upgrades(referrer_id, new_levels)


def update_order_status(order_id: int, status: str, *, start_count: int | None = None) -> bool:
    """
    تحديث حالة الطلب. عند الانتقال إلى «قيد التنفيذ» يمكن تمرير start_count
    (مستخرج من واجهة الحالة) ليُخزَّن مع الطلب.
    """
    with get_connection() as connection:
        row = connection.execute(
            "SELECT status FROM orders WHERE id = ?",
            (order_id,),
        ).fetchone()
        if row is None:
            return False
        status_changed = normalize_order_status_key(str(row["status"])) != normalize_order_status_key(
            status
        )
        ts_clause = ", status_changed_at = CURRENT_TIMESTAMP" if status_changed else ""
        if _is_execution_status(status) and start_count is not None:
            cursor = connection.execute(
                f"UPDATE orders SET status = ?, start_count = ?{ts_clause} WHERE id = ?",
                (status, int(start_count), order_id),
            )
        else:
            cursor = connection.execute(
                f"UPDATE orders SET status = ?{ts_clause} WHERE id = ?",
                (status, order_id),
            )
        connection.commit()
        ok = cursor.rowcount > 0

    if ok:
        st = normalize_order_status_key(status)
        # partial: عمولة فقط عبر apply_partial_or_full_refund أو set_order_status_by_admin
        if st == "completed":
            try_apply_referral_payout_for_order(order_id)
    return ok


def set_provider_order_id(order_id: int, provider_order_id: str) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE orders
            SET provider_order_id = ?,
                status = 'submitted',
                status_changed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (provider_order_id, order_id),
        )
        connection.commit()
        return cursor.rowcount > 0


def assign_provider_order_id(order_id: int, provider_order_id: str) -> bool:
    """Assign distributor order ref without changing order status."""
    ref = str(provider_order_id or "").strip()
    if not ref:
        return False
    with get_connection() as connection:
        cursor = connection.execute(
            """
            UPDATE orders
            SET provider_order_id = ?
            WHERE id = ?
            """,
            (ref, order_id),
        )
        connection.commit()
        return cursor.rowcount > 0


def get_order_id_by_provider_ref(
    provider_ref: str,
    *,
    provider_slug: str | None = None,
) -> int | None:
    ref = str(provider_ref or "").strip().lstrip("#")
    if not ref:
        return None
    slug = str(provider_slug or "").strip().lower() or None
    with get_connection() as connection:
        if slug:
            row = connection.execute(
                """
                SELECT id FROM orders
                WHERE provider_slug = ? AND provider_order_id = ?
                """,
                (slug, ref),
            ).fetchone()
            return int(row["id"]) if row else None
        rows = connection.execute(
            "SELECT id, provider_slug FROM orders WHERE provider_order_id = ?",
            (ref,),
        ).fetchall()
    if not rows:
        return None
    if len(rows) == 1:
        return int(rows[0]["id"])
    return None


def refund_order(order_id: int) -> bool:
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT user_id, amount, COALESCE(refunded_amount, 0) AS refunded_amount
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False

        order_amount = float(row["amount"])
        refunded_already = float(row["refunded_amount"])
        refund_due = max(0.0, order_amount - refunded_already)

        order_cursor = connection.execute(
            """
            UPDATE orders
            SET status = 'failed',
                refunded_amount = ROUND(COALESCE(amount, 0), 6),
                status_changed_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND LOWER(REPLACE(status, '_', ' ')) NOT IN (
                  'failed', 'canceled', 'refunded', 'partial', 'completed'
              )
            """,
            (order_id,),
        )
        if order_cursor.rowcount == 0:
            connection.rollback()
            return False

        _credit_user_order_refund(connection, int(row["user_id"]), refund_due)
        connection.commit()
        return True


def get_user_orders(user_id: int, limit: int = 10) -> list[OrderRecord]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, user_id, service_name, service_id, link, quantity, amount, status, provider_order_id,
                   start_count, created_at, status_note, api_account, provider_slug, provider_cost_dh
            FROM orders
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [_order_row_to_record(row) for row in rows]


def search_user_orders(user_id: int, query: str) -> list[OrderRecord]:
    needle = str(query or "").strip()
    if not needle:
        return []
    normalized_ref = needle[1:] if needle.startswith("#") else needle
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, user_id, service_name, service_id, link, quantity, amount, status, provider_order_id,
                   start_count, created_at, status_note, api_account, provider_slug, provider_cost_dh
            FROM orders
            WHERE user_id = ?
              AND (
                link = ?
                OR LOWER(link) = LOWER(?)
                OR provider_order_id = ?
              )
            ORDER BY id DESC
            """,
            (user_id, needle, needle, normalized_ref),
        ).fetchall()
        return [_order_row_to_record(row) for row in rows]


def get_last_orders(limit: int = 10) -> list[OrderRecord]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, user_id, service_name, service_id, link, quantity, amount, status, provider_order_id,
                   start_count, created_at, status_note, api_account, provider_slug, provider_cost_dh
            FROM orders
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_order_row_to_record(row) for row in rows]


_ORDER_STATUSES_NO_ADMIN_OVERRIDE = frozenset({"failed", "canceled", "refunded"})
_ORDER_STATUSES_NO_FURTHER_REFUND = frozenset(
    {"failed", "canceled", "partial", "refunded", "completed"}
)


def _reverse_referral_payout_in_tx(connection: sqlite3.Connection, order_id: int) -> None:
    """عكس عمولة مؤكدة سُجلت على الطلب (داخل معاملة مفتوحة)."""
    row = connection.execute(
        """
        SELECT COALESCE(o.referral_payout_done, 0) AS referral_payout_done,
               COALESCE(o.referral_commission_amount, 0) AS referral_commission_amount,
               u.referred_by AS referred_by
        FROM orders o
        JOIN users u ON u.user_id = o.user_id
        WHERE o.id = ?
        """,
        (order_id,),
    ).fetchone()
    if row is None or not int(row["referral_payout_done"] or 0):
        return
    commission = to_float(row["referral_commission_amount"])
    referred_by = row["referred_by"]
    connection.execute(
        """
        UPDATE orders
        SET referral_payout_done = 0,
            referral_commission_amount = 0
        WHERE id = ?
        """,
        (order_id,),
    )
    if referred_by is not None and commission > 0:
        connection.execute(
            """
            UPDATE users
            SET referral_balance = ROUND(
                    CASE
                        WHEN COALESCE(referral_balance, 0) >= ? THEN COALESCE(referral_balance, 0) - ?
                        ELSE 0
                    END,
                    6
                ),
                referral_earned_total = CASE
                    WHEN COALESCE(referral_earned_total, 0) >= ? THEN ROUND(referral_earned_total - ?, 6)
                    ELSE 0
                END
            WHERE user_id = ?
            """,
            (commission, commission, commission, commission, int(referred_by)),
        )


def try_reverse_referral_payout_for_order(order_id: int) -> None:
    """يخصم عمولة إحالة مؤكدة سُددت سابقاً (مسار منفصل خارج معاملة الأدمن)."""
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        _reverse_referral_payout_in_tx(connection, order_id)
        connection.commit()


def _credit_user_order_refund(
    connection: sqlite3.Connection,
    user_id: int,
    refund_amount: float,
) -> None:
    amount_money = to_float(refund_amount)
    if amount_money <= 0:
        return
    connection.execute(
        """
        UPDATE users
        SET
            balance = ROUND(balance + ?, 6),
            total_spent = CASE
                WHEN total_spent >= ? THEN ROUND(total_spent - ?, 6)
                ELSE 0
            END
        WHERE user_id = ?
        """,
        (amount_money, amount_money, amount_money, user_id),
    )


def set_order_status_by_admin(order_id: int, status: str) -> bool:
    """
    تعديل حالة الطلب من الأدمن مع حركة مالية آمنة:
    failed/canceled → استرداد المتبقي؛ completed → من حالات غير نهائية + عمولة إحالة.
    """
    new_status = str(status or "").strip()
    if not new_status:
        return False
    new_key = normalize_order_status_key(new_status)
    stored_status = new_status.lower().replace("_", " ")

    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT user_id, amount, status,
                   COALESCE(refunded_amount, 0) AS refunded_amount,
                   COALESCE(referral_payout_done, 0) AS referral_payout_done
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False

        current_key = normalize_order_status_key(str(row["status"]))
        if new_key == current_key:
            connection.commit()
            return True

        if current_key in _ORDER_STATUSES_NO_ADMIN_OVERRIDE:
            connection.rollback()
            return False

        order_amount = float(row["amount"])
        refunded_already = float(row["refunded_amount"])
        user_id = int(row["user_id"])
        had_referral_payout = bool(int(row["referral_payout_done"] or 0))

        if new_key in {"failed", "canceled", "refunded"}:
            refund_due = max(0.0, order_amount - refunded_already)
            order_cursor = connection.execute(
                """
                UPDATE orders
                SET status = ?,
                    refunded_amount = ROUND(COALESCE(amount, 0), 6),
                    status_changed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND LOWER(REPLACE(status, '_', ' ')) NOT IN ('failed', 'canceled', 'refunded')
                """,
                (stored_status, order_id),
            )
            if order_cursor.rowcount == 0:
                connection.rollback()
                return False
            _credit_user_order_refund(connection, user_id, refund_due)
            if had_referral_payout:
                _reverse_referral_payout_in_tx(connection, order_id)
            connection.commit()
            return True

        if new_key == "partial":
            if current_key in {"failed", "canceled", "refunded", "completed"}:
                connection.rollback()
                return False
            if refunded_already <= 0 and current_key != "partial":
                connection.rollback()
                return False
            order_cursor = connection.execute(
                """
                UPDATE orders
                SET status = ?,
                    status_changed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND LOWER(REPLACE(status, '_', ' ')) NOT IN ('failed', 'canceled', 'refunded')
                """,
                (stored_status, order_id),
            )
            if order_cursor.rowcount == 0:
                connection.rollback()
                return False
            connection.commit()
            try_apply_referral_payout_for_order(order_id)
            return True

        if new_key == "completed":
            if current_key in {"failed", "canceled", "refunded", "completed"}:
                connection.rollback()
                return False
            order_cursor = connection.execute(
                """
                UPDATE orders
                SET status = ?,
                    status_changed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND LOWER(REPLACE(status, '_', ' ')) NOT IN (
                      'failed', 'canceled', 'refunded', 'completed'
                  )
                """,
                (stored_status, order_id),
            )
            if order_cursor.rowcount == 0:
                connection.rollback()
                return False
            connection.commit()
            try_apply_referral_payout_for_order(order_id)
            return True

        order_cursor = connection.execute(
            """
            UPDATE orders
            SET status = ?,
                status_changed_at = CURRENT_TIMESTAMP
            WHERE id = ?
              AND LOWER(REPLACE(status, '_', ' ')) NOT IN ('failed', 'canceled', 'refunded')
            """,
            (stored_status, order_id),
        )
        if order_cursor.rowcount == 0:
            connection.rollback()
            return False
        connection.commit()
        return True


def count_orders() -> int:
    with get_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM orders").fetchone()
        return int(row["count"]) if row else 0


def count_user_orders(user_id: int) -> int:
    """عدد طلبات المستخدم (كل السجلات في جدول orders لهذا user_id)."""
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM orders WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return int(row["count"]) if row else 0


def sum_revenue() -> float:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM orders
            WHERE status != 'failed'
            """
        ).fetchone()
        return float(row["total"]) if row else 0.0


def get_pending_admin_orders_ordered() -> list[OrderRecord]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, user_id, service_name, service_id, link, quantity, amount, status, provider_order_id,
                   start_count, created_at, status_note, api_account, provider_slug, provider_cost_dh
            FROM orders
            WHERE LOWER(REPLACE(status, '_', ' ')) = 'pending admin'
            ORDER BY id ASC
            """,
        ).fetchall()
        return [_order_row_to_record(row) for row in rows]


def count_pending_admin_orders() -> int:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM orders
            WHERE LOWER(REPLACE(status, '_', ' ')) = 'pending admin'
            """,
        ).fetchone()
    return int(row["count"]) if row else 0


def get_trackable_orders(limit: int = 200) -> list[OrderRecord]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, user_id, service_name, service_id, link, quantity, amount, status, provider_order_id,
                   start_count, created_at, status_note, api_account, provider_slug, provider_cost_dh
            FROM orders
            WHERE provider_order_id IS NOT NULL
              AND provider_order_id != ''
              AND LOWER(status) NOT IN ('completed', 'canceled', 'partial', 'refunded', 'failed')
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_order_row_to_record(row) for row in rows]


def apply_partial_or_full_refund(
    order_id: int,
    refund_amount: float,
    next_status: str,
    *,
    status_note: str | None = None,
    actual_provider_usd: float | None = None,
    final_customer_price_dh: float | None = None,
    audit_payload_json: str | None = None,
) -> bool:
    refund_value = to_float(refund_amount)
    if refund_value <= 0:
        return False
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT user_id, amount, status, COALESCE(refunded_amount, 0) AS refunded_amount
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False

        current_key = normalize_order_status_key(str(row["status"]))
        if current_key in _ORDER_STATUSES_NO_FURTHER_REFUND:
            connection.rollback()
            return False

        order_amount = float(row["amount"])
        refunded_already = float(row["refunded_amount"])
        remaining = max(0.0, order_amount - refunded_already)
        safe_refund = min(max(refund_value, 0.0), remaining)
        if safe_refund <= 0:
            connection.rollback()
            return False
        if status_note is not None:
            order_cursor = connection.execute(
                """
                UPDATE orders
                SET status = ?,
                    refunded_amount = ROUND(COALESCE(refunded_amount, 0) + ?, 6),
                    status_note = ?,
                    status_changed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND LOWER(REPLACE(status, '_', ' ')) NOT IN (
                      'failed', 'canceled', 'partial', 'refunded', 'completed'
                  )
                """,
                (next_status.lower(), safe_refund, status_note, order_id),
            )
        else:
            order_cursor = connection.execute(
                """
                UPDATE orders
                SET status = ?,
                    refunded_amount = ROUND(COALESCE(refunded_amount, 0) + ?, 6),
                    status_changed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND LOWER(REPLACE(status, '_', ' ')) NOT IN (
                      'failed', 'canceled', 'partial', 'refunded', 'completed'
                  )
                """,
                (next_status.lower(), safe_refund, order_id),
            )
        if order_cursor.rowcount == 0:
            connection.rollback()
            return False

        _credit_user_order_refund(connection, int(row["user_id"]), safe_refund)
        connection.execute(
            """
            INSERT INTO refund_audit_log (
                order_id, user_id, refund_type, refund_amount_dh,
                actual_provider_usd, final_customer_price_dh, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                int(row["user_id"]),
                next_status.lower(),
                safe_refund,
                actual_provider_usd,
                final_customer_price_dh,
                audit_payload_json,
            ),
        )
        connection.commit()

    try_apply_referral_payout_for_order(order_id)
    return True


def create_deposit(user_id: int, amount: float, method: str, proof_file_id: str) -> int | None:
    """
    إنشاء طلب شحن معلّق. يُرجع None إذا كان الإيصال/الرمز مكرراً (معلّق أو مُعتمد).
    """
    amount_money = to_float(amount)
    proof = str(proof_file_id or "").strip()
    with get_connection() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                INSERT INTO deposits (user_id, amount, method, proof_file_id, status)
                VALUES (?, ?, ?, ?, 'pending')
                """,
                (user_id, amount_money, method, proof),
            )
            connection.commit()
            return int(cursor.lastrowid)
        except sqlite3.IntegrityError:
            connection.execute("ROLLBACK")
            return None


def get_deposit(deposit_id: int) -> DepositRecord | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, user_id, amount, method, proof_file_id, status
            FROM deposits
            WHERE id = ?
            """,
            (deposit_id,),
        ).fetchone()
        if row is None:
            return None

        return {
            "id": int(row["id"]),
            "user_id": int(row["user_id"]),
            "amount": float(row["amount"]),
            "method": str(row["method"]),
            "proof_file_id": str(row["proof_file_id"]),
            "status": str(row["status"]),
        }


def update_deposit_status(deposit_id: int, status: str) -> bool:
    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE deposits SET status = ? WHERE id = ?",
            (status, deposit_id),
        )
        connection.commit()
        return cursor.rowcount > 0


def update_pending_deposit_status(deposit_id: int, status: str) -> bool:
    """تحديث حالة طلب شحن لا يزال معلّقاً فقط (يمنع الكتابة فوق اعتماد سابق)."""
    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE deposits SET status = ? WHERE id = ? AND status = 'pending'",
            (status, deposit_id),
        )
        connection.commit()
        return cursor.rowcount > 0


def count_user_deposits(user_id: int) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM deposits WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return int(row["count"] or 0) if row else 0


def count_user_pending_deposits(user_id: int) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM deposits WHERE user_id = ? AND status = 'pending'",
            (user_id,),
        ).fetchone()
    return int(row["count"] or 0) if row else 0


def recharge_code_in_use(code: str, *, exclude_deposit_id: int | None = None) -> bool:
    """رمز تعبئة مُرسَل مسبقاً (معلّق أو مُعتمد) في طلب آخر."""
    normalized = str(code or "").strip()
    if not normalized:
        return False
    params: list[object] = [normalized]
    exclude_sql = ""
    if exclude_deposit_id is not None:
        exclude_sql = " AND id != ?"
        params.append(int(exclude_deposit_id))
    with get_connection() as connection:
        row = connection.execute(
            f"""
            SELECT 1
            FROM deposits
            WHERE proof_file_id = ?
              AND (status = 'pending' OR status LIKE 'approved:%')
              {exclude_sql}
            LIMIT 1
            """,
            params,
        ).fetchone()
    return row is not None


def get_user_deposits(user_id: int, *, limit: int = 10) -> list[DepositRecord]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, user_id, amount, method, proof_file_id, status
            FROM deposits
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, max(1, int(limit))),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "user_id": int(row["user_id"]),
            "amount": float(row["amount"]),
            "method": str(row["method"]),
            "proof_file_id": str(row["proof_file_id"]),
            "status": str(row["status"]),
        }
        for row in rows
    ]


def record_deposit_transaction(
    user_id: int,
    deposit_method: str,
    amount: float,
    *,
    status: str = "completed",
    deposit_id: int | None = None,
) -> int:
    """سجل إيداع ناجح — يُستخدم لاحقاً في السحب المغلق (نفس البنك / حد المبلغ)."""
    amount_money = to_float(amount)
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO deposit_transactions (user_id, deposit_method, amount, status, deposit_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, deposit_method, amount_money, status, deposit_id),
        )
        connection.commit()
        return int(cursor.lastrowid)


def finalize_approved_deposit(
    deposit_id: int,
    user_id: int,
    amount: float,
    deposit_method: str,
) -> bool:
    """
    اعتماد الشحن ذرّياً: يُحدَّث الرصيد فقط إذا كان الطلب لا يزال pending.
    يُرجع True عند النجاح.
    """
    amount_money = to_float(amount)
    approved_status = f"approved:{amount_money}"
    with get_connection() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE deposits
                SET amount = ?, status = ?
                WHERE id = ? AND user_id = ? AND status = 'pending'
                """,
                (amount_money, approved_status, deposit_id, user_id),
            )
            if cursor.rowcount == 0:
                connection.execute("ROLLBACK")
                return False
            user_cursor = connection.execute(
                "UPDATE users SET balance = ROUND(balance + ?, 6) WHERE user_id = ?",
                (amount_money, user_id),
            )
            if user_cursor.rowcount == 0:
                connection.execute("ROLLBACK")
                return False
            connection.execute(
                """
                INSERT INTO deposit_transactions (user_id, deposit_method, amount, status, deposit_id)
                VALUES (?, ?, ?, 'completed', ?)
                """,
                (user_id, deposit_method, amount_money, deposit_id),
            )
            connection.commit()
            return True
        except sqlite3.Error:
            connection.execute("ROLLBACK")
            raise


def sum_user_completed_withdrawals_by_method(user_id: int, deposit_method: str) -> float:
    method = str(deposit_method or "").strip()
    if not method:
        return 0.0
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM withdrawals
            WHERE user_id = ?
              AND method = ?
              AND status = 'completed'
              AND COALESCE(withdrawal_type, 'normal') = 'normal'
            """,
            (user_id, method),
        ).fetchone()
    return float(row["total"]) if row else 0.0


def sum_user_pending_withdrawals_by_method(
    user_id: int,
    deposit_method: str,
    *,
    exclude_withdrawal_id: int | None = None,
) -> float:
    method = str(deposit_method or "").strip()
    if not method:
        return 0.0
    params: list[object] = [user_id, method]
    exclude_sql = ""
    if exclude_withdrawal_id is not None:
        exclude_sql = " AND id != ?"
        params.append(int(exclude_withdrawal_id))
    with get_connection() as connection:
        row = connection.execute(
            f"""
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM withdrawals
            WHERE user_id = ?
              AND method = ?
              AND status = 'pending'
              AND COALESCE(withdrawal_type, 'normal') = 'normal'
              {exclude_sql}
            """,
            tuple(params),
        ).fetchone()
    return float(row["total"]) if row else 0.0


def _withdrawable_amount_for_method(
    deposited: float,
    completed_withdrawn: float,
    pending_withdrawn: float,
) -> float:
    return round(max(0.0, deposited - completed_withdrawn - pending_withdrawn), 6)


def _withdrawable_amount_for_method_tx(
    connection: sqlite3.Connection,
    user_id: int,
    deposit_method: str,
    *,
    exclude_withdrawal_id: int | None = None,
) -> float:
    method = str(deposit_method or "").strip()
    if not method:
        return 0.0
    deposited_row = connection.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM deposit_transactions
        WHERE user_id = ?
          AND deposit_method = ?
          AND status = 'completed'
        """,
        (user_id, method),
    ).fetchone()
    completed_row = connection.execute(
        """
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM withdrawals
        WHERE user_id = ?
          AND method = ?
          AND status = 'completed'
          AND COALESCE(withdrawal_type, 'normal') = 'normal'
        """,
        (user_id, method),
    ).fetchone()
    pending_params: list[object] = [user_id, method]
    pending_exclude_sql = ""
    if exclude_withdrawal_id is not None:
        pending_exclude_sql = " AND id != ?"
        pending_params.append(int(exclude_withdrawal_id))
    pending_row = connection.execute(
        f"""
        SELECT COALESCE(SUM(amount), 0) AS total
        FROM withdrawals
        WHERE user_id = ?
          AND method = ?
          AND status = 'pending'
          AND COALESCE(withdrawal_type, 'normal') = 'normal'
          {pending_exclude_sql}
        """,
        tuple(pending_params),
    ).fetchone()
    deposited = float((deposited_row or {})["total"] or 0.0)
    completed = float((completed_row or {})["total"] or 0.0)
    pending = float((pending_row or {})["total"] or 0.0)
    return _withdrawable_amount_for_method(deposited, completed, pending)


def get_method_withdrawal_ledger_summary(user_id: int, deposit_method: str) -> dict[str, float]:
    """ملخص إيداع/سحب مكتمل/معلق/متاح لطريقة دفع واحدة."""
    method = str(deposit_method or "").strip()
    deposited = sum_user_deposits_by_method(user_id, method)
    completed = sum_user_completed_withdrawals_by_method(user_id, method)
    pending = sum_user_pending_withdrawals_by_method(user_id, method)
    available = _withdrawable_amount_for_method(deposited, completed, pending)
    return {
        "deposited": deposited,
        "completed_withdrawn": completed,
        "pending_withdrawn": pending,
        "available": available,
    }


def get_user_withdrawable_amount(user_id: int, deposit_method: str) -> float:
    summary = get_method_withdrawal_ledger_summary(user_id, deposit_method)
    return float(summary["available"])


def get_user_withdrawable_method_balances(user_id: int) -> dict[str, float]:
    """المتبقي القابل للسحب لكل طريقة إيداع، مع استبعاد بطاقات التعبئة."""
    from utils.recharge_telecom import is_recharge_ledger

    with get_connection() as connection:
        deposit_rows = connection.execute(
            """
            SELECT deposit_method, COALESCE(SUM(amount), 0) AS deposited
            FROM deposit_transactions
            WHERE user_id = ?
              AND status = 'completed'
              AND amount > 0
            GROUP BY deposit_method
            """,
            (user_id,),
        ).fetchall()
        completed_rows = connection.execute(
            """
            SELECT method, COALESCE(SUM(amount), 0) AS withdrawn
            FROM withdrawals
            WHERE user_id = ?
              AND status = 'completed'
              AND COALESCE(withdrawal_type, 'normal') = 'normal'
            GROUP BY method
            """,
            (user_id,),
        ).fetchall()
        pending_rows = connection.execute(
            """
            SELECT method, COALESCE(SUM(amount), 0) AS pending
            FROM withdrawals
            WHERE user_id = ?
              AND status = 'pending'
              AND COALESCE(withdrawal_type, 'normal') = 'normal'
            GROUP BY method
            """,
            (user_id,),
        ).fetchall()

    completed_by_method = {
        str(row["method"] or "").strip(): float(row["withdrawn"] or 0.0)
        for row in completed_rows
    }
    pending_by_method = {
        str(row["method"] or "").strip(): float(row["pending"] or 0.0)
        for row in pending_rows
    }
    balances: dict[str, float] = {}
    for row in deposit_rows:
        method = str(row["deposit_method"] or "").strip()
        if not method or is_recharge_ledger(method):
            continue
        available = _withdrawable_amount_for_method(
            float(row["deposited"] or 0.0),
            completed_by_method.get(method, 0.0),
            pending_by_method.get(method, 0.0),
        )
        if available > 0:
            balances[method] = available
    return balances


def get_user_withdrawable_methods(user_id: int) -> list[str]:
    """طرق الإيداع التي لا يزال لديها متبقٍ قابل للسحب."""
    return list(get_user_withdrawable_method_balances(user_id).keys())


def _normalize_withdrawal_type(withdrawal_type: str | None) -> str:
    return "referral" if str(withdrawal_type or "").strip().lower() == "referral" else "normal"


def get_active_withdrawal(
    user_id: int,
    *,
    withdrawal_type: str = "normal",
) -> WithdrawalRecord | None:
    normalized_type = _normalize_withdrawal_type(withdrawal_type)
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, user_id, amount, method, details_json, status, withdrawal_type, created_at, updated_at
            FROM withdrawals
            WHERE user_id = ?
              AND status = 'pending'
              AND COALESCE(withdrawal_type, 'normal') = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id, normalized_type),
        ).fetchone()
    if row is None:
        return None
    return _withdrawal_from_row(row)


def create_withdrawal_with_balance_hold(
    user_id: int,
    amount: float,
    method: str,
    details_json: str,
    *,
    withdrawal_type: str = "normal",
) -> int | None:
    amount_money = to_float(amount)
    if amount_money <= 0:
        return None
    normalized_type = _normalize_withdrawal_type(withdrawal_type)
    if normalized_type == "referral" and amount_money < to_float(MIN_REFERRAL_WITHDRAW_DH):
        return None
    with get_connection() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            active = connection.execute(
                """
                SELECT 1
                FROM withdrawals
                WHERE user_id = ?
                  AND status = 'pending'
                  AND COALESCE(withdrawal_type, 'normal') = ?
                LIMIT 1
                """,
                (user_id, normalized_type),
            ).fetchone()
            if active is not None:
                connection.rollback()
                return None

            if normalized_type == "referral":
                user_cursor = connection.execute(
                    """
                    UPDATE users
                    SET referral_balance = ROUND(COALESCE(referral_balance, 0) - ?, 6)
                    WHERE user_id = ?
                      AND COALESCE(referral_balance, 0) >= ?
                    """,
                    (amount_money, user_id, amount_money),
                )
                if user_cursor.rowcount == 0:
                    connection.rollback()
                    return None
            else:
                if (
                    connection.execute(
                        "SELECT 1 FROM users WHERE user_id = ?",
                        (user_id,),
                    ).fetchone()
                    is None
                ):
                    connection.rollback()
                    return None
                available = _withdrawable_amount_for_method_tx(connection, user_id, method)
                if available < amount_money:
                    connection.rollback()
                    return None
                user_cursor = connection.execute(
                    """
                    UPDATE users
                    SET balance = ROUND(balance - ?, 6)
                    WHERE user_id = ?
                      AND balance >= ?
                    """,
                    (amount_money, user_id, amount_money),
                )
                if user_cursor.rowcount == 0:
                    connection.rollback()
                    return None
            cursor = connection.execute(
                """
                INSERT INTO withdrawals (user_id, amount, method, details_json, status, withdrawal_type)
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (user_id, amount_money, method, details_json, normalized_type),
            )
            connection.commit()
            return int(cursor.lastrowid)
        except Exception:
            connection.rollback()
            raise


def _withdrawal_from_row(row: sqlite3.Row) -> WithdrawalRecord:
    row_keys = set(row.keys())
    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]),
        "amount": float(row["amount"]),
        "method": str(row["method"]),
        "details_json": str(row["details_json"] or "{}"),
        "status": str(row["status"]),
        "withdrawal_type": _normalize_withdrawal_type(
            str(row["withdrawal_type"]) if "withdrawal_type" in row_keys else "normal"
        ),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]) if row["updated_at"] is not None else None,
    }


def get_user_withdrawals(
    user_id: int,
    *,
    limit: int | None = 5,
    include_pending: bool = True,
    withdrawal_type: str | None = None,
) -> list[WithdrawalRecord]:
    clauses = ["user_id = ?"]
    params: list[object] = [user_id]
    if not include_pending:
        clauses.append("status != 'pending'")
    if withdrawal_type is not None:
        clauses.append("COALESCE(withdrawal_type, 'normal') = ?")
        params.append(_normalize_withdrawal_type(withdrawal_type))
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT ?"
        params.append(limit)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT id, user_id, amount, method, details_json, status, withdrawal_type, created_at, updated_at
            FROM withdrawals
            WHERE {' AND '.join(clauses)}
            ORDER BY id DESC
            {limit_clause}
            """,
            tuple(params),
        ).fetchall()
    return [_withdrawal_from_row(row) for row in rows]


def count_user_withdrawals(
    user_id: int,
    *,
    include_pending: bool = True,
    withdrawal_type: str | None = None,
) -> int:
    clauses = ["user_id = ?"]
    params: list[object] = [user_id]
    if not include_pending:
        clauses.append("status != 'pending'")
    if withdrawal_type is not None:
        clauses.append("COALESCE(withdrawal_type, 'normal') = ?")
        params.append(_normalize_withdrawal_type(withdrawal_type))
    with get_connection() as connection:
        row = connection.execute(
            f"SELECT COUNT(*) AS count FROM withdrawals WHERE {' AND '.join(clauses)}",
            tuple(params),
        ).fetchone()
    return int(row["count"]) if row else 0


def sum_user_deposits_by_method(user_id: int, deposit_method: str) -> float:
    method = str(deposit_method or "").strip()
    if not method:
        return 0.0
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0) AS total
            FROM deposit_transactions
            WHERE user_id = ?
              AND deposit_method = ?
              AND status = 'completed'
            """,
            (user_id, method),
        ).fetchone()
    return float(row["total"]) if row else 0.0


def get_withdrawal(withdrawal_id: int) -> WithdrawalRecord | None:
    with get_connection() as connection:
        row = connection.execute(
            """
            SELECT id, user_id, amount, method, details_json, status, withdrawal_type, created_at, updated_at
            FROM withdrawals
            WHERE id = ?
            """,
            (withdrawal_id,),
        ).fetchone()
    if row is None:
        return None
    return _withdrawal_from_row(row)


def get_pending_withdrawals_ordered() -> list[WithdrawalRecord]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, user_id, amount, method, details_json, status, withdrawal_type, created_at, updated_at
            FROM withdrawals
            WHERE status = 'pending'
            ORDER BY id ASC
            """,
        ).fetchall()
    return [_withdrawal_from_row(row) for row in rows]


def count_pending_withdrawals() -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM withdrawals WHERE status = 'pending'",
        ).fetchone()
    return int(row["count"]) if row else 0


def approve_withdrawal_by_admin(withdrawal_id: int) -> WithdrawalRecord | None:
    with get_connection() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT id, user_id, amount, method, details_json, status, withdrawal_type, created_at, updated_at
                FROM withdrawals
                WHERE id = ?
                  AND status = 'pending'
                """,
                (withdrawal_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            approve_cursor = connection.execute(
                """
                UPDATE withdrawals
                SET status = 'completed',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = 'pending'
                """,
                (withdrawal_id,),
            )
            if approve_cursor.rowcount == 0:
                connection.rollback()
                return None
            connection.commit()
            updated = connection.execute(
                """
                SELECT id, user_id, amount, method, details_json, status, withdrawal_type, created_at, updated_at
                FROM withdrawals
                WHERE id = ?
                """,
                (withdrawal_id,),
            ).fetchone()
            return _withdrawal_from_row(updated) if updated else None
        except Exception:
            connection.rollback()
            raise


def reject_withdrawal_by_admin(withdrawal_id: int) -> WithdrawalRecord | None:
    with get_connection() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT user_id, amount, status, withdrawal_type
                FROM withdrawals
                WHERE id = ?
                """,
                (withdrawal_id,),
            ).fetchone()
            if row is None or str(row["status"]) != "pending":
                connection.rollback()
                return None
            user_id = int(row["user_id"])
            amount_money = to_float(row["amount"])
            refund_column = (
                "referral_balance"
                if _normalize_withdrawal_type(str(row["withdrawal_type"])) == "referral"
                else "balance"
            )
            reject_cursor = connection.execute(
                """
                UPDATE withdrawals
                SET status = 'rejected',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND status = 'pending'
                """,
                (withdrawal_id,),
            )
            if reject_cursor.rowcount == 0:
                connection.rollback()
                return None
            connection.execute(
                f"UPDATE users SET {refund_column} = ROUND(COALESCE({refund_column}, 0) + ?, 6) WHERE user_id = ?",
                (amount_money, user_id),
            )
            connection.commit()
            updated = connection.execute(
                """
                SELECT id, user_id, amount, method, details_json, status, withdrawal_type, created_at, updated_at
                FROM withdrawals
                WHERE id = ?
                """,
                (withdrawal_id,),
            ).fetchone()
            return _withdrawal_from_row(updated) if updated else None
        except Exception:
            connection.rollback()
            raise


def cancel_pending_withdrawal(withdrawal_id: int, user_id: int) -> bool:
    with get_connection() as connection:
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT amount, status, withdrawal_type
                FROM withdrawals
                WHERE id = ?
                  AND user_id = ?
                """,
                (withdrawal_id, user_id),
            ).fetchone()
            if row is None or str(row["status"]) != "pending":
                connection.rollback()
                return False
            amount_money = to_float(row["amount"])
            refund_column = (
                "referral_balance"
                if _normalize_withdrawal_type(str(row["withdrawal_type"])) == "referral"
                else "balance"
            )
            cancel_cursor = connection.execute(
                """
                UPDATE withdrawals
                SET status = 'canceled',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                  AND user_id = ?
                  AND status = 'pending'
                """,
                (withdrawal_id, user_id),
            )
            if cancel_cursor.rowcount == 0:
                connection.rollback()
                return False
            connection.execute(
                f"UPDATE users SET {refund_column} = ROUND(COALESCE({refund_column}, 0) + ?, 6) WHERE user_id = ?",
                (amount_money, user_id),
            )
            connection.commit()
            return True
        except Exception:
            connection.rollback()
            raise


def count_users() -> int:
    with get_connection() as connection:
        row = connection.execute("SELECT COUNT(*) AS count FROM users").fetchone()
        return int(row["count"]) if row else 0


def count_pending_deposits() -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM deposits WHERE status = 'pending'"
        ).fetchone()
        return int(row["count"]) if row else 0


def get_all_user_ids() -> list[int]:
    with get_connection() as connection:
        rows = connection.execute("SELECT user_id FROM users").fetchall()
        return [int(row["user_id"]) for row in rows]


def set_user_referral_level(user_id: int, level: int) -> bool:
    from services.referral import MAX_REFERRAL_LEVEL

    lvl = max(1, min(MAX_REFERRAL_LEVEL, int(level)))
    with get_connection() as connection:
        cursor = connection.execute(
            "UPDATE users SET referral_level = ? WHERE user_id = ?",
            (lvl, user_id),
        )
        connection.commit()
        return cursor.rowcount > 0


def set_user_partner_status(user_id: int, *, is_partner: bool) -> bool:
    """توافق قديم — الشريك = المستوى 4."""
    if not is_partner:
        return set_user_referral_level(user_id, 1)
    return set_user_referral_level(user_id, 4)


def count_invited_users(referrer_id: int) -> int:
    with get_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS c FROM users WHERE referred_by = ?",
            (referrer_id,),
        ).fetchone()
        return int(row["c"]) if row else 0


def sum_referrer_pending_commission_estimate(referrer_id: int) -> float:
    """تقدير معلّق للعرض فقط — لا يُضاف إلى referral_balance ولا يُسحب."""
    from services.referral import IN_PROGRESS_ORDER_STATUSES, commission_rate

    owner = get_user(referrer_id)
    if not owner:
        return 0.0
    rate = commission_rate(int(owner["referral_level"]))
    placeholders = ", ".join("?" for _ in IN_PROGRESS_ORDER_STATUSES)
    with get_connection() as connection:
        row = connection.execute(
            f"""
            SELECT COALESCE(SUM((o.amount - MIN(COALESCE(o.refunded_amount, 0), o.amount)) * ?), 0) AS pending
            FROM orders o
            JOIN users buyer ON buyer.user_id = o.user_id
            WHERE buyer.referred_by = ?
              AND COALESCE(o.referral_payout_done, 0) = 0
              AND LOWER(REPLACE(o.status, '_', ' ')) IN ({placeholders})
            """,
            (rate, referrer_id, *IN_PROGRESS_ORDER_STATUSES),
        ).fetchone()
        return float(row["pending"] or 0.0) if row else 0.0


def list_referral_invitees_summaries(referrer_id: int) -> list[dict[str, object]]:
    """قائمة المدعوين: مؤكد (من الطلبات) وتقدير معلّق (عرض فقط)."""
    from services.referral import IN_PROGRESS_ORDER_STATUSES, commission_rate

    owner = get_user(referrer_id)
    if not owner:
        return []
    rate = commission_rate(int(owner["referral_level"]))
    placeholders = ", ".join("?" for _ in IN_PROGRESS_ORDER_STATUSES)
    with get_connection() as connection:
        rows = connection.execute(
            f"""
            SELECT
                u.user_id AS user_id,
                u.telegram_name AS telegram_name,
                COALESCE((
                    SELECT SUM(COALESCE(o.referral_commission_amount, 0))
                    FROM orders o
                    WHERE o.user_id = u.user_id AND COALESCE(o.referral_payout_done, 0) = 1
                ), 0) AS earned,
                COALESCE((
                    SELECT SUM((o.amount - MIN(COALESCE(o.refunded_amount, 0), o.amount)) * ?)
                    FROM orders o
                    WHERE o.user_id = u.user_id
                      AND COALESCE(o.referral_payout_done, 0) = 0
                      AND LOWER(REPLACE(o.status, '_', ' ')) IN ({placeholders})
                ), 0) AS pending
            FROM users u
            WHERE u.referred_by = ?
            ORDER BY u.user_id ASC
            """,
            (rate, *IN_PROGRESS_ORDER_STATUSES, referrer_id),
        ).fetchall()
        return [
            {
                "user_id": int(r["user_id"]),
                "telegram_name": str(r["telegram_name"]) if r["telegram_name"] else None,
                "earned": float(r["earned"] or 0.0),
                "pending": float(r["pending"] or 0.0),
            }
            for r in rows
        ]


def add_pending_notification(user_id: int, chat_id: int, message_id: int) -> None:
    """تسجيل رسالة إشعار بانتظار نشاط المستخدم لبدء مؤقت الحذف."""
    add_user(user_id)
    with get_connection() as connection:
        connection.execute(
            """
            INSERT OR REPLACE INTO pending_notifications (user_id, chat_id, message_id)
            VALUES (?, ?, ?)
            """,
            (user_id, chat_id, message_id),
        )
        connection.commit()


def pop_pending_notifications(user_id: int) -> list[tuple[int, int]]:
    """إخراج كل الإشعارات المعلقة للمستخدم وحذفها من القائمة (تشغيل المؤقت مرة واحدة)."""
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT chat_id, message_id
            FROM pending_notifications
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchall()
        if rows:
            connection.execute(
                "DELETE FROM pending_notifications WHERE user_id = ?",
                (user_id,),
            )
            connection.commit()
    return [(int(r["chat_id"]), int(r["message_id"])) for r in rows]


def remove_pending_notification(user_id: int, message_id: int) -> None:
    with get_connection() as connection:
        connection.execute(
            """
            DELETE FROM pending_notifications
            WHERE user_id = ? AND message_id = ?
            """,
            (user_id, message_id),
        )
        connection.commit()


def _expire_timed_announcements(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE timed_announcements
        SET status = 'expired'
        WHERE status = 'active' AND ends_at <= datetime('now')
        """
    )


def get_pending_timed_announcements_for_user(user_id: int) -> list[dict[str, object]]:
    """All active, non-expired announcements — delivered on every /start."""
    _ = user_id  # kept for call-site clarity
    with get_connection() as connection:
        _expire_timed_announcements(connection)
        rows = connection.execute(
            """
            SELECT ta.id, ta.message_html, ta.ends_at, ta.auto_delete_seconds
            FROM timed_announcements ta
            WHERE ta.status = 'active'
              AND ta.ends_at > datetime('now')
            ORDER BY ta.id ASC
            """
        ).fetchall()
        connection.commit()
    return [
        {
            "id": int(row["id"]),
            "message_html": str(row["message_html"]),
            "ends_at": str(row["ends_at"]),
            "auto_delete_seconds": (
                int(row["auto_delete_seconds"])
                if row["auto_delete_seconds"] is not None
                else None
            ),
        }
        for row in rows
    ]
