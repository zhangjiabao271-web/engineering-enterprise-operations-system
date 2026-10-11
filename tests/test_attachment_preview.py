"""Attachment preview is read-only, type-restricted, and independent of download."""
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen


class AttachmentPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import db.connection as connection
        import db.migration_runner as runner
        from db.schema import init_db
        from services.attachment_service import add_attachment
        from web_workbench.synthetic import seed
        from PIL import Image
        from pypdf import PdfWriter

        cls.temp = tempfile.TemporaryDirectory(prefix='attachment_preview_')
        cls.folder = Path(cls.temp.name)
        cls.database = cls.folder / 'synthetic.db'
        with patch.object(connection, 'DB_PATH', cls.database), patch.object(runner, 'DB_PATH', cls.database), patch.dict(os.environ, {'SUPPLY_CHAIN_ATTACHMENTS_PATH': str(cls.folder / 'attachments')}):
            init_db()
            seed()
            cls.ids = {}
            for name in ('image.png', 'contract.PDF', 'script.html', 'vector.svg'):
                file = cls.folder / name
                if file.suffix == '.png':
                    Image.new('RGB', (320, 240), 'white').save(file)
                elif file.suffix.lower() == '.pdf':
                    writer = PdfWriter()
                    writer.add_blank_page(width=595, height=842)
                    writer.write(file)
                else:
                    file.write_text('<script>alert(1)</script>', encoding='utf-8')
                cls.ids[name] = add_attachment('contract', 1, file)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        import db.connection as connection
        from web_workbench.server import WorkbenchServer
        database_patch = patch.object(connection, 'DB_PATH', self.database)
        database_patch.start()
        self.addCleanup(database_patch.stop)
        self.server = WorkbenchServer(0, read_only=True, demo=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def get(self, path):
        with urlopen(self.server.origin + path) as response:
            return dict(response.headers), response.read()

    def test_supported_preview_types_and_embedding_are_explicit(self):
        for name, content_type in (('image.png', 'image/png'), ('contract.PDF', 'application/pdf')):
            with self.subTest(name=name):
                headers, raw = self.get(f'/api/attachment?id={self.ids[name]}&view=1')
                self.assertEqual(headers['Content-Type'], content_type)
                self.assertTrue(headers['Content-Disposition'].startswith('inline;'))
                self.assertIn("frame-ancestors 'self'", headers['Content-Security-Policy'])
                self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
                self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertEqual(raw, (self.folder / name).read_bytes())

    def test_html_svg_and_arbitrary_file_paths_cannot_be_previewed(self):
        for name in ('script.html', 'vector.svg'):
            with self.subTest(name=name), self.assertRaises(HTTPError) as caught:
                self.get(f'/api/attachment?id={self.ids[name]}&view=1')
            self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError):
            self.get('/api/attachment?id=../../supplier_data.db&view=1')

    def test_download_remains_attachment_and_cannot_be_embedded(self):
        headers, raw = self.get(f'/api/attachment?id={self.ids["contract.PDF"]}')
        self.assertEqual(headers['Content-Type'], 'application/octet-stream')
        self.assertTrue(headers['Content-Disposition'].startswith('attachment;'))
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertIn("img-src 'self' data: blob:", headers['Content-Security-Policy'])
        self.assertIn("script-src 'self';", headers['Content-Security-Policy'])
        self.assertEqual(raw, (self.folder / 'contract.PDF').read_bytes())

    def test_list_reports_only_supported_preview_kinds(self):
        _, raw = self.get('/api/attachments?entity=contract&id=1')
        rows = {row['original_name']: row for row in json.loads(raw)}
        self.assertEqual(rows['image.png']['preview_kind'], 'image')
        self.assertEqual(rows['contract.PDF']['preview_kind'], 'pdf')
        self.assertEqual(rows['script.html']['preview_kind'], '')
        self.assertEqual(rows['vector.svg']['preview_kind'], '')

    def test_preview_has_no_ledger_writes(self):
        from db.connection import db_read
        with db_read() as conn:
            before = list(conn.iterdump())
        self.get(f'/api/attachment?id={self.ids["image.png"]}&view=1')
        self.get(f'/api/attachment?id={self.ids["contract.PDF"]}&view=1')
        with db_read() as conn:
            self.assertEqual(list(conn.iterdump()), before)

    def test_slow_pdf_render_does_not_block_other_ledger_reads(self):
        started, release = threading.Event(), threading.Event()
        results = []

        def slow_preview(file, page=None):
            started.set()
            release.wait(5)
            return {'page_count': 1, 'page_sizes': [[595, 842]]}

        def preview_request():
            results.append(self.get(f'/api/attachment_pdf_info?id={self.ids["contract.PDF"]}'))

        with patch('web_workbench.attachment_preview.pdf_preview', slow_preview):
            worker = threading.Thread(target=preview_request)
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                with urlopen(self.server.origin + '/api/rows?module=projects', timeout=1) as response:
                    self.assertTrue(json.load(response)['rows'])
            finally:
                release.set()
                worker.join(timeout=3)
        self.assertEqual(len(results), 1)

    def test_pdf_concurrency_is_bounded_and_failed_render_releases_slot(self):
        release = threading.Event()
        path = f'/api/attachment_pdf_info?id={self.ids["contract.PDF"]}'
        self.server.pdf_preview_slots = threading.BoundedSemaphore(1)
        started = threading.Event()

        def slow_preview(file, page=None):
            started.set()
            release.wait(5)
            return {'pages': 1, 'page_sizes': []}

        with patch('web_workbench.attachment_preview.pdf_preview', slow_preview):
            worker = threading.Thread(target=lambda: self.get(path))
            worker.start()
            try:
                self.assertTrue(started.wait(2))
                with self.assertRaises(HTTPError) as caught:
                    self.get(path)
                self.assertEqual(caught.exception.code, 400)
                self.assertIn('稍后重试', json.load(caught.exception)['error'])
            finally:
                release.set()
                worker.join(timeout=3)
        with patch('web_workbench.attachment_preview.pdf_preview', side_effect=ValueError('合成渲染失败')):
            with self.assertRaises(HTTPError):
                self.get(path)
        with patch('web_workbench.attachment_preview.pdf_preview', return_value={'pages': 1}):
            _, raw = self.get(path)
            self.assertEqual(json.loads(raw)['pages'], 1)

    def test_history_and_missing_files_keep_existing_guards(self):
        from db.connection import db_transaction
        from services.attachment_service import add_attachment, attachment_path
        with patch.dict(os.environ, {'SUPPLY_CHAIN_ATTACHMENTS_PATH': str(self.folder / 'attachments')}):
            attachment_id = add_attachment('contract', 1, self.folder / 'image.png')
        with db_transaction() as conn:
            conn.execute("UPDATE business_attachments SET status='void' WHERE id=?", (attachment_id,))
            file = attachment_path(conn.execute('SELECT file_path FROM business_attachments WHERE id=?', (attachment_id,)).fetchone()[0])
        with self.assertRaises(HTTPError) as caught:
            self.get(f'/api/attachment?id={attachment_id}&view=1')
        self.assertEqual(caught.exception.code, 400)
        self.get(f'/api/attachment?id={attachment_id}&view=1&history=1')
        file.unlink()
        with self.assertRaises(HTTPError) as caught:
            self.get(f'/api/attachment?id={attachment_id}&view=1&history=1')
        self.assertEqual(caught.exception.code, 400)

    def test_preview_assets_and_app_policy(self):
        headers, _ = self.get('/')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        for path in ('/attachment-preview.js', '/attachment-preview.css'):
            _, raw = self.get(path)
            self.assertTrue(raw)

    def test_pdf_raster_preview_and_page_bounds(self):
        import io
        from PIL import Image
        attachment_id = self.ids['contract.PDF']
        _, raw = self.get(f'/api/attachment_pdf_info?id={attachment_id}')
        self.assertEqual(json.loads(raw)['pages'], 1)
        self.assertEqual(json.loads(raw)['page_sizes'], [{'width': 595.0, 'height': 842.0}])
        headers, pixels = self.get(f'/api/attachment_pdf_page?id={attachment_id}&page=1')
        self.assertEqual(headers['Content-Type'], 'image/png')
        with Image.open(io.BytesIO(pixels)) as page:
            self.assertLessEqual(max(page.size), 2400)
            self.assertGreater(min(page.size), 0)
        for number in ('0', '-1', '2', 'not-a-page'):
            with self.subTest(number=number), self.assertRaises(HTTPError) as caught:
                self.get(f'/api/attachment_pdf_page?id={attachment_id}&page={number}')
            self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError):
            self.get(f'/api/attachment_pdf_info?id={self.ids["image.png"]}')

    def test_pdf_rendering_preserves_source_and_ledger(self):
        from db.connection import db_read
        source = (self.folder / 'contract.PDF').read_bytes()
        with db_read() as conn:
            before = list(conn.iterdump())
        self.get(f'/api/attachment_pdf_page?id={self.ids["contract.PDF"]}&page=1')
        with db_read() as conn:
            self.assertEqual(list(conn.iterdump()), before)
        self.assertEqual((self.folder / 'contract.PDF').read_bytes(), source)

    def test_pdf_errors_are_friendly_and_rendering_is_bounded(self):
        import subprocess
        from web_workbench.attachment_preview import pdf_preview
        corrupt = self.folder / 'corrupt.pdf'
        corrupt.write_bytes(b'not a PDF')
        with self.assertRaisesRegex(ValueError, '损坏或加密'):
            pdf_preview(corrupt)
        with patch('web_workbench.attachment_preview.subprocess.run', side_effect=subprocess.TimeoutExpired('preview', 20)):
            with self.assertRaisesRegex(ValueError, '超时'):
                pdf_preview(self.folder / 'contract.PDF', 1)

    def test_encrypted_pdf_is_not_silently_rendered(self):
        from pypdf import PdfWriter
        from web_workbench.attachment_preview import pdf_preview
        encrypted = self.folder / 'encrypted.pdf'
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.encrypt('synthetic-password')
        writer.write(encrypted)
        with self.assertRaisesRegex(ValueError, '已加密'):
            pdf_preview(encrypted)

    def test_pdf_metadata_preserves_mixed_sizes_and_rotation(self):
        import io
        from PIL import Image
        from pypdf import PdfWriter
        from web_workbench.attachment_preview import pdf_preview
        mixed = self.folder / 'mixed-pages.pdf'
        writer = PdfWriter()
        writer.add_blank_page(width=595, height=842)
        writer.add_blank_page(width=842, height=595)
        writer.add_blank_page(width=400, height=600).rotate(90)
        writer.write(mixed)
        info = pdf_preview(mixed)
        self.assertEqual(info['pages'], 3)
        self.assertEqual(info['page_sizes'], [
            {'width': 595.0, 'height': 842.0},
            {'width': 842.0, 'height': 595.0},
            {'width': 600.0, 'height': 400.0},
        ])
        for number, size in enumerate(info['page_sizes'], 1):
            with Image.open(io.BytesIO(pdf_preview(mixed, number))) as picture:
                self.assertAlmostEqual(picture.width / picture.height, size['width'] / size['height'], places=2)


if __name__ == '__main__':
    unittest.main()
