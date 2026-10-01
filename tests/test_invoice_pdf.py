import hashlib
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from services.invoice_pdf_service import (
    attach_pdf_to_existing, find_existing_invoice_for_pdf,
    parse_invoice_text, recognize, save_with_pdf,
)
from tests import test_contract_pricing_modes as pricing

TEXT = '''电子发票（增值税专用发票） 发票号码：12345678901234567890
开票日期：2026年09月18日
购 名称：测试购买单位    销 名称：测试销售单位
项目名称 金额 税率/征收率 税额
建筑服务 2574.26 1% 25.74
合 计 ¥2574.26 ¥25.74
价税合计（大写）贰仟陆佰圆整 （小写）¥2600.00'''


class InvoicePdfParserTests(unittest.TestCase):
    def test_fields_and_single_rate(self):
        result = parse_invoice_text(TEXT)
        self.assertEqual(result['amount'], '2600.00')
        self.assertEqual(result['net_amount'], '2574.26')
        self.assertEqual(result['tax_amount'], '25.74')
        self.assertEqual(result['tax_rate'], '1')
        self.assertEqual(result['buyer_name'], '测试购买单位')
        self.assertEqual(result['invoice_date'], '2026-09-18')
        self.assertEqual(result['warnings'], [])

    def test_unknown_and_conflict_are_not_zero(self):
        result = parse_invoice_text('电子发票')
        self.assertEqual(result['tax_amount'], '')
        self.assertEqual(result['tax_rate'], '')
        self.assertTrue(result['warnings'])
        result = parse_invoice_text(TEXT.replace('¥2600.00', '¥2601.00'))
        self.assertTrue(any('不等于' in w for w in result['warnings']))

    def test_multiple_rates_preserved(self):
        result = parse_invoice_text(TEXT.replace('1% 25.74', '1% 25.74\n其他项目 3%'))
        self.assertEqual(result['tax_rate'], '多税率')
        self.assertEqual(result['tax_rate_label'], '1% / 3%')

    def test_red_invoice_not_auto_imported(self):
        with self.assertRaisesRegex(ValueError, '红字'):
            parse_invoice_text('红字' + TEXT)

    def test_blank_scan_or_encrypted_rejected(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'scan.pdf'
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=300)
            writer.write(path)
            with self.assertRaisesRegex(ValueError, '扫描'):
                recognize(path)
            writer.encrypt('test')
            writer.write(path)
            with self.assertRaisesRegex(ValueError, '加密'):
                recognize(path)


