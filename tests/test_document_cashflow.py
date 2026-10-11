"""End-to-end tests using freshly generated synthetic data, never the real DB."""

import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

from services.document_cashflow_extract import money_minor, parse_fragment, read_document, timeline


class ExtractionTests(unittest.TestCase):
    def parse(self, text):
        return parse_fragment({'locator': '第3条', 'text': text},
                              {'id': 1, 'name': '合成合同', 'version': 1, 'sha256': 'synthetic', 'kind': '合同正文'})

    def test_exact_money_dates_and_source(self):
        row = self.parse('收款 预付款；金额：1.23万元；日期：2026-12-01；条件：签约完成')
        self.assertEqual(row['amount_minor'], 1230000)
        self.assertEqual(row['due_date'], '2026-12-01')
        self.assertEqual(row['sources'][0]['locator'], '第3条')
        self.assertEqual(row['issues'], [])
        for value in ('NaN', '0', '-1', '1.234', 'Infinity'):
            with self.assertRaises(ValueError):
                money_minor(value)

    def test_unknown_percentage_relative_date_and_formula(self):
        for text in ('收款 尾款；金额：合同额30%；日期：验收后30天；条件：审批完成',
                     '付款 采购；金额：=A1*B1；日期：2026-02-30；条件：到货',
                     '甲方应于2026-12-01向乙方支付10000元'):
            row = self.parse(text)
            self.assertIsNone(row['amount_minor'])
            self.assertIsNone(row['due_date'])
            self.assertTrue(row['issues'])

    def test_repeated_fields_are_not_silently_chosen(self):
        row = self.parse('收款 尾款；金额：100元；金额：200元；日期：2026-12-01；条件：验收')
        self.assertIsNone(row['amount_minor'])
        self.assertTrue(any('重复字段' in issue for issue in row['issues']))

    def test_gap_scenario_and_same_day_order(self):
        rows = [self.parse('付款 材料；金额：100元；日期：2026-11-01；条件：下单'),
                self.parse('收款 预付款；金额：80元；日期：2026-11-01；条件：签约'),
                self.parse('收款 尾款；金额：30元；日期：2026-12-01；条件：验收')]
        base = timeline(rows)
        self.assertEqual(base['gap_minor'], 2000)
        self.assertEqual(base['outflow_first_gap_minor'], 10000)
        scenario = timeline(rows, income_delay_days=30, expense_increase_percent=10)
        self.assertEqual(scenario['gap_minor'], 11000)
        self.assertEqual(scenario['schedule'][1]['date'], '2026-12-01')
        for kwargs in ({'opening_minor': -1}, {'income_delay_days': -1}, {'expense_increase_percent': 1.5}):
            with self.assertRaises(ValueError):
                timeline(rows, **kwargs)

    def test_local_formats_and_precise_locators(self):
        from openpyxl import Workbook
        from pypdf import PdfWriter
        from zipfile import ZipFile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            text = root / '合成.txt'
            text.write_text('无关材料\n收款 预付款；金额：100元；日期：2026-12-01；条件：签约', encoding='utf-8')
            self.assertEqual(read_document(text)['fragments'][1]['locator'], '第2行')
            book = Workbook()
            book.active.title = '采购计划'
            book.active.append(['付款 材料', '金额：=1+1', '日期：2026-12-01', '条件：下单'])
            book.save(root / '合成.xlsx')
            self.assertIn('工作表「采购计划」第1行', read_document(root / '合成.xlsx')['fragments'][0]['locator'])
            with ZipFile(root / '合成.docx', 'w') as archive:
                archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p/><w:p><w:r><w:t>收款 预付款；金额：10元</w:t></w:r></w:p></w:body></w:document>')
            self.assertIn('第2段', read_document(root / '合成.docx')['fragments'][0]['locator'])
            writer = PdfWriter()
            writer.add_blank_page(width=200, height=300)
            writer.write(root / '扫描合成.pdf')
            self.assertIn('无可读文字', read_document(root / '扫描合成.pdf')['fragments'][0]['text'])


