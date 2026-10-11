"""A stale or cancelled AI worker must never commit conversation state."""

import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from ai_client import AIError
from pages.ai_page import AIAssistantPage


class TurnLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.page = AIAssistantPage.__new__(AIAssistantPage)
        page = self.page
        page.busy = False
        page.current_conversation_id = 1
        page._generation_token = 0
        page._cancel_event = None
        page._turn_results = queue.Queue()
        page._turn_poll_scheduled = False
        page._loading_context = False
        page.scope_map = {'工程乙': 2, '全公司': None}
        page.scope_var = Mock()
        page.scope_var.get.return_value = '工程乙'
        page.parent = Mock()
        page.stop_btn = Mock()
        page.turn_status_var = Mock()
        page._update_stream = Mock()
        page._schedule_turn_poll = Mock()
        page._finish_turn = Mock()
        page.load_conversation = Mock()

        def set_busy(value):
            page.busy = value

        page._set_busy = Mock(side_effect=set_busy)
        service_patch = patch('pages.ai_page.ai_conversation_service')
        self.service = service_patch.start()
        self.addCleanup(service_patch.stop)
        self.service.get_conversation.return_value = {'id': 1, 'context': {'time': '旧条件'}}
        self.service.update_context.return_value = {'context': {'pending_confirmation': {'label': '客户'}}}
        self.service.add_message.return_value = {'id': 9}
        engine_patch = patch('pages.ai_page.ai_engine.ask_ai_turn')
        self.engine = engine_patch.start()
        self.addCleanup(engine_patch.stop)

    def dispatch_worker(self):
        thread = Mock()
        with patch('pages.ai_page.threading.Thread', return_value=thread) as factory:
            self.page.dispatch_question('查看回款', append_user=False)
        thread.start.assert_called_once()
        return factory.call_args.kwargs['target']

    def answer(self):
        return {'answer': '测试答案', 'context_updates': {'time': '旧查询结果'}, 'answer_mode': 'local'}

    def test_worker_only_queues_and_current_result_commits_on_poll(self):
        self.engine.return_value = self.answer()
        worker = self.dispatch_worker()
        worker()
        self.service.update_context.assert_not_called()
        self.service.add_message.assert_not_called()
        self.page._poll_turn_results()
        self.service.update_context.assert_called_once_with(1, {'time': '旧查询结果'})
        self.service.add_message.assert_called_once()
        self.page._finish_turn.assert_called_once_with(1, cancelled=False)

    def test_scope_change_and_clear_discard_even_an_already_queued_answer(self):
        for change in ('on_scope_changed', 'clear_context', 'cancel_confirmation'):
            with self.subTest(change=change):
                self.service.reset_mock()
                self.page.busy = False
                self.engine.return_value = self.answer()
                worker = self.dispatch_worker()
                worker()
                getattr(self.page, change)()
                self.service.reset_mock()
                self.page._finish_turn.reset_mock()
                self.page._poll_turn_results()
                self.service.update_context.assert_not_called()
                self.service.add_message.assert_not_called()
                self.page._finish_turn.assert_not_called()
                self.assertFalse(self.page.busy)

    def test_late_worker_cannot_overwrite_scope_or_clear_changes(self):
        for change in ('on_scope_changed', 'clear_context'):
            with self.subTest(change=change):
                self.page.busy = False
                worker = self.dispatch_worker()
                old_cancel = self.page._cancel_event
                getattr(self.page, change)()
                self.assertTrue(old_cancel.is_set())
                self.service.reset_mock()
                self.engine.return_value = self.answer()
                worker()
                self.page._poll_turn_results()
                self.service.update_context.assert_not_called()
                self.service.add_message.assert_not_called()

    def test_stop_after_worker_completion_prevents_commit(self):
        self.engine.return_value = self.answer()
        worker = self.dispatch_worker()
        worker()
        self.page.stop_generation()
        self.page._poll_turn_results()
        self.service.update_context.assert_not_called()
        self.service.add_message.assert_not_called()
        self.page._finish_turn.assert_called_once_with(1, cancelled=True)

    def test_engine_cancellation_without_a_prior_stop_never_attempts_a_save(self):
        self.engine.side_effect = AIError('已停止', code='cancelled')
        worker = self.dispatch_worker()
        worker()
        self.page._poll_turn_results()
        self.service.update_context.assert_not_called()
        self.service.add_message.assert_not_called()
        self.page._finish_turn.assert_called_once_with(1, cancelled=True)

    def test_old_done_and_stream_do_not_finish_a_new_query(self):
        self.engine.return_value = self.answer()
        old_worker = self.dispatch_worker()
        token = self.page._generation_token
        self.page._invalidate_turn()
        new_worker = self.dispatch_worker()
        self.page._turn_results.put({'type': 'stream', 'token': token, 'text': '旧文本'})
        old_worker()
        new_worker()
        self.page._poll_turn_results()
        self.page._poll_turn_results()
        self.page._finish_turn.assert_not_called()
        self.page._update_stream.assert_not_called()
        self.page._poll_turn_results()
        self.service.update_context.assert_called_once()
        self.page._finish_turn.assert_called_once_with(1, cancelled=False)

    def test_error_is_saved_only_after_main_thread_accepts_current_result(self):
        for error in (AIError('连接失败', code='authentication'), RuntimeError('测试故障')):
            with self.subTest(error=error):
                self.service.reset_mock()
                self.page.busy = False
                self.engine.side_effect = error
                worker = self.dispatch_worker()
                worker()
                self.service.add_message.assert_not_called()
                self.page._poll_turn_results()
                self.service.add_message.assert_called_once()
                self.assertEqual(self.service.add_message.call_args.kwargs['message_type'], 'error')
                self.service.update_context.assert_not_called()

    def test_confirmation_message_link_and_metadata_are_preserved(self):
        self.engine.return_value = {**self.answer(), 'response_type': 'confirmation',
                                    'message_type': 'confirmation', 'candidates': [{'label': '客户甲'}],
                                    'sources': [{'module': '实际回款'}]}
        worker = self.dispatch_worker()
        worker()
        self.page._poll_turn_results()
        self.assertEqual(self.service.update_context.call_args.args,
                         (1, {'pending_confirmation': {'label': '客户', 'message_id': 9}}))
        self.assertEqual(self.service.add_message.call_args.kwargs['metadata']['sources'], [{'module': '实际回款'}])


