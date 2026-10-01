import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from db import connection
from db.backup import backup_database
from db.migration_runner import run_migrations
from services import backup_service, database_maintenance, project_profit_service, cost_service


class DatabaseOptimizationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='db_optimization_')
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.db = backup_database(connection.DB_PATH, self.root / 'isolated.db')
        run_migrations(self.db)
        self.path_patch = patch.object(connection, 'DB_PATH', self.db)
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def test_read_connection_rejects_writes_and_missing_database(self):
        with connection.db_read() as conn:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('CREATE TABLE forbidden(id INTEGER)')
            conn.execute('PRAGMA query_only=OFF')
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('CREATE TABLE still_forbidden(id INTEGER)')
        missing = self.root / 'missing.db'
        with self.assertRaises(sqlite3.OperationalError), connection.db_read(missing):
            pass
        self.assertFalse(missing.exists())

    def test_new_index_covers_worker_and_date(self):
        with connection.db_read() as conn:
            plan = conn.execute("""EXPLAIN QUERY PLAN SELECT SUM(amount_minor) FROM work_logs
                WHERE COALESCE(status,'active')='active' AND worker_id=1
                AND work_date>='2026-01-01' AND work_date<='2026-12-31'""").fetchall()
            self.assertIn('idx_work_logs_active_worker_date', str([tuple(r) for r in plan]))

    def test_portfolio_equals_individual_results_with_fixed_query_count(self):
        statements = []
        @contextmanager
        def traced():
            with connection.db_read() as conn:
                conn.set_trace_callback(statements.append)
                yield conn
        with patch.object(project_profit_service, 'db_read', traced):
            portfolio = project_profit_service.get_portfolio_summary()
        selects = [s for s in statements if s.lstrip().upper().startswith(('SELECT', 'WITH'))]
        self.assertLessEqual(len(selects), 10)
        for row in portfolio['projects']:
            single = project_profit_service.get_project_summary(row['project']['id'])
            for key, value in row.items():
                self.assertEqual(value, single[key], (row['project']['id'], key))

    def test_daily_backup_archive_verification_and_no_duplicates(self):
        now = datetime(2026, 9, 19, 1)
        directory = self.root / 'archives'
        first = database_maintenance.daily_backup(directory=directory, now=now)
        self.assertIn(first['status'], ('verified', 'warning'))
        again = database_maintenance.daily_backup(directory=directory, now=now + timedelta(hours=2))
        self.assertEqual(first['archive'], again['archive'])
        check = backup_service.inspect_backup_archive(first['archive'])
        self.assertIn('database_sha256', check['manifest'])
        bad = self.root / 'tampered.zip'
        with zipfile.ZipFile(first['archive']) as original, zipfile.ZipFile(bad, 'w') as target:
            for name in original.namelist():
                content = original.read(name)
                if name == 'manifest.json':
                    manifest = json.loads(content)
                    manifest['database_sha256'] = 'bad'
                    content = json.dumps(manifest).encode()
                target.writestr(name, content)
        with self.assertRaisesRegex(ValueError, '校验码'):
            backup_service.inspect_backup_archive(bad)

    def test_wal_reader_writer_and_backup_restore(self):
        with closing(connection.get_connection()) as writer:
            self.assertEqual(writer.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
            writer.execute('CREATE TABLE wal_probe(id INTEGER)')
            writer.commit()
            with connection.db_read() as reader:
                self.assertEqual(reader.execute('SELECT COUNT(*) FROM wal_probe').fetchone()[0], 0)
                writer.execute('INSERT INTO wal_probe VALUES(1)')
                writer.commit()
                self.assertEqual(reader.execute('SELECT COUNT(*) FROM wal_probe').fetchone()[0], 0)
                snapshot = backup_database(self.db, self.root / 'wal_snapshot.db')
            with closing(sqlite3.connect(snapshot)) as restored:
                self.assertEqual(restored.execute('SELECT COUNT(*) FROM wal_probe').fetchone()[0], 1)
                self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
                self.assertEqual(restored.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_cost_dashboard_matches_ledger_and_categories_conserve_total(self):
        projects = [None] + [p['project']['id'] for p in project_profit_service.get_portfolio_summary()['projects']]
        for project_id in projects:
            ledger = cost_service.list_cost_ledger(project_id)
            months = {r['business_date'][:7] for r in ledger if r.get('business_date')}
            for month in months:
                result = cost_service.get_cost_dashboard(month, project_id)
                expected = sum(r['amount_minor'] for r in ledger if r['business_date'].startswith(month))
                self.assertEqual(result['summary']['total_minor'], expected, (project_id, month))
                self.assertEqual(sum(v for _, v in result['by_category']), expected, (project_id, month))
                self.assertEqual(sum(r['amount_minor'] for r in result['by_project']), expected, (project_id, month))


if __name__ == '__main__':
    unittest.main()
