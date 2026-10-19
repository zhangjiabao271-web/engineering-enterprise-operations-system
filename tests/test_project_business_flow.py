"""Business examples for project FIFO and later contract signing."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ProjectBusinessFlowTests(unittest.TestCase):
    def setUp(self):
        from db import connection, migration_runner
        from db.schema import init_db
        from services import master_data_service, project_service, contract_service, finance_service
        temporary = tempfile.TemporaryDirectory(prefix='project_business_')
        self.addCleanup(temporary.cleanup)
        database = Path(temporary.name) / 'test.db'
        for module in (connection, migration_runner):
            override = patch.object(module, 'DB_PATH', database)
            override.start()
            self.addCleanup(override.stop)
        init_db()
        self.contracts = contract_service
        self.finance = finance_service
        self.customer = master_data_service.create_business_partner({'legal_name': '合成客户', 'roles': ['customer']})
        self.project = project_service.create_project({'name': '合成工程', 'customer_partner_id': self.customer,
                                                       'status': '进行中', 'business_mode': 'contract'})
        self.first = self.contract('第一份', '100', '2025-01-01')
        self.second = self.contract('第二份', '60', '2026-02-01')
        self.s1 = self.income('100', '2026-10-01', self.first)
        self.s2 = self.income('60', '2026-02-15', self.second)
        self.s3 = self.income('40', '2026-03-01')

    def contract(self, name, amount, sign_date, entity=1, **extra):
        return self.contracts.create_contract({'name': name, 'amount': amount, 'sign_date': sign_date,
            'customer_partner_id': self.customer, 'project_id': self.project, 'entity_id': entity,
            'status': 'active', **extra})

    def income(self, amount, day, contract=None, **extra):
        return self.contracts.create_settlement({'project_id': self.project, 'contract_id': contract,
            'amount': amount, 'settlement_date': day, 'basis': '客户已确认工程价款', **extra})

    def receipt(self, amount, day='2026-10-16', **extra):
        return self.finance.create_receipt({'project_id': self.project, 'amount': amount,
            'receipt_date': day, 'entity_id': 1, 'allocation_scope': 'project', **extra})

    def lines(self, receipt):
        from db.connection import db_read
        with db_read() as conn:
            return {row['settlement_id']: row['allocated_amount_minor'] for row in conn.execute(
                "SELECT * FROM receipt_allocations WHERE receipt_id=? AND status='active'", (receipt,))}

    def test_contract_selection_automatically_links_full_amount(self):
        links = self.contracts.list_allocations(contract_id=self.first)
        self.assertEqual([(r['project_id'], r['allocated_amount_minor']) for r in links], [(self.project, 10000)])

    def test_new_receipt_fills_old_contract_before_newer_contracts(self):
        earlier = self.receipt('90', '2026-04-01')
        later = self.receipt('90', '2026-10-01')
        self.assertEqual(self.lines(earlier), {self.s1: 9000})
        self.assertEqual(self.lines(later), {self.s1: 1000, self.s2: 6000, self.s3: 2000})
        record = self.finance.get_receipt(later)
        self.assertIsNone(record['contract_id'])
        self.assertEqual(record['amount_minor'], 9000)

    def test_backdated_preview_matches_save_and_reorders_only_automatic_receipts(self):
        later = self.receipt('100', '2026-10-16')
        values = {'project_id': self.project, 'entity_id': 1, 'amount': '80',
                  'receipt_date': '2026-04-01', 'allocation_scope': 'project'}
        preview = self.finance.preview_receipt_allocations(values)
        earlier = self.finance.create_receipt(values)
        self.assertEqual(self.lines(earlier), {r['settlement_id']: r['amount_minor'] for r in preview})
        self.assertEqual(self.lines(later), {self.s1: 2000, self.s2: 6000, self.s3: 2000})

    def test_explicit_contract_payment_remains_explicit(self):
        explicit = self.finance.create_receipt({'project_id': self.project, 'contract_id': self.second,
            'settlement_id': self.s2, 'amount': '60', 'receipt_date': '2026-04-01'})
        general = self.receipt('120')
        self.assertEqual(self.lines(explicit), {self.s2: 6000})
        self.assertEqual(self.lines(general), {self.s1: 10000, self.s3: 2000})

    def test_overpayment_waits_then_future_income_consumes_it_once(self):
        receipt = self.receipt('230')
        self.assertEqual(self.lines(receipt)[None], 3000)
        future = self.income('30', '2026-10-20')
        self.assertEqual(self.lines(receipt)[future], 3000)
        self.assertNotIn(None, self.lines(receipt))
        self.assertEqual(self.finance.get_receipt(receipt)['amount_minor'], 23000)

    def test_amount_correction_and_void_reflow_remaining_automatic_receipts(self):
        first = self.receipt('100', '2026-04-01')
        second = self.receipt('80', '2026-10-01')
        record = self.finance.get_receipt(first)
        self.finance.update_receipt(first, {**record, 'amount': '50', 'receipt_date': '2026-04-01'})
        self.assertEqual(self.lines(second), {self.s1: 5000, self.s2: 3000})
        self.finance.void_receipts([first])
        self.assertEqual(self.lines(second), {self.s1: 8000})

    def test_business_date_can_override_order_without_changing_confirmation_date(self):
        self.contracts.update_settlement(self.s3, {'amount': '40', 'settlement_date': '2026-03-01',
            'business_date': '2024-12-01', 'basis': '业务实际形成更早'})
        receipt = self.receipt('50')
        self.assertEqual(self.lines(receipt), {self.s3: 4000, self.s1: 1000})
        self.assertEqual(self.contracts.get_settlement(self.s3)['settlement_date'], '2026-03-01')

    def test_other_project_is_never_consumed(self):
        from services.project_service import create_project
        other = create_project({'name': '其他工程', 'customer_partner_id': self.customer, 'status': '进行中'})
        self.contracts.create_settlement({'project_id': other, 'amount': '999',
            'settlement_date': '2020-01-01', 'basis': '另一项目已确认'})
        self.assertEqual(self.lines(self.receipt('100')), {self.s1: 10000})

    def test_different_entity_is_not_consumed_and_unknown_requires_choice(self):
        third = self.contract('另一主体合同', '100', '2020-01-01', entity=2)
        wrong = self.income('100', '2020-01-01', third)
        receipt = self.receipt('100')
        self.assertNotIn(wrong, self.lines(receipt))
        with self.assertRaisesRegex(ValueError, '明确经营主体'):
            self.receipt('10', entity_id=None)

    def test_later_contract_attachment_preserves_income_and_received_money(self):
        from services.settlement_contract_service import attach
        from db.connection import db_read
        receipt = self.finance.create_receipt({'project_id': self.project, 'settlement_id': self.s3,
            'amount': '25', 'receipt_date': '2026-10-01', 'entity_id': 1})
        signed = self.contract('补签合同', '40', '2026-10-16')
        attach(self.s3, signed, '客户补签合同')
        after = self.contracts.get_settlement(self.s3)
        self.assertEqual((after['contract_id'], after['amount_minor'], after['settlement_date']),
                         (signed, 4000, '2026-03-01'))
        self.assertEqual(self.lines(receipt), {self.s3: 2500})
        with db_read() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM settlement_contract_revisions').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM receipts').fetchone()[0], 1)

    def test_attachment_rejects_insufficient_contract_and_rolls_back(self):
        from services.settlement_contract_service import attach
        small = self.contract('小额合同', '10', '2026-10-16')
        with self.assertRaisesRegex(ValueError, '不能超过'):
            attach(self.s3, small, '不能超额关联')
        self.assertIsNone(self.contracts.get_settlement(self.s3)['contract_id'])

    def test_project_ledger_reconciles_one_receipt_with_multiple_contract_lines(self):
        from services.project_ledger_service import get_details
        self.receipt('180')
        data = get_details(self.project)
        self.assertEqual(data['project']['receipt_count'], 1)
        self.assertTrue(all(detail['reconciled'] for detail in data['details'].values()))

    def test_unique_annual_receipt_policy_still_confirms_once_then_invoice_only_links(self):
        from services.project_service import create_project
        self.project = create_project({'name': '合成年度工程', 'customer_partner_id': self.customer, 'status': '进行中'})
        contract = self.contract('合成年度合同', '0', '2026-01-01', contract_type='annual',
                                 pricing_mode='actual', income_mode='receipt')
        receipt = self.receipt('250')
        self.assertEqual(self.contracts.get_contract(contract)['settled_minor'], 25000)
        self.assertNotIn(None, self.lines(receipt))
        self.finance.create_invoice({'project_id': self.project, 'contract_id': contract,
            'amount': '250', 'invoice_date': '2026-10-16', 'invoice_no': 'SYNTHETIC-ANNUAL-ONLY'})
        self.assertEqual(self.contracts.get_contract(contract)['settled_minor'], 25000)

    def test_save_failure_leaves_no_partial_receipt_or_reallocation(self):
        from db.connection import db_read
        from services.web_workbench_operations import database_fingerprint
        with db_read() as conn:
            before = database_fingerprint(conn)
        with patch('services.finance_service._reconcile_customer_invoice_receipts', side_effect=RuntimeError('合成异常')):
            with self.assertRaises(RuntimeError):
                self.receipt('180')
        with db_read() as conn:
            self.assertEqual(database_fingerprint(conn), before)


if __name__ == '__main__':
    unittest.main()
