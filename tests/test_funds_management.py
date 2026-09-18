import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from db import connection
from db.backup import backup_database
from db.migration_runner import run_migrations
from services import (funds_service as funds, finance_service, contract_service,
                      project_service, procurement_service, cost_service, labor_service)


class FundsManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='funds_test_')
        cls.base = Path(cls.folder.name)/'baseline.db'
        backup_database(connection.DB_PATH, cls.base)
        run_migrations(cls.base)
        # Real accounts now exist: reset only the disposable test baseline.
        with closing(sqlite3.connect(cls.base)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            for table in ('fund_reconciliations', 'fund_transactions', 'fund_plans',
                          'fund_source_openings', 'fund_audit', 'fund_accounts'):
                conn.execute(f'DELETE FROM {table}')
            conn.commit()

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def setUp(self):
        self.database = Path(self.folder.name)/(uuid4().hex+'.db')
        backup_database(self.base,self.database)
        self.patcher = patch.object(connection,'DB_PATH',self.database)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.today = date.today().isoformat()
        self.start = (date.today()-timedelta(days=35)).isoformat()
        self.old = (date.today()-timedelta(days=45)).isoformat()
        self.bank = self.account('测试公司银行','10000')
        self.cash = self.account('测试现金','200')
        self.project = project_service.create_project({'name':'资金回归测试工程','business_mode':'cash',
                                                       'invoice_policy':'not_required','customer_name':'测试客户'})

    def account(self,name,amount):
        return funds.save_account({'name':name,'kind':'bank','opening_date':self.start,'opening_amount':amount})

    def record(self,category,amount,**changes):
        data = {'request_key':uuid4().hex,'category':category,'amount':amount,'transaction_date':self.today,
                'counterparty':'测试往来单位'}
        if category in ('receipt','owner_in','loan_in','other_in'):
            data['to_account_id'] = self.bank
        else:
            data['from_account_id'] = self.bank
        return funds.record_transaction({**data,**changes})

    def cost(self,amount='1000',source_date=None):
        return cost_service.create_cost({'project_id':self.project,'cost_date':source_date or self.today,
                'category':'管理费','amount':amount,'counterparty_name':'测试代理会计','allocation_method':'direct'})

    def purchase(self,amount=100000,purchase_date=None,**changes):
        header = {'purchase_type':'零星采购','project_id':self.project,'merchant_name_snapshot':'测试工具商',
                  'purchase_date':purchase_date or self.today,'payment_status':'未付款',**changes}
        item = {'material_name_snapshot':'测试工具','settlement_mode':'total','settlement_total_cents':amount,'price_basis':'inclusive'}
        return procurement_service.add_purchase_order(header,item)

    def opening(self,kind,source_id,amount='0',period=''):
        funds.verify_source(kind,source_id,period,{'settled_amount':amount,'payee':'测试往来单位','reason':'测试核对依据'})

    def receipt(self,amount='1000',receipt_date=None,payment_method='现金'):
        settlement = contract_service.create_settlement({'project_id':self.project,'settlement_date':receipt_date or self.today,'amount':'10000'})
        return finance_service.create_receipt({'project_id':self.project,'settlement_id':settlement,
                'receipt_date':receipt_date or self.today,'amount':amount,'payment_method':payment_method})

    def test_account_openings_do_not_infer_any_legacy_payments(self):
        self.assertEqual(funds.get_overview()['balance_minor'],1020000)
        self.assertEqual(funds.list_transactions(),[])
        self.purchase(payment_status='已付款')
        self.assertEqual(funds.get_overview()['expense_minor'],0)
        self.assertEqual(funds.list_transactions(),[])

    def test_transfer_is_atomic_and_not_company_income_or_expense(self):
        before = funds.get_overview()
        transaction_id = self.record('transfer','123.45',to_account_id=self.cash)
        after = funds.get_overview()
        self.assertEqual(after['balance_minor'],before['balance_minor'])
        self.assertEqual(after['income_minor'],0)
        self.assertEqual(after['expense_minor'],0)
        by_id = {row['id']:row for row in funds.list_accounts()}
        self.assertEqual(by_id[self.bank]['balance_minor'],987655)
        self.assertEqual(by_id[self.cash]['balance_minor'],32345)
        funds.void_transaction(transaction_id,'测试错误转账')
        self.assertEqual(funds.get_overview()['balance_minor'],before['balance_minor'])
        with self.assertRaises(ValueError):
            self.record('transfer','100',to_account_id=self.bank)

    def test_partial_payment_and_repeated_submit_cannot_overpay(self):
        cost = self.cost()
        self.opening('cost',cost)
        token = uuid4().hex
        first = self.record('cost','300.01',source_id=cost,request_key=token)
        duplicate = self.record('cost','300.01',source_id=cost,request_key=token)
        self.assertEqual(first,duplicate)
        row = next(r for r in funds.list_sources('cost') if r['id']==cost)
        self.assertEqual(row['remaining_minor'],69999)
        with self.assertRaises(ValueError):
            self.record('cost','700',source_id=cost)
        with self.assertRaises(ValueError):
            self.record('cost','300.02',source_id=cost,request_key=token)
        self.record('cost','699.99',source_id=cost)
        self.assertFalse(any(r['id']==cost for r in funds.list_sources('cost')))
        with closing(connection.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT amount_minor FROM cost_entries WHERE id=?',(cost,)).fetchone()[0],100000)

    def test_first_payment_verification_rolls_back_if_payment_invalid(self):
        purchase = self.purchase()
        with self.assertRaises(ValueError):
            self.record('purchase','1200',source_id=purchase,
                opening={'settled_amount':'0','payee':'工具商','reason':'已核对'})
        row = next(r for r in funds.list_sources('purchase') if r['id']==purchase)
        self.assertFalse(row['verified'])
        self.assertEqual(funds.list_transactions(),[])

    def test_historical_payment_is_baseline_not_cash_out(self):
        purchase = self.purchase(purchase_date=self.old,payment_status='已付款')
        row = next(r for r in funds.list_sources('purchase') if r['id']==purchase)
        self.assertEqual(row['state'],'待核实')
        self.opening('purchase',purchase,'600')
        self.assertEqual(funds.get_overview()['balance_minor'],1020000)
        self.record('purchase','400',source_id=purchase)
        self.assertEqual(funds.get_overview()['expense_minor'],40000)
        with self.assertRaises(ValueError):
            self.opening('purchase',purchase,'601')

    def test_employee_advance_reimbursement_not_supplier_payment_flag(self):
        purchase = self.purchase(purchase_date=self.old,payment_status='已付款',payment_method='员工垫付',purchaser='测试员工')
        row = next(r for r in funds.list_sources('purchase') if r['id']==purchase)
        self.assertEqual(row['counterparty'],'测试员工')
        self.assertEqual(row['state'],'待核实')
        self.record('purchase','1000',source_id=purchase,
                    opening={'settled_amount':'0','payee':'测试员工','reason':'公司尚未报销'})
        self.assertEqual(funds.list_transactions()[0]['counterparty'],'测试员工')

    def test_receipt_assignment_does_not_create_duplicate_business_receipt(self):
        receipt = self.receipt()
        with closing(connection.get_connection()) as conn:
            before = conn.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
        self.record('receipt','400',source_id=receipt)
        self.record('receipt','600',source_id=receipt,to_account_id=self.cash)
        with self.assertRaises(ValueError):
            self.record('receipt','.01',source_id=receipt)
        with closing(connection.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM receipts').fetchone()[0],before)
        self.assertEqual(funds.get_overview()['income_minor'],100000)

    def test_preopening_receipt_not_reentered_and_bill_only_on_encashment(self):
        receipt = self.receipt(receipt_date=self.old)
        self.assertFalse(any(r['id']==receipt for r in funds.list_sources('receipt')))
        with self.assertRaises(ValueError):
            self.record('receipt','1000',source_id=receipt)
        bill = self.receipt(receipt_date=self.old,payment_method='票据')
        self.assertTrue(any(r['id']==bill for r in funds.list_sources('receipt')))
        self.assertEqual(funds.get_overview()['income_minor'],0)
        self.record('receipt','1000',source_id=bill)
        self.assertEqual(funds.get_overview()['income_minor'],100000)

    def test_linked_source_void_and_update_are_blocked_until_correction(self):
        purchase = self.purchase()
        self.opening('purchase',purchase)
        payment = self.record('purchase','100',source_id=purchase)
        with self.assertRaises(sqlite3.IntegrityError):
            procurement_service.void_purchase_orders([purchase])
        cost = self.cost()
        self.opening('cost',cost)
        self.record('cost','100',source_id=cost)
        with self.assertRaises(sqlite3.IntegrityError):
            cost_service.void_costs([cost])
        receipt = self.receipt()
        self.record('receipt','100',source_id=receipt)
        with self.assertRaises(sqlite3.IntegrityError):
            finance_service.void_receipts([receipt])
        with closing(connection.get_connection()) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE receipts SET amount_minor=1 WHERE id=?',(receipt,))
        funds.void_transaction(payment,'原采购录错，先更正资金记录')
        procurement_service.void_purchase_orders([purchase])

    def test_monthly_wages_partial_and_work_log_guards(self):
        worker = labor_service.add_worker({'name':'测试工人','daily_rate':'300'})
        log = labor_service.add_work_log({'worker_id':worker,'work_date':self.today,'work_days':1,
                    'project_id':self.project,'construction_site':'测试工地','daily_rate':'300'})
        period = self.today[:7]
        self.opening('wage',worker,period=period)
        self.record('wage','100',source_id=worker,source_period=period)
        row = next(r for r in funds.list_sources('wage') if r['id']==worker and r['period']==period)
        self.assertEqual(row['remaining_minor'],20000)
        with closing(connection.get_connection()) as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('UPDATE work_logs SET amount_minor=100 WHERE id=?',(log,))
        self.record('wage','200',source_id=worker,source_period=period)

    def test_multproject_cost_is_paid_once(self):
        second = project_service.create_project({'name':'分摊项目二'})
        cost = cost_service.create_cost({'cost_date':self.today,'category':'用车','amount':'100.01',
            'counterparty_name':'测试租车','allocation_method':'equal','project_ids':[self.project,second]})
        self.opening('cost',cost)
        self.record('cost','100.01',source_id=cost)
        self.assertEqual(funds.get_overview()['expense_minor'],10001)
        with closing(connection.get_connection()) as conn:
            self.assertEqual(conn.execute("SELECT SUM(amount_minor) FROM cost_allocation_lines WHERE cost_entry_id=? AND status='active'",(cost,)).fetchone()[0],10001)

    def test_plans_never_change_money_and_undated_is_not_overdue(self):
        plan = funds.save_plan({'direction':'out','title':'待安排付款','amount':'1000'})
        self.assertEqual(funds.list_plans()[0]['state'],'待安排')
        self.assertEqual(funds.get_overview()['expense_minor'],0)
        payment = self.record('other_out','400',plan_id=plan)
        self.assertEqual(funds.list_plans()[0]['remaining_minor'],60000)
        self.record('other_out','600',plan_id=plan)
        self.assertEqual(funds.list_plans(),[])
        funds.void_transaction(payment,'撤销错误付款')
        self.assertEqual(funds.list_plans()[0]['remaining_minor'],40000)

    def test_plan_direction_and_source_must_match(self):
        cost = self.cost()
        self.opening('cost',cost)
        plan = funds.save_plan({'direction':'out','title':'成本计划','amount':'1000','source_kind':'cost','source_id':cost})
        with self.assertRaises(ValueError):
            self.record('owner_in','100',plan_id=plan)
        with self.assertRaises(ValueError):
            self.record('other_out','100',plan_id=plan)
        self.record('cost','100',source_id=cost,plan_id=plan)
        with self.assertRaises(ValueError):
            funds.save_plan({'direction':'out','title':'重复成本计划','amount':'901','source_kind':'cost','source_id':cost})

    def test_project_receivable_plan_uses_receipt_project_attribution(self):
        receipt = self.receipt()
        plan = funds.save_plan({'direction':'in','title':'工程回款计划','amount':'1000','source_kind':'project','source_id':self.project})
        self.record('receipt','1000',source_id=receipt,plan_id=plan)
        self.assertEqual(funds.list_plans(),[])

    def test_receivable_plan_cannot_exceed_confirmed_outstanding(self):
        self.receipt()
        with self.assertRaises(ValueError):
            funds.save_plan({'direction':'in','title':'超额计划','amount':'9000.01','source_kind':'project','source_id':self.project})

    def test_paid_business_plan_needs_linking_without_repaying(self):
        cost = self.cost()
        self.opening('cost',cost)
        plan = funds.save_plan({'direction':'out','title':'费用计划','amount':'1000','source_kind':'cost','source_id':cost})
        transaction = self.record('cost','1000',source_id=cost)
        self.assertEqual(funds.list_plans()[0]['state'],'需核对来源')
        before = funds.get_overview()['balance_minor']
        funds.link_transaction_plan(transaction,plan,'补充对应关系')
        self.assertEqual(funds.list_plans(),[])
        self.assertEqual(funds.get_overview()['balance_minor'],before)
        funds.link_transaction_plan(transaction,None,'更正关联')
        self.assertEqual(funds.list_plans()[0]['state'],'需核对来源')

    def test_filter_as_of_balance_and_source_cancelled_plan(self):
        self.record('owner_in','1')
        self.assertEqual(funds.list_accounts(self.start)[0]['balance_minor'],1000000)
        self.assertEqual(len(funds.list_transactions(start=self.today,end=self.today,account_id=self.cash)),0)
        with self.assertRaises(ValueError):
            funds.list_transactions(start=self.today,end=self.start)
        cost = self.cost()
        self.opening('cost',cost)
        funds.save_plan({'direction':'out','title':'计划后业务作废','amount':'1000','source_kind':'cost','source_id':cost})
        cost_service.void_costs([cost])
        self.assertEqual(funds.list_plans()[0]['state'],'需核对来源')

    def test_cash_skill_answers_use_new_accounts(self):
        from services.ai_operating_query_service import retrieve_operating_query
        result = retrieve_operating_query('现在账户余额多少')
        self.assertEqual(result['intent'],'funds_overview')
        self.assertIn('10,200.00',result['answer'])
        self.assertEqual(result['sources'][0]['page_key'],'funds')

    def test_concurrent_payments_cannot_overpay_source(self):
        cost = self.cost()
        self.opening('cost',cost)
        def pay(_index):
            try:
                return self.record('cost','600',source_id=cost)
            except ValueError:
                return None
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(pay,range(2)))
        self.assertEqual(sum(value is not None for value in results),1)
        self.assertEqual(funds.get_overview()['expense_minor'],60000)

    def test_failed_plan_reassignment_preserves_previous_link(self):
        first = funds.save_plan({'direction':'out','title':'支出安排','amount':'500'})
        wrong = funds.save_plan({'direction':'in','title':'到账安排','amount':'500'})
        transaction = self.record('other_out','500',plan_id=first)
        with self.assertRaises(ValueError):
            funds.link_transaction_plan(transaction,wrong,'错误方向')
        self.assertEqual(funds.list_transactions()[0]['plan_id'],first)
        self.assertEqual(funds.get_overview()['expense_minor'],50000)

    def test_archived_account_corrections_require_reactivation(self):
        transaction = self.record('transfer','200',from_account_id=self.cash,to_account_id=self.bank)
        funds.set_account_archived(self.cash,True)
        with self.assertRaises(ValueError):
            funds.void_transaction(transaction,'测试更正')
        with self.assertRaises(ValueError):
            self.record('owner_in','1',to_account_id=self.cash)
        funds.set_account_archived(self.cash,False)
        funds.void_transaction(transaction,'测试更正')
        self.assertEqual(next(r for r in funds.list_accounts() if r['id']==self.cash)['balance_minor'],20000)

    def test_verified_opening_locks_global_tracking_boundary(self):
        self.opening('cost',self.cost())
        with self.assertRaises(ValueError):
            funds.save_account({'name':'更早账户','kind':'bank','opening_date':self.old,'opening_amount':'0'})

    def test_amount_precision_and_request_key_payload_changes_are_rejected(self):
        with self.assertRaises(ValueError):
            self.record('owner_in','1.001')
        token = uuid4().hex
        self.record('owner_in','1',request_key=token,counterparty='甲')
        with self.assertRaises(ValueError):
            self.record('owner_in','1',request_key=token,counterparty='乙')
        self.assertEqual(funds.get_overview()['income_minor'],100)

    def test_company_cash_not_presented_as_project_cash(self):
        from services.ai_operating_query_service import retrieve_operating_query
        result = retrieve_operating_query('这个项目的现金流怎么样',project_id=self.project)
        self.assertEqual(result['intent'],'project_profit')
        self.assertIn('不按项目计算独立银行余额',result['answer'])

    def test_reconciliation_preserves_snapshot_and_never_adjusts_balance(self):
        transaction = self.record('owner_in','100')
        funds.reconcile_account(self.bank,self.today,'10123','对照银行日终余额')
        row = funds.list_reconciliations()[0]
        self.assertEqual(row['difference_minor'],2300)
        self.assertFalse(row['needs_review'])
        self.assertEqual(funds.get_overview()['balance_minor'],1030000)
        funds.void_transaction(transaction,'录重')
        row = funds.list_reconciliations()[0]
        self.assertTrue(row['needs_review'])
        self.assertEqual(row['book_minor'],1010000)
        self.assertEqual(row['current_book_minor'],1000000)

    def test_account_boundaries_and_invalid_amounts(self):
        for amount in ('NaN','Infinity','-1','0','0.001','99999999999999999999999'):
            with self.assertRaises(ValueError):
                self.record('owner_in',amount)
        with self.assertRaises(ValueError):
            self.record('owner_in','10',transaction_date=self.old)
        with self.assertRaises(ValueError):
            self.record('owner_in','10',transaction_date=(date.today()+timedelta(days=1)).isoformat())
        with self.assertRaises(ValueError):
            funds.set_account_archived(self.bank,True)
        self.record('owner_in','10')
        with self.assertRaises(ValueError):
            funds.save_account({'name':'测试公司银行','kind':'bank','opening_date':self.start,'opening_amount':'20000','reason':'修改'},self.bank)
        with self.assertRaises(ValueError):
            self.opening('cost',self.cost(),'100')

    def test_owner_money_not_business_revenue_or_cost_and_integrity(self):
        with closing(connection.get_connection()) as conn:
            before = [conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ('receipts','settlements','cost_entries')]
        for kind in ('owner_in','loan_in','owner_out','loan_out'):
            self.record(kind,'100')
        with closing(connection.get_connection()) as conn:
            after = [conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ('receipts','settlements','cost_entries')]
            self.assertEqual(before,after)
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertEqual(funds.get_overview()['balance_minor'],1020000)


if __name__=='__main__':
    unittest.main()