class InvoicePdfSaveTests(pricing.ContractPricingServiceTests):
    def data(self):
        project = self._project('PDF项目')
        contract = self._contract('PDF合同', pricing_mode='actual', income_mode='invoice')
        self.contracts.create_allocation({'project_id': project, 'contract_id': contract})
        return {'project_id': project, 'contract_id': contract, 'invoice_no': uuid4().hex,
                'invoice_date': '2026-09-18', 'amount': '2600', 'net_amount': '2574.26',
                'tax_amount': '25.74', 'tax_rate': '1', 'buyer_name': '测试客户'}

    def test_tax_amount_validation_revision_and_multi_rate(self):
        data = self.data()
        invoice = self.finance.create_invoice(data)
        self.assertEqual(self.finance.get_invoice(invoice)['tax_amount_minor'], 2574)
        with self.assertRaises(ValueError):
            self.finance.update_invoice(invoice, dict(data, tax_amount='26'))
        self.finance.update_invoice(invoice, dict(data, tax_rate='多税率', tax_rate_label='1% / 3%'))
        self.assertEqual(self.finance.get_invoice(invoice)['tax_rate_label'], '1% / 3%')
        from db.connection import get_connection
        conn = get_connection()
        try:
            old = conn.execute('SELECT previous_tax_amount_minor FROM sales_invoice_revisions WHERE invoice_id=?', (invoice,)).fetchone()
            self.assertEqual(old[0], 2574)
        finally:
            conn.close()

    def evidence(self, data, folder):
        from pypdf import PdfWriter

        path = Path(folder) / 'invoice.pdf'
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        writer.write(path)
        return dict(data, source_path=str(path), warnings=[],
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def business_facts(self):
        from db.connection import db_read

        with db_read() as conn:
            return {row[0]: sorted(repr(tuple(value)) for value in conn.execute(f'SELECT * FROM "{row[0]}"'))
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('business_attachments','sqlite_sequence')")}

    def test_existing_invoice_only_gets_attachment_not_new_income_or_form_changes(self):
        from unittest.mock import patch
        from services.attachment_service import list_attachments

        data = self.data()
        invoice = self.finance.create_invoice(data)
        before = self.business_facts()
        with tempfile.TemporaryDirectory() as folder:
            evidence = self.evidence(data, folder)
            store = Path(folder) / 'stored'
            existing = find_existing_invoice_for_pdf(evidence)
            self.assertEqual(existing['id'], invoice)
            self.assertFalse(existing['has_available_attachment'])
            self.assertEqual(existing['pdf_conflicts'], [])
            with patch('services.attachment_service._storage_root', return_value=store):
                # Ignore the dialog's prior selected project and edited values.
                returned = save_with_pdf(dict(data, project_id=999999, amount='1'), evidence)
            self.assertEqual(returned, invoice)
            attachments = list_attachments('invoice', invoice)
            self.assertEqual(len(attachments), 1)
            self.assertTrue(attachments[0]['file_exists'])
            self.assertEqual(Path(attachments[0]['absolute_path']).read_bytes(), Path(evidence['source_path']).read_bytes())
            self.assertEqual(self.business_facts(), before)

    def test_existing_available_attachment_rejects_repeat_without_orphan_file(self):
        from unittest.mock import patch
        from services.attachment_service import list_attachments

        data = self.data()
        invoice = self.finance.create_invoice(data)
        with tempfile.TemporaryDirectory() as folder:
            evidence = self.evidence(data, folder)
            store = Path(folder) / 'stored'
            with patch('services.attachment_service._storage_root', return_value=store):
                attach_pdf_to_existing(evidence)
                self.assertTrue(find_existing_invoice_for_pdf(evidence)['has_available_attachment'])
                with self.assertRaisesRegex(ValueError, '已有可用附件'):
                    save_with_pdf(data, evidence)
            self.assertEqual(len(list_attachments('invoice', invoice)), 1)
            self.assertEqual(len(list(store.iterdir())), 1)

    def test_conflicting_amount_or_buyer_never_attaches_or_changes_invoice(self):
        from unittest.mock import patch
        from services.attachment_service import list_attachments

        data = self.data()
        invoice = self.finance.create_invoice(data)
        before = self.business_facts()
        with tempfile.TemporaryDirectory() as folder:
            evidence = self.evidence(data, folder)
            store = Path(folder) / 'stored'
            with patch('services.attachment_service._storage_root', return_value=store):
                for change in ({'amount': '2601'}, {'amount': ''}, {'buyer_name': '另一客户'}):
                    with self.subTest(change=change), self.assertRaisesRegex(ValueError, '核对原记录'):
                        attach_pdf_to_existing(dict(evidence, **change))
                self.assertEqual(list(store.iterdir()), [])
            self.assertEqual(list_attachments('invoice', invoice), [])
            self.assertEqual(self.business_facts(), before)

    def test_date_difference_is_visible_but_attachment_does_not_change_date(self):
        from unittest.mock import patch

        data = self.data()
        invoice = self.finance.create_invoice(dict(data, invoice_date='2026-09-17'))
        before = self.business_facts()
        with tempfile.TemporaryDirectory() as folder:
            evidence = self.evidence(data, folder)
            match = find_existing_invoice_for_pdf(evidence)
            self.assertTrue(any('不修改原日期' in note for note in match['pdf_warnings']))
            with patch('services.attachment_service._storage_root', return_value=Path(folder) / 'stored'):
                attach_pdf_to_existing(evidence)
            self.assertEqual(self.finance.get_invoice(invoice)['invoice_date'], '2026-09-17')
            self.assertEqual(self.business_facts(), before)

    def test_void_invoice_gets_evidence_without_restoring(self):
        from unittest.mock import patch

        data = self.data()
        invoice = self.finance.create_invoice(data)
        self.finance.void_invoices([invoice])
        before = self.business_facts()
        with tempfile.TemporaryDirectory() as folder:
            evidence = self.evidence(data, folder)
            with patch('services.attachment_service._storage_root', return_value=Path(folder) / 'stored'):
                self.assertEqual(attach_pdf_to_existing(evidence), invoice)
            self.assertEqual(self.finance.get_invoice(invoice)['status'], 'void')
            self.assertEqual(self.business_facts(), before)

    def test_missing_file_is_repaired_with_old_metadata_retained(self):
        from unittest.mock import patch
        from services.attachment_service import add_attachment, list_attachments

        data = self.data()
        invoice = self.finance.create_invoice(data)
        before = self.business_facts()
        with tempfile.TemporaryDirectory() as folder:
            evidence = self.evidence(data, folder)
            store = Path(folder) / 'stored'
            with patch('services.attachment_service._storage_root', return_value=store):
                old_id = add_attachment('invoice', invoice, evidence['source_path'])
                old_path = Path(list_attachments('invoice', invoice)[0]['absolute_path'])
                old_path.unlink()
                self.assertFalse(find_existing_invoice_for_pdf(evidence)['has_available_attachment'])
                attach_pdf_to_existing(evidence)
            attachments = list_attachments('invoice', invoice, include_void=True)
            self.assertEqual(len(attachments), 2)
            self.assertEqual(next(row for row in attachments if row['id'] == old_id)['status'], 'void')
            self.assertEqual(len(list_attachments('invoice', invoice)), 1)
            self.assertFalse(find_existing_invoice_for_pdf(evidence)['attachment_needs_attention'])
            self.assertEqual(self.business_facts(), before)

    def test_existing_attachment_failure_rolls_back_and_changed_pdf_is_rejected(self):
        from unittest.mock import patch
        from db.connection import get_connection
        from services.attachment_service import list_attachments

        data = self.data()
        invoice = self.finance.create_invoice(data)
        before = self.business_facts()
        with tempfile.TemporaryDirectory() as folder:
            evidence = self.evidence(data, folder)
            store = Path(folder) / 'stored'
            with patch('services.attachment_service._storage_root', return_value=store):
                with self.assertRaisesRegex(ValueError, '变化'):
                    attach_pdf_to_existing(dict(evidence, sha256='changed'))
                conn = get_connection()
                conn.execute("CREATE TRIGGER test_existing_pdf_fail BEFORE INSERT ON business_attachments BEGIN SELECT RAISE(ABORT,'test'); END")
                conn.commit()
                try:
                    with self.assertRaises(Exception):
                        attach_pdf_to_existing(evidence)
                    self.assertEqual(list(store.iterdir()), [])
                finally:
                    conn.execute('DROP TRIGGER test_existing_pdf_fail')
                    conn.commit()
                    conn.close()
            self.assertEqual(list_attachments('invoice', invoice), [])
            self.assertEqual(self.business_facts(), before)

    def test_invoice_income_and_attachment_rollback_together(self):
        from pypdf import PdfWriter
        from unittest.mock import patch
        from db.connection import get_connection
        from services.attachment_service import list_attachments
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'invoice.pdf'
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=300)
            writer.write(path)
            evidence = {'source_path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            data = self.data()
            store = Path(folder) / 'stored'
            with patch('services.attachment_service._storage_root', return_value=store):
                conn = get_connection()
                conn.execute("CREATE TRIGGER test_fail_pdf BEFORE INSERT ON business_attachments BEGIN SELECT RAISE(ABORT,'test'); END")
                conn.commit()
                try:
                    with self.assertRaises(Exception):
                        save_with_pdf(data, evidence)
                    self.assertEqual(self.contracts.get_contract(data['contract_id'])['settled_minor'], 0)
                    self.assertEqual(list(store.iterdir()), [])
                finally:
                    conn.execute('DROP TRIGGER test_fail_pdf')
                    conn.commit()
                    conn.close()
                invoice = save_with_pdf(data, evidence)
                self.assertEqual(len(list_attachments('invoice', invoice)), 1)
                with self.assertRaises(ValueError):
                    save_with_pdf(data, evidence)
                self.assertEqual(len(list(store.iterdir())), 1)
                with self.assertRaisesRegex(ValueError, '变化'):
                    save_with_pdf(data, dict(evidence, sha256='wrong'))
