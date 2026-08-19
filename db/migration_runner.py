import logging
import shutil
from datetime import datetime
from pathlib import Path

from .connection import DB_PATH, get_connection
from .migrations import MIGRATIONS


logger = logging.getLogger(__name__)


def _ensure_migration_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
    """)
    conn.commit()


BACKUP_KEEP = 5


def _backup_database(db_path, keep=BACKUP_KEEP):
    """Copy the database before the first change, then prune old backups.

    Only backups matching the convention ``<stem>.backup_v3_<stamp><suffix>``
    are pruned; rescue/aliases backups and the live database are untouched.
    """
    path = Path(db_path)
    if not path.exists():
        return None
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = path.with_name(f"{path.stem}.backup_v3_{stamp}{path.suffix}")
    shutil.copy2(path, backup)

    pattern = f"{path.stem}.backup_v3_*{path.suffix}"
    backups = sorted(
        (p for p in path.parent.glob(pattern) if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in backups[keep:]:
        try:
            old.unlink()
            logger.info("Pruned old database backup: %s", old.name)
        except OSError as error:
            logger.warning("Could not prune backup %s: %s", old.name, error)
    return backup


def run_migrations(db_path=None):
    """Apply pending migrations transactionally, backing up before the first change."""
    path = Path(db_path) if db_path else DB_PATH
    conn = get_connection(path)
    try:
        _ensure_migration_table(conn)
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
        pending = [migration for migration in MIGRATIONS if migration[0] not in applied]
        if not pending:
            return {"applied": [], "backup": None}

        backup = _backup_database(path)
        applied_now = []
        for version, description, migration in pending:
            try:
                conn.execute("BEGIN IMMEDIATE")
                migration(conn)
                conn.execute(
                    "INSERT INTO schema_migrations(version, description, applied_at) VALUES (?, ?, ?)",
                    (version, description, datetime.now().astimezone().isoformat(timespec="seconds")),
                )
                violations = conn.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise RuntimeError(f"Migration {version} created foreign-key violations: {violations[:5]}")
                conn.commit()
                applied_now.append(version)
                logger.info("Applied database migration %s: %s", version, description)
            except Exception:
                conn.rollback()
                raise
        return {"applied": applied_now, "backup": str(backup) if backup else None}
    finally:
        conn.close()
