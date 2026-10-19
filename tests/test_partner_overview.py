"""Partner overview regressions on fresh synthetic ledgers only."""
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from db import connection, migration_runner
from db.schema import init_db
from services import contract_service as contracts, finance_service as finance
from services import historical_receivable_service as history
from services import master_data_service as partners, partner_overview_service as overview
from services import procurement_service as procurement, project_service as projects


class PartnerOverviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='partner_overview_')
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / 'synthetic.db'
        for module in (connection, migration_runner):
            selected = patch.object(module, 'DB_PATH', self.database)
            selected.start()
            self.addCleanup(selected.stop)
        init_db()
        self.customer = self.partner('合成双角色客商', ['customer', 'supplier'])
        self.other = self.partner('合成其他客户', ['customer'])
        self.p1 = self.project('合成未签工程', self.customer)
        self.p2 = self.project('合成已完工程', self.customer, status='已完工')

    def partner(self, name, roles, **extra):
        return partners.create_business_partner(dict(legal_name=name, roles=roles,
            contact_name='合成联系人', contact_phone='13800000000', **extra))

    def project(self, name, customer=None, **extra):
        return projects.create_project({'name':name, 'customer_partner_id':customer, 'status':'进行中', **extra})

    def income(self, amount='100', day='2025-03-01', project=None, contract=None):
        return contracts.create_settlement(dict(project_id=project or self.p1, contract_id=contract,
            amount=amount, settlement_date=day, basis='合成客户确认', entity_id=1))

    def receipt(self, amount, day='2025-04-01', project=None, settlement=None, **extra):
        contract = contracts.get_settlement(settlement)['contract_id'] if settlement else None
        return finance.create_receipt(dict(project_id=project or self.p1, settlement_id=settlement,
            contract_id=contract, allocation_scope='contract' if settlement else 'project',
            amount=amount, receipt_date=day, entity_id=1, **extra))

    def purchase(self, amount=10001, day='2025-02-01', supplier=None, **extra):
        header = dict(purchase_type='零星采购', supplier_id=supplier or self.customer,
            merchant_name_snapshot='合成商户', purchase_date=day, payment_status='已付款',
            freight_amount_cents=3, **extra)
        items = [dict(material_name_snapshot='合成材料甲', quantity=1, unit_price_cents=amount,
                      tax_inclusive_unit_price_cents=amount, price_basis='inclusive', cost_category='材料费'),
                 dict(material_name_snapshot='合成材料乙', quantity=1, unit_price_cents=6,
                      tax_inclusive_unit_price_cents=6, price_basis='inclusive', cost_category='材料费')]
        return procurement.add_purchase_order(header, items)

    def row(self, data, identity=None):
        return next(row for row in data['rows'] if row['id'] == (identity or self.customer))

    def fingerprint(self):
        with connection.db_read() as conn:
            return list(conn.iterdump())

    def test_year_is_confirmation_or_arrival_date_not_contract_or_business_year(self):
        contract = contracts.create_contract(dict(name='合成历年框架', customer_partner_id=self.customer,
            project_id=self.p1, contract_type='annual', pricing_mode='actual', amount='0',
            sign_date='2023-01-01', status='active', entity_id=1))
        old = self.income('100', '2024-12-01', contract=contract)
        current = self.income('60.01', '2025-01-01')
        contracts.update_settlement(current, dict(amount='60.01', settlement_date='2025-01-01',
                                                  business_date='2022-01-01', basis='合成形成时间'))
        self.receipt('30.01', settlement=old)
        self.receipt('20', day='2024-12-02', settlement=old)
        finance.create_invoice(dict(project_id=self.p1, contract_id=contract, settlement_id=old, amount='60.01',
            invoice_date='2025-05-01', invoice_no='SYNTHETIC-OVERVIEW', entity_id=1))
        data = overview.get_overview('customers', 2025)
        row = self.row(data)
        self.assertEqual(row['yearly_business_minor'], 6001)
        self.assertEqual(row['yearly_receipt_minor'], 3001)
        self.assertEqual(row['current_receivable_minor'], 11000)
        self.assertEqual(row['active_project_count'], 1)
        self.assertEqual(row['latest_business_date'], '2025-05-01')
        self.assertNotIn(2023, data['years'])  # no signing-year substitution
        self.assertEqual(self.row(overview.get_overview('customer', 2024))['yearly_business_minor'], 10000)

    def test_split_receipt_counts_allocated_portions_once_and_recent_event_once(self):
        first = self.income('60')
        second = self.income('40', project=self.p2)
        receipt = self.receipt('60', settlement=first)
        # Model a historical explicit multi-project allocation without touching a live ledger.
        with connection.db_transaction() as conn:
            conn.execute('UPDATE receipts SET amount_minor=10000 WHERE id=?', (receipt,))
            conn.execute('UPDATE receipt_allocations SET allocated_amount_minor=6000 WHERE receipt_id=?', (receipt,))
            conn.execute('''INSERT INTO receipt_allocations(public_id,receipt_id,project_id,settlement_id,
                allocated_amount_minor,created_at,status) VALUES (?,?,?,?,4000,'synthetic','active')''',
                (str(uuid4()), receipt, self.p2, second))
        row = self.row(overview.get_overview('customers', 2025))
        self.assertEqual(row['yearly_receipt_minor'], 10000)
        self.assertEqual(row['current_receivable_minor'], 0)
        receipt_events = [event for event in row['recent_activity'] if event['module'] == 'receipts']
        self.assertEqual(len(receipt_events), 1)
        self.assertEqual(receipt_events[0]['amount_minor'], 10000)
        scoped = overview.get_overview('customers', 2025, self.p2)
        self.assertEqual(self.row(scoped)['yearly_receipt_minor'], 4000)
        self.assertEqual(self.row(scoped)['active_project_count'], 0)

    def test_void_allocation_void_receipt_and_void_income_never_count(self):
        income = self.income()
        receipt = self.receipt('40', settlement=income)
        with connection.db_transaction() as conn:
            conn.execute("UPDATE receipt_allocations SET status='void' WHERE receipt_id=?", (receipt,))
        data = overview.get_overview('customers', 2025)
        self.assertEqual(self.row(data)['yearly_receipt_minor'], 0)
        self.assertEqual(self.row(data)['current_receivable_minor'], 10000)
        self.assertEqual(data['unattributed']['unallocated_receipt_minor'], 4000)
        with connection.db_transaction() as conn:
            conn.execute("UPDATE receipt_allocations SET status='active' WHERE receipt_id=?", (receipt,))
            conn.execute("UPDATE receipts SET status='void' WHERE id=?", (receipt,))
        self.assertEqual(self.row(overview.get_overview('customers', 2025))['yearly_receipt_minor'], 0)
        with connection.db_transaction() as conn:
            conn.execute("UPDATE settlements SET status='void' WHERE id=?", (income,))
        self.assertEqual(self.row(overview.get_overview('customers', 2025))['yearly_business_minor'], 0)

    def test_project_preallocation_does_not_offset_other_project_or_income(self):
        self.income('100', project=self.p2)
        self.receipt('150', project=self.p1)
        row = self.row(overview.get_overview('customers', 2025))
        self.assertEqual((row['yearly_receipt_minor'], row['pending_receipt_minor']), (15000, 15000))
        self.assertEqual(row['current_receivable_minor'], 10000)
        self.assertEqual(self.row(overview.get_overview('customers', 2024))['current_receivable_minor'], 10000)

    def test_one_arrival_split_across_customers_is_not_repeated_in_customer_totals(self):
        first = self.income('60')
        other_project = self.project('合成跨客户到账工程', self.other)
        second = self.income('40', project=other_project)
        receipt = self.receipt('60', settlement=first)
        with connection.db_transaction() as conn:
            conn.execute('UPDATE receipts SET amount_minor=11000 WHERE id=?', (receipt,))
            conn.execute('''INSERT INTO receipt_allocations(public_id,receipt_id,project_id,settlement_id,
                allocated_amount_minor,created_at,status) VALUES (?,?,?,?,4000,'synthetic','active')''',
                (str(uuid4()), receipt, other_project, second))
        data = overview.get_overview('customers', 2025)
        self.assertEqual(self.row(data)['yearly_receipt_minor'], 6000)
        self.assertEqual(self.row(data,self.other)['yearly_receipt_minor'], 4000)
        self.assertEqual(data['summary']['yearly_receipt_minor'], 10000)
        self.assertEqual(data['unattributed']['unallocated_receipt_minor'], 1000)

    def test_invalid_bound_income_and_overallocated_arrival_remain_unassigned(self):
        income = self.income()
        receipt = self.receipt('40', settlement=income)
        with connection.db_transaction() as conn:
            conn.execute('UPDATE receipt_allocations SET allocated_amount_minor=5000 WHERE receipt_id=?', (receipt,))
        data = overview.get_overview('customers', 2025)
        self.assertEqual(self.row(data)['yearly_receipt_minor'], 0)
        self.assertEqual(self.row(data)['current_receivable_minor'], 10000)
        self.assertEqual(data['unattributed']['overallocated_receipt_count'], 1)
        self.assertEqual(data['unattributed']['unallocated_receipt_minor'], 4000)
        with connection.db_transaction() as conn:
            conn.execute('UPDATE receipt_allocations SET allocated_amount_minor=4000 WHERE receipt_id=?', (receipt,))
            conn.execute("UPDATE settlements SET status='void' WHERE id=?", (income,))
        data = overview.get_overview('customers', 2025)
        self.assertEqual(self.row(data)['yearly_receipt_minor'], 0)
        self.assertEqual(data['unattributed']['unallocated_receipt_minor'], 4000)

    def test_project_customer_precedes_contract_and_fallback_is_explicit_only(self):
        foreign_contract = contracts.create_contract(dict(name='合成历史关系', customer_partner_id=self.other,
            amount='0', pricing_mode='actual', sign_date='2024-01-01', status='active', entity_id=1))
        self.income('100')
        with connection.db_transaction() as conn:
            conn.execute('UPDATE settlements SET contract_id=? WHERE project_id=?', (foreign_contract, self.p1))
        fallback = self.project('合成仅合同归属工程')
        legacy_income = self.income('70', project=fallback)
        with connection.db_transaction() as conn:
            conn.execute('UPDATE settlements SET contract_id=? WHERE id=?', (foreign_contract, legacy_income))
        unknown = self.project('合成客户待归属工程')
        self.income('30', project=unknown)
        data = overview.get_overview('customers', 2025)
        self.assertEqual(self.row(data)['yearly_business_minor'], 10000)
        self.assertEqual(self.row(data,self.other)['yearly_business_minor'], 7000)
        self.assertEqual(self.row(data,self.other)['active_project_count'], 1)
        self.assertEqual(data['unattributed']['yearly_business_minor'], 3000)
        self.assertEqual(data['unattributed']['current_receivable_minor'], 3000)

    def test_old_debt_is_separate_unknown_balance_is_not_zero_and_scope_does_not_guess(self):
        old = history.save_project(self.customer, '合成已知旧账', '100')
        history.save_project(self.customer, '合成总额待确认旧账')
        history.record_receipt(old, '2025-03-02', '20', 'synthetic-history-1')
        self.income('50')
        data = overview.get_overview('customers', 2025)
        row = self.row(data)
        self.assertEqual((row['yearly_business_minor'], row['yearly_receipt_minor']), (5000, 2000))
        self.assertEqual((row['project_receivable_minor'], row['historical_receivable_minor']), (5000, 8000))
        self.assertEqual(row['current_receivable_minor'], 13000)
        self.assertFalse(row['receivable_complete'])
        self.assertIsNone(row['receivable_share_bps'])
        self.assertIsNone(self.row(data,self.other)['receivable_share_bps'])
        scoped = self.row(overview.get_overview('customers', 2025, self.p1))
        self.assertEqual((scoped['yearly_receipt_minor'], scoped['current_receivable_minor']), (0, 5000))
        self.assertEqual(scoped['historical_projects'], [])

    def test_dual_role_archive_status_and_integer_shares_include_unsigned_cash_projects(self):
        cash = self.project('合成现金工程', self.other, business_mode='cash')
        self.income('30.01')
        self.income('69.99', project=cash)
        with connection.db_transaction() as conn:
            conn.execute("UPDATE business_partners SET status='inactive' WHERE id=?", (self.customer,))
        data = overview.get_overview('customers', 2025)
        row = self.row(data)
        self.assertEqual(set(row['roles']), {'customer','supplier'})
        self.assertEqual(row['status'], 'inactive')
        self.assertEqual(row['income_share_bps'], 3001)
        self.assertEqual(row['receivable_share_bps'], 3001)
        self.assertEqual(self.row(data,self.other)['income_share_bps'], 6999)
        self.assertEqual(data['summary']['active_project_count'], 2)

    def test_supplier_is_order_grain_and_paid_mark_is_not_cash_or_payable(self):
        order = self.purchase()
        self.purchase(day='2024-01-01')
        with connection.db_transaction() as conn:
            conn.execute("UPDATE supplier_profiles SET supplier_kind='manufacturer' WHERE partner_id=?", (self.customer,))
        data = overview.get_overview('suppliers', 2025)
        row = self.row(data)
        self.assertEqual((row['yearly_purchase_minor'], row['purchase_count']), (10010, 1))
        self.assertEqual(row['latest_purchase_id'], order)
        self.assertEqual(row['supplier_kind'], 'manufacturer')
        self.assertEqual(row['contact_phone'], '13800000000')
        self.assertEqual(row['purchase_share_bps'], 10000)
        self.assertFalse(any('payable' in key or 'paid_minor' in key for key in row))
        with connection.db_read() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM fund_transactions').fetchone()[0], 0)

    def test_supplier_project_scope_uses_allocated_share_and_ignores_void_orders(self):
        order = self.purchase(allocation_method='equal', project_ids=[self.p1,self.p2])
        company = self.row(overview.get_overview('supplier', 2025))
        p1 = self.row(overview.get_overview('supplier', 2025, self.p1))
        p2 = self.row(overview.get_overview('supplier', 2025, self.p2))
        self.assertEqual(company['yearly_purchase_minor'], p1['yearly_purchase_minor'] + p2['yearly_purchase_minor'])
        self.assertEqual((p1['purchase_count'],p2['purchase_count']), (1,1))
        with connection.db_transaction() as conn:
            conn.execute("UPDATE purchase_orders SET status='void' WHERE id=?", (order,))
        self.assertEqual(self.row(overview.get_overview('supplier', 2025))['yearly_purchase_minor'], 0)

    def test_supplier_name_does_not_assign_an_unlinked_purchase(self):
        order = self.purchase()
        with connection.db_transaction() as conn:
            conn.execute('UPDATE purchase_orders SET supplier_partner_id=NULL,merchant_name_snapshot=? WHERE id=?',
                         ('合成双角色客商', order))
        data = overview.get_overview('suppliers', 2025)
        self.assertEqual(self.row(data)['yearly_purchase_minor'], 0)
        self.assertEqual(data['unattributed'], {'yearly_purchase_minor':10010, 'purchase_count':1})

    def test_snapshot_is_readonly_nested_services_share_connection_and_json_keeps_big_money(self):
        self.income('100')
        huge = 9007199254740993
        with connection.db_transaction() as conn:
            conn.execute('UPDATE settlements SET amount_minor=?', (huge,))
        before = self.fingerprint()
        original = partners.list_business_partners
        def checked(*args, **kwargs):
            with connection.db_read() as conn:
                self.assertEqual(conn.execute('PRAGMA query_only').fetchone()[0], 1)
                self.assertIs(conn, connection._READ_SNAPSHOT.get())
            return original(*args, **kwargs)
        with patch.object(partners, 'list_business_partners', side_effect=checked), patch.object(
                connection, 'get_connection', side_effect=AssertionError('must not open a write connection')):
            data = overview.get_overview('customers', 2025)
            overview.get_overview('suppliers', 2025)
        self.assertEqual(before, self.fingerprint())
        self.assertEqual(self.row(data)['yearly_business_minor'], huge)
        from web_workbench.server import json_value
        encoded = json.loads(json.dumps(json_value(data)))
        self.assertEqual(self.row(encoded)['yearly_business_minor'], str(huge))
        self.assertEqual(self.row(encoded)['income_share_bps'], 10000)

    def test_validation_and_empty_years(self):
        for kwargs in [dict(role='bad'),dict(role='customers',year=2025.5),dict(role='customers',year=True),
                       dict(role='suppliers',year=9999),dict(role='suppliers',project_id='bad'),
                       dict(role='customers',project_id=999999)]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                overview.get_overview(**kwargs)
        data = overview.get_overview('customers', 2020)
        self.assertIn(2020, data['years'])
        self.assertEqual(data['summary']['yearly_business_minor'], 0)
        self.assertIsNone(self.row(data)['income_share_bps'])


class PartnerOverviewJavaScriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node is required for isolated frontend checks')
    def test_navigation_preferences_precision_and_late_response(self):
        root = Path(__file__).resolve().parents[1]
        program = r'''
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
const context=vm.createContext({state:{route:'customers',project:'7',page:9,keyword:'unrelated'},
  query:values=>new URLSearchParams(Object.entries(values).filter(([,v])=>v!==''&&v!=null)).toString(),
  esc:value=>String(value).replace(/[&<>"']/g,x=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[x])),
  money:value=>'¥'+BigInt(value).toString(),URLSearchParams});
vm.runInContext(fs.readFileSync('web_workbench/static/partners.js','utf8'),context);
assert.equal(vm.runInContext("partnerLink('projects',3)",context),'#projects?customer=3');
assert.equal(vm.runInContext("partnerLink('offers',3)",context),'#offers?supplier=3');
assert.equal(vm.runInContext("partnerLink('purchases',3)",context),'#purchases?supplier=3');
assert.equal(vm.runInContext("partnerLink('purchases',3,{year:2025})",context),'#purchases?supplier=3&year=2025');
const annualLink=vm.runInContext("partnerMoneyLink({id:3,name:'合成供应商',yearly_purchase_minor:'123'},'yearly_purchase_minor','suppliers',2025)",context);
assert.ok(annualLink.includes('#purchases?supplier=3&amp;year=2025'));
context.state.boot={schema:{suppliers:{can_update:true}},read_only:true};
const actions=vm.runInContext("partnerActions('suppliers',{id:3})",context);
assert.ok(actions.includes('#purchases?supplier=3\"'));
assert.ok(!actions.includes('year=2025'));assert.ok(!actions.includes('data-partner-edit'));
assert.ok(actions.includes('tab=input_invoices&supplier=3'));
context.state.boot.operations={supplier_deactivate:{title:'停用供应商'},customer_deactivate:{title:'停用客户档案'}};
context.state.boot.schema.customers={can_update:true};
context.state.boot.read_only=false;
assert.ok(vm.runInContext("partnerActions('suppliers',{id:3,status:'active'})",context).includes('data-operation="supplier_deactivate"'));
assert.ok(vm.runInContext("partnerActions('customers',{id:1,status:'active',historical_projects:[]})",context).includes('data-operation="customer_deactivate"'));
context.state.boot.read_only=true;
assert.ok(!vm.runInContext("partnerActions('suppliers',{id:3,status:'active'})",context).includes('data-operation='));
vm.runInContext("partnerPreferences('customers').page=2;partnerPreferences('suppliers').keyword='门店'",context);
assert.equal(context.state.page,9);assert.equal(context.state.keyword,'unrelated');
assert.equal(context.state.partnerOverview.customers.page,2);
assert.equal(context.state.partnerOverview.suppliers.page,0);
const share=vm.runInContext("partnerShare(3001,'9007199254740993','30000000000000000','收入贡献')",context);
assert.ok(share.includes('30.0%'));assert.ok(share.includes('9007199254740993'));
assert.ok(share.includes('<meter'));assert.ok(share.includes('value="3001"'));assert.ok(!share.includes('style='));
const escaped=vm.runInContext("partnerRecent({recent_activity:[{date:'2025-01-01',module:'receipts',id:1,amount_minor:'1',number:'<script>'}]})",context);
assert.ok(escaped.includes('&lt;script&gt;'));assert.ok(!escaped.includes('<script>'));
(async()=>{
  let resolve;context.api=()=>new Promise(done=>{resolve=done;});
  const promise=vm.runInContext("renderPartnerOverview('customers')",context);
  context.state.route='suppliers';resolve({});await promise;
  context.state.route='customers';
  const scoped=vm.runInContext("renderPartnerOverview('customers')",context);
  context.state.project='8';resolve({});await scoped;
  console.log('partner frontend isolated checks passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
        result = subprocess.run(['node', '-e', program], cwd=root, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('isolated checks passed', result.stdout)


if __name__ == '__main__':
    unittest.main()
