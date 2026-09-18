import hashlib
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from services.invoice_pdf_service import parse_invoice_text, recognize, save_with_pdf
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
