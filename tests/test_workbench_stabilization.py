"""Business consistency checks on fresh synthetic ledgers only."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class WorkbenchStabilizationTests(unittest.TestCase):
    def setUp(self):
        from db import connection, migration_runner
        from db.schema import init_db
        from services import web_workbench_service as business
        from web_workbench.synthetic import seed

        self.temp = tempfile.TemporaryDirectory(prefix='workbench_stabilization_')
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'synthetic.db'
        for module in (connection, migration_runner):
            database_patch = patch.object(module, 'DB_PATH', self.database)
            database_patch.start()
            self.addCleanup(database_patch.stop)
        init_db()
        seed()
        self.business = business

    def ledger(self):
        from db.connection import db_read
        with db_read() as conn:
            return list(conn.iterdump())

    def receipt_with_funds(self):
        from services import finance_service, funds_service
        receipt_id = finance_service.create_receipt({
            'project_id': 1, 'contract_id': 1, 'receipt_date': '2026-10-03',
            'amount': '500.00', 'payment_method': '银行转账',
        })
        funds_service.record_transaction({
            'request_key': 'synthetic-receipt-500', 'category': 'receipt',
            'source_id': receipt_id, 'transaction_date': '2026-10-03',
            'amount': '500.00', 'to_account_id': 1,
        })
        return receipt_id

    def test_cost_summary_and_rows_use_the_same_period(self):
        self.business.save_record('costs', {
            'project_id': 1, 'category': '工程施工 / 材料辅料',
            'amount': '100.01', 'cost_date': '2026-09-20',
        })
        for period in (None, 'all', '2026-09', '2026-10'):
            with self.subTest(period=period):
                data = self.business.dashboards('cost', 1, period)
                self.assertEqual(data['summary']['total_minor'],
                                 sum(row['amount_minor'] for row in data['rows']))
                if period and period != 'all':
                    self.assertTrue(all(row['business_date'].startswith(period)
                                        for row in data['rows']))
        self.assertEqual(self.business.dashboards('cost', 1, '2026-09')['summary']['total_minor'], 10001)

    def test_cost_period_validation_is_explicit(self):
        for month in ('2026-13', '2026-1', '2026-00', 'nonsense'):
            with self.subTest(month=month), self.assertRaisesRegex(ValueError, '月份'):
                self.business.dashboards('cost', 1, month)

    def test_shared_cost_is_counted_once_and_each_project_share_is_exact(self):
        from services import cost_service, project_profit_service
        second = self.business.save_record('projects', {'name': '合成均摊项目', 'status': '进行中'})
        before = {pid: self.business.dashboards('cost', pid)['summary']['total_minor']
                  for pid in (1, second)}
        cost_service.create_cost({
            'category': '管理与财务 / 利息手续费', 'amount': '0.03',
            'cost_date': '2026-10-04', 'allocation_method': 'equal',
            'project_ids': [1, second],
        })
        for pid, share in ((1, 2), (second, 1)):
            data = self.business.dashboards('cost', pid)
            self.assertEqual(data['summary']['total_minor'] - before[pid], share)
            self.assertEqual(data['summary']['total_minor'],
                             project_profit_service.get_project_summary(pid)['total_cost_minor'])
        all_rows = self.business.dashboards('cost')['rows']
        self.assertEqual(sum(row['amount_minor'] for row in all_rows if row['category'] == '管理与财务 / 利息手续费'), 3)

    def test_receipt_with_funds_rejects_fact_changes_atomically(self):
        from services import finance_service
        receipt_id = self.receipt_with_funds()
        original = self.business.get_record('receipts', receipt_id)
        for change in ({'amount': '400.00'}, {'amount': '600.00'},
                       {'receipt_date': '2026-10-04'}, {'payment_method': '现金'}):
            before = self.ledger()
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, '资金流水'):
                finance_service.update_receipt(receipt_id, {**original, **change})
            self.assertEqual(self.ledger(), before)

    def test_linked_receipt_can_add_notes_without_changing_funds(self):
        from services import finance_service
        receipt_id = self.receipt_with_funds()
        record = self.business.get_record('receipts', receipt_id)
        finance_service.update_receipt(receipt_id, {**record, 'notes': '合成补充依据'})
        self.assertEqual(self.business.get_record('receipts', receipt_id)['notes'], '合成补充依据')
        self.assertEqual(self.business.rows('transactions')[0]['amount_minor'], 50000)

    def test_voiding_a_batch_with_linked_funds_preserves_every_receipt(self):
        from services import finance_service
        linked = self.receipt_with_funds()
        other = finance_service.create_receipt({
            'project_id': 1, 'contract_id': 1, 'receipt_date': '2026-10-03', 'amount': '1.00',
        })
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '资金流水'):
            finance_service.void_receipts([other, linked])
        self.assertEqual(self.ledger(), before)

    def test_invoice_and_receipt_subject_can_be_confirmed_in_the_original_form(self):
        invoice = self.business.get_record('invoices', 1)
        self.business.save_record('invoices', {**invoice, 'entity_id': 2}, 1, invoice['_version'])
        self.assertEqual(self.business.get_record('invoices', 1)['entity_id'], 2)
        receipt = self.business.save_record('receipts', {
            'project_id': 1, 'contract_id': 1, 'amount': '500.00',
            'receipt_date': '2026-10-03', 'entity_id': 2,
        })
        self.assertEqual(self.business.get_record('receipts', receipt)['entity_id'], 2)
        invoice = self.business.get_record('invoices', 1)
        self.assertEqual(invoice['received_minor'], 50000)
        self.assertEqual(invoice['unreceived_minor'], 9950000)

    def test_invalid_subject_rolls_back_new_receipt(self):
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '经营主体'):
            self.business.save_record('receipts', {
                'project_id': 1, 'contract_id': 1, 'amount': '500.00',
                'receipt_date': '2026-10-03', 'entity_id': 99999,
            })
        self.assertEqual(self.ledger(), before)

    def test_subject_id_does_not_accept_boolean_or_fraction(self):
        before = self.ledger()
        for entity_id in (True, False, 1.5, 2.0, '1.5', 'invalid'):
            with self.subTest(entity_id=entity_id), self.assertRaisesRegex(ValueError, '经营主体'):
                self.business.save_record('receipts', {
                    'project_id': 1, 'contract_id': 1, 'amount': '1.00',
                    'receipt_date': '2026-10-03', 'entity_id': entity_id,
                })
            self.assertEqual(self.ledger(), before)

    def test_confirmed_funds_subject_cannot_conflict_with_receipt_subject(self):
        from services import operating_entity_service as entities
        receipt_id = self.receipt_with_funds()
        transaction_id = self.business.rows('transactions')[0]['id']
        entities.assign_records('fund_transactions', [transaction_id], 1)
        original = self.business.get_record('receipts', receipt_id)
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '资金流水.*经营主体'):
            self.business.save_record('receipts', {**original, 'entity_id': 2}, receipt_id, original['_version'])
        self.assertEqual(self.ledger(), before)
        entities.assign_records('receipts', [receipt_id], 1)
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '业务单据.*经营主体'):
            entities.assign_records('fund_transactions', [transaction_id], 2)
        self.assertEqual(self.ledger(), before)

    def test_paid_cost_and_purchase_changes_have_friendly_atomic_guards(self):
        from services import cost_service, funds_service, procurement_service
        for kind, record_id in (('cost', 1), ('purchase', 1)):
            funds_service.record_transaction({
                'request_key': 'synthetic-paid-' + kind, 'category': kind,
                'source_id': record_id, 'transaction_date': '2026-10-03',
                'amount': '1.00', 'from_account_id': 1,
                'opening': {'settled_amount': '0.00', 'payee': '合成收款人', 'reason': '合成付款依据'},
            })
        cost = self.business.get_record('costs', 1)
        purchase = self.business.get_record('purchases', 1)
        before = self.ledger()
        actions = [
            lambda: cost_service.update_cost_details(1, {**cost, 'amount': '1999.00'}),
            lambda: cost_service.update_cost_details(1, {**cost, 'cost_date': '2026-10-04'}),
            lambda: cost_service.void_costs([1]),
            lambda: self.business.save_record('purchases', {**purchase, 'purchase_date': '2026-10-04'}, 1, purchase['_version']),
            lambda: procurement_service.update_purchase_order_status([1], '现金', '已付款', '有发票'),
            lambda: procurement_service.void_purchase_orders([1]),
        ]
        for index, action in enumerate(actions):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, '资金流水'):
                action()
            self.assertEqual(self.ledger(), before)


if __name__ == '__main__':
    unittest.main()
