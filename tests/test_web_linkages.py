"""Cross-entry linkage regressions on an independent synthetic ledger."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


class WebLinkageTests(unittest.TestCase):
    def setUp(self):
        from db import connection, migration_runner
        from db.schema import init_db
        from services import finance_service, operating_entity_service, web_workbench_service
        from web_workbench.synthetic import seed

        temporary = tempfile.TemporaryDirectory(prefix='web_linkages_')
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / 'synthetic.db'
        for module in (connection, migration_runner):
            override = patch.object(module, 'DB_PATH', self.database)
            override.start()
            self.addCleanup(override.stop)
        init_db()
        seed()
        self.finance = finance_service
        self.ownership = operating_entity_service
        self.business = web_workbench_service

    def ledger(self):
        from db.connection import db_read
        with db_read() as conn:
            return list(conn.iterdump())

    def case(self, *, invoice=True, parent=None):
        project = self.business.save_record('projects', {
            'name': '合成联动项目 ' + uuid4().hex[:8], 'customer_partner_id': 1,
            'status': '进行中', 'business_mode': 'contract',
        })
        contract = self.business.save_record('contracts', {
            'name': '合成联动合同', 'customer_partner_id': 1, 'amount': '0',
            'pricing_mode': 'actual', 'sign_date': '2026-10-01', 'status': 'active',
            'contract_type': 'supplement' if parent else 'project',
            'parent_contract_id': parent,
        })
        self.business.save_record('allocations', {
            'project_id': project, 'contract_id': contract, 'amount': '0',
        })
        settlement = self.business.save_record('settlements', {
            'project_id': project, 'contract_id': contract, 'amount': '1000',
            'settlement_date': '2026-10-01', 'basis': '合成结算事实',
        })
        self.project, self.contract, self.settlement = project, contract, settlement
        return self.invoice('2026-10-02') if invoice else None

    def invoice(self, date, amount='100'):
        return self.business.save_record('invoices', {
            'project_id': self.project, 'contract_id': self.contract,
            'settlement_id': self.settlement, 'amount': amount,
            'invoice_date': date, 'invoice_no': 'SYNTHETIC-' + uuid4().hex,
        })

    def receipt(self, date='2026-10-03', amount='60'):
        return self.business.save_record('receipts', {
            'project_id': self.project, 'contract_id': self.contract,
            'amount': amount, 'receipt_date': date, 'payment_method': '银行转账',
        })

    def active_matches(self):
        from db.connection import db_read
        with db_read() as conn:
            return [tuple(row) for row in conn.execute(
                "SELECT invoice_id,receipt_id,allocated_amount_minor FROM invoice_receipt_allocations "
                "WHERE status='active' ORDER BY invoice_id,receipt_id")]

    def test_contract_confirmation_reconciles_inherited_invoices_and_reads_do_not_write(self):
        invoice = self.case()
        receipt = self.receipt()
        self.assertEqual(self.finance.get_invoice(invoice)['received_minor'], 0)
        self.ownership.assign_records('contracts', [self.contract], 1)
        self.assertEqual(self.active_matches(), [(invoice, receipt, 6000)])
        before = self.ledger()
        self.assertEqual(self.finance.get_invoice(invoice)['unreceived_minor'], 4000)
        self.assertEqual(self.finance.get_receipt(receipt)['invoice_matched_minor'], 6000)
        self.finance.list_invoices(self.project)
        self.ownership.assign_records('contracts', [self.contract], 1)
        self.assertEqual(self.ledger(), before)
        from db.connection import db_read
        with db_read() as conn:
            self.assertIsNone(self.ownership.entity_for(conn, 'sales_invoices', invoice))
            self.assertIsNone(self.ownership.entity_for(conn, 'receipts', receipt))

    def test_explicit_invoice_subject_wins_and_subject_correction_preserves_history(self):
        invoice = self.case()
        receipt = self.receipt()
        self.ownership.assign_records('sales_invoices', [invoice], 2)
        self.ownership.assign_records('contracts', [self.contract], 1)
        self.assertEqual(self.active_matches(), [])
        self.ownership.assign_records('contracts', [self.contract], 2)
        self.assertEqual(self.active_matches(), [(invoice, receipt, 6000)])
        self.ownership.assign_records('receipts', [receipt], 1)
        self.assertEqual(self.active_matches(), [])
        self.assertEqual(self.finance.get_invoice(invoice)['received_minor'], 0)
        from db.connection import db_read
        with db_read() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM invoice_receipt_allocations WHERE status='void'").fetchone()[0], 1)
            self.assertEqual(self.ownership.entity_for(conn, 'sales_invoices', invoice), 2)

    def test_receipt_before_invoice_fifo_date_change_and_full_settlement(self):
        self.case(invoice=False)
        self.ownership.assign_records('contracts', [self.contract], 1)
        receipt = self.receipt('2026-10-01', '150')
        first = self.invoice('2026-10-02')
        second = self.invoice('2026-10-03')
        self.assertEqual(self.finance.get_invoice(first)['unreceived_minor'], 0)
        self.assertEqual(self.finance.get_invoice(second)['unreceived_minor'], 5000)
        original = self.business.get_record('invoices', second)
        self.business.save_record('invoices', {**original, 'invoice_date': '2026-10-01'}, second, original['_version'])
        self.assertEqual(self.finance.get_invoice(second)['unreceived_minor'], 0)
        self.assertEqual(self.finance.get_invoice(first)['unreceived_minor'], 5000)
        self.receipt('2026-10-04', '50')
        self.assertEqual(self.finance.get_invoice(first)['unreceived_minor'], 0)
        self.assertEqual(self.finance.get_receipt(receipt)['invoice_unmatched_minor'], 0)

    def test_parent_contract_and_original_contract_form_refresh_children_atomically(self):
        invoice = self.case(parent=1)
        receipt = self.receipt()
        self.ownership.assign_records('sales_invoices', [1], 2)
        original = self.business.get_record('contracts', 1)
        self.business.save_record('contracts', {**original, 'entity_id': 1}, 1, original['_version'])
        self.assertEqual(self.active_matches(), [(invoice, receipt, 6000)])
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '不存在'):
            self.ownership.assign_records('contracts', [1, 999999], 2)
        self.assertEqual(self.ledger(), before)
        with patch('services.finance_service._reconcile_customer_ids', side_effect=RuntimeError('synthetic failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                self.ownership.assign_records('contracts', [1], 2)
        self.assertEqual(self.ledger(), before)

    def split_receipt(self, *, cash):
        invoice = self.case()
        self.ownership.assign_records('contracts', [self.contract], 1)
        receipt = self.receipt()
        other = self.business.save_record('projects', {
            'name': '合成混合分配', 'status': '进行中',
            'business_mode': 'cash' if cash else 'contract',
            'invoice_policy': 'not_required' if cash else 'required',
            'customer_partner_id': 1 if cash else None,
        })
        # Build an uncommon historical split; direct ownership must not bypass scope checks.
        from db.connection import db_transaction
        with db_transaction() as conn:
            conn.execute('UPDATE receipt_allocations SET allocated_amount_minor=3000 WHERE receipt_id=?', (receipt,))
            row = dict(conn.execute('SELECT * FROM receipt_allocations WHERE receipt_id=?', (receipt,)).fetchone())
            row.update(public_id=uuid4().hex, project_id=other, contract_id=None,
                       settlement_id=None, invoice_id=None, allocated_amount_minor=3000)
            columns = [key for key in row if key != 'id']
            conn.execute(f"INSERT INTO receipt_allocations ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                         [row[key] for key in columns])
        self.ownership.assign_records('receipts', [receipt], 1)
        self.assertEqual(self.active_matches(), [])
        self.assertEqual(self.finance.get_invoice(invoice)['received_minor'], 0)
        self.assertEqual(self.finance.get_receipt(receipt)['invoice_matches'], [])

    def test_supplement_parent_change_refreshes_inherited_invoice_matches(self):
        invoice = self.case(parent=1)
        self.finance.void_invoices([1])
        self.ownership.assign_records('contracts', [1], 1)
        self.ownership.assign_records('sales_invoices', [invoice], 1)
        receipt = self.receipt()
        self.assertEqual(self.active_matches(), [(invoice, receipt, 6000)])
        other = self.business.save_record('contracts', {
            'name': '合成另一主体原合同', 'customer_partner_id': 1,
            'amount': '100', 'sign_date': '2026-10-03', 'entity_id': 2,
        })
        original = self.business.get_record('contracts', self.contract)
        self.business.save_record('contracts', {**original, 'parent_contract_id': other},
                                  self.contract, original['_version'])
        self.assertEqual(self.active_matches(), [])
        self.assertEqual(self.finance.get_invoice(invoice)['received_minor'], 0)
        self.assertEqual(self.finance.get_receipt(receipt)['amount_minor'], 6000)

    def test_mixed_cash_receipt_cannot_match_its_entire_amount_to_contract_invoices(self):
        self.split_receipt(cash=True)

    def test_missing_customer_in_a_split_cannot_match_its_entire_amount(self):
        self.split_receipt(cash=False)

    def test_options_expose_stable_scopes_and_invoice_income_links(self):
        invoice = self.case()
        before = self.ledger()
        options = self.business.options(self.project)
        self.assertEqual([row['value'] for row in options['contracts']], [self.contract])
        self.assertEqual(options['contracts'][0]['income_mode'], 'manual')
        self.assertEqual(options['settlements'][0]['project_id'], self.project)
        self.assertEqual(options['settlements'][0]['contract_id'], self.contract)
        self.assertEqual(options['invoices'][0]['settlement_ids'], [self.settlement])
        self.assertEqual(options['invoices'][0]['value'], invoice)
        self.assertEqual(self.ledger(), before)


if __name__ == '__main__':
    unittest.main()
