import hashlib
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

import ai_engine
from ai_client import AIError
from db import connection
from services import ai_operating_query_service as operating, ai_query_capabilities as capabilities
from services import ai_query_planner as planner
from services.ai_query_tools import QueryValidationError, catalog, execute_plan, validate_plan


class OperatingCapabilityTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'facts.db'
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.executescript("""
                CREATE TABLE projects(id INTEGER PRIMARY KEY,name TEXT);
                CREATE TABLE balances(project_id INTEGER,unreceived_minor INTEGER);
                INSERT INTO projects VALUES(1,'同名工程'),(2,'同名工程');
                INSERT INTO balances VALUES(1,40000),(2,80000);
            """)

    def balances(self, project_id=None):
        with connection.db_read() as conn:
            sql = 'SELECT * FROM balances'
            params = []
            if project_id:
                sql += ' WHERE project_id=?'
                params.append(project_id)
            return [dict(row, invoice_date='2025-01-01', invoice_no=f"I-{row['project_id']}",
                         project_name='同名工程', amount_minor=100000,
                         received_minor=100000-row['unreceived_minor'], collection_status='部分回款')
                    for row in conn.execute(sql, params)]

    def test_catalog_registers_existing_capabilities_without_exposing_code(self):
        tools = catalog()
        self.assertTrue(set(capabilities.TOOLS) <= set(tools))
        for name in capabilities.TOOLS:
            self.assertTrue(tools[name]['read_only'])
            self.assertNotIn('handler', tools[name])
            self.assertNotIn('sql', tools[name])

    def test_native_balance_uses_the_requested_readonly_snapshot_and_project_id(self):
        original = hashlib.sha256(self.path.read_bytes()).hexdigest()
        with patch.object(operating.finance_service, 'list_invoices', side_effect=self.balances):
            result = execute_plan({'queries': [{'dataset': 'invoice_balance', 'project_id': 1,
                                              'filters': {'project': '同名工程'}}]}, db_path=self.path)
        self.assertIn('¥400.00', result['answer'])
        self.assertNotIn('¥1,200.00', result['answer'])
        self.assertEqual(result['results'][0]['project_id'], 1)
        self.assertEqual(result['sources'][0]['details'][0]['project_id'], 1)
        self.assertIn('项目编号1', result['sources'][0]['scope_label'])
        self.assertEqual(original, hashlib.sha256(self.path.read_bytes()).hexdigest())

    def test_native_query_rejects_unknown_and_ignored_conditions(self):
        for query in (
            {'dataset': 'invoice_balance', 'start_date': '2025-01-01'},
            {'dataset': 'invoice_balance', 'filters': {'customer': '客户甲'}},
            {'dataset': 'funds_overview', 'project_id': 1},
            {'dataset': 'collection_actions', 'filters': {'project': '同名工程'}},
            {'dataset': 'invoice_balance', 'sql': 'DELETE FROM balances'},
            {'dataset': 'invoice_balance', 'metrics': ['amount_minor']},
            {'dataset': 'project_finance'},
            {'dataset': 'customer_business', 'year': True},
        ):
            with self.subTest(query=query), self.assertRaises(QueryValidationError):
                validate_plan({'queries': [query]})
        with self.assertRaises(QueryValidationError):
            validate_plan({'queries': [{'dataset': 'invoice_balance'}], 'calculations': [
                {'label': '无来源计算', 'operation': 'average_record', 'inputs': [{'query': 0, 'metric': 'amount_minor'}]}]})

    def test_native_same_name_scope_requires_confirmation(self):
        with self.assertRaisesRegex(QueryValidationError, '项目编号'):
            execute_plan({'queries': [{'dataset': 'invoice_balance', 'filters': {'project': '同名工程'}}]}, db_path=self.path)

    def test_planned_followup_can_select_balance_and_receipt_matching(self):
        client = Mock()
        client.business_query_plan.side_effect = [
            {'queries': [{'dataset': 'invoice_balance', 'filters': {'project': '同名工程'}}]},
            {'queries': [{'dataset': 'receipt_matching', 'project_id': 1}]},
        ]
        prior = {'query_plan': {'queries': [{'dataset': 'income', 'filters': {'project': '同名工程'},
                   'project_id': 1, 'metrics': ['amount_minor'], 'end_date': '2025-12-31'}]}}
        with patch.object(operating.finance_service, 'list_invoices', side_effect=self.balances), patch.object(
            operating.finance_service, 'list_receipts', return_value=[]
        ), patch.object(planner.project_service, 'list_projects', return_value=[{'id': 1, 'name': '同名工程'}]):
            first = planner.query_turn('那哪些发票还没结清，余额多少？', client,
                                       project_id=1, conversation_context=prior, db_path=self.path)
            second = planner.query_turn('然后看看等后续发票的回款', client,
                                        project_id=1, conversation_context=first['context_updates'], db_path=self.path)
        self.assertIn('¥400.00', first['answer'])
        self.assertEqual(first['context_updates']['query_plan']['queries'][0]['project_id'], 1)
        self.assertEqual(second['context_updates']['query_plan']['queries'][0]['dataset'], 'receipt_matching')
        self.assertEqual(second['context_updates']['query_plan']['queries'][0]['project_id'], 1)
        self.assertIn('待匹配金额', second['answer'])

    def test_model_planning_can_combine_native_tools(self):
        client = Mock()
        client.business_query_plan.return_value = {'queries': [
            {'dataset': 'invoice_balance'}, {'dataset': 'receipt_matching'}]}
        with patch.object(operating.finance_service, 'list_invoices', side_effect=self.balances), patch.object(
            operating.finance_service, 'list_receipts', return_value=[]
        ):
            turn = planner.query_turn('发票余额和待开票抵扣回款一起看看', client, db_path=self.path)
        self.assertEqual(len(turn['sources']), 2)
        self.assertIn('¥1,200.00', turn['answer'])
        validate_plan(turn['context_updates']['query_plan'])

    def test_offline_current_state_request_after_plan_does_not_require_model(self):
        prior = {'query_plan': {'queries': [{'dataset': 'income', 'metrics': ['amount_minor'],
                                            'end_date': '2025-12-31'}]}}
        with patch.object(ai_engine, 'make_ai_client', side_effect=AssertionError('no network')), patch.object(
            ai_engine.ai_labor_query_service, 'retrieve_labor_query', return_value=None
        ), patch.object(ai_engine.business_knowledge_service, 'retrieve_business_knowledge', return_value={}), patch.object(
            ai_engine, '_confirmation_turn', return_value=None
        ), patch.object(operating.project_service, 'list_projects', return_value=[]), patch.object(
            operating.finance_service, 'list_invoices', side_effect=self.balances
        ), connection.db_read(self.path) as conn, connection.use_read_snapshot(conn):
            turn = ai_engine.ask_ai_turn('哪些发票还有余额？', conversation_context=prior)
        self.assertEqual(turn['intent'], 'invoice_balance')
        self.assertIn('¥1,200.00', turn['answer'])
        self.assertEqual(turn['context_updates']['query_plan']['queries'][0]['dataset'], 'invoice_balance')
        validate_plan(turn['context_updates']['query_plan'])

    def test_scoped_native_tool_does_not_drop_selection(self):
        client = Mock()
        client.business_query_plan.return_value = {'queries': [{'dataset': 'funds_overview'}]}
        with patch.object(planner.project_service, 'list_projects', return_value=[{'id': 1, 'name': '同名工程'}]), patch.object(
            planner.ai_query_tools, 'execute_plan'
        ) as execute:
            result = planner.query_turn('这个项目账户余额', client, project_id=1, db_path=self.path)
        execute.assert_not_called()
        self.assertIn('未正确限定项目', result['answer'])

    def test_cancelled_native_plan_does_not_read_facts(self):
        cancelled = threading.Event()
        client = Mock()

        def plan(_messages):
            cancelled.set()
            return {'queries': [{'dataset': 'invoice_balance'}]}

        client.business_query_plan.side_effect = plan
        with patch.object(operating.finance_service, 'list_invoices') as read:
            with self.assertRaises(AIError):
                planner.query_turn('发票余额是多少', client, cancel_event=cancelled, db_path=self.path)
        read.assert_not_called()

    def test_legacy_scope_uses_selected_id_when_projects_are_identically_named(self):
        with patch.object(operating.project_service, 'list_projects', return_value=[
            {'id': 1, 'name': '同名工程'}, {'id': 2, 'name': '同名工程'}
        ]):
            project, ambiguous = operating._project_scope('同名工程发票余额', project_id=1)
            self.assertEqual(project['id'], 1)
            self.assertEqual(ambiguous, [])
            project, ambiguous = operating._project_scope('同名工程发票余额')
            self.assertIsNone(project)
            self.assertEqual(len(ambiguous), 2)


class ReadSnapshotScopeTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'snapshot.db'
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('CREATE TABLE marker(value INTEGER)')
            conn.execute('INSERT INTO marker VALUES(7)')

    def test_nested_services_share_snapshot_and_cannot_write(self):
        with connection.db_read(self.path) as conn, connection.use_read_snapshot(conn):
            with connection.db_read() as nested:
                self.assertIs(conn, nested)
            self.assertEqual(conn.execute('SELECT value FROM marker').fetchone()[0], 7)
            with self.assertRaises(RuntimeError), connection.db_transaction():
                self.fail('write context opened')
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute('DELETE FROM marker')
            with self.assertRaises(ValueError), connection.db_read(self.path):
                self.fail('explicit DB path escaped')
        with connection.db_read(self.path) as fresh:
            self.assertIsNot(fresh, conn)
            self.assertEqual(fresh.execute('SELECT value FROM marker').fetchone()[0], 7)

    def test_scope_restored_after_exception_and_thread_isolated(self):
        results = []
        with connection.db_read(self.path) as conn:
            with self.assertRaisesRegex(RuntimeError, 'simulated'):
                with connection.use_read_snapshot(conn):
                    def other_thread():
                        with connection.db_read(self.path) as other:
                            results.append(other is not conn)
                    worker = threading.Thread(target=other_thread)
                    worker.start()
                    worker.join(timeout=3)
                    raise RuntimeError('simulated')
            self.assertEqual(results, [True])
            with connection.db_read(self.path) as other:
                self.assertIsNot(other, conn)

    def test_shared_connection_must_be_readonly_and_transactional(self):
        with closing(sqlite3.connect(self.path)) as conn:
            with self.assertRaises(ValueError), connection.use_read_snapshot(conn):
                self.fail('writable connection accepted')


if __name__ == '__main__':
    unittest.main()
