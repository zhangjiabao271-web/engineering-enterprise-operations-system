"""Source readers may compute off-thread, but never schedule or modify Tk there."""

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pages.document_cashflow_dialog import DocumentCashflowDialog


class SourceReaderThreadTests(unittest.TestCase):
    def check_reader(self, cancel=False):
        owner_thread = threading.get_ident()
        callbacks = []
        release = threading.Event()
        started = threading.Event()
        computed = threading.Event()
        def after(delay, callback):
            self.assertEqual(threading.get_ident(), owner_thread)
            callbacks.append(callback)
        dialog = SimpleNamespace(after=after, winfo_exists=lambda: True, grab_set=Mock())
        subject = DocumentCashflowDialog.__new__(DocumentCashflowDialog)
        subject.dialog = dialog
        subject._source_reading = False
        subject.document_editor = Mock()
        subject.run = lambda action: action()
        def read(path):
            self.assertNotEqual(threading.get_ident(), owner_thread)
            started.set()
            self.assertTrue(release.wait(2))
            computed.set()
            return {'name': '合成扫描.pdf', 'fragments': [{'locator': 'PDF第1页', 'text': '合成文字'}]}
        with patch('pages.document_cashflow_dialog.ttk.Toplevel') as progress, \
             patch('pages.document_cashflow_dialog.ttk.Label'), \
             patch('pages.document_cashflow_dialog.ttk.Button') as button, \
             patch('pages.document_cashflow_dialog.style_dialog'), \
             patch('pages.document_cashflow_dialog.read_document', side_effect=read):
            subject.read_source('synthetic.pdf')
            try:
                self.assertTrue(started.wait(1))
                self.assertTrue(subject._source_reading)
                with self.assertRaises(ValueError):
                    subject.read_source('another.pdf')
                if cancel:
                    button.call_args.kwargs['command']()
                    progress.return_value.destroy.assert_called_once()
            finally:
                release.set()
            self.assertTrue(computed.wait(1))
            for _ in range(30):
                callbacks.pop(0)()
                if not subject._source_reading:
                    break
                time.sleep(.01)
            self.assertFalse(subject._source_reading)
            if cancel:
                subject.document_editor.assert_not_called()
            else:
                subject.document_editor.assert_called_once()

    def test_only_main_thread_schedules_and_opens_source_preview(self):
        self.check_reader()

    def test_cancel_drops_worker_result_without_opening_editor(self):
        self.check_reader(cancel=True)


if __name__ == '__main__':
    unittest.main()
