"""Rehearse migration 470; --apply also backs up and upgrades the formal DB."""

import argparse
import hashlib
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path


def business_fingerprints(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("BEGIN")
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ) if row[0] not in ("schema_migrations", "cost_entry_revisions")]
        result = {}
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            rows = sorted(repr(row) for row in conn.execute(f"SELECT * FROM {quoted}"))
            digest = hashlib.sha256()
            for row in rows:
                digest.update((row + "\n").encode("utf-8"))
            result[name] = (len(rows), digest.hexdigest())
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    from db.connection import DB_PATH
    from db.backup import backup_database
    from db.migration_runner import run_migrations
    from db.migrations import MIGRATIONS
    from services.backup_service import create_backup_archive

    with closing(sqlite3.connect(DB_PATH)) as conn:
        applied = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    pending = [item[0] for item in MIGRATIONS if item[0] not in applied]
    if pending != [470]:
        raise RuntimeError(f"Expected only migration 470 pending, got {pending}")
    before = business_fingerprints(DB_PATH)
    with tempfile.TemporaryDirectory(prefix="migration_470_") as folder:
        snapshot = backup_database(DB_PATH, Path(folder) / "rehearsal.db")
        run_migrations(snapshot)
        assert business_fingerprints(snapshot) == before, "Rehearsal changed business records"
    print(f"Rehearsal passed: {len(before)} business tables unchanged; integrity/FK checks passed")
    if not args.apply:
        return
    backup_dir = root / "backups"
    backup_dir.mkdir(exist_ok=True)
    archive_path = backup_dir / f"before_cost_revision_470_{datetime.now():%Y%m%d_%H%M%S_%f}.zip"
    manifest = create_backup_archive(archive_path)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None, "Backup archive failed verification"
    if manifest["missing_files"]:
        raise RuntimeError("Backup reports missing files; formal migration not started")
    assert business_fingerprints(DB_PATH) == before, "Concurrent business changes; retry while idle"
    result = run_migrations(DB_PATH)
    assert result["applied"] == [470]
    assert business_fingerprints(DB_PATH) == before, "Post-migration business comparison failed"
    with closing(sqlite3.connect(DB_PATH)) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 470
    print(f"Formal upgrade passed: schema 470; {len(before)} business tables unchanged")
    print(f"Backup: {archive_path}; attachments: {len(manifest['files'])}; missing: 0")


if __name__ == "__main__":
    main()
