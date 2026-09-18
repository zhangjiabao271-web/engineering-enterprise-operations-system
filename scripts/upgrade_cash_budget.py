"""Rehearse 520 and owner-authorized, unambiguous expense-purpose changes."""
from contextlib import closing
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db import connection
from db.backup import backup_database
from db.migration_runner import run_migrations
from services import cost_service
from services.expense_categories import suggestion


def snapshot(path):
    with closing(sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True)) as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {name: conn.execute('SELECT * FROM "' + name + '" ORDER BY rowid').fetchall() for name in names}


class BorrowedConnection:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, parameters=()):
        if sql.strip().upper() == 'BEGIN IMMEDIATE':
            return self.conn.execute('SELECT 1')
        return self.conn.execute(sql, parameters)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


def classify(path):
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        conn.execute('BEGIN IMMEDIATE')
        rows = [dict(r) for r in conn.execute("SELECT * FROM cost_entries WHERE status='active' AND source_type IN ('manual','legacy_manual') ORDER BY id")]
        changes = []
        try:
            with patch.object(cost_service, 'get_connection', return_value=BorrowedConnection(conn)):
                for row in rows:
                    category, certain = suggestion(row)
                    if not certain or category == row['category']:
                        continue
                    cost_service.update_cost_details(row['id'], {
                        'category': category, 'cost_date': row['cost_date'], 'cost_no': row['cost_no'],
                        'counterparty_name': row['counterparty_name_snapshot'], 'vehicle_no': row['vehicle_no'],
                        'notes': row['notes'],
                    })
                    changes.append((row['id'], row['category'], category, row['amount_minor']))
            after = {r['id']: dict(r) for r in conn.execute("SELECT * FROM cost_entries")}
            for row in rows:
                assert {k: v for k, v in row.items() if k not in ('category', 'updated_at')} == {
                    k: v for k, v in after[row['id']].items() if k not in ('category', 'updated_at')}
            assert conn.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            assert not conn.execute('PRAGMA foreign_key_check').fetchall()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return changes


def upgrade(path, backup):
    before = snapshot(path)
    with patch('db.migration_runner._backup_database', return_value=backup):
        result = run_migrations(path)
    after_migration = snapshot(path)
    for name, rows in before.items():
        if name != 'schema_migrations':
            assert rows == after_migration[name], f'Migration changed {name}'
    changes = classify(path)
    after = snapshot(path)
    for name, rows in after_migration.items():
        if name not in ('cost_entries', 'cost_entry_revisions'):
            assert rows == after[name], f'Classification changed {name}'
    assert len(after['cost_entry_revisions']) - len(after_migration['cost_entry_revisions']) == len(changes)
    print('Migration:', result['applied'], 'Classifications:', len(changes), 'Amount unchanged:', sum(r[3] for r in changes) / 100)
    print('Changes:', changes)
    return changes


if __name__ == '__main__':
    live = connection.DB_PATH.resolve()
    folder = live.parent / 'backups' / ('cash_budget_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    backup = backup_database(live, folder / 'before.db')
    rehearsal = backup_database(backup, folder / 'rehearsal.db')
    baseline = snapshot(backup)
    print('Backup:', backup)
    changes = upgrade(rehearsal, str(backup))
    assert classify(rehearsal) == [], 'Classification must be idempotent'
    if '--apply' in sys.argv:
        assert snapshot(live) == baseline, 'Formal database changed during rehearsal; retry after inspecting'
        actual = upgrade(live, str(backup))
        assert actual == changes
        print('Production upgraded; existing amounts, allocations, cash transactions and other business tables unchanged.')
    else:
        print('Rehearsal only; production untouched.')
