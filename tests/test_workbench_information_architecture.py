"""Consolidated entry points still query the authoritative ledger, read-only."""
import json
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from db import connection, migration_runner
from db.schema import init_db
from services import web_workbench_service as business
from services.web_workbench_operations import database_fingerprint
from web_workbench.synthetic import seed


class WorkbenchInformationArchitectureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='workbench_ia_')
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / 'synthetic.db'
        for module in (connection, migration_runner):
            selected = patch.object(module, 'DB_PATH', self.database)
            selected.start()
            self.addCleanup(selected.stop)
        init_db()
        seed()

    def fingerprint(self):
        with connection.db_read() as conn:
            return database_fingerprint(conn)

    def test_navigation_consolidates_views_without_removing_ledger_modules(self):
        entries = [key for _, links in business.NAVIGATION for key, _ in links]
        self.assertEqual(len(entries), 16)
        self.assertFalse({'profit', 'compare', 'cashflow'} & set(entries))
        self.assertTrue({'projects', 'offers', 'funds', 'customers', 'suppliers'} <= set(entries))
        self.assertTrue({'settlements', 'receipts', 'input_invoices', 'purchases'} <= business.SPECS.keys())

    def test_supplier_drilldown_filters_exact_identity_and_purchase_year(self):
        supplier = business.save_record('suppliers', {'name':'合成另一钢材供应商'})
        offer = business.save_record('offers', {'supplier_id':supplier, 'name':'合成演示 · H型钢',
            'specification':'H300', 'unit':'吨', 'price':'10', 'tax_rate_percent':'13'})
        purchase = business.save_record('purchases', {'project_id':1, 'supplier_id':supplier,
            'purchase_date':'2025-12-31', 'items':[{'product_id':offer,
            'material_name_snapshot':'合成演示 · H型钢', 'unit_snapshot':'吨', 'quantity':'1',
            'unit_price':'10', 'price_basis':'inclusive', 'tax_percent':'13'}]})
        before = self.fingerprint()
        self.assertEqual([row['id'] for row in business.rows('offers', supplier_id=supplier)], [offer])
        self.assertEqual([row['id'] for row in business.rows('purchases', supplier_id=supplier, year='2025')], [purchase])
        self.assertEqual(business.rows('purchases', supplier_id=supplier, year='2026'), [])
        self.assertEqual(self.fingerprint(), before)

    def test_customer_project_scope_and_profit_scope_agree(self):
        customer = business.save_record('customers', {'legal_name':'合成第二客户'})
        project = business.save_record('projects', {'name':'合成第二客户未签项目',
            'customer_partner_id':customer, 'status':'进行中'})
        self.assertEqual([row['id'] for row in business.rows('projects', customer_id=customer)], [project])
        dashboard = business.dashboards('profit', customer_id=customer)
        self.assertEqual([row['id'] for row in dashboard['projects']], [project])
        self.assertEqual(dashboard['accounting_summary']['pending_count'], 1)

    def test_customer_drilldown_keeps_explicit_contract_fallback(self):
        from services.partner_overview_service import get_overview
        with connection.db_transaction() as conn:
            conn.execute('UPDATE projects SET customer_partner_id=NULL WHERE id=1')
        overview = get_overview('customers', year=2026)
        self.assertEqual([row['project_id'] for row in overview['rows'][0]['projects']], [1])
        self.assertEqual([row['id'] for row in business.rows('projects', customer_id=1)], [1])
        self.assertEqual([row['id'] for row in business.dashboards('profit', customer_id=1)['projects']], [1])
        customer = business.save_record('customers', {'legal_name':'合成项目正式归属'})
        with connection.db_transaction() as conn:
            conn.execute('UPDATE projects SET customer_partner_id=? WHERE id=1', (customer,))
        self.assertEqual(business.rows('projects', customer_id=1), [])
        self.assertEqual([row['id'] for row in business.rows('projects', customer_id=customer)], [1])

    def test_unsupported_context_is_not_silently_ignored(self):
        for args in ({'supplier_id':2}, {'customer_id':1}, {'year':'2026'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                business.rows('workers', **args)

    def test_old_customer_detail_also_excludes_void_receipt_allocations(self):
        from services import master_data_service as partners
        receipt = business.save_record('receipts', {'project_id':1, 'contract_id':1,
            'settlement_id':1, 'amount':'100', 'receipt_date':'2026-10-10',
            'payment_method':'银行转账'})
        with connection.db_transaction() as conn:
            conn.execute("UPDATE receipt_allocations SET status='void' WHERE receipt_id=?", (receipt,))
        customer = next(row for row in partners.list_customers(year=2026) if row['id']==1)
        self.assertEqual(customer['yearly_receipt_minor'], 0)
        detail = partners.get_customer_business_detail(1, year=2026)
        self.assertEqual(detail['summary']['yearly_receipt_minor'], 0)

    def test_new_overviews_and_profit_are_read_only(self):
        before = self.fingerprint()
        for view in ('profit', 'customers', 'suppliers'):
            with self.subTest(view=view):
                self.assertIsInstance(business.dashboards(view, year='2026'), dict)
        self.assertEqual(before, self.fingerprint())

    def test_void_parent_contract_is_not_an_effective_project_boundary(self):
        from services import operations_service
        with connection.db_transaction() as conn:
            conn.execute("UPDATE contracts SET status='void' WHERE id=1")
        project = business.rows('projects')[0]
        self.assertEqual(project['allocated_minor'], 0)
        self.assertFalse(project['is_accountable'])
        self.assertFalse(operations_service.get_accountability_map()[1])

    def test_partner_assets_and_dashboard_http_integration(self):
        from web_workbench.server import WorkbenchServer
        server = WorkbenchServer(0, read_only=True, demo=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            before = self.fingerprint()
            for asset in ('partners.js', 'partners.css'):
                with urllib.request.urlopen(server.origin+'/'+asset) as response:
                    self.assertEqual(response.status, 200)
            for view in ('customers', 'suppliers', 'profit'):
                with urllib.request.urlopen(server.origin+'/api/dashboard?view='+view+'&year=2026') as response:
                    self.assertIsInstance(json.load(response), dict)
            with urllib.request.urlopen(server.origin+'/api/rows?module=projects&customer_id=1') as response:
                self.assertEqual([row['id'] for row in json.load(response)['rows']], [1])
            self.assertEqual(before, self.fingerprint())
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
