"""Rehearse the payer snapshot migration and verify all existing facts."""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from db.connection import DB_PATH
from db.backup import backup_database
from db import migration_runner
from scripts.correct_sangyedian_20260912 import snapshot


def upgrade(path, baseline, backup):
    with sqlite3.connect(path) as conn:
        conn.row_factory=sqlite3.Row
        assert snapshot(conn)==baseline,'数据库已变化，请重新核对'
    with patch.object(migration_runner,'_backup_database',return_value=backup):
        migration_runner.run_migrations(path)
    with sqlite3.connect(path) as conn:
        conn.row_factory=sqlite3.Row
        after=snapshot(conn)
        for table,rows in baseline.items():
            if table=='schema_migrations':
                continue
            if table=='historical_receipts':
                assert after[table]==[dict(row,payer_name_snapshot=row.get('payer_name_snapshot','')) for row in rows]
            else:
                assert after[table]==rows,table
        assert conn.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert not conn.execute('PRAGMA foreign_key_check').fetchall()
    print('PASS',path)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    folder=DB_PATH.parent/'backups'/('receipt_payer_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    backup=backup_database(DB_PATH,folder/'before.db')
    rehearsal=backup_database(backup,folder/'rehearsal.db')
    with sqlite3.connect(backup) as conn:
        conn.row_factory=sqlite3.Row
        baseline=snapshot(conn)
    upgrade(rehearsal,baseline,backup)
    if args.apply: upgrade(DB_PATH,baseline,backup)
    print('BACKUP',backup)


if __name__=='__main__': main()
