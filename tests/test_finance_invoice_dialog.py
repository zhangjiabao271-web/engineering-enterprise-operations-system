"""Native PDF duplicate-upload routing; all business services are mocked."""
import os
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import ttkbootstrap as ttk

from pages.finance_invoice_dialog import FinanceInvoiceDialogMixin
from tests.test_frontend_components import descendants
from ui.theme import configure_design_system


@unittest.skipUnless(os.environ.get('SUPPLY_CHAIN_GUI_TESTS') == '1', 'requires isolated Tk GUI process')
class FinanceInvoiceDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = ttk.Window(themename='flatly')
        except tk.TclError as error:
            raise unittest.SkipTest(str(error)) from error
        configure_design_system(cls.root)
        cls.root.geometry('1100x800')

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.page = FinanceInvoiceDialogMixin()
        self.page.parent = self.root
        self.page._invoice_target_map = Mock(return_value={})
        self.page.money = lambda value: f'¥{value / 100:.2f}'
        self.page.refresh = Mock()
        self.page.notebook = SimpleNamespace(select=Mock())
        self.recognition = {'invoice_no': '12345678901234567890', 'invoice_date': '2026-09-18',
                            'amount': '2600', 'net_amount': '2574.26', 'tax_amount': '25.74',
                            'tax_rate': '1', 'buyer_name': '测试客户', 'warnings': [],
                            'source_path': 'invoice.pdf', 'sha256': 'test'}
        self.existing = {'id': 123, 'project_id': 8, 'contract_id': 9,
                         'project_name': '原发票项目', 'contract_no': 'HT-ORIGINAL',
                         'status': 'active', 'pdf_warnings': [], 'pdf_conflicts': [],
                         'has_available_attachment': False}
        self.callback_errors = []
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)

    def tearDown(self):
        for child in self.root.winfo_children():
            if isinstance(child, tk.Toplevel):
                child.destroy()
        self.root.update()
        self.assertEqual(self.callback_errors, [])

    def dialog(self):
        return next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))

    def button(self, text):
        return next(widget for widget in descendants(self.dialog())
                    if isinstance(widget, ttk.Button) and widget.cget('text') == text)

    def wait_for(self, button_text):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.root.update()
            if any(isinstance(widget, ttk.Button) and widget.cget('text') == button_text
                   for widget in descendants(self.dialog())):
                return
            time.sleep(0.01)
        self.fail(f'PDF routing did not produce {button_text}')

    def test_missing_attachment_routes_to_original_without_current_project_options(self):
        from services import invoice_pdf_service

        with patch('pages.finance_invoice_dialog.filedialog.askopenfilename', return_value='invoice.pdf'), \
             patch('pages.finance_invoice_dialog.messagebox.askyesno', return_value=True), \
             patch('pages.finance_invoice_dialog.messagebox.showwarning') as warning, \
             patch('pages.finance_invoice_dialog.messagebox.showinfo') as info, \
             patch.object(invoice_pdf_service, 'recognize', return_value=self.recognition), \
             patch.object(invoice_pdf_service, 'find_existing_invoice_for_pdf', return_value=self.existing), \
             patch.object(invoice_pdf_service, 'attach_pdf_to_existing', return_value=123) as attach:
            self.page.open_invoice_dialog()
            self.button('选择 PDF 自动识别').invoke()
            self.wait_for('补充附件')
            combo = next(widget for widget in descendants(self.dialog()) if isinstance(widget, ttk.Combobox))
            self.assertEqual(combo.get(), '原发票项目 · HT-ORIGINAL')
            self.assertEqual(str(combo.cget('state')), 'disabled')
            for widget in descendants(self.dialog()):
                if isinstance(widget, (ttk.Entry, ttk.Text)):
                    self.assertEqual(str(widget.cget('state')), 'disabled')
            self.button('补充附件').invoke()
            attach.assert_not_called()
            warning.assert_called_once()
            checkbox = next(widget for widget in descendants(self.dialog()) if isinstance(widget, ttk.Checkbutton))
            checkbox.invoke()
            self.button('补充附件').invoke()
            attach.assert_called_once_with(self.recognition)
            self.page.refresh.assert_called_once()
            info.assert_called_once()

    def test_available_attachment_disables_duplicate_save(self):
        from services import invoice_pdf_service

        with patch('pages.finance_invoice_dialog.filedialog.askopenfilename', return_value='invoice.pdf'), \
             patch('pages.finance_invoice_dialog.messagebox.askyesno', return_value=True), \
             patch.object(invoice_pdf_service, 'recognize', return_value=self.recognition), \
             patch.object(invoice_pdf_service, 'find_existing_invoice_for_pdf',
                          return_value=dict(self.existing, has_available_attachment=True)), \
             patch.object(invoice_pdf_service, 'attach_pdf_to_existing') as attach:
            self.page.open_invoice_dialog()
            self.button('选择 PDF 自动识别').invoke()
            self.wait_for('附件已存在')
            self.assertEqual(str(self.button('附件已存在').cget('state')), 'disabled')
            self.button('附件已存在').invoke()
            attach.assert_not_called()

    def test_switching_to_new_pdf_unlocks_form_and_clears_attachment_mode(self):
        from services import invoice_pdf_service

        next_recognition = dict(self.recognition, invoice_no='98765432109876543210')
        with patch('pages.finance_invoice_dialog.filedialog.askopenfilename', return_value='invoice.pdf'), \
             patch('pages.finance_invoice_dialog.messagebox.askyesno', return_value=True), \
             patch('pages.finance_invoice_dialog.contract_service.list_contracts', return_value=[]), \
             patch.object(invoice_pdf_service, 'recognize', side_effect=[self.recognition, next_recognition]), \
             patch.object(invoice_pdf_service, 'find_existing_invoice_for_pdf', side_effect=[self.existing, None]):
            self.page.open_invoice_dialog()
            self.button('选择 PDF 自动识别').invoke()
            self.wait_for('补充附件')
            self.button('选择 PDF 自动识别').invoke()
            self.wait_for('保存发票')
            combo = next(widget for widget in descendants(self.dialog()) if isinstance(widget, ttk.Combobox))
            self.assertEqual(str(combo.cget('state')), 'readonly')
            self.assertEqual(combo.get(), '请选择开票项目 / 合同')
            self.assertEqual(str(self.button('保存发票').cget('state')), 'normal')
