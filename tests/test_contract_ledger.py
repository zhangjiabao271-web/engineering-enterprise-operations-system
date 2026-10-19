"""Customer navigation must not merge facts or reinterpret signing-year filters."""
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch

from db import connection, migration_runner
from db.schema import init_db
from services import contract_ledger_service as ledger, web_workbench_service as business
from services.web_workbench_operations import database_fingerprint
from web_workbench.synthetic import seed


class ContractLedgerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='contract_ledger_')
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.database = self.directory / 'synthetic.db'
        for module in (connection, migration_runner):
            selected = patch.object(module, 'DB_PATH', self.database)
            selected.start()
            self.addCleanup(selected.stop)
        init_db()
        seed()

    def fingerprint(self):
        with connection.db_read() as conn:
            return database_fingerprint(conn)

    def receipt(self, amount='25000.01'):
        return business.save_record('receipts', {'project_id':1,'contract_id':1,'settlement_id':1,
            'amount':amount,'receipt_date':'2026-10-10','payment_method':'银行转账'})

    def another_contract(self, year='2025', customer=1, actual=False):
        return business.save_record('contracts', {'customer_partner_id':customer,
            'name':'合成演示 · 历年年度合同','contract_type':'annual',
            'pricing_mode':'actual' if actual else 'fixed','amount':'0' if actual else '80000',
            'sign_date':year+'-01-01','status':'active'})

    def test_lifetime_totals_and_details_reconcile_without_writing(self):
        self.receipt()
        before = self.fingerprint()
        overview = ledger.get_overview()
        self.assertEqual((overview['customer_count'], overview['contract_count']), (1,1))
        expected = {'settlement_minor':10_000_000,'receipt_minor':2_500_001,
                    'invoice_minor':10_000_000,'receivable_minor':7_499_999}
        self.assertEqual(overview['summary'], expected)
        self.assertEqual({key:overview['customers'][0][key] for key in expected}, expected)
        details = ledger.get_details(1)
        for detail in details['details'].values():
            self.assertTrue(detail['reconciled'])
            self.assertEqual(detail['total_minor'], sum(row[detail['amount_key']] for row in detail['rows']))
        self.assertEqual(before, self.fingerprint())

    def test_year_selects_signing_year_not_receipt_or_income_year(self):
        with connection.db_transaction() as conn:
            conn.execute("UPDATE contracts SET sign_date='2025-12-01' WHERE id=1")
        self.receipt()
        self.another_contract('2026', actual=True)
        data = ledger.get_overview(customer_key='customer:1', year='2025')
        self.assertEqual(data['years'], ['2026','2025'])
        self.assertEqual([row['id'] for row in data['contracts']], [1])
        self.assertEqual(data['summary']['receipt_minor'], 2_500_001)
        self.assertEqual(data['summary']['settlement_minor'], 10_000_000)
        self.assertEqual(ledger.get_overview(year='2026')['summary']['settlement_minor'], 0)

    def test_one_customer_can_have_both_entities_without_mixing_filters(self):
        from services.operating_entity_service import assign_records
        second = self.another_contract()
        assign_records('contracts',[1],1,'合成归属')
        assign_records('contracts',[second],2,'合成归属')
        self.assertEqual(ledger.get_overview()['customers'][0]['contract_count'],2)
        self.assertEqual([row['id'] for row in ledger.get_overview(entity_id=2)['contracts']], [second])
        self.assertEqual(ledger.get_overview(status='completed')['contracts'], [])
        self.assertEqual(ledger.get_overview(keyword='DEMO-HT-001')['contract_count'], 1)

    def test_void_allocations_and_receipts_are_excluded(self):
        receipt = self.receipt()
        with connection.db_transaction() as conn:
            conn.execute("UPDATE receipt_allocations SET status='void' WHERE receipt_id=?", (receipt,))
        summary = ledger.get_overview()['summary']
        self.assertEqual((summary['receipt_minor'],summary['receivable_minor']), (0,10_000_000))
        with connection.db_transaction() as conn:
            conn.execute("UPDATE receipt_allocations SET status='active' WHERE receipt_id=?", (receipt,))
            conn.execute("UPDATE receipts SET status='void' WHERE id=?", (receipt,))
        self.assertEqual(ledger.get_details(1)['details']['receipts']['rows'], [])

    def test_split_receipt_counts_only_the_selected_contract_and_project(self):
        second = self.another_contract(actual=True)
        project = business.save_record('projects',{'name':'合成演示 · 第二项目','customer_partner_id':1,'status':'进行中'})
        business.save_record('allocations',{'contract_id':second,'project_id':project,'amount':'0'})
        settlement = business.save_record('settlements',{'contract_id':second,'project_id':project,
            'amount':'50000','settlement_date':'2026-10-03','basis':'合成结算'})
        receipt = self.receipt('10000')
        with connection.db_transaction() as conn:
            conn.execute("UPDATE receipts SET amount_minor=1500000 WHERE id=?",(receipt,))
            conn.execute('''INSERT INTO receipt_allocations
                (public_id,receipt_id,project_id,contract_id,settlement_id,allocated_amount_minor,status,created_at)
                VALUES ('split-contract-test',?,?,?,?,500000,'active','2026-10-10')''',
                (receipt,project,second,settlement))
        self.assertEqual(ledger.get_overview()['summary']['receipt_minor'],1_500_000)
        self.assertEqual(ledger.get_details(second)['summary']['receipt_minor'],500_000)
        scoped = ledger.get_overview(project_id=1)
        self.assertEqual(scoped['contract_count'],1)
        self.assertEqual(scoped['summary']['receipt_minor'],1_000_000)
        with self.assertRaises(ValueError):
            ledger.get_details(second,project_id=1)

    def test_unknown_customers_are_visible_and_not_merged_by_similar_names(self):
        with connection.db_transaction() as conn:
            conn.execute("UPDATE contracts SET customer_partner_id=NULL,customer_name_snapshot='' WHERE id=1")
        self.another_contract()
        rows = ledger.get_overview()['customers']
        self.assertEqual(len(rows),2)
        self.assertIn('unlinked:1',[row['customer_key'] for row in rows])

    def test_http_read_only_assets_and_validation(self):
        from web_workbench.server import WorkbenchServer
        server = WorkbenchServer(0,read_only=True,demo=True)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(server.origin+'/api/contract_ledger?year=2026') as response:
                data = json.load(response)
            self.assertEqual(data['year_basis'],'sign_date')
            self.assertEqual(data['summary']['settlement_minor'],'10000000')
            for asset in ('contract-ledger.js','contract-ledger.css','contract-project-business.js'):
                with urllib.request.urlopen(server.origin+'/'+asset) as response:
                    self.assertEqual(response.status,200)
            with urllib.request.urlopen(server.origin+'/api/contract_ledger_detail?id=1') as response:
                self.assertTrue(json.load(response)['details']['receipts']['reconciled'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
        for arguments in ({'year':'2026-01'},{'status':'nonsense'},{'entity_id':'bad'}):
            with self.assertRaises(ValueError):
                ledger.get_overview(**arguments)

    def test_cash_business_http_drilldown_is_read_only_and_scope_checked(self):
        from web_workbench.server import WorkbenchServer
        project = business.save_record('projects', {'name':'合成现金HTTP工程',
            'customer_partner_id':1,'business_mode':'cash','cash_agreed_amount':'100'})
        business.save_record('settlements', {'project_id':project,'amount':'100',
            'settlement_date':'2026-10-19','basis':'合成收入确认'})
        before = self.fingerprint()
        server = WorkbenchServer(0,read_only=True,demo=True)
        thread = threading.Thread(target=server.serve_forever,daemon=True)
        thread.start()
        def read(url):
            with urllib.request.urlopen(server.origin+'/api/'+url) as response:
                return json.load(response)
        try:
            overview = read('contract_ledger?business_scope=all')
            self.assertEqual(overview['summary']['settlement_minor'],'10010000')
            self.assertEqual(overview['project_business_count'],1)
            combined = read('contract_business_detail?business_scope=all')
            self.assertEqual(combined['summary'],overview['summary'])
            self.assertTrue(all(detail['reconciled'] for detail in combined['details'].values()))
            detail = read('contract_project_business?id='+str(project))
            self.assertEqual(detail['summary']['settlement_minor'],'10000')
            self.assertEqual(detail['project']['project_id'],project)
            for url in ('contract_project_business?id='+str(project)+'&project_id=1',
                        'contract_business_detail?business_scope=all&year=2026',
                        'contract_ledger?business_scope=nonsense'):
                with self.subTest(url=url), self.assertRaises(urllib.error.HTTPError) as error:
                    read(url)
                self.assertEqual(error.exception.code,400)
            self.assertEqual(self.fingerprint(),before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
