"""Rehearse migration 500 and compare every original business value before deploy."""
import argparse
import hashlib
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path


def fingerprint(conn, columns):
    result = {}
    for table, fields in columns.items():
        selected = ','.join('"' + field.replace('"', '""') + '"' for field in fields)
        rows = sorted(repr(tuple(r)) for r in conn.execute(f'SELECT {selected} FROM "{table}"'))
        result[table] = (len(rows), hashlib.sha256('\n'.join(rows).encode()).hexdigest())
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from db.connection import DB_PATH, get_connection
    from db.backup import backup_database
    from db.migration_runner import run_migrations
    from services.backup_service import create_backup_archive
    with closing(get_connection()) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 490
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'schema_migrations'")]
        columns = {table: [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')] for table in tables}
        original = fingerprint(conn, columns)

    def upgrade(path):
        result = run_migrations(path)
        assert result["applied"] == [500], result
        with closing(get_connection(path)) as conn:
            assert fingerprint(conn, columns) == original, "Original business records changed"
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
            assert not conn.execute("SELECT 1 FROM purchase_order_items WHERE settlement_mode<>'quantity'").fetchone()

    with tempfile.TemporaryDirectory(prefix="settlement_upgrade_") as folder:
        snapshot = backup_database(DB_PATH, Path(folder)/"test.db")
        upgrade(snapshot)
    print(f"Rehearsal passed: {len(tables)} original tables unchanged; old purchases remain quantity mode")
    if args.apply:
        destination = root / "backups" / f"before_weight_purchase_{datetime.now():%Y%m%d_%H%M%S_%f}.zip"
        manifest = create_backup_archive(destination)
        with zipfile.ZipFile(destination) as archive:
            assert archive.testzip() is None
        assert not manifest["missing_files"], "Backup reports missing attachments"
        with closing(get_connection()) as conn:
            assert fingerprint(conn, columns) == original, "Concurrent changes; repeat verification"
        upgrade(DB_PATH)
        print(f"Formal upgrade passed: schema 500; {len(tables)} original tables unchanged; backup: {destination}")


if __name__ == "__main__":
    main()
