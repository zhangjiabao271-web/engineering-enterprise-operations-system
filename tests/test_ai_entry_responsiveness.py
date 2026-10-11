"""Keep history entry and text layout responsive in an isolated Tk process."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


class AnswerResizeTests(unittest.TestCase):
    def test_unmapped_or_tiny_text_is_never_measured(self):
        from ui.assistant_content import AnswerText

        for mapped, width in ((False, 700), (True, 1), (True, 20)):
            widget = Mock()
            widget.bounded = False
            widget.winfo_exists.return_value = True
            widget.winfo_ismapped.return_value = mapped
            widget.winfo_width.return_value = width
            widget._measured_width = None
            with patch('ui.assistant_content.scale_px', side_effect=lambda _widget, value: value):
                AnswerText._fit_height(widget)
            widget.count.assert_not_called()
            widget.configure.assert_not_called()

    def test_height_only_change_does_not_trigger_another_measurement(self):
        from ui.assistant_content import AnswerText

        widget = Mock()
        widget.bounded = False
        widget.winfo_exists.return_value = True
        widget.winfo_ismapped.return_value = True
        widget.winfo_width.return_value = 700
        widget._measured_width = 700
        with patch('ui.assistant_content.scale_px', side_effect=lambda _widget, value: value):
            AnswerText._queue_resize(widget, Mock())
            AnswerText._fit_height(widget)
        widget.after.assert_not_called()
        widget.count.assert_not_called()

    def test_destroy_cancels_pending_measurement(self):
        from ui.assistant_content import AnswerText

        widget = Mock()
        widget._resize_after = 'pending-layout'
        AnswerText._cancel_resize(widget, Mock(widget=widget))
        widget.after_cancel.assert_called_once_with('pending-layout')
        self.assertIsNone(widget._resize_after)

    def test_long_text_does_not_use_auto_height_measurement(self):
        from ui.assistant_content import AnswerText

        widget = Mock()
        widget.bounded = True
        AnswerText._queue_resize(widget, Mock())
        AnswerText._fit_height(widget)
        widget.count.assert_not_called()
        widget.after.assert_not_called()

    def test_paging_keeps_every_message_in_order_and_latest_at_the_end(self):
        from pages.ai_page import _message_page_ranges

        for messages in ([], [{'content': '短回答'}] * 40,
                         [{'content': '长' * 6000}] * 27, [{'content': '长' * 20000}]):
            ranges = _message_page_ranges(messages)
            indices = [index for start, end in ranges for index in range(start, end)]
            self.assertEqual(indices, list(range(len(messages))))
            for start, end in ranges:
                self.assertLessEqual(end - start, 12)
                if end - start > 1:
                    self.assertLessEqual(sum(len(message['content']) for message in messages[start:end]), 10000)
            if ranges:
                self.assertEqual(ranges[-1][1], len(messages))

    def test_current_tree_selection_does_not_reload_the_conversation(self):
        from pages.ai_page import AIAssistantPage

        page = Mock()
        page._loading_sessions = False
        page.current_conversation_id = 3
        page.conversation_tree.selection.return_value = ('3',)
        AIAssistantPage.on_conversation_selected(page)
        page.load_conversation.assert_not_called()
        page.conversation_tree.selection.return_value = ('2',)
        AIAssistantPage.on_conversation_selected(page)
        page.load_conversation.assert_called_once_with(2)


def exercise_history_entry():
    """Child entrypoint: disposable database, synthetic messages, no network."""
    import ctypes
    import faulthandler
    import time
    import ttkbootstrap as ttk
    from db.schema import init_db
    from main import SupplierManagerApp
    from pages.ai_page import AIAssistantPage
    from services import ai_conversation_service
    from ui.assistant_content import AnswerText
    from tests.test_ai_answer_formatting import profit_answer

    ctypes.windll.kernel32.SetErrorMode(2)
    faulthandler.dump_traceback_later(8)
    init_db()
    conversation = ai_conversation_service.create_conversation(title='布局回归测试')
    text = '这是较长的历史回答，用于验证页面首次定宽前不会测量换行。' * 200
    for index in range(27):
        content = '查看历史记录'
        if index == 25:
            content = profit_answer()
        elif index % 2:
            content = text
        ai_conversation_service.add_message(
            conversation['id'], 'assistant' if index % 2 else 'user',
            content,
            message_type='answer' if index % 2 else 'text',
            metadata={'answer_mode': 'local'},
        )
    root = ttk.Window(themename='flatly')
    errors, counts, pages, beats = [], [], [], []
    root.report_callback_exception = lambda *args: errors.append(f'{args[0].__name__}: {args[1]}')
    original_init = AIAssistantPage.__init__
    original_count = AnswerText.count
    def capture(page, *args, **kwargs):
        original_init(page, *args, **kwargs)
        pages.append(page)
    def count(widget, *args):
        assert widget.winfo_ismapped() and widget.winfo_width() >= 40
        counts.append(widget.winfo_width())
        return original_count(widget, *args)
    def pump(milliseconds=180):
        root.after(milliseconds, root.quit)
        root.mainloop()
    def beat():
        beats.append(time.monotonic())
        root.after(20, beat)
    try:
        root.geometry('1200x800')
        root.after(0, beat)
        with patch.object(AIAssistantPage, '__init__', capture), patch.object(AnswerText, 'count', count):
            app = SupplierManagerApp(root)
            root.update()
            started = time.monotonic()
            app.navigate_to('ai')
            root.update_idletasks()
            assert time.monotonic() - started < 3, 'AI entry blocked the event loop'
            pump(400)
            page = pages[-1]
            assert page.current_conversation_id == conversation['id']
            assert counts, 'Wrapped text was never measured'
            for size, side in (('1200x800', 'history'), ('980x760', 'context'),
                               ('1400x900', 'history'), ('1200x800', 'history')):
                root.geometry(size)
                page._toggle_side_panel(side)
                root.update_idletasks()
                pump()
                page.thread.yview_moveto(0)
                page.thread.yview_moveto(1)
                assert page.input_text.winfo_ismapped()
            measured = len(counts)
            pump(220)
            assert len(counts) == measured, 'Height changes keep rescheduling layout'
            assert len(beats) >= 15, 'Tk heartbeat stalled'
            gap = max(b - a for a, b in zip(beats, beats[1:]))
            assert gap < 1, f'UI stalls after entry: {gap:.3f}s'
            assert not errors, errors
            for index in range(len(page._history_pages)):
                page._render_history_page(index)
                root.update_idletasks()
                pump(100)
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            bounded = next(widget for widget in descendants(page.thread)
                           if isinstance(widget, AnswerText) and widget.bounded)
            assert bounded.get('1.0', 'end-1c') == text, 'Long paragraph was truncated'
            assert int(bounded.cget('height')) == 12
            bounded.yview_moveto(0)
            before = page.thread.vscroll.get()
            bounded.event_generate('<MouseWheel>', delta=-120)
            pump(50)
            assert bounded.yview()[0] > 0, 'Long paragraph does not scroll'
            assert page.thread.vscroll.get() == before, 'Inner wheel also scrolls the conversation'
            assert max(b - a for a, b in zip(beats, beats[1:])) < 1, 'History paging stalls the UI'
            assert not errors, errors
            page.render_messages([{'role': 'assistant', 'content': text}])
            app.navigate_to('home')
            pump()
            assert not errors, errors
        print('AI_HISTORY_ENTRY_OK', len(beats), 'heartbeats', len(counts), 'measurements',
              f"max_gap={max(b-a for a, b in zip(beats, beats[1:])):.3f}s")
    finally:
        root.destroy()
        faulthandler.cancel_dump_traceback_later()


@unittest.skipUnless(os.environ.get('SUPPLY_CHAIN_GUI_TESTS') == '1', 'Opt-in Tk checks')
class AIEntryGuiTests(unittest.TestCase):
    def test_full_entry_and_long_history_layout_have_a_hard_timeout(self):
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='ai_layout_test_') as temporary:
            env = dict(os.environ, SUPPLY_CHAIN_DB_PATH=str(Path(temporary) / 'test.db'),
                       SUPPLY_CHAIN_ATTACHMENTS_PATH=str(Path(temporary) / 'attachments'),
                       ENGINEERING_REDUCED_MOTION='1', PYTHONIOENCODING='utf-8')
            try:
                result = subprocess.run(
                    [sys.executable, '-c', 'from tests.test_ai_entry_responsiveness import exercise_history_entry; exercise_history_entry()'],
                    cwd=repository, env=env, capture_output=True, text=True, encoding='utf-8', timeout=20,
                )
            except subprocess.TimeoutExpired as error:
                self.fail(f'AI entry timed out: {error.stdout}\n{error.stderr}')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('AI_HISTORY_ENTRY_OK', result.stdout)
