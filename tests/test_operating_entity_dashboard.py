"""Ownership, exact entity totals and non-mutating drill-downs on synthetic ledgers."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class OperatingEntityDashboardTests(unittest.TestCase):
    def setUp(self):
        from db import connection, migration_runner
        from db.schema import init_db
        from services import web_workbench_service, operating_entity_service, operating_entity_dashboard
        from web_workbench.synthetic import seed

        self.temp = tempfile.TemporaryDirectory(prefix='entity_dashboard_')
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'synthetic.db'
        for module in (connection, migration_runner):
            database_patch = patch.object(module, 'DB_PATH', self.database)
            database_patch.start()
            self.addCleanup(database_patch.stop)
        init_db()
        seed()
        self.business = web_workbench_service
        self.ownership = operating_entity_service
        self.dashboard = operating_entity_dashboard

    def ledger(self):
        from db.connection import db_read
        with db_read() as conn:
            return list(conn.iterdump())

    def finance_rows(self):
        from db.connection import db_read
        with db_read() as conn:
            return {table: [dict(row) for row in conn.execute(f'SELECT * FROM {table} ORDER BY id')]
                    for table in ('projects','contracts','settlements','sales_invoices','receipts','receipt_allocations',
                                  'cost_entries','cost_allocation_lines','work_logs','purchase_orders','fund_transactions')}

    def cash_project(self):
        return self.business.save_record('projects', {'name':'合成演示 · 无合同现金工程', 'status':'已完工',
            'business_mode':'cash','invoice_policy':'not_required'})

    def test_reads_preserve_the_ledger_and_unknowns_are_not_assigned_to_the_first_entity(self):
        before = self.ledger()
        overview = self.dashboard.get_overview()
        self.assertEqual(overview['total_metrics']['income_minor'], 10_000_000)
        self.assertEqual(overview['pending_metrics']['income_minor'], 10_000_000)
        self.assertTrue(all(row['metrics']['income_minor'] == 0 for row in overview['entities']))
        self.assertGreater(overview['counts']['pending'], 0)
        self.dashboard.get_detail('income', 'all')
        self.dashboard.get_detail('income')
        for kind in self.ownership.RECORDS:
            self.ownership.list_records(kind)
        self.assertEqual(self.ledger(), before)

    def test_contract_confirmation_automatically_changes_project_status_without_writing_project_facts(self):
        before = self.finance_rows()
        impact = self.ownership.preview_assignment('contracts', [1], 1)
        self.assertEqual(impact['projects'][0]['project_name'], '合成演示 · 厂房钢结构工程')
        self.assertEqual(impact['projects'][0]['ownership_label'], '已自动归属')
        self.assertEqual(self.ownership.assign_records('contracts', [1], 1)['updated_count'], 1)
        project = self.ownership.list_records('projects')[0]
        self.assertEqual((project['entity_id'], project['ownership_status'], project['assignable']), (1,'inherited',False))
        self.assertEqual(self.ownership.list_records('contracts')[0]['ownership_status'], 'confirmed')
        self.assertEqual(self.finance_rows(), before)
        from db.connection import db_read
        with db_read() as conn:
            self.assertIsNone(self.ownership.entity_for(conn, 'projects', 1))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM operating_entity_audit').fetchone()[0], 1)
            self.assertFalse(conn.execute('PRAGMA foreign_key_check').fetchall())
        repeated = self.ownership.assign_records('contracts', [1], 1)
        self.assertEqual(repeated, {'updated_count':0,'unchanged_count':1})
        self.assertEqual(len(self.ownership.ownership_history('contracts', 1)), 1)

    def test_related_projects_cannot_be_manually_reconfirmed_or_reassigned(self):
        before = self.ledger()
        for action in (lambda:self.ownership.assign_records('projects',[1],1),
                       lambda:self.ownership.preview_assignment('projects',[1],1)):
            with self.assertRaisesRegex(ValueError, '项目已关联合同'):
                action()
        self.assertEqual(self.ledger(), before)
        self.assertEqual(self.ownership.list_records('projects', assignable_only=True), [])

    def test_original_contract_form_can_confirm_and_correct_subject_atomically(self):
        contract = self.business.get_record('contracts',1)
        self.business.save_record('contracts',{**contract,'entity_id':1},1,contract['_version'])
        self.assertEqual(self.business.rows('contracts')[0]['entity_name'], '示例钢结构有限公司')
        self.assertEqual(self.business.rows('projects')[0]['ownership_source'], '沿用合同')
        contract = self.business.get_record('contracts',1)
        self.business.save_record('contracts',{**contract,'entity_id':2},1,contract['_version'])
        self.assertEqual(self.ownership.list_records('projects')[0]['entity_id'],2)
        before = self.ledger()
        contract = self.business.get_record('contracts',1)
        with self.assertRaisesRegex(ValueError, '经营主体'):
            self.business.save_record('contracts',{**contract,'name':'不应保存此名称','entity_id':9999},1,contract['_version'])
        self.assertEqual(self.ledger(),before)

    def test_cash_project_confirmation_is_preserved_and_completed_business_is_included(self):
        project = self.cash_project()
        self.ownership.assign_records('projects',[project],2)
        self.business.save_record('settlements',{'project_id':project,'amount':'75000','settlement_date':'2026-10-01','basis':'合成最终结算'})
        self.business.save_record('receipts',{'project_id':project,'amount':'60000','receipt_date':'2026-10-02','payment_method':'银行转账'})
        overview = self.dashboard.get_overview()
        entity = next(row for row in overview['entities'] if row['id']==2)
        self.assertEqual(entity['metrics']['income_minor'],7_500_000)
        self.assertEqual(entity['metrics']['receipts_minor'],6_000_000)
        self.assertEqual(entity['metrics']['outstanding_minor'],1_500_000)
        self.assertEqual(entity['active_project_count'],0)

    def test_all_amounts_reconcile_with_drilldowns_and_authoritative_project_finance(self):
        from db.connection import db_read
        from db.business_facts import COST_FACTS_SQL, project_finance_rows
        self.ownership.assign_records('contracts',[1],1)
        overview = self.dashboard.get_overview()
        for metric in self.dashboard.METRICS:
            key=metric+'_minor'
            total = overview['pending_metrics'][key]+sum(row['metrics'][key] for row in overview['entities'])
            self.assertEqual(total,overview['total_metrics'][key])
            for identity in (None,1,2,'all'):
                detail = self.dashboard.get_detail(metric,identity)
                self.assertEqual(detail['total_minor'],sum(row['amount_minor'] for row in detail['rows']))
                expected = overview['total_metrics'][key] if identity=='all' else (overview['pending_metrics'][key]
                    if identity is None else next(row['metrics'][key] for row in overview['entities'] if row['id']==identity))
                self.assertEqual(detail['total_minor'],expected)
        with db_read() as conn:
            facts=project_finance_rows(conn)
            self.assertEqual(overview['total_metrics']['income_minor'],sum(row['settlement_minor'] for row in facts))
            self.assertEqual(overview['total_metrics']['receipts_minor'],sum(row['receipt_minor'] for row in facts))
            self.assertEqual(overview['total_metrics']['outstanding_minor'],sum(row['receivable_minor'] for row in facts))
            self.assertEqual(overview['total_metrics']['costs_minor'],conn.execute(f'SELECT SUM(amount_minor) FROM ({COST_FACTS_SQL}) WHERE project_id IS NOT NULL').fetchone()[0])

    def test_multi_subject_shared_costs_preserve_every_cent_without_charging_each_entity_the_full_bill(self):
        from services import cost_service
        self.ownership.assign_records('contracts',[1],1)
        project=self.cash_project()
        self.ownership.assign_records('projects',[project],2)
        before={row['id']:row['metrics']['costs_minor'] for row in self.dashboard.get_overview()['entities']}
        cost_service.create_cost({'category':'管理费','amount':'0.03','allocation_method':'equal',
            'project_ids':[1,project],'cost_date':'2026-10-03'})
        after={row['id']:row['metrics']['costs_minor'] for row in self.dashboard.get_overview()['entities']}
        self.assertEqual(after[1]-before[1],2)
        self.assertEqual(after[2]-before[2],1)
        last=self.ownership.list_records('cost_entries')[0]
        self.assertEqual(last['ownership_status'],'pending')
        self.assertEqual(last['ownership_source'],'多主体项目分摊')
        self.assertIsNone(last['entity_id'])

    def test_multiple_contract_subjects_are_conflicts_and_unconfirmed_contracts_block_automatic_attribution(self):
        second=self.business.save_record('contracts',{'name':'合成补充施工合同','customer_partner_id':1,
            'amount':'1000','sign_date':'2026-10-03','status':'active'})
        self.business.save_record('allocations',{'contract_id':second,'project_id':1,'amount':'1000'})
        self.ownership.assign_records('contracts',[1],1)
        self.assertEqual(self.ownership.list_records('projects')[0]['ownership_status'],'pending')
        self.ownership.assign_records('contracts',[second],2)
        project=self.ownership.list_records('projects')[0]
        self.assertEqual(project['ownership_status'],'conflict')
        self.assertIsNone(project['entity_id'])
        self.assertEqual(self.dashboard.get_overview()['pending_metrics']['income_minor'],10_000_000)
        self.ownership.assign_records('contracts',[second],1)
        self.assertEqual(self.ownership.list_records('projects')[0]['ownership_status'],'inherited')

    def test_existing_explicit_invoice_and_funds_subjects_are_not_overwritten_by_contract_ownership(self):
        self.ownership.assign_records('sales_invoices',[1],2)
        before=self.finance_rows()
        self.ownership.assign_records('contracts',[1],1)
        invoices=self.ownership.list_records('sales_invoices')
        self.assertEqual((invoices[0]['entity_id'],invoices[0]['ownership_status']),(2,'confirmed'))
        overview=self.dashboard.get_overview()
        entities={row['id']:row['metrics'] for row in overview['entities']}
        self.assertEqual(entities[1]['income_minor'],10_000_000)
        self.assertEqual(entities[2]['invoices_minor'],10_000_000)
        self.assertEqual(self.finance_rows(),before)

    def test_project_filter_does_not_silently_assign_an_entire_input_invoice_to_one_project(self):
        self.ownership.assign_records('contracts',[1],1)
        overview=self.dashboard.get_overview(1)
        self.assertFalse(overview['input_scope_available'])
        self.assertEqual(overview['total_metrics']['income_minor'],10_000_000)
        self.assertTrue(all(1 in row['project_ids'] for row in self.ownership.list_records('contracts',project_id=1)))
        with self.assertRaisesRegex(ValueError,'切回全部项目'):
            self.dashboard.get_detail('input_gross',1,1)

    def test_input_invoice_candidates_follow_contracts_but_explicit_cost_subjects_take_priority(self):
        from services import input_invoice_service
        self.ownership.assign_records('contracts',[1],1)
        self.assertEqual([row['id'] for row in input_invoice_service.cost_candidates(1)], [1])
        self.ownership.assign_records('purchase_orders',[1],2)
        self.assertEqual(input_invoice_service.cost_candidates(1), [])
        self.assertEqual([row['id'] for row in input_invoice_service.cost_candidates(2)], [1])

    def test_input_invoice_tax_does_not_duplicate_costs_or_assume_deductions(self):
        from services import input_invoice_service
        self.ownership.assign_records('contracts',[1],1)
        costs_before=self.dashboard.get_overview()['total_metrics']['costs_minor']
        data={'invoice_no':'12345678901234567890','invoice_date':'2026-10-03',
              'entity_id':1,'supplier_id':2,'buyer_name':'示例钢结构有限公司',
              'seller_name':'合成演示 · 钢材供应商','net_amount':'100','tax_amount':'13',
              'amount':'113','tax_rate_label':'13%'}
        invoice=input_invoice_service.create_invoice(data,[{'kind':'purchase_orders','record_id':1,'amount':'113'}])
        overview=self.dashboard.get_overview()
        self.assertEqual(overview['total_metrics']['costs_minor'],costs_before)
        self.assertEqual(overview['total_metrics']['input_gross_minor'],11300)
        self.assertEqual(overview['total_metrics']['input_tax_minor'],1300)
        self.assertEqual(overview['total_metrics']['deducted_tax_minor'],0)
        input_invoice_service.set_deduction(invoice,'eligible',reason='合成会计确认可抵扣')
        self.assertEqual(self.dashboard.get_detail('deducted_tax',1)['total_minor'],0)
        input_invoice_service.set_deduction(invoice,'deducted','2026-10',reason='合成会计已抵扣')
        detail=self.dashboard.get_detail('deducted_tax',1)
        self.assertEqual(detail['total_minor'],1300)
        self.assertEqual(detail['rows'][0]['number'],data['invoice_no'])

    def test_contract_scope_includes_explicit_business_links_without_a_budget_allocation(self):
        project=self.business.save_record('projects',{'name':'合成演示 · 仅事实关联项目','status':'进行中','customer_partner_id':1,
            'business_mode':'contract','invoice_policy':'required'})
        contract=self.business.save_record('contracts',{'name':'合成演示 · 据实结算关联合同','customer_partner_id':1,
            'pricing_mode':'actual','amount':'0','status':'active','sign_date':'2026-10-01'})
        allocation=self.business.save_record('allocations',{'contract_id':contract,'project_id':project,'amount':'0'})
        self.business.save_record('settlements',{'project_id':project,'contract_id':contract,'amount':'1000',
            'settlement_date':'2026-10-03','basis':'合成施工事实'})
        from db.connection import db_transaction
        with db_transaction() as conn:
            conn.execute("UPDATE contract_project_allocations SET status='void' WHERE id=?",(allocation,))
        self.ownership.assign_records('contracts',[contract],1)
        rows=self.ownership.list_records('contracts',project_id=project)
        self.assertEqual([row['id'] for row in rows],[contract])
        self.assertEqual(self.dashboard.get_overview(project)['entities'][0]['metrics']['income_minor'],100000)

    def test_multi_project_receipt_checks_all_active_allocations(self):
        from db.connection import db_read, db_transaction
        from services.entity_ownership import OwnershipResolver
        self.ownership.assign_records('contracts',[1],1)
        project=self.cash_project()
        self.ownership.assign_records('projects',[project],2)
        # A synthetic split tests the resolver independently of entry-form limits.
        self.business.save_record('receipts',{'project_id':1,'contract_id':1,'settlement_id':1,
            'amount':'100','receipt_date':'2026-10-03','payment_method':'银行转账'})
        with db_transaction() as conn:
            conn.execute("UPDATE receipt_allocations SET project_id=?,contract_id=NULL WHERE receipt_id=1",(project,))
            original=dict(conn.execute('SELECT * FROM receipt_allocations WHERE receipt_id=1').fetchone())
            columns=[key for key in original if key!='id']
            copied={**original,'public_id':'synthetic-extra-allocation','project_id':1,'contract_id':1}
            conn.execute(f"INSERT INTO receipt_allocations({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                         [copied[key] for key in columns])
        with db_read() as conn:
            row=conn.execute('SELECT * FROM receipts WHERE id=1').fetchone()
            resolved=OwnershipResolver(conn).record('receipts',row)
        self.assertIsNone(resolved['entity_id'])
        self.assertEqual(resolved['ownership_status'],'conflict')

    def test_supplement_contract_automatically_inherits_its_parent_subject(self):
        self.ownership.assign_records('contracts',[1],1)
        supplement=self.business.save_record('contracts',{'name':'合成补充协议','contract_type':'supplement',
            'parent_contract_id':1,'customer_partner_id':1,'amount':'500','sign_date':'2026-10-03','status':'active'})
        row=next(row for row in self.ownership.list_records('contracts') if row['id']==supplement)
        self.assertEqual((row['entity_id'],row['ownership_status'],row['ownership_source']),(1,'inherited','沿用原合同'))

    def test_preview_and_invalid_batches_leave_no_partial_confirmations(self):
        before=self.ledger()
        self.ownership.preview_assignment('contracts',[1],1)
        self.assertEqual(self.ledger(),before)
        with self.assertRaisesRegex(ValueError,'不存在'):
            self.ownership.assign_records('contracts',[1,99999],1)
        self.assertEqual(self.ledger(),before)


if __name__ == '__main__':
    unittest.main()
