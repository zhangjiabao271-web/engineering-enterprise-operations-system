import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services import initial_setup_service as setup


class InitialSetupPrivacyTests(unittest.TestCase):
    def setUp(self):
        from db import connection, migration_runner
        from db.schema import init_db
        folder = tempfile.TemporaryDirectory(prefix='new_ledger_privacy_')
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'synthetic.db'
        for module in (connection, migration_runner):
            override = patch.object(module, 'DB_PATH', self.path)
            override.start()
            self.addCleanup(override.stop)
        init_db()

    def test_new_install_has_only_anonymous_defaults_and_no_private_seed_bytes(self):
        from db.connection import db_read
        from db.migrations.operating_entities import add_operating_entities
        with db_read() as conn:
            names = {row[0] for row in conn.execute('SELECT name FROM operating_entities')}
        self.assertEqual(names, set(setup.DEFAULT_ENTITIES.values()))
        with sqlite3.connect(':memory:') as legacy:
            add_operating_entities(legacy)
            historical_names = {row[0] for row in legacy.execute('SELECT name FROM operating_entities')}
        content = self.path.read_bytes()
        for name in historical_names - names:
            self.assertNotIn(name.encode('utf-8'), content)

    def test_configured_entities_survive_restart_and_cannot_be_reconfigured(self):
        from db.schema import init_db
        from db.connection import db_read
        setup.configure_entity(1, '合成实际经营主体', 'SYNTHETIC-TAX', '首次设置')
        init_db()
        with db_read() as conn:
            self.assertEqual(conn.execute('SELECT name FROM operating_entities WHERE id=1').fetchone()[0], '合成实际经营主体')
        with self.assertRaisesRegex(ValueError, '已设置'):
            setup.configure_entity(1, '合成其他主体', reason='改名')

    def test_existing_business_cannot_be_initialized_as_a_new_ledger(self):
        from services import project_service
        project_service.create_project({'name':'合成现有业务','status':'进行中'})
        with self.assertRaisesRegex(ValueError, '已有经营事实'):
            setup.initialize_new_ledger(self.path)

    def test_release_guard_rejects_names_artifacts_and_seeds_without_printing_values(self):
        from scripts.check_release_privacy import check_files
        root = self.path.parent
        source = root / 'sample.py'
        source.write_text("name='合成保密名称'", encoding='utf-8')
        findings = check_files(root, ['sample.py','supplier_data.db'], ['合成保密名称'])
        self.assertIn(('sample.py','private-entity-name'), findings)
        self.assertIn(('supplier_data.db','private-artifact'), findings)
        self.assertNotIn('合成保密名称', repr(findings))

    def test_release_guard_rejects_business_documents_binaries_and_export_dirs(self):
        from scripts.check_release_privacy import check_files
        root = self.path.parent
        (root / 'output').mkdir(exist_ok=True)
        (root / 'output' / 'export.md').write_text('普通文本', encoding='utf-8')
        (root / '合同扫描件.pdf').write_bytes(b'%PDF-1.4\x00\x01binary')
        (root / '现场照片.jpg').write_bytes(b'\xff\xd8\xff\xe0\x00\x10JFIF')
        (root / 'app.ico').write_bytes(b'\x00\x01icon-binary')
        findings = check_files(
            root,
            ['output/export.md', '合同扫描件.pdf', '现场照片.jpg', 'app.ico'],
        )
        self.assertIn(('output/export.md', 'private-artifact'), findings)
        self.assertIn(('合同扫描件.pdf', 'private-artifact'), findings)
        self.assertIn(('现场照片.jpg', 'binary-artifact'), findings)
        self.assertNotIn(('app.ico', 'binary-artifact'), findings)

    def test_release_guard_reads_chinese_and_whitespace_paths_without_git_quoting(self):
        import subprocess
        from scripts.check_release_privacy import release_files
        with patch('scripts.check_release_privacy.subprocess.run',
                   return_value=subprocess.CompletedProcess([], 0, '资料/中文 名称.md\0sample.py\0')) as run:
            self.assertEqual(release_files(self.path.parent), ['资料/中文 名称.md', 'sample.py'])
        self.assertIn('-z', run.call_args.args[0])
        self.assertIn('core.quotepath=false', run.call_args.args[0])
