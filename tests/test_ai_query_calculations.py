import sqlite3
from contextlib import closing
from unittest.mock import Mock, patch

from tests.test_ai_query_tools import QueryToolsTests
from services.ai_query_tools import QueryValidationError, execute_plan, validate_plan
from services import ai_query_planner


class QueryCalculationTests(QueryToolsTests):
    def setUp(self):
        super().setUp()
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript('''
                CREATE TABLE purchase_orders(id INTEGER, purchase_date TEXT, order_no TEXT,
                    merchant_name_snapshot TEXT,total_amount_cents INTEGER,status TEXT);
                CREATE TABLE purchase_project_costs(purchase_order_id INTEGER,project_id INTEGER,cost_minor INTEGER);
                CREATE TABLE cost_entries(id INTEGER,cost_date TEXT,cost_no TEXT,category TEXT,
                    counterparty_name_snapshot TEXT,amount_minor INTEGER,project_id INTEGER,status TEXT);
                CREATE TABLE cost_allocation_lines(cost_entry_id INTEGER,project_id INTEGER,amount_minor INTEGER,status TEXT);
                INSERT INTO cost_entries VALUES
                    (1,'2025-01-01','C1','生活与家庭 / 家庭日常','测试',990000,1,'active');
            ''')

    def calculation(self, operation, inputs=None):
        return {'label': '测试计算', 'operation': operation,
                'inputs': inputs or [{'query': 0, 'metric': 'gross_profit_minor'}]}

    def profit_plan(self, operation='average_month'):
        return {'queries': [{'dataset': 'profit', 'start_date': '2025-01-01',
                             'end_date': '2025-02-28', 'filters': {'project': '工程甲'},
                             'metrics': ['income_minor', 'cost_minor', 'gross_profit_minor']}],
                'calculations': [self.calculation(operation)]}

    def test_profit_excludes_personal_cost_and_months_include_empty_month(self):
        result = execute_plan(self.profit_plan(), db_path=self.path)
        self.assertEqual(result['results'][0]['totals']['gross_profit_minor'], 55000)
        self.assertIn('¥275.00', result['answer'])
        self.assertIn('2个自然月', result['answer'])

    def test_ratio_and_project_average(self):
        plan = self.profit_plan('average_project')
        plan['calculations'].append(self.calculation('ratio_percent', [
            {'query': 0, 'metric': 'gross_profit_minor'}, {'query': 0, 'metric': 'income_minor'}]))
        result = execute_plan(plan, db_path=self.path)
        self.assertIn('55.00%', result['answer'])
        self.assertIn('¥550.00', result['answer'])

    def test_calculation_cannot_inject_code_or_constants(self):
        for ref in ({'query': 9, 'metric': 'gross_profit_minor'}, {'value': 100}):
            plan = self.profit_plan()
            plan['calculations'][0]['inputs'] = [ref]
            with self.assertRaises(QueryValidationError):
                validate_plan(plan)
        plan = self.profit_plan()
        plan['calculations'][0]['operation'] = '__import__("os")'
        with self.assertRaises(QueryValidationError):
            validate_plan(plan)

    def test_missing_values_do_not_become_zero(self):
        plan = self.profit_plan()
        plan['queries'][0]['start_date'] = '2024-01-01'
        plan['queries'][0]['end_date'] = '2024-12-31'
        result = execute_plan(plan, db_path=self.path)
        self.assertIn('无法可靠计算', result['answer'])

    def test_growth_uses_whole_totals_not_display_limit(self):
        plan = {'queries': [self.query('income', filters={'project': name}, limit=1)
                            for name in ('工程甲', '工程乙')],
                'calculations': [self.calculation('growth_percent', [
                    {'query': 0, 'metric': 'amount_minor'}, {'query': 1, 'metric': 'amount_minor'}])]}
        self.assertIn('100.00%', execute_plan(plan, db_path=self.path)['answer'])

    def test_same_named_projects_remain_separate_in_profit_grouping(self):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute("INSERT INTO projects(id,name,customer_partner_id) VALUES(3,'工程甲',1)")
            conn.execute("INSERT INTO settlements VALUES(3,'2025-01-04','S3',3,NULL,30000,'active')")
        plan = {'queries': [{'dataset': 'profit', 'metrics': ['gross_profit_minor'],
                             'group_by': ['project'], 'start_date': '2025-01-01',
                             'end_date': '2025-02-28'}]}
        result = execute_plan(plan, db_path=self.path)
        rows = [row for row in result['results'][0]['groups'] if row['project'] == '工程甲']
        self.assertEqual({row['project_id'] for row in rows}, {1, 3})
        self.assertIn('工程甲（项目编号1）', result['answer'])
        self.assertIn('工程甲（项目编号3）', result['answer'])
        self.assertIn('月均毛利 ¥275.00', result['answer'])

    def test_clarification_then_all_projects_query_on_isolated_database(self):
        model = Mock()
        model.business_query_plan.side_effect = [
            {'queries': [], 'clarification': '你是问毛利率，还是平均每月毛利金额？'},
            {'queries': [{'dataset': 'profit', 'metrics': [
                'gross_profit_minor', 'income_minor', 'cost_minor'],
                'group_by': ['project'], 'start_date': '2025-01-01',
                'end_date': '2025-02-28'}]},
        ]
        with patch.object(ai_query_planner.project_service, 'list_projects',
                          return_value=[{'id': 1, 'name': '工程甲'}]):
            first = ai_query_planner.query_turn(
                '2025年这个项目的平均毛利是多少', model, project_id=1,
                db_path=self.path)
        self.assertIn('毛利率', first['answer'])
        second = ai_query_planner.query_turn(
            '所有的项目，每个项目的平均毛利金额', model, project_id=1,
            conversation_context=first['context_updates'], db_path=self.path)
        self.assertIsNone(second['context_updates']['project_id'])
        self.assertIn('工程甲（项目编号1）', second['answer'])
        self.assertIn('工程乙（项目编号2）', second['answer'])
        self.assertIn('月均毛利 ¥275.00', second['answer'])
        self.assertIn('月均毛利 ¥25.00', second['answer'])
        payload = model.business_query_plan.call_args.args[0][1]['content']
        self.assertIn('"previous_question": "2025年这个项目的平均毛利是多少"', payload)
        self.assertNotIn('990000', payload)
