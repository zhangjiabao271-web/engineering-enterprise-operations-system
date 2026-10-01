import json
import threading
import unittest
from unittest.mock import Mock, patch

from ai_client import AIClient, AIError
from services import ai_query_planner as planner


def income_plan():
    return {"queries": [{"dataset": "income", "metrics": ["amount_minor"],
                         "start_date": "2025-01-01", "end_date": "2025-12-31",
                         "group_by": ["customer"]}]}


class QueryPlannerTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.business_query_plan.return_value = income_plan()
        self.result = {"answer": "本机计算的结果", "sources": [{"module": "确认收入"}],
                       "results": [{"resolved_filters": {}}]}
        self.execute = patch.object(planner.ai_query_tools, 'execute_plan', return_value=self.result)
        self.executor = self.execute.start()
        self.addCleanup(self.execute.stop)

    def test_only_authorized_payload_and_local_result(self):
        result = planner.query_turn('按客户列出收入占比', self.client, conversation_context={
            'secret': 'must-not-leave', 'ledger': [{'amount': 12345678}], 'phone': 'phone-private',
        })
        messages = self.client.business_query_plan.call_args.args[0]
        payload = json.loads(messages[1]['content'])
        self.assertEqual(set(payload), {'today', 'selected_project', 'scope_request', 'previous_plan', 'previous_question', 'question', 'tools'})
        self.assertNotIn('must-not-leave', json.dumps(messages))
        self.assertNotIn('sql', payload['tools']['income'])
        self.assertEqual(result['answer'], self.result['answer'])
        self.client.chat_completion.assert_not_called()

    def test_followup_passes_only_validated_plan(self):
        planner.query_turn('那去年呢', self.client, conversation_context={
            'query_plan': income_plan(), 'query_question': '今年客户收入', 'irrelevant': 'private',
        })
        payload = json.loads(self.client.business_query_plan.call_args.args[0][1]['content'])
        self.assertEqual(payload['previous_plan'], income_plan())
        planner.query_turn('那去年呢', self.client, conversation_context={'query_plan': {'sql': 'private'}})
        payload = json.loads(self.client.business_query_plan.call_args.args[0][1]['content'])
        self.assertIsNone(payload['previous_plan'])

    def test_labor_followup_bridges_local_context(self):
        with patch.object(planner.labor_service, 'get_worker_by_id', return_value={'id': 5, 'name': '测试工人'}):
            planner.query_turn('分别按月份列一下', self.client, conversation_context={
                'labor_query': {'worker_id': 5, 'time': {'start_date': '2025-01-01', 'end_date': '2025-12-31'}},
            })
        payload = json.loads(self.client.business_query_plan.call_args.args[0][1]['content'])
        self.assertEqual(payload['previous_plan']['queries'][0]['filters']['worker'], '测试工人（编号5）')

    def test_invalid_plan_does_not_fall_back_to_guessing(self):
        self.client.business_query_plan.return_value = {'queries': [{'dataset': 'drop_table'}]}
        result = planner.query_turn('删除台账', self.client)
        self.executor.assert_not_called()
        self.assertEqual(result['sources'], [])
        self.assertEqual(result['intent'], 'query_needs_clarification')

    def test_selected_project_cannot_be_silently_ignored(self):
        with patch.object(planner.project_service, 'list_projects', return_value=[{'id': 3, 'name': '项目甲'}]):
            result = planner.query_turn('今年收入', self.client, project_id=3)
        self.executor.assert_not_called()
        self.assertIn('未正确限定项目', result['answer'])

    def test_whole_company_cannot_retain_project_filter(self):
        plan = income_plan()
        plan['queries'][0]['filters'] = {'project': '项目甲'}
        self.client.business_query_plan.return_value = plan
        result = planner.query_turn('全公司今年收入', self.client)
        self.executor.assert_not_called()
        self.assertIn('仍限定了项目', result['answer'])

    def test_all_projects_clears_old_project_scope_and_keeps_prior_question(self):
        prior = income_plan()
        prior['queries'][0]['filters'] = {'project': '青岭台风'}
        plan = {'queries': [{'dataset': 'profit', 'metrics': ['gross_profit_minor'],
                            'group_by': ['project'], 'start_date': '2025-01-01',
                            'end_date': '2025-12-31'}]}
        self.client.business_query_plan.return_value = plan
        with patch.object(planner.project_service, 'list_projects', return_value=[{'id': 3, 'name': '青岭台风'}]):
            answer = planner.query_turn('所有的项目，每个项目的平均毛利金额', self.client, project_id=3,
                                        conversation_context={'query_plan': prior, 'query_question': '今年这个项目的平均毛利'})
        payload = json.loads(self.client.business_query_plan.call_args.args[0][1]['content'])
        self.assertEqual(payload['scope_request'], 'all_projects')
        self.assertIsNone(payload['selected_project'])
        self.assertEqual(payload['previous_question'], '今年这个项目的平均毛利')
        self.assertNotIn('project', payload['previous_plan']['queries'][0]['filters'])
        self.assertIn('project', prior['queries'][0]['filters'])
        self.assertIsNone(answer['context_updates']['project_id'])
        self.executor.assert_called_once()

    def test_invalid_scope_repaired_once_before_query(self):
        first = income_plan()
        first['queries'][0]['filters'] = {'project': '青岭台风'}
        corrected = income_plan()
        self.client.business_query_plan.side_effect = [first, corrected]
        result = planner.query_turn('所有项目今年收入', self.client)
        self.assertEqual(result['answer'], self.result['answer'])
        self.assertEqual(self.client.business_query_plan.call_count, 2)
        repair = self.client.business_query_plan.call_args.args[0][-1]['content']
        self.assertIn('仍限定了项目', repair)
        self.assertNotIn('本机计算的结果', repair)
        self.executor.assert_called_once()

    def test_per_project_grouping_repaired_once(self):
        first = {'queries': [{'dataset': 'profit', 'metrics': ['gross_profit_minor'],
                              'start_date': '2025-01-01', 'end_date': '2025-12-31'}]}
        corrected = {'queries': [{**first['queries'][0], 'group_by': ['project']}]}
        self.client.business_query_plan.side_effect = [first, corrected]
        planner.query_turn('每个项目的毛利', self.client)
        self.executor.assert_called_once()
        self.assertEqual(self.client.business_query_plan.call_count, 2)

    def test_ambiguous_ledger_match_is_not_sent_to_model(self):
        self.executor.side_effect = planner.ai_query_tools.QueryValidationError('项目匹配多项：客户内部项目甲、客户内部项目乙')
        result = planner.query_turn('今年项目甲收入', self.client)
        self.assertIn('客户内部项目甲', result['answer'])
        self.client.business_query_plan.assert_called_once()

    def test_invalid_json_repaired_once(self):
        self.client.business_query_plan.side_effect = [
            AIError('模型输出不是完整 JSON', code='invalid_query_plan'), income_plan()]
        result = planner.query_turn('今年客户收入', self.client)
        self.assertEqual(result['answer'], self.result['answer'])
        self.executor.assert_called_once()

    def test_negative_all_projects_phrase_does_not_clear_scope(self):
        self.assertFalse(planner._whole_company_request('不是所有项目，只查一个项目'))
        self.assertTrue(planner._whole_company_request('全部的项目今年收入'))
        self.assertTrue(planner._whole_company_request('不是所有项目，现在查全部项目'))

    def test_profit_question_must_use_profit_dataset(self):
        self.client.business_query_plan.return_value = income_plan()
        result = planner.query_turn('所有项目的毛利', self.client)
        self.executor.assert_not_called()
        self.assertIn('没有查询按项目归集的毛利', result['answer'])

    def test_all_projects_bypasses_stale_selected_project(self):
        self.client.business_query_plan.return_value = {'queries': [{
            'dataset': 'profit', 'metrics': ['gross_profit_minor'],
            'group_by': ['project'], 'start_date': '2025-01-01', 'end_date': '2025-12-31'}]}
        with patch.object(planner.project_service, 'list_projects', side_effect=AssertionError('old project lookup')):
            result = planner.query_turn('所有项目的毛利', self.client, project_id=999)
        self.assertIsNone(result['context_updates']['project_id'])

    def test_failed_all_projects_plan_still_clears_old_scope(self):
        invalid = income_plan()
        invalid['queries'][0]['filters'] = {'project': '青岭台风'}
        self.client.business_query_plan.return_value = invalid
        result = planner.query_turn('所有项目今年收入', self.client, project_id=999)
        self.assertEqual(result['intent'], 'query_needs_clarification')
        self.assertIsNone(result['context_updates']['project_id'])
        self.executor.assert_not_called()

    def test_cancel_before_and_after_network(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(AIError) as error:
            planner.query_turn('今年收入', self.client, cancel_event=cancelled)
        self.assertEqual(error.exception.code, 'cancelled')
        self.client.business_query_plan.assert_not_called()
        cancelled.clear()
        def respond(_messages):
            cancelled.set()
            return income_plan()
        self.client.business_query_plan.side_effect = respond
        with self.assertRaises(AIError):
            planner.query_turn('今年收入', self.client, cancel_event=cancelled)
        self.executor.assert_not_called()

    def test_scope_and_complex_questions_use_planner(self):
        for question in ('8月客户回款', '今年谁做工最多', '今年客户收入以及未回款占比', '青岭今年人工和材料成本是多少'):
            self.assertFalse(planner.use_fast_path(question))
        self.assertTrue(planner.use_fast_path('示例工人甲今年做了几工'))

    def test_engine_routes_and_never_sends_ledger(self):
        import ai_engine
        with patch.object(ai_engine, 'make_ai_client', return_value=self.client), patch.object(
            ai_engine, 'build_operating_context', side_effect=AssertionError('no ledger upload')
        ):
            result = ai_engine.ask_ai_turn('今年客户收入以及未回款占比')
        self.assertEqual(result['intent'], 'planned_business_query')


class StructuredClientTests(unittest.TestCase):
    def test_json_response_contract(self):
        client = AIClient('test-only')
        response = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(income_plan())}}]}
        with patch.object(client, '_request_json', return_value=response) as request:
            self.assertEqual(client.business_query_plan([]), income_plan())
        self.assertEqual(request.call_args.args[2]['response_format'], {'type': 'json_object'})

    def test_truncated_reasoning_or_non_object_rejected(self):
        client = AIClient('test-only')
        for response in (
            {'choices': [{'finish_reason': 'length', 'message': {'content': '{}'}}]},
            {'choices': [{'finish_reason': 'stop', 'message': {'reasoning_content': '{}'}}]},
            {'choices': [{'finish_reason': 'stop', 'message': {'content': '[]'}}]},
            {'choices': []},
        ):
            with self.subTest(response=response), patch.object(client, '_request_json', return_value=response), self.assertRaises(AIError):
                client.business_query_plan([])

    def test_truncated_plan_retries_with_larger_budget(self):
        client = AIClient('test-only')
        short = {'choices': [{'finish_reason': 'length', 'message': {'content': '{'}}]}
        complete = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(income_plan())}}]}
        with patch.object(client, '_request_json', side_effect=[short, complete]) as request:
            self.assertEqual(client.business_query_plan([]), income_plan())
        self.assertEqual([call.args[2]['max_tokens'] for call in request.call_args_list], [4096, 8192])
        with patch.object(client, '_request_json', return_value=short) as request:
            with self.assertRaises(AIError) as error:
                client.business_query_plan([])
        self.assertEqual(error.exception.code, 'query_plan_truncated')
        self.assertIn('输出长度上限', str(error.exception))
        self.assertEqual(request.call_count, 2)
