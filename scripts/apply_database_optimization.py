"""Rehearse then apply migration 600 + WAL, preserving every business row."""
import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db import connection
from db.backup import backup_database
from db.migration_runner import run_migrations
from db.migrations import MIGRATIONS


def fingerprint(path):
    with connection.db_read(path) as conn:
        result = {}
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name<>'schema_migrations'")]
        for table in tables:
            escaped = table.replace('"', '""')
            rows = sorted(json.dumps(tuple(r), ensure_ascii=True, default=str) for r in conn.execute(f'SELECT * FROM "{escaped}"'))
            result[table] = {'count': len(rows), 'sha256': hashlib.sha256('\n'.join(rows).encode()).hexdigest()}
        return result


def enable_wal(path):
    if str(Path(path).resolve()).startswith('\\\\'):
        raise ValueError('WAL只在本机磁盘使用，不对网络共享启用')
    with closing(connection.get_connection(path)) as conn:
        mode = conn.execute('PRAGMA journal_mode=WAL').fetchone()[0]
        if mode != 'wal':
            raise RuntimeError('未能启用WAL')
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('完整性校验失败')
        if conn.execute('PRAGMA foreign_key_check').fetchone():
            raise RuntimeError('外键校验失败')
    return mode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    with connection.db_read() as conn:
        applied = {r[0] for r in conn.execute('SELECT version FROM schema_migrations')}
    pending = [v for v, _, _ in MIGRATIONS if v not in applied]
    if any(v != 600 for v in pending):
        raise RuntimeError('发现本次优化以外的待执行迁移，停止自动应用')
    root = connection.DB_PATH.parent / 'backups' / f'db_optimization_{datetime.now():%Y%m%d_%H%M%S_%f}'
    before = backup_database(connection.DB_PATH, root / 'before.db')
    expected = fingerprint(before)
    rehearsal = backup_database(before, root / 'rehearsal.db')
    run_migrations(rehearsal, backup_keep=None)
    enable_wal(rehearsal)
    if fingerprint(rehearsal) != expected:
        raise RuntimeError('副本业务记录发生变化，停止应用')
    report = {'backup': str(before), 'rehearsal': 'passed', 'applied': False}
    if args.apply:
        # Stop if someone changed the live ledger while the rehearsal ran.
        if fingerprint(connection.DB_PATH) != expected:
            raise RuntimeError('演练期间正式账目有变化，请在录入结束后重试')
        report['migration'] = run_migrations(connection.DB_PATH, backup_keep=None)
        report['journal_mode'] = enable_wal(connection.DB_PATH)
        report['business_rows_unchanged'] = fingerprint(connection.DB_PATH) == expected
        report['applied'] = True
        if not report['business_rows_unchanged']:
            raise RuntimeError('应用期间账目有变化，未执行任何自动回滚，请人工核对')
    (root / 'verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
