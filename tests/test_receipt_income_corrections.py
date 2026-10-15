from tests import test_contract_pricing_modes as pricing_tests
from db.connection import db_read, db_transaction
from services import receipt_income_service as income
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ReceiptIncomeCorrectionTests(unittest.TestCase):
    _project = pricing_tests.ContractPricingServiceTests._project
    _contract = pricing_tests.ContractPricingServiceTests._contract

    def setUp(self):
        from db import connection, migration_runner
        from db.schema import init_db
        from services import contract_service, finance_service, project_service
        folder = tempfile.TemporaryDirectory(prefix='receipt_income_corrections_')
        self.addCleanup(folder.cleanup)
        for module in (connection, migration_runner):
            override = patch.object(module, 'DB_PATH', Path(folder.name) / 'synthetic.db')
            override.start()
            self.addCleanup(override.stop)
        init_db()
        self.contracts, self.finance, self.projects = contract_service, finance_service, project_service

    def case(self):
        project = self._project('合成回款更正')
        contract = self._contract('合成年度', pricing_mode='actual', income_mode='receipt')
        self.contracts.create_allocation({'contract_id': contract, 'project_id': project})
        data = {'project_id': project, 'contract_id': contract, 'amount': '200', 'receipt_date': '2026-10-14'}
        receipt = self.finance.create_receipt(data)
        return data, receipt

    def settled(self, data):
        return self.contracts.get_contract(data['contract_id'])['settled_minor']

    def test_correction_requires_explicit_choice_and_does_not_write_on_failure(self):
        data, receipt = self.case()
        with self.assertRaisesRegex(ValueError, '明确选择'):
            self.finance.update_receipt(receipt, dict(data, amount='100'))
        with self.assertRaisesRegex(ValueError, '明确选择'):
            self.finance.void_receipts([receipt])
        self.assertEqual(self.finance.get_receipt(receipt)['amount_minor'], 20000)
        self.assertEqual(self.settled(data), 20000)

    def test_retain_income_is_explicit_and_audited(self):
        data, receipt = self.case()
        self.finance.update_receipt(receipt, dict(data, amount='100', receipt_income_action='retain',
                                                receipt_income_reason='实际业务已结算，纠正到账误录'))
        self.assertEqual(self.settled(data), 20000)
        with db_read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoice_income_audit WHERE action='receipt_income_retain'").fetchone()[0], 1)

    def test_reverse_repeated_topups_and_void_preserve_old_amounts(self):
        data, receipt = self.case()
        self.finance.update_receipt(receipt, dict(data, amount='350'))
        self.finance.update_receipt(receipt, dict(data, amount='100', receipt_income_action='reverse',
                                                receipt_income_reason='撤销误补收入'))
        self.assertEqual(self.settled(data), 10000)
        self.assertEqual(self.finance.get_receipt(receipt)['pending_allocation_minor'], 0)
        self.finance.void_receipts([receipt], receipt_income_action='reverse', receipt_income_reason='整笔录错')
        self.assertEqual(self.settled(data), 0)
        with db_read() as conn:
            rows = conn.execute('SELECT amount_minor,status FROM settlements WHERE contract_id=?', (data['contract_id'],)).fetchall()
            self.assertTrue(all(row['status'] == 'void' and row['amount_minor'] > 0 for row in rows))
            self.assertFalse(conn.execute('PRAGMA foreign_key_check').fetchall())

    def test_automatic_receipt_settlement_cannot_be_edited_or_voided_manually(self):
        data, receipt = self.case()
        settlement = self.contracts.list_settlements(project_id=data['project_id'])[0]
        with self.assertRaisesRegex(ValueError, '回款补足'):
            self.contracts.update_settlement(settlement['id'], dict(settlement, amount='250'))
        with self.assertRaisesRegex(ValueError, '回款补足'):
            self.contracts.void_settlements([settlement['id']])
        self.assertEqual(self.settled(data), 20000)

    def test_invoice_link_blocks_reverse_but_allows_confirmed_retention(self):
        data, receipt = self.case()
        self.finance.create_invoice(dict(data, invoice_no='1234567820261014', invoice_date='2026-10-14', amount='200'))
        impact = income.public_preview(receipt, '100')
        self.assertFalse(impact['can_reverse'])
        with self.assertRaisesRegex(ValueError, '发票或其他回款'):
            self.finance.update_receipt(receipt, dict(data, amount='100', receipt_income_action='reverse', receipt_income_reason='更正'))
        self.assertEqual(self.finance.get_receipt(receipt)['amount_minor'], 20000)
        self.finance.update_receipt(receipt, dict(data, amount='100', receipt_income_action='retain', receipt_income_reason='收入真实，仅更正回款'))
        self.assertEqual(self.settled(data), 20000)

    def test_other_receipt_links_block_owned_income_reversal(self):
        data, first = self.case()
        second = self.finance.create_receipt(dict(data, amount='100'))
        with db_transaction(immediate=True) as conn:
            owned = {row['receipt_id']: row['settlement_id'] for row in conn.execute('SELECT * FROM receipt_confirmed_settlements')}
            conn.execute('UPDATE receipt_allocations SET settlement_id=? WHERE receipt_id=?', (owned[first], second))
            conn.execute('UPDATE receipt_allocations SET settlement_id=? WHERE receipt_id=?', (owned[second], first))
        impact = income.public_preview(first)
        self.assertFalse(impact['can_reverse'])
        with self.assertRaisesRegex(ValueError, '发票或其他回款'):
            self.finance.void_receipts([first], receipt_income_action='reverse', receipt_income_reason='更正')
        self.assertEqual(self.settled(data), 30000)

    def test_void_allocation_is_not_capacity_or_reconciliation_input(self):
        project = self._project('合成状态过滤')
        contract = self._contract('合成手动结算', pricing_mode='actual')
        self.contracts.create_allocation({'contract_id': contract, 'project_id': project})
        settlement = self.contracts.create_settlement({'project_id': project, 'contract_id': contract,
            'amount': '100', 'settlement_date': '2026-10-14'})
        receipt = self.finance.create_receipt({'project_id': project, 'contract_id': contract,
            'amount': '80', 'receipt_date': '2026-10-14'})
        with db_transaction(immediate=True) as conn:
            conn.execute("UPDATE receipt_allocations SET status='void' WHERE receipt_id=?", (receipt,))
            rows = self.finance._settlement_capacity_rows(conn, project, contract)
            self.assertEqual(rows[0]['available_minor'], 10000)
            from services.advance_receipt_service import reconcile
            reconcile(conn, project, contract)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM receipt_allocations WHERE receipt_id=? AND status='active'", (receipt,)).fetchone()[0], 0)
        self.contracts.update_settlement(settlement, {'amount': '50', 'settlement_date': '2026-10-14'})
        self.assertEqual(self.contracts.get_settlement(settlement)['amount_minor'], 5000)