class ScannedExtractionTests(unittest.TestCase):
    def test_scanned_pdf_retains_ocr_page_locators_and_empty_pages(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / '合成扫描.pdf'
            writer = PdfWriter()
            writer.add_blank_page(width=200, height=300)
            writer.add_blank_page(width=200, height=300)
            writer.write(path)
            with patch('services.document_ocr_service.recognize_pdf_pages',
                       return_value=({1: '收款 预付款\n金额：100元', 2: ''}, ['扫描文字须核对'])) as ocr:
                result = read_document(path)
            self.assertEqual(ocr.call_args.args[1], [1, 2])
            self.assertEqual(result['recognition']['ocr_pages'], [1])
            self.assertEqual(result['fragments'][0]['locator'], 'PDF第1页/OCR第1行')
            self.assertIn('待人工补充', result['fragments'][-1]['text'])

    def test_ocr_page_limit_and_busy_guard(self):
        from services import document_ocr_service as ocr
        with self.assertRaisesRegex(ValueError, '20页'):
            ocr.recognize_pdf_pages(b'not-read', list(range(1, 22)))
        with patch.dict('sys.modules', {'pypdfium2': ModuleType('pypdfium2'),
                        'rapidocr_onnxruntime': SimpleNamespace(RapidOCR=None)}):
            ocr._ocr_lock.acquire()
            try:
                with self.assertRaisesRegex(ValueError, '正在识别'):
                    ocr.recognize_pdf_pages(b'not-read', [1])
            finally:
                ocr._ocr_lock.release()

    def test_missing_ocr_dependency_is_explicit(self):
        from services.document_ocr_service import recognize_pdf_pages
        with patch.dict('sys.modules', {'pypdfium2': None}):
            pages, warnings = recognize_pdf_pages(b'not-read', [1])
        self.assertEqual(pages, {})
        self.assertIn('requirements-ocr.txt', warnings[0])


class CashflowLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import db.connection as connection
        import db.migration_runner as runner
        from db.schema import init_db
        cls.temp = tempfile.TemporaryDirectory(prefix='cashflow_synthetic_')
        cls.template = Path(cls.temp.name) / 'synthetic_template.db'
        with patch.object(connection, 'DB_PATH', cls.template), patch.object(runner, 'DB_PATH', cls.template):
            init_db()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        import db.connection as connection
        from services import document_cashflow_service, project_service, contract_service
        self.db_path = Path(self.temp.name) / (self.id().split('.')[-1] + '.db')
        with closing(sqlite3.connect(self.template)) as source, closing(sqlite3.connect(self.db_path)) as target:
            source.backup(target)
        self.db_patch = patch.object(connection, 'DB_PATH', self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.service = document_cashflow_service
        self.project_id = project_service.create_project({'name': '合成施工项目', 'project_code': 'SYNTH-001', 'status': '筹备中'})
        self.contract_id = contract_service.create_contract({'name': '合成合同', 'contract_no': 'SYNTH-HT-001',
            'contract_type': 'project', 'pricing_mode': 'fixed', 'amount': '10000', 'status': 'draft', 'sign_date': '2026-10-01'})
        self.scope = {'project_id': self.project_id}

    def document(self, text=None, name='合成采购安排', reason='', **extra):
        data = {'name': name, 'kind': '采购付款安排', 'change_reason': reason,
                'fragments': [{'locator': '第2条 / 第1页', 'text': text or
                    '付款 材料；金额：100元；日期：2026-11-01；条件：下单确认；对方：合成供应商'}], **extra}
        return self.service.save_document(data, **self.scope)

    def count(self, table):
        with closing(sqlite3.connect(self.db_path)) as conn:
            return conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]

    def analysis(self):
        return self.service.create_analysis(**self.scope, income_delay_days=30, expense_increase_percent=10)

    def confirm(self, analysis_id, keys=None, **kwargs):
        rows = self.service.get_analysis(analysis_id)['payload']['rows']
        return self.service.confirm_analysis(analysis_id, keys or [row['key'] for row in rows],
            acknowledged=kwargs.get('acknowledged', True), reason=kwargs.get('reason', '合成测试核对原文条件'))

    def test_dedup_versions_and_stale_draft(self):
        first = self.document()
        self.assertTrue(self.document()['duplicate'])
        self.assertEqual(self.count('cashflow_documents'), 1)
        analysis_id = self.analysis()
        with self.assertRaises(ValueError):
            self.document('付款 材料；金额：120元；日期：2026-11-01；条件：下单确认')
        second = self.document('付款 材料；金额：120元；日期：2026-11-01；条件：下单确认', reason='合成修订')
        self.assertEqual(second['version'], 2)
        self.assertNotEqual(first['id'], second['id'])
        self.assertTrue(self.service.get_analysis(analysis_id)['stale_reason'])
        with self.assertRaises(ValueError):
            self.confirm(analysis_id)
        self.assertEqual(self.count('fund_plans'), 0)

    def test_confirmation_boundary_idempotency_and_no_actual_cash(self):
        self.document()
        analysis_id = self.analysis()
        self.assertEqual(self.count('fund_plans'), 0)
        with self.assertRaises(ValueError):
            self.confirm(analysis_id, acknowledged=False)
        with self.assertRaises(ValueError):
            self.confirm(analysis_id, reason='')
        plans = self.confirm(analysis_id)
        self.assertEqual(self.confirm(analysis_id), plans)
        self.assertEqual(self.count('fund_plans'), 1)
        self.assertEqual(self.count('fund_transactions'), 0)
        self.assertEqual(self.count('cost_entries'), 0)
        self.assertEqual(self.count('receipts'), 0)
        with closing(sqlite3.connect(self.db_path)) as conn:
            amount, notes = conn.execute('SELECT amount_minor,notes FROM fund_plans').fetchone()
            self.assertEqual(amount, 10000)  # not the 10% scenario increase
            self.assertIn('第2条 / 第1页', notes)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0], 630)

    def test_conflicts_preserve_both_sources_and_exclude_schedule(self):
        self.document()
        self.document('付款 材料；金额：120元；日期：2026-11-02；条件：到货', name='合成另一报价')
        analysis_id = self.analysis()
        payload = self.service.get_analysis(analysis_id)['payload']
        self.assertEqual(len(payload['rows']), 2)
        self.assertTrue(all(row['conflict'] for row in payload['rows']))
        self.assertEqual(payload['base']['schedule'], [])
        self.assertEqual(len(payload['base']['excluded_keys']), 2)
        with self.assertRaises(ValueError):
            self.confirm(analysis_id)

    def test_equal_nodes_merge_evidence_without_double_amount(self):
        self.document()
        self.document(name='合成第二来源')
        payload = self.service.get_analysis(self.analysis())['payload']
        self.assertEqual(len(payload['rows']), 1)
        self.assertEqual(len(payload['rows'][0]['sources']), 2)
        self.assertEqual(payload['base']['gap_minor'], 10000)

    def test_unknown_and_atomic_selection(self):
        self.document()
        self.document('收款 尾款；金额：待补充；日期：验收后30天；条件：审批', name='合成合同摘录')
        analysis_id = self.analysis()
        with self.assertRaises(ValueError):
            self.confirm(analysis_id)
        self.assertEqual(self.count('fund_plans'), 0)
        self.assertEqual(self.service.get_analysis(analysis_id)['status'], 'draft')

    def test_cancel_and_retire_invalidate(self):
        document = self.document()
        analysis_id = self.analysis()
        self.service.cancel_analysis(analysis_id)
        with self.assertRaises(ValueError):
            self.confirm(analysis_id)
        second = self.analysis()
        self.service.retire_document(document['id'], '合成移出')
        self.assertTrue(self.service.get_analysis(second)['stale_reason'])
        self.assertEqual(self.count('cashflow_documents'), 1)

    def test_file_changes_and_missing_file_invalidate(self):
        path = Path(self.temp.name) / '合成外部文件.txt'
        path.write_text('收款 预付款；金额：100元；日期：2026-12-01；条件：签约', encoding='utf-8')
        self.service.save_document({**read_document(path), 'kind': '合同正文'}, **self.scope)
        analysis_id = self.analysis()
        path.write_text('已换版', encoding='utf-8')
        self.assertTrue(self.service.get_analysis(analysis_id)['stale_reason'])
        with self.assertRaises(ValueError):
            self.confirm(analysis_id)
        path.unlink()
        self.assertTrue(self.service.get_analysis(analysis_id)['stale_reason'])

    def test_scope_isolation_and_contract_change_invalidate(self):
        self.document()
        self.assertEqual(self.service.list_documents(contract_id=self.contract_id), [])
        with self.assertRaises(ValueError):
            self.service.create_analysis(contract_id=self.contract_id, project_id=self.project_id)
        self.service.save_document({'name': '合成整份合同', 'kind': '合同正文', 'fragments': [
            {'locator': '第1条', 'text': '收款 预付款；金额：10元；日期：2026-12-01；条件：签约'}]}, contract_id=self.contract_id)
        analysis_id = self.service.create_analysis(contract_id=self.contract_id)
        with closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute('UPDATE contracts SET name=? WHERE id=?', ('合成合同修订', self.contract_id))
            conn.commit()
        self.assertTrue(self.service.get_analysis(analysis_id)['stale_reason'])

    def test_new_analysis_cannot_duplicate_existing_plan(self):
        self.document()
        first = self.analysis()
        self.confirm(first)
        self.document('付款 材料；金额：120元；日期：2026-11-01；条件：下单确认', reason='合成换版')
        self.assertTrue(self.service.get_analysis(first)['stale_reason'])
        with self.assertRaises(ValueError):
            self.confirm(self.analysis())
        self.assertEqual(self.count('fund_plans'), 1)

    def test_project_attachments_follow_sites_and_contract_links(self):
        from services import attachment_service as attachments, contract_service, web_workbench_service as business
        contract_service.create_allocation({'contract_id': self.contract_id,
            'project_id': self.project_id, 'amount': '10000'})
        path = Path(self.temp.name) / '合成附件.txt'
        path.write_text('收款 尾款；金额：100元；日期：2026-12-01；条件：验收', encoding='utf-8')
        with patch.object(attachments, '_storage_root', return_value=Path(self.temp.name) / 'source-files'):
            contract_attachment = attachments.add_attachment('contract', self.contract_id, path)
            record = business.save_record('construction', {'project_id': self.project_id,
                'work_area': '合成区域', 'work_details': '合成施工', 'work_amount': '100',
                'start_date': '2026-10-01', 'end_date': '2026-10-01'})
            construction_attachment = attachments.add_attachment('construction', record, path)
        combined = {'contract_id': self.contract_id, 'project_id': self.project_id}
        rows = self.service.available_attachments(**combined)
        expected = {contract_attachment, construction_attachment}
        self.assertEqual({row['id'] for row in rows}, expected)
        self.assertTrue(all(row['file_exists'] and not row['already_linked'] for row in rows))
        self.assertEqual({row['id'] for row in self.service.available_attachments(**self.scope)}, expected)
        source = rows[0]
        self.service.save_document({**read_document(source['absolute_path']), 'kind': '合同正文',
                                    'attachment_id': source['id']}, **combined)
        self.assertTrue(self.service.available_attachments(**combined)[0]['already_linked'])
        self.assertFalse(self.service.available_attachments(contract_id=self.contract_id)[0]['already_linked'])
        Path(source['absolute_path']).unlink()
        self.assertFalse(self.service.available_attachments(**combined)[0]['file_exists'])
        attachments.void_attachments([construction_attachment])
        self.assertEqual(len(self.service.available_attachments(**combined)), 1)

    def test_export_escapes_untrusted_source_text(self):
        from services.document_cashflow_report import export_html
        self.document('付款 <script>alert(1)</script>；金额：100元；日期：2026-11-01；条件：下单')
        path = Path(self.temp.name) / '合成报告.html'
        export_html(self.analysis(), path)
        html = path.read_text(encoding='utf-8')
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('第2条 / 第1页', html)


if __name__ == '__main__':
    unittest.main()
