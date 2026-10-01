import hashlib
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from services.ai_query_tools import QueryValidationError, catalog, execute_plan, validate_plan


class QueryToolsTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'facts.db'
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript('''
                CREATE TABLE workers(id INTEGER PRIMARY KEY,name TEXT);
                CREATE TABLE projects(id INTEGER PRIMARY KEY,name TEXT,customer_partner_id INTEGER);
                CREATE TABLE business_partners(id INTEGER PRIMARY KEY,legal_name TEXT);
                CREATE TABLE contracts(id INTEGER PRIMARY KEY,customer_partner_id INTEGER);
                CREATE TABLE work_logs(id INTEGER PRIMARY KEY,worker_id INTEGER,project_id INTEGER,
                    work_date TEXT,construction_site TEXT,work_days REAL,is_overtime INTEGER,
                    amount_minor INTEGER,amount REAL,status TEXT);
                CREATE TABLE settlements(id INTEGER PRIMARY KEY,settlement_date TEXT,settlement_no TEXT,
                    project_id INTEGER,contract_id INTEGER,amount_minor INTEGER,status TEXT);
                CREATE TABLE receipts(id INTEGER PRIMARY KEY,receipt_date TEXT,receipt_no TEXT,status TEXT);
                CREATE TABLE receipt_allocations(id INTEGER PRIMARY KEY,receipt_id INTEGER,
                    project_id INTEGER,contract_id INTEGER,settlement_id INTEGER,
                    allocated_amount_minor INTEGER,status TEXT);
                CREATE TABLE sales_invoices(id INTEGER PRIMARY KEY,invoice_date TEXT,invoice_no TEXT,
                    project_id INTEGER,contract_id INTEGER,amount_minor INTEGER,tax_amount_minor INTEGER,status TEXT);
                INSERT INTO workers VALUES(1,'张三'),(2,'张三'),(3,'李四');
                INSERT INTO projects VALUES(1,'工程甲',1),(2,'工程乙',2);
                INSERT INTO business_partners VALUES(1,'客户甲'),(2,'客户乙');
                INSERT INTO work_logs VALUES
                    (1,1,1,'2025-01-01','工地',1,0,30000,300,'active'),
                    (2,1,1,'2025-01-02','工地',0.5,1,15000,150,'active'),
                    (3,3,2,'2025-01-01','工地',1,0,20000,200,'active'),
                    (4,1,1,'2025-01-03','工地',1,0,30000,300,'void'),
                    (5,2,2,'2025-02-01','工地',1,0,25000,250,'active');
                INSERT INTO settlements VALUES
                    (1,'2025-01-01','S1',1,NULL,100000,'active'),
                    (2,'2025-01-02','S2',2,NULL,50000,'active');
                INSERT INTO receipts VALUES(1,'2025-01-03','R1','active');
                INSERT INTO receipt_allocations VALUES
                    (1,1,1,NULL,1,30000,'active'),
                    (2,1,1,NULL,NULL,10000,'active'),
                    (3,1,2,NULL,2,10000,'void');
                INSERT INTO sales_invoices VALUES
                    (1,'2025-01-01','I1',1,NULL,10000,NULL,'active'),
                    (2,'2025-01-02','I2',1,NULL,20000,1000,'active');
            ''')
            conn.executescript('''
                ALTER TABLE projects ADD COLUMN project_code TEXT;
                ALTER TABLE projects ADD COLUMN status TEXT;
                ALTER TABLE projects ADD COLUMN business_mode TEXT;
                ALTER TABLE projects ADD COLUMN invoice_policy TEXT;
                ALTER TABLE receipts ADD COLUMN automatic_income_allocation INTEGER DEFAULT 0;
                CREATE TABLE contract_project_allocations(project_id INTEGER, allocated_amount_minor INTEGER,status TEXT);
            ''')

    def query(self, dataset='labor', **kwargs):
        query = dict(dataset=dataset, metrics=['amount_minor'], **kwargs)
        if dataset != 'receivables':
            query.setdefault('end_date', '2025-12-31')
        return query

    def run_query(self, query):
        return execute_plan({'queries': [query]}, db_path=self.path)

    def test_half_days_overtime_and_void(self):
        query = self.query(start_date='2025-01-01', end_date='2025-01-31', filters={'worker': '张三（编号1）'})
        query['metrics'] = ['days', 'overtime_days', 'amount_minor']
        result = self.run_query(query)['results'][0]
        self.assertEqual(result['totals']['days'], 1.5)
        self.assertEqual(result['totals']['overtime_days'], 0.5)
        self.assertEqual(result['totals']['amount_minor'], 45000)

    def test_ambiguous_name_requires_confirmation(self):
        with self.assertRaisesRegex(QueryValidationError, '匹配多项'):
            self.run_query(self.query(filters={'worker': '张三'}))
        with self.assertRaisesRegex(QueryValidationError, '不能按零处理'):
            self.run_query(self.query(filters={'worker': '不存在'}))

    def test_empty_period_not_missing_worker(self):
        result = self.run_query(self.query(start_date='2024-01-01', end_date='2024-12-31', filters={'worker': '李四'}))
        self.assertIn('没有有效记录', result['answer'])

    def test_group_limit_does_not_truncate_totals_or_share_denominator(self):
        result = self.run_query(self.query(group_by=['project'], limit=1))['results'][0]
        self.assertEqual(result['totals']['amount_minor'], 90000)
        self.assertEqual(len(result['groups']), 1)
        self.assertEqual(result['groups'][0]['share_percent'], 50)
        self.assertTrue(result['groups_truncated'])

    def test_detail_limit_is_visible_and_preserves_total(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executemany('INSERT INTO work_logs VALUES(?,3,2,\'2025-03-01\',\'工地\',1,0,100,1,\'active\')', [(n,) for n in range(10, 220)])
        result = self.run_query(self.query())
        self.assertEqual(result['results'][0]['totals']['amount_minor'], 111000)
        self.assertEqual(len(result['sources'][0]['details']), 200)
        self.assertIn('汇总未截断', result['sources'][0]['scope_label'])

    def test_unknown_tax_is_not_invented_zero(self):
        query = self.query('invoices')
        query['metrics'] = ['tax_amount_minor']
        result = self.run_query(query)
        self.assertEqual(result['results'][0]['totals']['tax_amount_minor'], 1000)
        self.assertIn('1条未知', result['answer'])
        self.assertIsNone(result['sources'][0]['details'][1]['tax_amount_minor'])

    def test_cross_module_plan_and_advance_receipts(self):
        from db.business_facts import customer_receivable_rows
        result = execute_plan({'queries': [self.query('income', group_by=['customer']), self.query('receivables', group_by=['customer'])]}, db_path=self.path)
        self.assertEqual(len(result['sources']), 2)
        self.assertEqual(result['results'][0]['totals']['amount_minor'], 150000)
        self.assertEqual(result['results'][1]['totals']['amount_minor'], 120000)
        receipt = self.run_query(self.query('receipts'))
        self.assertEqual(receipt['results'][0]['totals']['amount_minor'], 40000)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            self.assertEqual(sum(r['receivable_minor'] for r in customer_receivable_rows(conn)), 120000)

    def test_reject_sql_unknown_dimensions_and_snapshot_dates(self):
        for query in (
            {**self.query(), 'sql': 'DELETE FROM workers'},
            self.query(filters={'password': 'anything'}),
            self.query(group_by=['worker; DROP TABLE workers']),
            self.query('receivables', start_date='2025-01-01'),
            self.query(end_date='2999-01-01'),
            self.query(end_date='20250101'),
            self.query(limit=10000),
            self.query(dataset_override='anything'),
            {**self.query(), 'dataset': ['labor']},
        ):
            with self.subTest(query=query), self.assertRaises(QueryValidationError):
                self.run_query(query)

    def test_filter_injection_is_only_a_name(self):
        with self.assertRaises(QueryValidationError):
            self.run_query(self.query(filters={'worker': "' OR 1=1 --"}))
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM workers').fetchone()[0], 3)

    def test_readonly_and_cancel(self):
        before = hashlib.sha256(self.path.read_bytes()).digest()
        self.run_query(self.query())
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(QueryValidationError, '取消'):
            execute_plan({'queries': [self.query()]}, db_path=self.path, cancel_event=cancelled)

    def test_catalog_and_clarifications(self):
        self.assertNotIn('sql', catalog()['labor'])
        answer = execute_plan({'queries': [], 'clarification': '需要查哪个月份？'}, db_path=self.path)
        self.assertEqual(answer['sources'], [])
        with self.assertRaises(QueryValidationError):
            validate_plan({'queries': [self.query()] * 7})


if __name__ == '__main__':
    unittest.main()
