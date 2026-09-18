"""Back up and prove migration preserves every existing business fact."""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db.connection import DB_PATH
from db.backup import backup_database
from db import migration_runner
from scripts.correct_sangyedian_20260912 import snapshot


def upgrade(path, baseline, backup):
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        assert snapshot(conn) == baseline, '数据库已变化，请重新演练'
    with patch.object(migration_runner, '_backup_database', return_value=backup):
        migration_runner.run_migrations(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        after = snapshot(conn)
        for table, rows in baseline.items():
            if table == 'schema_migrations':
                continue
            expected = rows
            if table == 'receipts':
                expected = [dict(r, automatic_income_allocation=r.get('automatic_income_allocation', 0))
                            for r in rows]
            assert after[table] == expected, table
        assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    print('PASS all business facts preserved:', path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    folder = DB_PATH.parent / 'backups' / ('advance_receipts_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    backup = backup_database(DB_PATH, folder / 'before.db')
    rehearsal = backup_database(backup, folder / 'rehearsal.db')
    with sqlite3.connect(backup) as conn:
        conn.row_factory = sqlite3.Row
        baseline = snapshot(conn)
    upgrade(rehearsal, baseline, backup)
    if args.apply:
        upgrade(DB_PATH, baseline, backup)
    print('BACKUP', backup)


if __name__ == '__main__':
    main()
