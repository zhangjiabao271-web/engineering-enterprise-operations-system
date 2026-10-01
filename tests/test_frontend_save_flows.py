"""Native save/edit flows against a temporary database copy."""

import os
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import ttkbootstrap as ttk


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def invoke_action(dialog, label):
    button = next(
        (
            child for child in descendants(dialog)
            if isinstance(child, ttk.Button) and child.cget("text") == label
        ),
        None,
    )
    if button is None:
        raise AssertionError(f"Missing action: {label}")
    button.invoke()


@unittest.skipUnless(
    os.environ.get("SUPPLY_CHAIN_SAVE_GUI_TESTS") == "1",
    "save-flow GUI checks run in their own desktop process",
)
class FrontendSaveFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from db import connection
        from db.backup import backup_database
        from db.migration_runner import run_migrations
        from ui.theme import configure_design_system

        cls.temp_dir = tempfile.TemporaryDirectory(prefix="frontend_save_")
        cls.database = Path(cls.temp_dir.name) / "test.db"
        source = Path(__file__).resolve().parents[1] / "supplier_data.db"
        backup_database(source, cls.database)
        run_migrations(cls.database)
        cls.original_db_path = connection.DB_PATH
        connection.DB_PATH = cls.database
        try:
            cls.root = ttk.Window(themename="flatly")
        except tk.TclError as error:
            connection.DB_PATH = cls.original_db_path
            cls.temp_dir.cleanup()
            raise unittest.SkipTest(f"Tk runtime unavailable: {error}") from error
        cls.root.geometry("1200x800")
        configure_design_system(cls.root)
        cls.callback_errors = []
        cls.root.report_callback_exception = lambda *error: cls.callback_errors.append(error)

    @classmethod
    def tearDownClass(cls):
        from db import connection

        cls.root.destroy()
        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def setUp(self):
        self.host = ttk.Frame(self.root)
        self.host.pack(fill="both", expand=True)

    def tearDown(self):
        self.host.destroy()
        self.root.update()
        self.assertEqual(self.callback_errors, [])

    def test_purchase_batch_create_and_edit(self):
        from pages.purchase_management_page import PurchaseManagementPage
        from services import procurement_service, project_service

        project_id = project_service.create_project({"name": "窗口保存采购测试"})
        project = next(row for row in project_service.list_projects() if row["id"] == project_id)
        project_label = f"{project['project_code']} · {project['name']}"
        page = PurchaseManagementPage.__new__(PurchaseManagementPage)
        page.parent = self.host
        page.month_var = ttk.StringVar()
        page.refresh_filters = Mock()
        page.refresh_all = Mock()
        captured = []
        build = page._prepare_purchase_dialog

        def capture(*args):
            ctx = build(*args)
            captured.append(ctx)
            return ctx

        with patch.object(page, "_purchase_projects", return_value=[project]), \
             patch.object(page, "_prepare_purchase_dialog", side_effect=capture), \
             patch("pages.purchase_order_dialog.messagebox.showwarning") as warning:
            page.open_purchase_dialog("零星采购")
            ctx = captured[-1]
            ctx.vars_["project"].set(project_label)
            ctx.vars_["merchant"].set("测试材料店")
            ctx.vars_["material"].set("钢板")
            ctx.vars_["qty"].set("2")
            ctx.vars_["material_unit_price"].set("10")
            invoke_action(ctx.dialog, "加入本单 / 继续填材料")
            self.assertEqual(len(ctx.pending_items), 1)

            ctx.vars_["material"].set("螺丝")
            ctx.vars_["qty"].set("3")
            ctx.vars_["material_unit_price"].set("5")
            invoke_action(ctx.dialog, "保存采购记录")
            self.assertFalse(ctx.dialog.winfo_exists())
            self.assertFalse(warning.called, warning.call_args_list)

            orders = procurement_service.list_purchase_orders(project_id=project_id)
            self.assertEqual(len(orders), 1)
            order_id = orders[0]["id"]
            saved = procurement_service.get_purchase_order(order_id)
            self.assertEqual(saved["item_count"], 2)
            self.assertEqual(saved["project_cost_cents"], 3500)

            page.open_purchase_dialog("零星采购", order_id)
            edit_ctx = captured[-1]
            edit_ctx.vars_["material_unit_price"].set("12")
            invoke_action(edit_ctx.dialog, "保存修改")
            self.assertFalse(edit_ctx.dialog.winfo_exists())
            self.assertFalse(warning.called, warning.call_args_list)
            updated = procurement_service.get_purchase_order(order_id)
            self.assertEqual(updated["item_count"], 2)
            self.assertEqual(updated["project_cost_cents"], 3900)

    def test_cash_receipt_create_and_edit(self):
        from pages.finance_page import ReceivablePage
        from services import finance_service, project_service

        project_id = project_service.create_project({
            "name": "窗口保存回款测试", "business_mode": "cash",
        })
        page = ReceivablePage.__new__(ReceivablePage)
        page.parent = self.host
        page.selected_project_id = Mock(return_value=project_id)
        page.refresh = Mock()
        page.notebook = Mock()
        captured = []
        build = page._build_receipt_form

        def capture(ctx):
            captured.append(ctx)
            return build(ctx)

        with patch.object(page, "_build_receipt_form", side_effect=capture), \
             patch("pages.finance_receipt_dialog.messagebox.showwarning") as warning:
            page.open_receipt_dialog()
            ctx = captured[-1]
            ctx.variables["date"].set("2026-09-28")
            ctx.variables["amount"].set("100.00")
            invoke_action(ctx.dialog, "保存回款")
            self.assertFalse(ctx.dialog.winfo_exists())
            self.assertFalse(warning.called, warning.call_args_list)

            receipts = finance_service.list_receipts(project_id=project_id)
            self.assertEqual(len(receipts), 1)
            receipt_id = receipts[0]["id"]
            self.assertEqual(receipts[0]["allocated_amount_minor"], 10000)

            page.open_receipt_dialog(receipt_id)
            edit_ctx = captured[-1]
            edit_ctx.variables["amount"].set("125.00")
            invoke_action(edit_ctx.dialog, "保存修改")
            self.assertFalse(edit_ctx.dialog.winfo_exists())
            self.assertFalse(warning.called, warning.call_args_list)
            updated = finance_service.get_receipt(receipt_id)
            self.assertEqual(updated["allocated_amount_minor"], 12500)