def exercise_turn_lifecycle():
    """Real Tk scheduling and real conversation persistence; no model or live data."""
    import ctypes
    import time
    import ttkbootstrap as ttk
    from db.schema import init_db
    from services import ai_conversation_service as conversations, project_service
    from ui.theme import configure_design_system
    from ui.desktop_style import install_desktop_style

    ctypes.windll.kernel32.SetErrorMode(2)
    init_db()
    root = ttk.Window(themename='flatly')
    configure_design_system(root)
    install_desktop_style(root)
    root.geometry('1200x800')
    errors = []
    root.report_callback_exception = lambda *args: errors.append(str(args[1]))
    workers = []
    main_thread = threading.get_ident()
    writes = []
    update_context = conversations.update_context

    def record_update(*args, **kwargs):
        writes.append(threading.get_ident())
        return update_context(*args, **kwargs)

    def delayed_answer(_question, **_kwargs):
        started, release, completed = workers[-1]
        started.set()
        assert release.wait(4), 'Test worker never released'
        completed.set()
        return {'answer': '过期回答', 'context_updates': {'test_marker': '旧结果'}, 'answer_mode': 'local'}

    def pump_until(predicate):
        deadline = time.monotonic() + 4
        while not predicate():
            assert time.monotonic() < deadline, 'Tk test timed out'
            root.after(15, root.quit)
            root.mainloop()

    try:
        with patch.object(project_service, 'list_projects', return_value=[]), \
             patch.object(AIAssistantPage, 'load_daily_briefing'), \
             patch.object(AIAssistantPage, 'check_config'), \
             patch('pages.ai_page.ai_engine.ask_ai_turn', side_effect=delayed_answer), \
             patch.object(conversations, 'update_context', side_effect=record_update):
            page = AIAssistantPage(root)
            root.update()
            first = page.current_conversation_id
            second = conversations.create_conversation(title='另一会话')['id']
            for action in ('clear', 'scope', 'conversation', 'stop', 'complete'):
                conversations.replace_context(page.current_conversation_id, {'test_marker': '基线'})
                started, release, completed = (threading.Event() for _ in range(3))
                workers.append((started, release, completed))
                current = page.current_conversation_id
                before = len(conversations.list_messages(current))
                page.dispatch_question('测试回款')
                pump_until(started.is_set)
                if action == 'clear':
                    page.clear_context()
                elif action == 'scope':
                    page.scope_var.set('全公司经营总览')
                    page.on_scope_changed()
                elif action == 'conversation':
                    page.load_conversation(second if current == first else first)
                elif action == 'stop':
                    page.stop_generation()
                release.set()
                pump_until(completed.is_set)
                if action in ('stop', 'complete'):
                    pump_until(lambda: not page.busy)
                else:
                    # Give the stale completion a full polling cycle.
                    root.after(100, root.quit)
                    root.mainloop()
                context = conversations.get_conversation(current)['context']
                messages = conversations.list_messages(current)[before:]
                if action == 'complete':
                    assert context['test_marker'] == '旧结果'
                    assert any(m['content'] == '过期回答' for m in messages)
                else:
                    assert context.get('test_marker') != '旧结果', (action, context)
                    assert not any(m['content'] == '过期回答' for m in messages), action
                assert not page.busy
            assert writes and all(identity == main_thread for identity in writes), writes
            assert not errors, errors
        print('AI_TURN_LIFECYCLE_OK: clear, scope, conversation, stop, complete; Tk-thread commits only')
    finally:
        for _started, release, _completed in workers:
            release.set()
        root.destroy()


@unittest.skipUnless(os.environ.get('SUPPLY_CHAIN_GUI_TESTS') == '1', 'Opt-in Tk checks')
class TurnLifecycleGuiTests(unittest.TestCase):
    def test_real_tk_and_isolated_conversation_state(self):
        with tempfile.TemporaryDirectory(prefix='ai_turn_lifecycle_') as temporary:
            env = dict(os.environ, SUPPLY_CHAIN_DB_PATH=str(Path(temporary) / 'test.db'),
                       SUPPLY_CHAIN_ATTACHMENTS_PATH=str(Path(temporary) / 'attachments'),
                       PYTHONIOENCODING='utf-8')
            result = subprocess.run(
                [sys.executable, '-B', '-c', 'from tests.test_ai_turn_lifecycle import exercise_turn_lifecycle; exercise_turn_lifecycle()'],
                cwd=Path(__file__).resolve().parents[1], env=env,
                capture_output=True, text=True, encoding='utf-8', timeout=25,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('AI_TURN_LIFECYCLE_OK', result.stdout)
