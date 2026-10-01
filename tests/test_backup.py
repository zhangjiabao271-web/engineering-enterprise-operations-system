"""备份模块测试：db.backup.backup_database 与 services.backup_service.create_backup_archive。

全部读写都在 tempfile.TemporaryDirectory 内进行，不触碰项目目录。
"""

import hashlib
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest import mock

from db.backup import backup_database


class BackupDatabaseTests(unittest.TestCase):
    """db.backup.backup_database：SQLite 备份 API 快照及错误路径。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="backup_db_")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _make_source_db(self, rows=5):
        path = self.root / "source.db"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            conn.executemany(
                "INSERT INTO sample (id, name) VALUES (?, ?)",
                [(i, f"row{i}") for i in range(1, rows + 1)],
            )
            conn.commit()
        return path

    @staticmethod
    def _fetch_rows(db_path):
        with closing(sqlite3.connect(db_path)) as conn:
            return conn.execute("SELECT id, name FROM sample ORDER BY id").fetchall()

    def test_backup_produces_complete_usable_copy(self):
        source = self._make_source_db()
        destination = self.root / "copy.db"

        result = backup_database(source, destination)

        self.assertEqual(result, destination.resolve())
        with closing(sqlite3.connect(destination)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(integrity, "ok")
        self.assertEqual(self._fetch_rows(destination), self._fetch_rows(source))

    def test_backup_captures_committed_wal_contents(self):
        # WAL 模式下已提交但未 checkpoint 的数据只存在于 -wal 文件中；
        # 只有 SQLite 备份 API（而非裸文件复制）才能拿到一致快照。
        source = self.root / "wal_source.db"
        destination = self.root / "wal_copy.db"
        conn = sqlite3.connect(source)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA wal_autocheckpoint=0")
            conn.execute("CREATE TABLE sample (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
            conn.executemany(
                "INSERT INTO sample (id, name) VALUES (?, ?)",
                [(i, f"wal{i}") for i in range(1, 4)],
            )
            conn.commit()
            wal_file = Path(str(source) + "-wal")
            self.assertTrue(wal_file.is_file())
            self.assertGreater(wal_file.stat().st_size, 0)

            backup_database(source, destination)
        finally:
            conn.close()

        self.assertEqual(
            self._fetch_rows(destination),
            [(1, "wal1"), (2, "wal2"), (3, "wal3")],
        )

    def test_source_equals_destination_rejected(self):
        source = self._make_source_db()
        with self.assertRaisesRegex(ValueError, "备份不能覆盖原数据库"):
            backup_database(source, source)

    def test_missing_source_raises_file_not_found(self):
        missing = self.root / "no_such.db"
        destination = self.root / "copy.db"
        with self.assertRaises(FileNotFoundError):
            backup_database(missing, destination)
        self.assertFalse(destination.exists())

    def test_existing_destination_never_overwritten(self):
        source = self._make_source_db()
        destination = self.root / "copy.db"
        destination.write_bytes(b"sentinel")

        with self.assertRaises(FileExistsError):
            backup_database(source, destination)
        self.assertEqual(destination.read_bytes(), b"sentinel")

    def test_missing_destination_parent_directories_are_created(self):
        source = self._make_source_db()
        destination = self.root / "nested" / "deeper" / "copy.db"

        backup_database(source, destination)

        self.assertTrue(destination.is_file())
        self.assertEqual(self._fetch_rows(destination), self._fetch_rows(source))

    def test_destination_parent_component_is_file_raises(self):
        source = self._make_source_db()
        blocker = self.root / "blocker"
        blocker.write_bytes(b"file")
        destination = blocker / "copy.db"

        with self.assertRaises(OSError):
            backup_database(source, destination)

    def test_integrity_failure_cleans_up_partial_destination(self):
        # 模拟快照完整性校验失败：模块应删除残缺副本并抛出 RuntimeError。
        source = self._make_source_db()
        destination = self.root / "copy.db"
        real_connect = sqlite3.connect

        class FakeCursor:
            def fetchone(self):
                return ("corruption detected",)

        class IntegrityFailingConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if "integrity_check" in str(sql).lower():
                    return FakeCursor()
                return super().execute(sql, *args, **kwargs)

        def connect_with_factory(*args, **kwargs):
            kwargs["factory"] = IntegrityFailingConnection
            return real_connect(*args, **kwargs)

        with mock.patch("db.backup.sqlite3.connect", side_effect=connect_with_factory):
            with self.assertRaisesRegex(RuntimeError, "备份完整性检查失败"):
                backup_database(source, destination)
        self.assertFalse(destination.exists())


class BackupServiceTests(unittest.TestCase):
    """services.backup_service.create_backup_archive：zip 产物、manifest、附件与错误路径。"""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="backup_service_")
        cls.test_db = Path(cls.temp_dir.name) / "supplier_data.db"
        source_db = Path(__file__).resolve().parent.parent / "supplier_data.db"
        _oss_source_db = source_db
        if _oss_source_db.exists():
            backup_database(_oss_source_db, cls.test_db)
        else:
            # 开源环境无生产库：从空库初始化基础表并跑全量迁移构建测试库
            import db.connection as _conn_module
            import db.migration_runner as _runner_module
            _saved_paths = (_conn_module.DB_PATH, _runner_module.DB_PATH)
            _conn_module.DB_PATH = cls.test_db
            _runner_module.DB_PATH = cls.test_db
            try:
                from db.schema import init_db as _init_db
                _init_db()
            finally:
                _conn_module.DB_PATH, _runner_module.DB_PATH = _saved_paths
        import db.connection as connection
        from db.migration_runner import run_migrations

        cls.original_db_path = connection.DB_PATH
        run_migrations(cls.test_db)
        connection.DB_PATH = cls.test_db

        from services import backup_service

        cls.backup_service = backup_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="backup_archive_")
        self.root = Path(self.temp.name)
        # 实时库副本里带有真实附件行，清掉以保证每个用例的基线确定。
        with closing(sqlite3.connect(self.test_db)) as conn:
            conn.execute("DELETE FROM business_attachments")
            conn.execute("DELETE FROM construction_photos")
            conn.commit()

    def tearDown(self):
        self.temp.cleanup()

    def _add_business_attachment(self, file_path, public_id):
        with closing(sqlite3.connect(self.test_db)) as conn:
            contract_id = conn.execute("SELECT id FROM contracts LIMIT 1").fetchone()[0]
            conn.execute(
                """
                INSERT INTO business_attachments (
                    public_id, organization_id, contract_id, file_path,
                    original_name, description, status, created_at, updated_at
                ) VALUES (?, 1, ?, ?, ?, '', 'active', '2026-09-17 10:00:00', '2026-09-17 10:00:00')
                """,
                (public_id, contract_id, file_path, Path(file_path).name),
            )
            conn.commit()

    @staticmethod
    def _expected_archive_name(stored_path):
        suffix = Path(stored_path).suffix
        digest = hashlib.sha256(stored_path.encode("utf-8")).hexdigest()
        return "files/" + digest + suffix

    def test_archive_layout_and_manifest(self):
        destination = self.root / "backup.zip"

        manifest = self.backup_service.create_backup_archive(destination)

        self.assertTrue(destination.is_file())
        self.assertTrue(zipfile.is_zipfile(destination))
        self.assertEqual(manifest["database"], "supplier_data.db")
        self.assertEqual(manifest["files"], [])
        self.assertEqual(manifest["missing_files"], [])
        with zipfile.ZipFile(destination) as archive:
            names = set(archive.namelist())
            self.assertIn("supplier_data.db", names)
            self.assertIn("manifest.json", names)
            self.assertIn("恢复说明.txt", names)
            stored_manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        self.assertEqual(stored_manifest, manifest)

    @unittest.skipUnless((Path(__file__).resolve().parent.parent / "supplier_data.db").exists(), "依赖本地生产库数据，开源环境跳过")
    def test_archived_database_is_usable_snapshot(self):
        destination = self.root / "backup.zip"
        self.backup_service.create_backup_archive(destination)

        extracted = self.root / "extracted.db"
        with zipfile.ZipFile(destination) as archive:
            extracted.write_bytes(archive.read("supplier_data.db"))

        with closing(sqlite3.connect(extracted)) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            counts = {
                table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                for table in ("projects", "contracts", "business_partners", "purchase_orders")
            }
        self.assertEqual(integrity, "ok")
        with closing(sqlite3.connect(self.test_db)) as conn:
            for table, count in counts.items():
                expected = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                self.assertEqual(count, expected, table)
                self.assertGreater(count, 0, table)

    @unittest.skipUnless((Path(__file__).resolve().parent.parent / "supplier_data.db").exists(), "依赖本地生产库数据，开源环境跳过")
    def test_existing_attachments_are_packed_with_manifest_entries(self):
        first = self.root / "验收单.pdf"
        first.write_bytes(b"pdf-content")
        second = self.root / "说明.txt"
        second.write_bytes("附件内容".encode("utf-8"))
        self._add_business_attachment(str(first), "ATT-TEST-1")
        self._add_business_attachment(str(second), "ATT-TEST-2")

        destination = self.root / "backup.zip"
        manifest = self.backup_service.create_backup_archive(destination)

        self.assertEqual(manifest["missing_files"], [])
        self.assertEqual(len(manifest["files"]), 2)
        expected = {
            str(first): (self._expected_archive_name(str(first)), first.read_bytes()),
            str(second): (self._expected_archive_name(str(second)), second.read_bytes()),
        }
        with zipfile.ZipFile(destination) as archive:
            for entry in manifest["files"]:
                stored = entry["original_path"]
                archive_name, content = expected[stored]
                self.assertEqual(entry["archive_path"], archive_name)
                self.assertEqual(archive.read(archive_name), content)

    @unittest.skipUnless((Path(__file__).resolve().parent.parent / "supplier_data.db").exists(), "依赖本地生产库数据，开源环境跳过")
    def test_missing_attachment_recorded_not_fatal(self):
        ghost = self.root / "已删除.pdf"
        self._add_business_attachment(str(ghost), "ATT-TEST-GHOST")

        destination = self.root / "backup.zip"
        manifest = self.backup_service.create_backup_archive(destination)

        self.assertEqual(manifest["files"], [])
        self.assertEqual(manifest["missing_files"], [str(ghost)])
        self.assertTrue(zipfile.is_zipfile(destination))

    def test_existing_destination_not_overwritten(self):
        destination = self.root / "backup.zip"
        self.backup_service.create_backup_archive(destination)
        original_bytes = destination.read_bytes()

        with self.assertRaises(FileExistsError):
            self.backup_service.create_backup_archive(destination)
        self.assertEqual(destination.read_bytes(), original_bytes)

        second = self.root / "backup2.zip"
        self.backup_service.create_backup_archive(second)
        self.assertTrue(second.is_file())
        self.assertTrue(destination.is_file())

    def test_missing_destination_parent_raises(self):
        destination = self.root / "no_such_dir" / "backup.zip"

        with self.assertRaises(FileNotFoundError):
            self.backup_service.create_backup_archive(destination)
        self.assertFalse(destination.exists())

    def test_zip_write_failure_cleans_up_partial_archive(self):
        # 模拟打包中途失败：模块应删除半成品 zip 并把异常抛给调用方。
        class FailingZipFile(zipfile.ZipFile):
            def write(self, *args, **kwargs):
                raise OSError("模拟磁盘写入失败")

        destination = self.root / "backup.zip"
        with mock.patch.object(self.backup_service.zipfile, "ZipFile", FailingZipFile):
            with self.assertRaises(OSError):
                self.backup_service.create_backup_archive(destination)
        self.assertFalse(destination.exists())


class RestoreBackupTests(unittest.TestCase):
    """services.backup_service.restore_backup_archive：覆盖恢复、附件还原与坏包拒绝。"""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="restore_service_")
        cls.source_db = Path(cls.temp_dir.name) / "source.db"
        cls.live_db = Path(cls.temp_dir.name) / "live.db"
        production = Path(__file__).resolve().parent.parent / "supplier_data.db"
        _oss_source_db = production
        if _oss_source_db.exists():
            backup_database(_oss_source_db, cls.source_db)
        else:
            # 开源环境无生产库：从空库初始化基础表并跑全量迁移构建测试库
            import db.connection as _conn_module
            import db.migration_runner as _runner_module
            _saved_paths = (_conn_module.DB_PATH, _runner_module.DB_PATH)
            _conn_module.DB_PATH = cls.source_db
            _runner_module.DB_PATH = cls.source_db
            try:
                from db.schema import init_db as _init_db
                _init_db()
            finally:
                _conn_module.DB_PATH, _runner_module.DB_PATH = _saved_paths
        import db.connection as connection

        cls.connection = connection
        cls.original_db_path = connection.DB_PATH
        connection.DB_PATH = cls.live_db

        from services import backup_service

        cls.backup_service = backup_service

    @classmethod
    def tearDownClass(cls):
        cls.connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="restore_case_")
        self.root = Path(self.temp.name)
        # 每个用例从干净快照重建“当前库”
        self.live_db.unlink(missing_ok=True)
        backup_database(self.source_db, self.live_db)
        # 清掉快照里的真实附件行，避免恢复用例写真实附件目录
        with closing(sqlite3.connect(self.live_db)) as conn:
            conn.execute("DELETE FROM business_attachments")
            conn.execute("DELETE FROM construction_photos")
            conn.commit()

    def tearDown(self):
        self.temp.cleanup()

    def test_unsafe_attachment_rejected_before_database_restore(self):
        for target in ('main.py', 'config.ini', 'supplier_data.db', 'attachments/../main.py'):
            with self.subTest(target=target):
                archive = self.root / 'unsafe.zip'
                manifest = {'database':'supplier_data.db', 'files':[
                    {'original_path':target, 'archive_path':'files/evil.pdf'}]}
                with zipfile.ZipFile(archive, 'w') as zf:
                    zf.write(self.source_db, 'supplier_data.db')
                    zf.writestr('files/evil.pdf', b'unsafe')
                    zf.writestr('manifest.json', json.dumps(manifest))
                with mock.patch.object(self.backup_service, 'backup_database') as backup:
                    with self.assertRaisesRegex(ValueError, '附件目录'):
                        self.backup_service.restore_backup_archive(archive)
                    backup.assert_not_called()

    def test_restore_targets_allow_configured_storage_and_reject_duplicates(self):
        storage = self.root / 'attachments'
        with mock.patch('services.attachment_service._storage_root', return_value=storage):
            item = {'original_path':str(storage/'original.pdf')}
            self.assertEqual(self.backup_service._restore_targets({'files':[item]}), [(storage/'original.pdf').resolve()])
            with self.assertRaisesRegex(ValueError, '重复'):
                self.backup_service._restore_targets({'files':[item,item]})

    def test_archive_rejects_windows_path_escape(self):
        archive = self.root / 'windows_escape.zip'
        with zipfile.ZipFile(archive, 'w') as zf:
            zf.write(self.source_db, 'supplier_data.db')
            zf.writestr('manifest.json', json.dumps({'database':'supplier_data.db', 'files':[]}))
            zf.writestr('files/..\\..\\escaped.py', b'unsafe')
        with self.assertRaisesRegex(ValueError, '非法路径'):
            self.backup_service.restore_backup_archive(archive)
        self.assertFalse((self.root/'escaped.py').exists())

    def test_restore_rejects_attachment_symlink_escape(self):
        storage = self.root / 'attachments'
        outside = self.root / 'outside'
        storage.mkdir()
        outside.mkdir()
        try:
            (storage/'linked').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('当前环境不允许创建符号链接')
        with mock.patch('services.attachment_service._storage_root', return_value=storage):
            with self.assertRaisesRegex(ValueError, '附件目录'):
                self.backup_service._restore_targets({'files':[{'original_path':str(storage/'linked'/'main.py')}]})

    def test_round_trip_reverts_changes_and_keeps_safety_copy(self):
        archive = self.root / "backup.zip"
        self.backup_service.create_backup_archive(archive)
        with closing(sqlite3.connect(self.source_db)) as conn:
            project_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]

        # 模拟恢复前库已被改动
        with closing(sqlite3.connect(self.live_db)) as conn:
            conn.execute("CREATE TABLE marker_table (id INTEGER)")
            conn.execute("DELETE FROM projects")
            conn.commit()

        result = self.backup_service.restore_backup_archive(
            archive, safety_backup_dir=self.root / "safety"
        )

        with closing(sqlite3.connect(self.live_db)) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            restored_count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
        self.assertNotIn("marker_table", tables)
        self.assertEqual(restored_count, project_count)
        self.assertTrue(Path(result["safety_backup"]).is_file())

    def test_restore_recovers_deleted_attachment(self):
        # Use the isolated attachment root, never a checkout's business folder.
        from services.attachment_service import _storage_root
        attachment_file = _storage_root() / "restore_test_回执单.pdf"
        attachment_file.parent.mkdir(parents=True, exist_ok=True)
        attachment_file.write_bytes(b"%PDF-fake")
        self.addCleanup(attachment_file.unlink, missing_ok=True)
        stored_path = str(attachment_file)
        with closing(sqlite3.connect(self.live_db)) as conn:
            cursor = conn.execute(
                """
                INSERT INTO contracts (
                    public_id, organization_id, contract_no, name,
                    sign_date, tax_inclusive_amount_minor,
                    created_at, updated_at
                ) VALUES ('C-RESTORE-TEST', 1, 'C-RESTORE-TEST', '恢复测试合同',
                          '2026-09-18', 0, '2026-09-18 10:00:00', '2026-09-18 10:00:00')
                """
            )
            conn.execute(
                """
                INSERT INTO business_attachments (
                    public_id, organization_id, contract_id, file_path,
                    original_name, description, status, created_at, updated_at
                ) VALUES ('ATT-RESTORE-1', 1, ?, ?, '回执单.pdf', '', 'active',
                          '2026-09-18 10:00:00', '2026-09-18 10:00:00')
                """,
                (cursor.lastrowid, stored_path),
            )
            conn.commit()
        archive = self.root / "backup.zip"
        self.backup_service.create_backup_archive(archive)

        attachment_file.unlink()
        result = self.backup_service.restore_backup_archive(
            archive, safety_backup_dir=self.root / "safety"
        )

        self.assertEqual(attachment_file.read_bytes(), b"%PDF-fake")
        self.assertIn(stored_path, result["restored_files"])
        self.assertEqual(result["skipped_files"], [])

    def test_rejects_archive_without_manifest(self):
        archive = self.root / "broken.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.write(self.source_db, "supplier_data.db")

        with self.assertRaisesRegex(ValueError, "缺少数据库或清单"):
            self.backup_service.restore_backup_archive(
                archive, safety_backup_dir=self.root / "safety"
            )

    def test_rejects_corrupt_database_member(self):
        archive = self.root / "corrupt.zip"
        manifest = {"database": "supplier_data.db", "files": [], "missing_files": []}
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("supplier_data.db", b"not a sqlite database")
            zf.writestr("manifest.json", json.dumps(manifest))

        with self.assertRaises((ValueError, sqlite3.DatabaseError)):
            self.backup_service.restore_backup_archive(
                archive, safety_backup_dir=self.root / "safety"
            )


if __name__ == "__main__":
    unittest.main()
