"""Audit regressions on independent synthetic ledgers; no formal mutations."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


class BusinessBoundaryGuardTests(unittest.TestCase):
    def setUp(self):
        from db import connection, migration_runner
        from db.schema import init_db
        from services import web_workbench_service, operating_entity_service
        from web_workbench.synthetic import seed
        temporary = tempfile.TemporaryDirectory(prefix='business_boundaries_')
        self.addCleanup(temporary.cleanup)
        database = Path(temporary.name) / 'synthetic.db'
        for module in (connection, migration_runner):
            override = patch.object(module, 'DB_PATH', database)
            override.start()
            self.addCleanup(override.stop)
        init_db()
        seed()
        self.business = web_workbench_service
        self.owners = operating_entity_service

    def ledger(self):
        from db.connection import db_read
        with db_read() as conn:
            return list(conn.iterdump())

    def save_edit(self, module, identity, **changes):
        original = self.business.get_record(module, identity)
        return self.business.save_record(module, {**original, **changes}, identity, original['_version'])

    def receipt_with_funds(self, project=1, contract=1):
        from services import funds_service
        receipt = self.business.save_record('receipts', {
            'project_id': project, 'contract_id': contract, 'amount': '500',
            'receipt_date': '2026-10-03', 'payment_method': '银行转账',
        })
        transaction = funds_service.record_transaction({
            'request_key': uuid4().hex, 'category': 'receipt', 'source_id': receipt,
            'transaction_date': '2026-10-03', 'amount': '500', 'to_account_id': 1,
        })
        self.owners.assign_records('fund_transactions', [transaction], 1)
        return receipt, transaction

    def input_invoice(self, kind='purchase_orders', identity=1):
        from services import input_invoice_service
        return input_invoice_service.create_invoice({
            'invoice_no': str(uuid4().int)[:20], 'invoice_date': '2026-10-03',
            'entity_id': 1, 'supplier_id': 2,
            'buyer_name': self.owners.list_entities()[0]['name'], 'seller_name': '合成演示 · 钢材供应商',
            'net_amount': '100', 'tax_amount': '13', 'amount': '113', 'tax_rate_label': '13%',
        }, [{'kind': kind, 'record_id': identity, 'amount': '113'}])

    def test_contract_assignment_and_original_form_reject_inherited_funds_conflicts_atomically(self):
        self.owners.assign_records('contracts', [1], 1)
        self.receipt_with_funds()
        before = self.ledger()
        for action in (
            lambda: self.owners.preview_assignment('contracts', [1], 2),
            lambda: self.owners.assign_records('contracts', [1], 2),
            lambda: self.save_edit('contracts', 1, entity_id=2, notes='不能部分保存'),
        ):
            with self.assertRaisesRegex(ValueError, '资金流水.*经营主体'):
                action()
            self.assertEqual(self.ledger(), before)

    def test_contract_change_rejects_purchase_input_invoice_conflict(self):
        self.owners.assign_records('contracts', [1], 1)
        self.input_invoice()
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '进项票.*经营主体'):
            self.save_edit('contracts', 1, entity_id=2)
        self.assertEqual(self.ledger(), before)

    def test_contract_change_rejects_expense_input_invoice_conflict(self):
        self.owners.assign_records('contracts', [1], 1)
        self.save_edit('costs', 1, counterparty_name='合成演示 · 钢材供应商')
        self.input_invoice('cost_entries')
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '进项票.*经营主体'):
            self.owners.assign_records('contracts', [1], 2)
        self.assertEqual(self.ledger(), before)

    def test_explicit_source_subjects_take_priority_and_do_not_block_unrelated_contract_correction(self):
        self.owners.assign_records('contracts', [1], 1)
        receipt, _ = self.receipt_with_funds()
        self.owners.assign_records('receipts', [receipt], 1)
        self.owners.assign_records('purchase_orders', [1], 1)
        self.input_invoice()
        self.save_edit('contracts', 1, entity_id=2)
        rows = self.owners.list_records('purchase_orders')
        self.assertEqual(rows[0]['entity_id'], 1)
        self.assertEqual(self.owners.list_records('receipts')[0]['entity_id'], 1)
        self.assertEqual(self.owners.list_records('contracts')[0]['entity_id'], 2)

    def test_voided_evidence_does_not_block_owner_correction(self):
        from services import funds_service, input_invoice_service
        self.owners.assign_records('contracts', [1], 1)
        _, transaction = self.receipt_with_funds()
        invoice = self.input_invoice()
        funds_service.void_transaction(transaction, '合成录错更正')
        input_invoice_service.void_invoice(invoice, '合成录错更正')
        self.save_edit('contracts', 1, entity_id=2)
        self.assertEqual(self.owners.list_records('contracts')[0]['entity_id'], 2)

    def test_batch_failure_rolls_back_earlier_safe_subject_change(self):
        self.owners.assign_records('contracts', [1], 1)
        self.receipt_with_funds()
        other = self.business.save_record('contracts', {
            'name': '合成未关联合同', 'amount': '100', 'sign_date': '2026-10-03', 'status': 'active',
        })
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '资金流水'):
            self.owners.assign_records('contracts', [other, 1], 2)
        self.assertEqual(self.ledger(), before)

    def test_supplement_parent_edit_cannot_bypass_owner_guards(self):
        self.owners.assign_records('contracts', [1], 1)
        project = self.business.save_record('projects', {
            'name': '合成补充施工项目', 'customer_partner_id': 1, 'status': '进行中',
        })
        child = self.business.save_record('contracts', {
            'name': '合成补充协议', 'contract_type': 'supplement', 'parent_contract_id': 1,
            'customer_partner_id': 1, 'pricing_mode': 'actual', 'amount': '0',
            'sign_date': '2026-10-03', 'status': 'active',
        })
        self.business.save_record('allocations', {'project_id': project, 'contract_id': child, 'amount': '0'})
        self.business.save_record('settlements', {'project_id': project, 'contract_id': child,
            'amount': '1000', 'settlement_date': '2026-10-03', 'basis': '合成补充结算'})
        self.receipt_with_funds(project, child)
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '资金流水'):
            self.owners.assign_records('contracts', [1], 2)
        self.assertEqual(self.ledger(), before)
        unknown = self.business.save_record('contracts', {
            'name': '合成未确认原合同', 'customer_partner_id': 1, 'amount': '100',
            'sign_date': '2026-10-03', 'status': 'active',
        })
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '资金流水'):
            self.save_edit('contracts', child, parent_contract_id=unknown)
        self.assertEqual(self.ledger(), before)
        other = self.business.save_record('contracts', {
            'name': '合成另一主体原合同', 'customer_partner_id': 1, 'amount': '100',
            'sign_date': '2026-10-03', 'status': 'active', 'entity_id': 2,
        })
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '资金流水'):
            self.save_edit('contracts', child, parent_contract_id=other)
        self.assertEqual(self.ledger(), before)

    def test_multi_contract_conflict_cannot_make_linked_invoice_ownership_unknown(self):
        self.owners.assign_records('contracts', [1], 1)
        other = self.business.save_record('contracts', {
            'name': '合成同主体第二合同', 'customer_partner_id': 1, 'amount': '0',
            'pricing_mode': 'actual', 'sign_date': '2026-10-03', 'entity_id': 1,
        })
        self.business.save_record('allocations', {'project_id': 1, 'contract_id': other, 'amount': '0'})
        self.input_invoice()
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '进项票'):
            self.owners.assign_records('contracts', [other], 2)
        self.assertEqual(self.ledger(), before)

    def test_existing_project_and_contract_cannot_diverge_from_their_customer(self):
        customer = self.business.save_record('customers', {'legal_name': '合成另一客户'})
        before = self.ledger()
        for kind in ('projects', 'contracts'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, '同一客户'):
                self.save_edit(kind, 1, customer_partner_id=customer, notes='不能部分保存')
            self.assertEqual(self.ledger(), before)

    def test_unused_project_and_contract_allow_customer_correction(self):
        customer = self.business.save_record('customers', {'legal_name': '合成另一客户'})
        for kind in ('projects', 'contracts'):
            values = {'name': '合成待完善资料', 'customer_partner_id': 1}
            if kind == 'contracts':
                values.update(amount='100', sign_date='2026-10-03', status='draft')
            else:
                values['status'] = '筹备中'
            identity = self.business.save_record(kind, values)
            self.save_edit(kind, identity, customer_partner_id=customer)
            self.assertEqual(self.business.get_record(kind, identity)['customer_partner_id'], customer)

    def test_existing_business_without_current_contract_links_still_protects_its_customer(self):
        from db.connection import db_transaction
        project = self.business.save_record('projects', {
            'name': '合成现金工程', 'business_mode': 'cash', 'invoice_policy': 'not_required',
            'customer_partner_id': 1, 'status': '进行中',
        })
        self.business.save_record('settlements', {'project_id': project, 'amount': '100',
            'settlement_date': '2026-10-03', 'basis': '合成现金结算'})
        customer = self.business.save_record('customers', {'legal_name': '合成另一客户'})
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '已有.*收入'):
            self.save_edit('projects', project, customer_partner_id=customer)
        self.assertEqual(self.ledger(), before)
        # Receipt-only historical business must also protect its original customer.
        self.business.save_record('receipts', {'project_id': 1, 'contract_id': 1,
            'amount': '50', 'receipt_date': '2026-10-03', 'allow_advance': True})
        with db_transaction() as conn:
            conn.execute("UPDATE sales_invoices SET status='void' WHERE id=1")
            conn.execute("UPDATE settlements SET status='void' WHERE project_id=1")
            conn.execute("UPDATE contract_project_allocations SET status='void' WHERE project_id=1")
            conn.execute('UPDATE receipt_allocations SET contract_id=NULL,settlement_id=NULL WHERE project_id=1')
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '客户'):
            self.save_edit('projects', 1, customer_partner_id=customer)
        self.assertEqual(self.ledger(), before)

    def test_unknown_customer_can_be_completed_only_with_matching_known_relationships(self):
        from db.connection import db_transaction
        with db_transaction() as conn:
            conn.execute('UPDATE projects SET customer_partner_id=NULL WHERE id=1')
        customer = self.business.save_record('customers', {'legal_name': '合成另一客户'})
        before = self.ledger()
        with self.assertRaisesRegex(ValueError, '同一客户'):
            self.save_edit('projects', 1, customer_partner_id=customer)
        self.assertEqual(self.ledger(), before)
        self.save_edit('projects', 1, customer_partner_id=1)
        self.assertEqual(self.business.get_record('projects', 1)['customer_partner_id'], 1)
        with db_transaction() as conn:
            conn.execute('UPDATE contracts SET customer_partner_id=NULL WHERE id=1')
        self.save_edit('contracts', 1, customer_partner_id=1)
        self.assertEqual(self.business.get_record('contracts', 1)['customer_partner_id'], 1)

    def test_same_customer_metadata_edit_does_not_rewrite_business_facts(self):
        from db.connection import db_read
        with db_read() as conn:
            before = {table: list(conn.execute(f'SELECT * FROM {table}'))
                      for table in ('settlements', 'sales_invoices', 'receipt_allocations')}
        self.save_edit('projects', 1, notes='合成补充项目说明')
        self.save_edit('contracts', 1, notes='合成补充合同说明')
        with db_read() as conn:
            after = {table: list(conn.execute(f'SELECT * FROM {table}')) for table in before}
        self.assertEqual(after, before)

    def test_receipt_scope_changes_are_explicitly_rejected_without_partial_save(self):
        from services import finance_service
        receipt = self.business.save_record('receipts', {'project_id': 1, 'contract_id': 1,
            'amount': '500', 'receipt_date': '2026-10-03', 'payment_method': '银行转账'})
        project = self.business.save_record('projects', {'name': '合成新项目', 'status': '进行中'})
        contract = self.business.save_record('contracts', {'name': '合成新合同', 'amount': '1000',
            'sign_date': '2026-10-03', 'status': 'active'})
        original = self.business.get_record('receipts', receipt)
        for change in ({'project_id': project}, {'contract_id': contract},
                       {'project_id': project, 'contract_id': contract},
                       {'contract_id': None}):
            before = self.ledger()
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, '归属已锁定'):
                finance_service.update_receipt(receipt, {**original, **change, 'notes': '不能部分保存'})
            self.assertEqual(self.ledger(), before)
        finance_service.update_receipt(receipt, {**original, 'notes': '允许补充备注'})
        actual = self.business.get_record('receipts', receipt)
        self.assertEqual((actual['project_id'], actual['contract_id'], actual['notes']),
                         (1, 1, '允许补充备注'))

    def test_cash_receipt_keeps_its_null_contract_and_still_allows_notes(self):
        from services import finance_service
        project = self.business.save_record('projects', {'name': '合成现金项目', 'status': '进行中',
            'business_mode': 'cash', 'invoice_policy': 'not_required'})
        self.business.save_record('settlements', {'project_id': project, 'amount': '100',
            'settlement_date': '2026-10-03', 'basis': '合成结算'})
        receipt = self.business.save_record('receipts', {'project_id': project, 'amount': '100',
            'receipt_date': '2026-10-03', 'payment_method': '现金'})
        original = self.business.get_record('receipts', receipt)
        finance_service.update_receipt(receipt, {**original, 'contract_id': None, 'notes': '合成现金补充'})
        actual = self.business.get_record('receipts', receipt)
        self.assertIsNone(actual['contract_id'])
        self.assertEqual(actual['notes'], '合成现金补充')


if __name__ == '__main__':
    unittest.main()
