"""Focused Tk interaction checks; skip cleanly when the host has no Tcl/Tk runtime."""

import os
import threading
import time
import tkinter as tk
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import ttkbootstrap as ttk

from ui.theme import configure_design_system


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


@unittest.skipUnless(
    os.environ.get("SUPPLY_CHAIN_GUI_TESTS") == "1",
    "GUI checks are opt-in and require a verified Tcl/Tk desktop runtime",
)
class FrontendComponentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = ttk.Window(themename="flatly")
        except tk.TclError as error:
            raise unittest.SkipTest(f"Tk runtime unavailable: {error}") from error
        cls.root.geometry("1400x850")
        configure_design_system(cls.root)

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.host = ttk.Frame(self.root)
        self.host.pack(fill="both", expand=True)

    def tearDown(self):
        self.host.destroy()
        self.root.update()

    def test_construction_tables_sort_select_and_show_empty_state(self):
        from pages import construction_page

        with patch.object(construction_page, "safe_init_loaders"):
            page = construction_page.ConstructionRecordPage(self.host)
        from ui.components import DataTable
        for table in (page.site_table, page.area_table, page.record_table):
            self.assertIsInstance(table, DataTable)

        empty_dashboard = {
            "summary": {}, "by_site": [], "by_area": [],
        }
        with patch.object(construction_page.db, "get_construction_dashboard", return_value=empty_dashboard), \
             patch.object(construction_page.db, "get_construction_records", return_value=[]):
            page.refresh_all()
        self.root.update()
        for table in (page.site_table, page.area_table, page.record_table):
            self.assertTrue(
                table.empty_label.winfo_ismapped(),
                f"{table}: table={table.winfo_ismapped()} h={table.winfo_height()} "
                f"tree={table.tree.winfo_ismapped()} req={table.tree.winfo_reqheight()} "
                f"pack={table.tree.pack_info()}, "
                f"empty_place={table.empty_label.place_info()}",
            )

        rows = [
            {"id": 11, "project_name": "项目甲", "start_date": "2026-09-01",
             "end_date": "2026-09-01", "work_area": "一区", "work_details": "安装",
             "work_amount_cents": 20000, "inspection_status": "待验收", "photo_count": 0},
            {"id": 12, "project_name": "项目乙", "start_date": "2026-09-02",
             "end_date": "2026-09-02", "work_area": "二区", "work_details": "收尾",
             "work_amount_cents": 10000, "inspection_status": "已验收", "photo_count": 1},
        ]
        with patch.object(construction_page.db, "get_construction_records", return_value=rows):
            page.refresh_records()
        self.root.update()
        self.assertFalse(page.record_table.empty_label.winfo_ismapped())
        page.record_table._sort_by("amount")
        self.assertEqual(page.tree.get_children(), ("12", "11"))
        page.tree.selection_set("11")
        self.assertEqual(page.selected_record_id(), 11)

    def test_quote_comparison_empty_results_and_price_sort(self):
        from pages import compare_page
        from ui.components import DataTable

        page = compare_page.ComparePage(self.host)
        self.assertIsInstance(page.table, DataTable)
        page.name_entry.insert(0, "彩钢瓦")
        with patch.object(compare_page.master_data_service, "list_supplier_offers", return_value=[]):
            page.search()
        self.root.update()
        self.assertTrue(page.table.empty_label.winfo_ismapped())

        rows = [
            {"supplier_name": name, "category": "工厂", "name": "彩钢瓦",
             "specification": "4.4m", "price": price, "tax_rate_percent": 13,
             "tax_inclusive_price": gross, "tax_inclusive_price_minor": int(gross * 100),
             "price_minor": int(price * 100), "unit": "张", "quality": "良",
             "price_level": "中", "export": "否", "notes": ""}
            for name, price, gross in (("乙", 20, 22.6), ("甲", 10, 11.3))
        ]
        with patch.object(compare_page.master_data_service, "list_supplier_offers", return_value=rows):
            page.search()
        self.root.update()
        children = page.tree.get_children()
        self.assertEqual([page.tree.set(item, "supplier") for item in children], ["甲", "乙"])
        self.assertEqual(page.tree.set(children[0], "recommend"), "最低含税价")
        page.table._sort_by("tax_price")
        page.table._sort_by("tax_price")
        self.assertEqual(page.tree.set(page.tree.get_children()[0], "supplier"), "乙")

    def test_material_quote_selection_populates_form(self):
        from pages import product_page
        from ui.components import DataTable

        with patch.object(product_page, "safe_init_loaders"):
            page = product_page.ProductPage(self.host)
        self.assertIsInstance(page.table, DataTable)
        offer = {"id": 7, "supplier_name": "材料厂", "name": "彩钢瓦",
                 "specification": "4.4m", "unit": "张", "price": 10.0,
                 "tax_rate_percent": 13, "tax_inclusive_price": 11.3, "notes": ""}
        with patch.object(product_page.master_data_service, "list_supplier_offers", return_value=[offer]):
            page.load_data()
        self.root.update()
        self.assertFalse(page.table.empty_label.winfo_ismapped())
        with patch.object(product_page.master_data_service, "get_supplier_offer", return_value=offer), \
             patch.object(page, "set_form_data") as fill_form:
            page.tree.selection_set("7")
            page.on_select(None)
        self.assertEqual(page.selected_id, 7)
        fill_form.assert_called_with(offer)

    def test_input_invoice_wide_table_and_empty_state(self):
        from pages import input_invoice_page
        from ui.components import DataTable

        entity = {"id": 1, "name": "测试主体"}
        with patch.object(input_invoice_page.entities, "list_entities", return_value=[entity]), \
             patch.object(input_invoice_page.invoices, "list_invoices", return_value=[]):
            page = input_invoice_page.InputInvoicePage(self.host)
        self.root.update()
        self.assertIsInstance(page.table, DataTable)
        self.assertTrue(page.table.empty_label.winfo_ismapped())
        horizontal = [child for child in page.table.winfo_children()
                      if isinstance(child, ttk.Scrollbar) and str(child.cget("orient")) == "horizontal"]
        self.assertEqual(len(horizontal), 1)
        self.assertTrue(horizontal[0].winfo_ismapped())
        self.assertLess(page.tree.xview()[1], 1)
        page.tree.xview_moveto(1)
        self.root.update()
        self.assertGreater(horizontal[0].get()[0], 0)

    def test_import_export_sections_are_present(self):
        from pages.import_export_page import ImportExportPage
        from ui.components import SectionPanel

        ImportExportPage(self.host)
        self.root.update()
        sections = [child for child in self.host.winfo_children()
                    if isinstance(child, SectionPanel)]
        self.assertEqual(len(sections), 4)
        labels = [child.cget("text") for section in sections
                  for child in descendants(section) if isinstance(child, ttk.Label)]
        for title in ("供应商数据", "材料与供应商报价", "采购记录", "经营数据归档"):
            self.assertIn(title, labels)

    def test_extracted_finance_invoice_dialog_opens(self):
        from pages import finance_page

        page = finance_page.ReceivablePage.__new__(finance_page.ReceivablePage)
        page.parent = self.host
        target = {"project_id": 1, "contract_id": 2, "income_mode": "settlement",
                  "amount_minor": 10000, "invoiced_minor": 0,
                  "uninvoiced_minor": 10000, "settlement_count": 1}
        with patch.object(page, "_invoice_target_map", return_value={"合同 · 项目": target}):
            page.open_invoice_dialog()
        self.root.update()
        dialogs = [child for child in descendants(self.root) if isinstance(child, ttk.Toplevel)]
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(dialogs[0].title(), "登记销项发票")
        dialogs[0].destroy()

    def test_extracted_finance_receipt_dialog_opens(self):
        from pages import finance_page, finance_receipt_dialog

        page = finance_page.ReceivablePage.__new__(finance_page.ReceivablePage)
        page.parent = self.host
        cash_project = {"id": 1, "project_code": "TEST-1", "name": "测试零星工程",
                        "business_mode": "cash", "status": "进行中"}
        with patch.object(page, "selected_project_id", return_value=None), \
             patch.object(finance_receipt_dialog.contract_service, "list_settlements", return_value=[]), \
             patch.object(finance_receipt_dialog.contract_service, "list_allocations", return_value=[]), \
             patch.object(finance_receipt_dialog.project_service, "list_projects", return_value=[cash_project]), \
             patch.object(finance_receipt_dialog.finance_service, "list_invoices", return_value=[]):
            page.open_receipt_dialog()
        self.root.update()
        dialogs = [child for child in descendants(self.root) if isinstance(child, ttk.Toplevel)]
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(dialogs[0].title(), "登记回款")
        dialogs[0].destroy()

    def test_extracted_purchase_dialog_opens(self):
        from pages import purchase_management_page, purchase_order_dialog

        page = purchase_management_page.PurchaseManagementPage.__new__(
            purchase_management_page.PurchaseManagementPage)
        page.parent = self.host
        project = {"id": 1, "project_code": "TEST-1", "name": "测试工程"}
        with patch.object(page, "_purchase_projects", return_value=[project]), \
             patch.object(purchase_order_dialog.master_data_service, "list_suppliers", return_value=[]):
            page.open_purchase_dialog("零星采购")
        self.root.update()
        dialogs = [child for child in descendants(self.root) if isinstance(child, ttk.Toplevel)]
        self.assertEqual(len(dialogs), 1)
        self.assertEqual(dialogs[0].title(), "快速记零星采购")
        dialogs[0].destroy()

    def test_formal_purchase_offer_selection_updates_current_line(self):
        from pages import purchase_management_page, purchase_order_dialog

        page = purchase_management_page.PurchaseManagementPage.__new__(
            purchase_management_page.PurchaseManagementPage)
        page.parent = self.host
        project = {"id": 1, "project_code": "TEST-1", "name": "测试工程"}
        offer = {"id": 9, "name": "彩钢瓦", "specification": "4.4m",
                 "unit": "张", "price": 20.0, "quoted_price": 22.6,
                 "price_basis": "inclusive", "tax_rate_percent": 13}
        states = []
        original_prepare = page._prepare_purchase_dialog

        def capture_state(*args):
            ctx = original_prepare(*args)
            states.append(ctx)
            return ctx

        with patch.object(page, "_purchase_projects", return_value=[project]), \
             patch.object(page, "_prepare_purchase_dialog", side_effect=capture_state), \
             patch.object(purchase_order_dialog.master_data_service, "list_suppliers",
                          return_value=[{"id": 7, "name": "材料厂"}]), \
             patch.object(purchase_order_dialog.master_data_service, "list_supplier_offers",
                          return_value=[offer]):
            page.open_purchase_dialog("正式采购")
        self.root.update()
        ctx = states[0]
        ctx.vars_["product"].set("彩钢瓦 · 4.4m")
        page._select_product(ctx)
        self.assertEqual(ctx.vars_["material"].get(), "彩钢瓦")
        self.assertEqual(ctx.vars_["price_basis"].get(), "含税价")
        self.assertEqual(ctx.vars_["material_unit_price"].get(), "22.6")
        self.assertEqual(ctx.vars_["tax_rate"].get(), "13")
        ctx.dialog.destroy()

    def test_edit_purchase_keeps_historical_price_after_loading_offer(self):
        from pages import purchase_order_dialog, purchase_order_product

        offer = {"id": 9, "name": "彩钢瓦", "specification": "4.4m",
                 "unit": "张", "price": 50.0, "quoted_price": 56.5,
                 "price_basis": "inclusive", "tax_rate_percent": 13}
        keys = ("supplier", "product", "material", "spec", "unit",
                "material_unit_price", "tax_rate", "settlement_mode",
                "price_basis", "qty", "freight")
        variables = {key: ttk.StringVar() for key in keys}
        variables["supplier"].set("材料厂")
        variables["settlement_mode"].set("按数量")
        ctx = SimpleNamespace(
            vars_=variables, supplier_map={"材料厂": 7}, products_by_label={},
            supplier_combo=ttk.Combobox(self.host),
            product_combo=ttk.Combobox(self.host),
            product_hint_var=ttk.StringVar(), order_id=5,
            edit_data={"product_id": 9, "price_basis": "inclusive",
                       "tax_inclusive_unit_price_cents": 2260,
                       "tax_rate_bps": 1300, "quantity": 2,
                       "freight_amount_cents": 500},
        )
        with patch.object(purchase_order_product.master_data_service,
                          "list_supplier_offers", return_value=[offer]):
            purchase_order_dialog.PurchaseOrderDialogMixin()._setup_product_typeahead(ctx)
        self.assertEqual(variables["material_unit_price"].get(), "22.6")
        self.assertEqual(variables["qty"].get(), "2")
        self.assertEqual(variables["freight"].get(), "5.0")

    def test_receipt_distribution_dialog_uses_selected_amount(self):
        from pages import finance_page, finance_receipt_allocation, finance_receipt_dialog

        page = finance_page.ReceivablePage.__new__(finance_page.ReceivablePage)
        page.parent = self.host
        parent_dialog = ttk.Toplevel(self.host)
        ctx = SimpleNamespace(
            dialog=parent_dialog, receipt=None, receipt_id=None, editing=False,
            variables={"invoice": ttk.StringVar(value=finance_receipt_dialog.AUTOMATIC_INVOICE_LABEL),
                       "amount": ttk.StringVar(value="100.00")},
            invoice_map={finance_receipt_dialog.AUTOMATIC_INVOICE_LABEL: None},
            manual_items=None,
        )
        allocation = {"project_id": 1, "contract_id": 2}
        settlement = {"id": 3, "source_type": "contract", "unreceived_minor": 10000,
                      "settlement_no": "JS-3", "settlement_date": "2026-09-01",
                      "amount_minor": 10000}
        with patch.object(page, "_selected_formal_allocation", return_value=allocation), \
             patch.object(page, "_sync_receipt_distribution_summary") as sync_summary, \
             patch.object(finance_receipt_allocation.finance_service,
                          "preview_receipt_allocations",
                          return_value=[{"settlement_id": 3, "amount_minor": 10000}]) as preview, \
             patch.object(finance_receipt_allocation.contract_service,
                          "list_settlements", return_value=[settlement]):
            page._open_receipt_distribution_dialog(ctx)
            self.root.update()
            dialogs = [child for child in descendants(self.root)
                       if isinstance(child, ttk.Toplevel)
                       and child.title() == "调整收入确认分配"]
            self.assertEqual(len(dialogs), 1)
            dialog = dialogs[0]
            self.assertEqual(dialog.title(), "调整收入确认分配")
            button = next(child for child in descendants(dialog)
                          if isinstance(child, ttk.Button)
                          and child.cget("text") == "使用该分配")
            button.invoke()
            self.assertEqual(ctx.manual_items, [{"settlement_id": 3, "amount_minor": 10000}])
            self.assertEqual(preview.call_count, 2)
            sync_summary.assert_called_once_with(ctx)
        parent_dialog.destroy()

    def _open_ai_config(self):
        from pages.ai_page import AIAssistantPage

        page = AIAssistantPage.__new__(AIAssistantPage)
        page.parent = self.host
        page.open_config_dialog()
        self.root.update()
        dialog = next(child for child in descendants(self.root) if isinstance(child, ttk.Toplevel))
        button = next(child for child in descendants(dialog)
                      if isinstance(child, ttk.Button) and child.cget("text") == "测试连接")
        status = next(child for child in descendants(dialog)
                      if isinstance(child, ttk.Label)
                      and str(child.cget("textvariable"))
                      and "可先测试连接" in str(self.root.getvar(child.cget("textvariable"))))
        return dialog, button, status

    def test_ai_connection_result_returns_to_main_thread(self):
        from pages import ai_page

        for failure in (False, True):
            with self.subTest(failure=failure), \
                 patch.object(ai_page.ai_engine, "get_ai_config", return_value={
                     "api_key": "test-only-key", "model": "test-model",
                     "api_base": "https://invalid.example", "use_system_proxy": False,
                 }), \
                 patch.object(ai_page.ai_engine, "test_ai_connection",
                              side_effect=RuntimeError("模拟失败") if failure else None,
                              return_value={"model": "test-model", "models": ["test-model"]}):
                dialog, button, status = self._open_ai_config()
                main_thread = threading.get_ident()
                original_after = tk.Misc.after

                def main_thread_after(widget, *args):
                    self.assertEqual(threading.get_ident(), main_thread)
                    return original_after(widget, *args)

                with patch.object(tk.Misc, "after", main_thread_after):
                    button.invoke()
                    self.assertIn("disabled", button.state())
                    deadline = time.monotonic() + 2
                    while "disabled" in button.state() and time.monotonic() < deadline:
                        self.root.update()
                        time.sleep(0.01)
                self.assertNotIn("disabled", button.state())
                value = str(self.root.getvar(status.cget("textvariable")))
                self.assertIn("连接失败" if failure else "连接成功", value)
                dialog.destroy()

    def test_ai_connection_can_finish_after_dialog_closes(self):
        from pages import ai_page

        started = threading.Event()
        release = threading.Event()

        def delayed_result(_values):
            started.set()
            release.wait(1)
            return {"model": "test-model", "models": ["test-model"]}

        with patch.object(ai_page.ai_engine, "get_ai_config", return_value={
                 "api_key": "test-only-key", "model": "test-model",
                 "api_base": "https://invalid.example", "use_system_proxy": False,
             }), patch.object(ai_page.ai_engine, "test_ai_connection", side_effect=delayed_result):
            dialog, button, _status = self._open_ai_config()
            button.invoke()
            self.assertTrue(started.wait(1))
            dialog.destroy()
            release.set()
            deadline = time.monotonic() + 0.2
            while time.monotonic() < deadline:
                self.root.update()
                time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
