"""Restore fault injection; every database and attachment lives in a temp folder."""

import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from services import backup_service as backup


class RestoreRollbackTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.current = self.root / 'current.db'
        self.incoming = self.root / 'incoming.db'
        self.storage = self.root / 'attachments'
        self.storage.mkdir()
        self.safety = self.root / 'safety'
        for path, marker in ((self.current, 'original'), (self.incoming, 'incoming')):
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.execute('CREATE TABLE marker(value TEXT)')
                conn.execute('INSERT INTO marker VALUES(?)', (marker,))
        for patcher in (patch.object(backup.connection, 'DB_PATH', self.current),
                        patch('services.attachment_service._storage_root', return_value=self.storage)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def archive(self, count=3):
        path = self.root / 'backup.zip'
        entries = [{'original_path': str(self.storage / f'{index}.pdf'),
                    'archive_path': f'files/{index}.pdf'} for index in range(count)]
        with zipfile.ZipFile(path, 'w') as packed:
            packed.write(self.incoming, 'supplier_data.db')
            for index, entry in enumerate(entries):
                packed.writestr(entry['archive_path'], f'incoming-{index}'.encode())
            packed.writestr('manifest.json', json.dumps({'database': 'supplier_data.db', 'files': entries}))
        return path

    def marker(self):
        with closing(sqlite3.connect(self.current)) as conn:
            return conn.execute('SELECT value FROM marker').fetchone()[0]

    def restore(self, archive):
        return backup.restore_backup_archive(archive, safety_backup_dir=self.safety)

    def test_later_failure_restores_overwritten_files_and_removes_new_file(self):
        original = self.storage / '0.pdf'
        original.write_bytes(b'original-file')
        unrelated = self.storage / 'unrelated.pdf'
        unrelated.write_bytes(b'unrelated')
        archive = self.archive()
        real_replace = backup._replace_attachment

        def replace(source, target):
            if target.name == '2.pdf' and source.parent.name == 'files' and source.name == '2.pdf':
                raise PermissionError('simulated attachment failure')
            real_replace(source, target)

        with patch.object(backup, '_replace_attachment', side_effect=replace):
            with self.assertRaisesRegex(RuntimeError, '已回滚数据库'):
                self.restore(archive)
        self.assertEqual(self.marker(), 'original')
        self.assertEqual(original.read_bytes(), b'original-file')
        self.assertFalse((self.storage / '1.pdf').exists())
        self.assertFalse((self.storage / '2.pdf').exists())
        self.assertEqual(unrelated.read_bytes(), b'unrelated')
        self.assertEqual(len(list(self.safety.glob('*/before.db'))), 1)
        manifest = json.loads(next(self.safety.glob('*/rollback_manifest.json')).read_text(encoding='utf-8'))
        self.assertIsNone(manifest['files'][1]['before'])
        self.assertEqual(Path(manifest['files'][0]['before']).read_bytes(), b'original-file')

    def test_first_attachment_failure_rolls_back_database(self):
        archive = self.archive(1)
        with patch.object(backup, '_replace_attachment', side_effect=PermissionError('denied')):
            with self.assertRaisesRegex(RuntimeError, '已回滚数据库'):
                self.restore(archive)
        self.assertEqual(self.marker(), 'original')

    def test_database_failure_after_replacement_is_rolled_back(self):
        archive = self.archive(0)
        real_restore = backup._restore_database

        def restore(path):
            real_restore(path)
            if Path(path).name != 'before.db':
                raise RuntimeError('simulated post-restore check failure')

        with patch.object(backup, '_restore_database', side_effect=restore):
            with self.assertRaisesRegex(RuntimeError, '已回滚数据库'):
                self.restore(archive)
        self.assertEqual(self.marker(), 'original')

    def test_failed_preservation_does_not_touch_live_state(self):
        archive = self.archive(1)
        original = self.storage / '0.pdf'
        original.write_bytes(b'original-file')
        with patch.object(backup.shutil, 'copy2', side_effect=PermissionError('cannot save original')):
            with self.assertRaises(PermissionError):
                self.restore(archive)
        self.assertEqual(self.marker(), 'original')
        self.assertEqual(original.read_bytes(), b'original-file')

    def test_attachment_rollback_failure_preserves_materials_and_reports_incomplete(self):
        archive = self.archive(1)
        original = self.storage / '0.pdf'
        original.write_bytes(b'original-file')
        with patch.object(backup, '_replace_attachment', side_effect=PermissionError('still locked')):
            with self.assertRaisesRegex(RuntimeError, '自动回滚未完成.*rollback_manifest'):
                self.restore(archive)
        self.assertEqual(self.marker(), 'original')
        self.assertTrue(next(self.safety.glob('*/files/0')).is_file())

    def test_database_rollback_failure_is_not_reported_as_success(self):
        archive = self.archive(1)
        real_restore = backup._restore_database

        def restore(path):
            if Path(path).name == 'before.db':
                raise PermissionError('database locked')
            real_restore(path)

        with patch.object(backup, '_restore_database', side_effect=restore), patch.object(
            backup, '_replace_attachment', side_effect=PermissionError('attachment locked')
        ):
            with self.assertRaisesRegex(RuntimeError, '自动回滚未完成.*数据库'):
                self.restore(archive)
        self.assertEqual(self.marker(), 'incoming')
        self.assertTrue(next(self.safety.glob('*/before.db')).is_file())

    def test_successful_restore_keeps_both_originals_and_unique_safety_folders(self):
        archive = self.archive(1)
        target = self.storage / '0.pdf'
        target.write_bytes(b'original-file')
        first = self.restore(archive)
        second = self.restore(archive)
        self.assertNotEqual(first['safety_backup'], second['safety_backup'])
        self.assertEqual(self.marker(), 'incoming')
        self.assertEqual(target.read_bytes(), b'incoming-0')
        self.assertEqual((Path(first['safety_backup']).parent / 'files' / '0').read_bytes(), b'original-file')
        self.assertEqual(first['restored_files'], [str(target)])

    def test_atomic_attachment_staging_failure_does_not_truncate_original(self):
        source = self.root / 'source.pdf'
        source.write_bytes(b'incoming')
        target = self.storage / 'target.pdf'
        target.write_bytes(b'original')
        with patch.object(backup.shutil, 'copyfile', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                backup._replace_attachment(source, target)
        self.assertEqual(target.read_bytes(), b'original')
        self.assertEqual(list(self.storage.glob('.restore_*')), [])


if __name__ == '__main__':
    unittest.main()
