"""The cutover verifier only reads ledgers and saves sanitized evidence."""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from scripts.verify_desktop_cutover import inspect_ledger, inspect_origin


class DesktopCutoverEvidenceTests(unittest.TestCase):
    def test_ledger_inspection_preserves_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ledger.db'
            tables = ('projects', 'receipts', 'historical_receipts',
                      'sales_invoices', 'purchase_orders', 'work_logs')
            with closing(sqlite3.connect(path)) as conn:
                conn.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)')
                conn.execute('INSERT INTO schema_migrations VALUES(630)')
                for table in tables:
                    conn.execute(f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY)')
                    conn.execute(f'INSERT INTO "{table}" VALUES(1)')
                conn.commit()
            before = path.read_bytes()
            actual = inspect_ledger(path)
            self.assertEqual(actual['schema_version'], 630)
            self.assertEqual(actual['integrity'], 'ok')
            self.assertEqual(actual['foreign_key_violations'], 0)
            self.assertEqual(actual['counts'], dict.fromkeys(tables, 1))
            self.assertEqual(before, path.read_bytes())
            self.assertEqual(len(actual['digest']), 64)

    def test_origin_evidence_excludes_session_data(self):
        boot = {'read_only': False, 'demo': False, 'rehearsal': True,
                'csrf': 'synthetic-session-only', 'options': {'synthetic': True}}
        with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps(boot).encode())):
            self.assertEqual(inspect_origin('http://127.0.0.1:12345'),
                             {'read_only': False, 'demo': False, 'rehearsal': True})

    def test_remote_origins_are_refused_before_network_access(self):
        with patch('urllib.request.urlopen') as request:
            for origin in ('https://127.0.0.1:12345', 'http://example.com:12345',
                           'http://localhost:12345', 'http://127.0.0.1'):
                with self.subTest(origin=origin), self.assertRaises(ValueError):
                    inspect_origin(origin)
            request.assert_not_called()
