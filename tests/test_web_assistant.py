"""In-memory web turn lifecycle; no model credentials or production writes."""

import threading
import time
import unittest
from unittest.mock import patch

from ai_client import AIError
from web_workbench.assistant import WebAssistant


class WebAssistantTests(unittest.TestCase):
    def assistant(self, engine):
        instance = WebAssistant(engine)
        self.addCleanup(instance.close)
        return instance, instance.create()['session_id']

    def finished(self, instance, identity):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snapshot = instance.snapshot(identity)
            if not snapshot['busy']:
                return snapshot
            threading.Event().wait(.01)
        self.fail('Worker did not finish')

    def test_same_engine_context_sources_and_explicit_model_consent(self):
        calls = []
        def engine(question, **kwargs):
            calls.append((question, kwargs))
            return {'answer': '已核对', 'sources': [{'module': '实际回款', 'record_count': 2}],
                    'context_updates': {'query_question': question}}
        instance, identity = self.assistant(engine)
        instance.ask(identity, '回款多少')
        first = self.finished(instance, identity)
        self.assertEqual(first['messages'][-1]['sources'][0]['record_count'], 2)
        self.assertFalse(calls[0][1]['allow_model'])
        instance.ask(identity, '只看今年', allow_model=True)
        self.finished(instance, identity)
        self.assertTrue(calls[1][1]['allow_model'])
        self.assertEqual(calls[1][1]['conversation_context']['query_question'], '回款多少')
        first['messages'][0]['content'] = '不能篡改服务内存'
        self.assertEqual(instance.snapshot(identity)['messages'][0]['content'], '回款多少')

    def test_scope_clear_stop_reject_old_answers_and_stale_requests(self):
        entered = threading.Event()
        release = threading.Event()
        done = threading.Event()
        def engine(question, **kwargs):
            entered.set()
            release.wait(3)
            done.set()
            return {'answer': '旧范围答案', 'context_updates': {'project_id': 99}}
        instance, identity = self.assistant(engine)
        generation = instance.snapshot(identity)['generation']
        instance.ask(identity, '第一问', expected_generation=generation)
        self.assertTrue(entered.wait(2))
        with self.assertRaises(ValueError):
            instance.ask(identity, '重复点击')
        changed = instance.change(identity, project_id=2, clear=True)
        self.assertEqual(changed['messages'], [])
        with self.assertRaises(ValueError):
            instance.ask(identity, '迟到请求', expected_generation=generation)
        release.set()
        self.assertTrue(done.wait(2))
        self.assertEqual(instance.snapshot(identity)['project_id'], 2)
        self.assertEqual(instance.snapshot(identity)['messages'], [])

    def test_spontaneous_cancel_finishes_without_saving_answer(self):
        def engine(question, **kwargs):
            kwargs['on_chunk']('未完成的片段')
            raise AIError('停止', code='cancelled')
        instance, identity = self.assistant(engine)
        instance.ask(identity, '问题')
        snapshot = self.finished(instance, identity)
        self.assertEqual(len(snapshot['messages']), 1)
        self.assertEqual(snapshot['stream'], '')

    def test_candidate_must_come_from_current_server_turn(self):
        contexts = []
        def engine(question, **kwargs):
            contexts.append(kwargs['conversation_context'])
            if len(contexts) == 1:
                return {'answer': '请选择', 'message_type': 'confirmation', 'candidates': [
                    {'label': '合成供应商', 'context_updates': {'supplier_name': '合成供应商'}}]}
            return {'answer': '候选已查询', 'sources': []}
        instance, identity = self.assistant(engine)
        instance.ask(identity, '供应商成本')
        snapshot = self.finished(instance, identity)
        for index in (-1, 1, '0', True):
            with self.assertRaises(ValueError):
                instance.confirm(identity, snapshot['generation'], index)
        instance.confirm(identity, snapshot['generation'], 0)
        result = self.finished(instance, identity)
        self.assertEqual(contexts[-1]['supplier_name'], '合成供应商')
        self.assertEqual(result['messages'][-2]['content'], '确认：合成供应商')
        with self.assertRaises(ValueError):
            instance.confirm(identity, snapshot['generation'], 0)

    def test_scope_change_clears_inherited_conditions(self):
        contexts = []
        def engine(question, **kwargs):
            contexts.append(kwargs['conversation_context'])
            return {'answer': '事实', 'context_updates': {'supplier_name': '旧供应商', 'time': '去年'}}
        instance, identity = self.assistant(engine)
        instance.ask(identity, '第一问')
        self.finished(instance, identity)
        instance.change(identity, project_id=3)
        instance.ask(identity, '新范围')
        self.finished(instance, identity)
        self.assertEqual(contexts[-1], {'project_id': 3})

    def test_model_consent_validation_errors_history_bound_and_expiry(self):
        instance, identity = self.assistant(lambda question, **kwargs: {'answer': '正常'})
        for question in ('', 'x' * 4001):
            with self.assertRaises(ValueError):
                instance.ask(identity, question)
        with self.assertRaises(ValueError):
            instance.ask(identity, '问题', allow_model='true')
        for index in range(22):
            instance.ask(identity, str(index))
            self.finished(instance, identity)
        snapshot = instance.snapshot(identity)
        self.assertEqual(len(snapshot['messages']), 40)
        self.assertEqual(snapshot['dropped_messages'], 4)
        instance.change(identity, clear=True)
        self.assertEqual(instance.snapshot(identity)['dropped_messages'], 0)
        with patch('web_workbench.assistant.time.monotonic', return_value=time.monotonic() + 1801):
            with self.assertRaisesRegex(ValueError, '过期'):
                instance.snapshot(identity)

    def test_unexpected_failure_reports_unknown_not_no_data(self):
        def engine(question, **kwargs):
            raise RuntimeError('test-only fault')
        instance, identity = self.assistant(engine)
        with patch('web_workbench.assistant.logging.exception') as logger:
            instance.ask(identity, '问题')
            result = self.finished(instance, identity)
        logger.assert_called_once()
        self.assertEqual(result['messages'][-1]['message_type'], 'error')
        self.assertIn('不能据此认定没有', result['messages'][-1]['content'])

    def test_closed_server_cannot_restart_queries(self):
        instance, identity = self.assistant(lambda *args, **kwargs: {'answer': '不应执行'})
        instance.close()
        with self.assertRaises(ValueError):
            instance.ask(identity, '问题')
        with self.assertRaises(ValueError):
            instance.create()

    def test_many_browser_tabs_reclaim_idle_session_instead_of_blocking_entry(self):
        instance, first = self.assistant(lambda *args, **kwargs: {'answer':'事实'})
        for _ in range(8):
            instance.create()
        self.assertEqual(len(instance._sessions), 8)
        with self.assertRaisesRegex(ValueError, '过期'):
            instance.snapshot(first)

    def test_session_limit_never_evicts_running_query(self):
        release = threading.Event()
        def engine(question, **kwargs):
            release.wait(3)
            return {'answer':'事实'}
        instance, first = self.assistant(engine)
        self.addCleanup(release.set)
        instance.ask(first, '忙碌的查询')
        for _ in range(8):
            instance.create()
        self.assertTrue(instance.snapshot(first)['busy'])


class WebModelConsentTests(unittest.TestCase):
    def test_default_desktop_behavior_preserved_but_web_without_consent_does_not_build_client(self):
        import ai_engine
        with patch.object(ai_engine.ai_query_planner, 'use_fast_path', return_value=False), \
             patch.object(ai_engine, 'make_ai_client') as client, \
             patch.object(ai_engine.ai_query_planner, 'query_turn', return_value={'answer': '计划结果'}) as planner:
            with self.assertRaises(AIError) as failure:
                ai_engine.ask_ai_turn('任意需要理解的问题', allow_model=False)
            self.assertEqual(failure.exception.code, 'model_consent_required')
            client.assert_not_called()
            self.assertEqual(ai_engine.ask_ai_turn('默认桌面入口')['answer'], '计划结果')
            client.assert_called_once()
            planner.assert_called_once()


if __name__ == '__main__':
    unittest.main()
