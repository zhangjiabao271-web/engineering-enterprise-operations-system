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
                CREATE TABLE historical_receivable_projects(id INTEGER PRIMARY KEY,customer_id INTEGER,name TEXT);
                CREATE TABLE historical_receipts(id INTEGER PRIMARY KEY,project_id INTEGER,
                    receipt_date TEXT,amount_minor INTEGER,status TEXT);
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
                ALTER TABLE receipts ADD COLUMN amount_minor INTEGER;
                ALTER TABLE contracts ADD COLUMN status TEXT DEFAULT 'active';
                UPDATE receipts SET amount_minor=40000;
                CREATE TABLE contract_project_allocations(project_id INTEGER, contract_id INTEGER,
                    allocated_amount_minor INTEGER,status TEXT);
            ''')

    def query(self, dataset='labor', **kwargs):
        query = dict(dataset=dataset, **kwargs)
        query.setdefault('metrics', ['amount_minor'])
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

    def test_same_name_projects_require_identity_and_remain_separate(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("UPDATE projects SET name='工程甲' WHERE id=2")
        with self.assertRaisesRegex(QueryValidationError, '项目编号'):
            self.run_query(self.query('income', filters={'project': '工程甲'}))
        result = self.run_query(self.query('income', project_id=1, filters={'project': '工程甲'}))
        self.assertEqual(result['results'][0]['totals']['amount_minor'], 100000)
        self.assertEqual(result['results'][0]['project_id'], 1)
        self.assertEqual(result['sources'][0]['details'][0]['project_id'], 1)
        self.assertIn('编号1', result['sources'][0]['scope_label'])
        grouped = self.run_query(self.query('income', group_by=['project'], result_mode='ranking'))
        self.assertEqual(len(grouped['results'][0]['groups']), 2)
        self.assertEqual({row['project_id'] for row in grouped['results'][0]['groups']}, {1, 2})

    def test_project_identity_cannot_ignore_name_or_unsupported_scope(self):
        with self.assertRaisesRegex(QueryValidationError, '项目'):
            self.run_query(self.query('income', project_id=1, filters={'project': '工程乙'}))
        for invalid in (True, 0, '1', -1):
            with self.subTest(invalid=invalid), self.assertRaises(QueryValidationError):
                validate_plan({'queries': [self.query('income', project_id=invalid)]})
        with self.assertRaisesRegex(QueryValidationError, '项目'):
            validate_plan({'queries': [self.query('purchases', project_id=1)]})

    def test_empty_period_not_missing_worker(self):
        result = self.run_query(self.query(start_date='2024-01-01', end_date='2024-12-31', filters={'worker': '李四'}))
        self.assertIn('没有有效记录', result['answer'])

    def test_group_limit_does_not_truncate_totals_or_share_denominator(self):
        result = self.run_query(self.query(group_by=['project'], limit=1))['results'][0]
        self.assertEqual(result['totals']['amount_minor'], 90000)
        self.assertEqual(len(result['groups']), 1)
        self.assertEqual(result['groups'][0]['share_percent'], 50)
        self.assertTrue(result['groups_truncated'])

    def test_leader_is_a_direct_answer_not_the_entire_customer_ranking(self):
        result = self.run_query(self.query('income', group_by=['customer'], result_mode='leader', limit=1, order_by=None))
        self.assertIn('客户甲', result['answer'])
        self.assertIn('¥1,000.00', result['answer'])
        self.assertNotIn('客户乙', result['answer'])
        self.assertEqual(result['results'][0]['totals']['amount_minor'], 150000)
        self.assertEqual(result['results'][0]['groups'][0]['share_percent'], 66.67)
        self.assertEqual(len(result['sources'][0]['details']), 2)

    def test_leader_uses_whole_groups_and_keeps_ties_despite_limit_one(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("UPDATE settlements SET amount_minor=100000 WHERE id=2")
        result = self.run_query(self.query('income', group_by=['customer'], result_mode='leader', limit=1))
        self.assertIn('并列', result['answer'])
        self.assertIn('客户甲', result['answer'])
        self.assertIn('客户乙', result['answer'])
        self.assertEqual(len(result['results'][0]['groups']), 2)

    def test_smallest_and_labor_leader_share_the_same_protocol(self):
        result = self.run_query(self.query('income', group_by=['customer'], result_mode='leader', descending=False))
        self.assertIn('最低', result['answer'])
        self.assertIn('客户乙', result['answer'])
        self.assertNotIn('客户甲', result['answer'])
        query = self.query(group_by=['worker'], result_mode='leader')
        query['metrics'] = ['days']
        result = self.run_query(query)
        self.assertIn('张三（编号1）', result['answer'])
        self.assertNotIn('李四', result['answer'])

    def test_unassigned_group_and_unknown_values_do_not_claim_complete_leader(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("INSERT INTO settlements VALUES(3,'2025-01-03','S3',NULL,NULL,900000,'active')")
            conn.execute("INSERT INTO settlements VALUES(4,'2025-01-03','S4',2,NULL,NULL,'active')")
        result = self.run_query(self.query('income', group_by=['customer'], result_mode='leader'))
        self.assertIn('客户甲', result['answer'])
        self.assertIn('待归属', result['answer'])
        self.assertIn('未知', result['answer'])
        self.assertIn('不能认定完整排名', result['answer'])
        self.assertEqual(result['results'][0]['totals']['amount_minor'], 1050000)

    def test_ranking_summary_and_details_have_distinct_answers(self):
        ranking = self.run_query(self.query('income', group_by=['customer'], result_mode='ranking'))
        self.assertIn('| 客户 |', ranking['answer'])
        summary = self.run_query(self.query('income', result_mode='summary'))
        self.assertIn('合计', summary['answer'])
        self.assertNotIn('客户甲', summary['answer'])
        details = self.run_query(self.query('income', result_mode='details', limit=1))
        self.assertIn('| 来源编号 |', details['answer'])
        self.assertIn('正文展示前 1 条', details['answer'])
        with self.assertRaises(QueryValidationError):
            self.run_query(self.query('income', result_mode='leader'))
        with self.assertRaises(QueryValidationError):
            self.run_query(self.query('income', group_by=['customer'], result_mode='leader', order_by='customer'))
        with self.assertRaises(QueryValidationError):
            self.run_query(self.query('income', group_by=['customer'], result_mode='summary'))

    def test_unknown_tax_warning_survives_every_answer_mode(self):
        for mode in ('leader', 'summary', 'ranking', 'details'):
            query = self.query('invoices', result_mode=mode)
            query['metrics'] = ['amount_minor', 'tax_amount_minor']
            if mode in ('leader', 'ranking'):
                query['group_by'] = ['customer']
            with self.subTest(mode=mode):
                self.assertIn('已知税额有1条未知', self.run_query(query)['answer'])

    def test_summary_names_each_metric_and_details_preserve_zero(self):
        query = self.query(result_mode='summary')
        query['metrics'] = ['days', 'overtime_days']
        answer = self.run_query(query)['answer']
        self.assertIn('工天 **3.5**', answer)
        self.assertIn('其中加班工天 **0.5**', answer)
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("UPDATE sales_invoices SET invoice_no=0 WHERE id=2")
        query = self.query('invoices', result_mode='details')
        self.assertIn('| 2 | 0 |', self.run_query(query)['answer'])

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

    def test_actual_collections_include_history_once_without_changing_income(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript('''
                INSERT INTO historical_receivable_projects VALUES(1,2,'独立旧账');
                INSERT INTO historical_receipts VALUES(1,1,'2025-01-04',150000,'active'),
                    (2,1,'2025-01-05',900000,'void'),(3,1,'2026-01-01',50000,'active');
            ''')
        before = hashlib.sha256(self.path.read_bytes()).digest()
        result = self.run_query(self.query('collections', group_by=['customer'], result_mode='leader'))
        self.assertEqual(result['results'][0]['totals']['amount_minor'], 190000)
        self.assertEqual(result['results'][0]['totals']['count'], 2)
        self.assertIn('客户乙', result['answer'])
        self.assertNotIn('客户甲', result['answer'])
        self.assertEqual({r['id'] for r in result['sources'][0]['details']}, {'receipt-1', 'historical-1'})
        historical = self.run_query(self.query('historical_receipts', historical_project_id=1))
        self.assertEqual(historical['results'][0]['totals']['amount_minor'], 150000)
        self.assertNotIn('project_id', historical['results'][0])
        self.assertEqual(self.run_query(self.query('income'))['results'][0]['totals']['amount_minor'], 150000)
        self.assertEqual(self.run_query(self.query('receipts', project_id=1))['results'][0]['totals']['amount_minor'], 40000)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())

    def test_mixed_or_missing_customer_collections_remain_unassigned(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("INSERT INTO receipt_allocations VALUES(4,1,2,NULL,NULL,10000,'active')")
        result = self.run_query(self.query('collections', group_by=['customer'], result_mode='leader'))
        self.assertEqual(result['results'][0]['totals']['amount_minor'], 40000)
        self.assertEqual(result['results'][0]['totals']['unassigned_records'], 1)
        self.assertEqual(result['results'][0]['groups'], [])
        self.assertIn('客户归属待确认', result['answer'])
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('UPDATE projects SET customer_partner_id=NULL WHERE id=2')
        result = self.run_query(self.query('collections'))
        self.assertIsNone(result['sources'][0]['details'][0]['customer'])
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("INSERT INTO receipts(id,receipt_date,receipt_no,status,amount_minor) VALUES(2,'2025-01-06','R2','active',20000)")
        result = self.run_query(self.query('collections'))
        self.assertEqual(result['results'][0]['totals']['amount_minor'], 60000)
        self.assertEqual(result['results'][0]['totals']['unassigned_records'], 2)

    def test_historical_identity_is_distinct_and_same_names_require_confirmation(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript('''
                INSERT INTO historical_receivable_projects VALUES(1,1,'工程甲'),(2,2,'工程甲');
                INSERT INTO historical_receipts VALUES(1,1,'2025-01-01',10000,'active'),
                    (2,2,'2025-01-02',20000,'active');
            ''')
        with self.assertRaisesRegex(QueryValidationError, '历史旧账项目编号'):
            self.run_query(self.query('historical_receipts', filters={'historical_project': '工程甲'}))
        result = self.run_query(self.query('historical_receipts', group_by=['historical_project'], result_mode='ranking'))
        self.assertEqual({r['historical_project_id'] for r in result['results'][0]['groups']}, {1, 2})
        self.assertIn('历史旧账项目编号1', result['answer'])
        for dataset, scope in (('historical_receipts', {'project_id': 1}),
                               ('receipts', {'historical_project_id': 1}),
                               ('collections', {'project_id': 1}),
                               ('collections', {'filters': {'project': '工程甲'}})):
            with self.subTest(dataset=dataset, scope=scope), self.assertRaises(QueryValidationError):
                self.run_query(self.query(dataset, **scope))

    def test_details_sort_dates_numbers_and_metrics_in_both_directions(self):
        for field, expected in (('date', [1, 2]), ('number', [1, 2]), ('amount_minor', [2, 1])):
            for descending in (False, True):
                with self.subTest(field=field, descending=descending):
                    result = self.run_query(self.query('income', result_mode='details', order_by=field,
                                                       descending=descending, limit=1))
                    ids = expected[::-1] if descending else expected
                    self.assertEqual([r['id'] for r in result['sources'][0]['details']], ids)
                    self.assertIn(f'| {ids[0]} | S{ids[0]} |', result['answer'])
                    self.assertEqual(result['results'][0]['totals']['amount_minor'], 150000)
        default = self.run_query(self.query('receivables', result_mode='details'))
        self.assertEqual([r['id'] for r in default['sources'][0]['details']], [1, 2])
        early_first = self.run_query(self.query('income', result_mode='details', descending=False))
        self.assertEqual([r['id'] for r in early_first['sources'][0]['details']], [1, 2])

    def test_detail_sort_happens_before_limit_and_keeps_nulls_last_and_ties_stable(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executemany("INSERT INTO settlements VALUES(?,'2025-01-03',?,1,NULL,?,'active')",
                             [(n, f'S{n}', n) for n in range(10, 230)])
            conn.execute("INSERT INTO settlements VALUES(300,'2025-01-03','S300',1,NULL,NULL,'active')")
            conn.execute("INSERT INTO settlements VALUES(301,'2025-01-03','S301',1,NULL,100000,'active')")
        for descending, first_ids in ((True, [1, 301, 2]), (False, [10, 11, 12])):
            result = self.run_query(self.query('income', result_mode='details', order_by='amount_minor', descending=descending))
            self.assertEqual([r['id'] for r in result['sources'][0]['details'][:3]], first_ids)
            self.assertEqual(len(result['sources'][0]['details']), 200)
            self.assertEqual(result['results'][0]['totals']['count'], 224)
            self.assertNotIn(300, [r['id'] for r in result['sources'][0]['details']])
        result = self.run_query(self.query('invoices', result_mode='details', order_by='tax_amount_minor',
                                           metrics=['tax_amount_minor'], descending=False))
        self.assertEqual([r['id'] for r in result['sources'][0]['details']], [2, 1])

    def test_detail_sort_rejects_unavailable_fields_and_injection(self):
        for field in ('amount_minor DESC;DELETE FROM workers', 'unknown', 'count', '', 1, []):
            with self.subTest(field=field), self.assertRaises(QueryValidationError):
                self.run_query(self.query('income', result_mode='details', order_by=field))
        with self.assertRaises(QueryValidationError):
            self.run_query({'dataset': 'invoices', 'metrics': ['amount_minor'], 'result_mode': 'details',
                            'end_date': '2025-12-31', 'order_by': 'tax_amount_minor'})

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
