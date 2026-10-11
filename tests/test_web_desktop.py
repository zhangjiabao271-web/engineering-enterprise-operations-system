"""Desktop startup boundaries, with temporary ledgers and no production writes."""

import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

from web_workbench import desktop, runtime


class DesktopStartupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='desktop_test_')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)

    def ledger(self, *, pending=False):
        from db.migrations import MIGRATIONS
        path = self.directory / 'ledger.db'
        with closing(sqlite3.connect(path)) as conn:
            conn.execute('CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)')
            versions = MIGRATIONS[:-1] if pending else MIGRATIONS
            conn.executemany('INSERT INTO schema_migrations VALUES(?)', [(row[0],) for row in versions])
            conn.commit()
        return path

    def test_missing_or_ambiguous_selection_never_creates_a_ledger(self):
        path = self.directory / 'missing.db'
        for arguments in ({'db': path}, {}, {'db': path, 'demo': self.directory}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                runtime.prepare_database(**arguments)
        self.assertFalse(path.exists())

    def test_existing_db_is_inspected_without_writes_or_backups(self):
        path = self.ledger()
        before = path.read_bytes()
        self.assertEqual(runtime.prepare_database(db=path), path)
        self.assertEqual(os.environ['SUPPLY_CHAIN_DB_PATH'], str(path))
        self.assertEqual(before, path.read_bytes())
        self.assertFalse((path.parent / 'backups').exists())

    def test_pending_or_invalid_db_is_not_upgraded(self):
        path = self.ledger(pending=True)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, '不会自动执行'):
            runtime.prepare_database(db=path)
        self.assertEqual(before, path.read_bytes())
        invalid = self.directory / 'invalid.db'
        invalid.write_bytes(b'not a database')
        with self.assertRaisesRegex(ValueError, '不是可用'):
            runtime.prepare_database(db=invalid)
        self.assertEqual(invalid.read_bytes(), b'not a database')

    def test_demo_refuses_existing_ledger(self):
        path = self.directory / 'supplier_data.db'
        path.write_bytes(b'preserve')
        with self.assertRaisesRegex(ValueError, '已有数据库'):
            runtime.prepare_database(demo=self.directory)
        self.assertEqual(path.read_bytes(), b'preserve')

    def test_demo_selects_database_and_attachments_before_service_import(self):
        directory = self.directory / 'fresh'
        script = ('from web_workbench.runtime import prepare_database; '
                  'from pathlib import Path; import os, sqlite3; '
                  f'p=prepare_database(demo=Path({str(directory)!r})); '
                  'assert os.environ["SUPPLY_CHAIN_ATTACHMENTS_PATH"]==str(p.parent/"attachments"); '
                  'c=sqlite3.connect(p); assert c.execute("SELECT COUNT(*) FROM projects").fetchone()[0]>0; '
                  'assert c.execute("PRAGMA integrity_check").fetchone()[0]=="ok"; c.close()')
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script],
                                cwd=desktop.ROOT, capture_output=True, text=True, timeout=25)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((directory / 'supplier_data.db').exists())

    def test_explicit_write_creates_a_non_overwriting_backup(self):
        path = self.ledger()
        runtime.prepare_database(db=path, allow_write=True)
        backups = list((path.parent / 'backups').glob('before_web_write_*.db'))
        self.assertEqual(len(backups), 1)
        with closing(sqlite3.connect(backups[0])) as backup, closing(sqlite3.connect(path)) as source:
            self.assertEqual(backup.execute('SELECT * FROM schema_migrations').fetchall(),
                             source.execute('SELECT * FROM schema_migrations').fetchall())

    def assert_closed(self, address):
        with socket.socket() as client:
            client.settimeout(.2)
            self.assertNotEqual(client.connect_ex(address), 0)

    def test_server_socket_stops_on_normal_and_exceptional_exit(self):
        for failure in (False, True):
            with self.subTest(failure=failure):
                try:
                    with runtime.local_server(read_only=True, demo=False) as server:
                        address = server.server_address
                        with urllib.request.urlopen(server.origin + '/motion.js') as response:
                            self.assertEqual(response.status, 200)
                        self.assertTrue(server.read_only)
                        if failure:
                            raise RuntimeError('synthetic window failure')
                except RuntimeError:
                    self.assertTrue(failure)
                self.assert_closed(address)
                self.assertFalse(server.staging.exists())

    def test_native_backend_is_owned_and_closed_even_if_webview_fails(self):
        webview = Mock(settings={})
        addresses = []
        def create(title, origin, **kwargs):
            from urllib.parse import urlsplit
            addresses.append(('127.0.0.1', urlsplit(origin).port))
            self.assertIn('只读', title)
            self.assertLessEqual(kwargs['min_size'][0], kwargs['width'])
            self.assertLessEqual(kwargs['min_size'][1], kwargs['height'])
            self.assertTrue(kwargs['maximized'])
        webview.create_window.side_effect = create
        for failure in (False, True):
            webview.start.side_effect = RuntimeError('synthetic WebView failure') if failure else None
            if failure:
                with self.assertRaises(RuntimeError):
                    desktop.run_window(webview, read_only=True, demo=False, profile=self.directory)
            else:
                desktop.run_window(webview, read_only=True, demo=False, profile=self.directory)
            self.assert_closed(addresses[-1])
        self.assertTrue(webview.settings['ALLOW_DOWNLOADS'])
        self.assertEqual(webview.start.call_args.kwargs['gui'], 'edgechromium')
        self.assertFalse(webview.start.call_args.kwargs['debug'])

    def test_window_title_is_plain_for_live_and_explicit_for_other_modes(self):
        cases = (
            (False, False, '工程经营工作台'),
            (True, False, '工程经营工作台 · 只读'),
            (False, True, '工程经营工作台 · 演示'),
            (True, True, '工程经营工作台 · 演示'),
        )
        with patch('web_workbench.runtime.local_server') as local_server:
            local_server.return_value.__enter__.return_value.origin = 'http://127.0.0.1:12345'
            for read_only, demo, expected in cases:
                with self.subTest(read_only=read_only, demo=demo):
                    webview = Mock(settings={})
                    desktop.run_window(webview, read_only=read_only, demo=demo, profile=self.directory)
                    self.assertEqual(webview.create_window.call_args.args[0], expected)

    @unittest.skipUnless(os.name == 'nt', 'Windows named mutex')
    def test_same_ledger_cannot_open_twice_and_lock_is_released(self):
        path = self.directory / 'unique-ledger.db'
        with desktop.single_window(path):
            with self.assertRaisesRegex(ValueError, '已经打开'):
                with desktop.single_window(path):
                    self.fail('second native window acquired the mutex')
        with desktop.single_window(path):
            pass

    def test_native_preferences_persist_only_valid_visual_booleans(self):
        path = self.directory / 'profile' / 'ui-preferences.json'
        preferences = desktop.PresentationPreferences(path)
        self.assertEqual(preferences.get_ui_preferences(), {'reduced': False, 'solid': False})
        preferences.save_ui_preferences(True, True)
        self.assertEqual(desktop.PresentationPreferences(path).get_ui_preferences(), {'reduced': True, 'solid': True})
        with self.assertRaises(ValueError):
            preferences.save_ui_preferences('true', 1)
        self.assertEqual(desktop.PresentationPreferences(path).get_ui_preferences(), {'reduced': True, 'solid': True})
        self.assertFalse(path.with_suffix('.tmp').exists())

    def test_window_fits_small_and_large_work_areas(self):
        self.assertEqual(desktop.window_size((1920, 1080)), (1380, 920))
        self.assertEqual(desktop.window_size((1366, 864)), (1334, 832))
        self.assertEqual(desktop.window_size((800, 600)), (768, 568))


if __name__ == '__main__':
    unittest.main()
