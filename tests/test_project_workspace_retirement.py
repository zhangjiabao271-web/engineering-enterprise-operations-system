"""Project-scoped navigation after retiring the duplicate workspace entry."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from db import connection
from db.backup import backup_database
from db.migration_runner import run_migrations
from services import contract_service, finance_service, operations_service, project_service


class _Variable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Combo:
    def __init__(self):
        self.values = ()

    def configure(self, *, values):
        self.values = values

    def __setitem__(self, key, value):
        if key == "values":
            self.values = value


class _IsolatedProjectDatabase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="project_navigation_")
        self.addCleanup(folder.cleanup)
        target = Path(folder.name) / "supplier_data.db"
        backup_database(connection.DB_PATH, target)
        run_migrations(target)
        db_patch = patch.object(connection, "DB_PATH", target)
        db_patch.start()
        self.addCleanup(db_patch.stop)


class ProjectWorkspaceRetirementTests(_IsolatedProjectDatabase):
    def test_project_guidance_matches_dashboard(self):
        overview = operations_service.get_executive_overview()
        for project in overview["projects"]:
            guidance = operations_service.get_project_guidance(project["project_id"])
            for key in ("stage_code", "stage_label", "gaps", "gap_text"):
                self.assertEqual(guidance[key], project[key])

    def test_contract_project_filter_includes_only_related_contracts(self):
        project_ids = [
            project_service.create_project({"name": f"跳转筛选-{uuid4().hex[:8]}"})
            for _ in range(2)
        ]
        contract_ids = []
        for project_id in project_ids:
            contract_id = contract_service.create_contract({
                "name": f"跳转合同-{uuid4().hex[:8]}",
                "contract_type": "project",
                "sign_date": "2026-09-28",
                "amount": "1000.00",
                "status": "active",
            })
            contract_service.create_allocation({
                "contract_id": contract_id,
                "project_id": project_id,
                "amount": "1000.00",
            })
            contract_ids.append(contract_id)
        for project_id, expected_id in zip(project_ids, contract_ids):
            visible_ids = {
                row["id"] for row in contract_service.list_contracts(project_id=project_id)
            }
            self.assertEqual(visible_ids.intersection(contract_ids), {expected_id})

    def test_advance_receipt_is_not_described_as_invalid_over_collection(self):
        project_id = project_service.create_project({
            "name": f"预收提示-{uuid4().hex[:8]}",
            "business_mode": "cash", "invoice_policy": "not_required",
        })
        finance_service.create_receipt({
            "project_id": project_id, "amount": "40000.00",
            "receipt_date": "2026-09-28",
        })
        guidance = operations_service.get_project_guidance(project_id)
        self.assertIn("部分回款待确认收入", guidance["gaps"])
        self.assertNotIn("回款超过结算", guidance["gap_text"])

    def test_destination_pages_preselect_and_keep_project(self):
        from pages.contract_page import ContractManagementPage
        from pages.finance_page import ReceivablePage
        from pages.project_profit_page import ProjectProfitPage

        selected_id = project_service.create_project(
            {"name": f"预选项目-{uuid4().hex[:8]}"}
        )
        for page_class, refresh_name in (
            (ContractManagementPage, "_refresh_project_options"),
            (ReceivablePage, "_refresh_project_options"),
            (ProjectProfitPage, "refresh_projects"),
        ):
            page = page_class.__new__(page_class)
            page.project_var = _Variable("全部项目")
            page.project_map = {}
            page.project_combo = _Combo()
            page.initial_project_id = selected_id
            getattr(page, refresh_name)()
            self.assertEqual(page.project_map[page.project_var.get()], selected_id)
            getattr(page, refresh_name)()
            self.assertEqual(page.project_map[page.project_var.get()], selected_id)

    def test_dispatch_carries_project_id(self):
        from main import SupplierManagerApp

        calls = []
        stub = type("AppStub", (), {})()
        stub.current_page = "profit"
        stub.page_commands = {
            "contract": lambda **kwargs: calls.append(("contract", kwargs)),
            "profit": lambda **kwargs: calls.append(("profit", kwargs)),
        }
        SupplierManagerApp.navigate_to(stub, "contract", project_id=42)
        SupplierManagerApp.navigate_to(stub, "profit")
        self.assertEqual(calls, [("contract", {"project_id": 42})])


@unittest.skipUnless(
    os.environ.get("SUPPLY_CHAIN_GUI_TESTS") == "1",
    "GUI validation requires an interactive Windows desktop",
)
class ProjectWorkspaceRetirementGuiTests(_IsolatedProjectDatabase):
    def test_project_context_in_real_pages(self):
        import ctypes
        import tkinter as tk
        import ttkbootstrap as ttk

        from pages.contract_page import ContractManagementPage
        from pages.finance_page import ReceivablePage
        from pages.project_profit_page import ProjectProfitPage
        from main import SupplierManagerApp

        ctypes.windll.kernel32.SetErrorMode(2)
        try:
            root = ttk.Window(themename="flatly")
        except tk.TclError as error:
            raise unittest.SkipTest(f"Tk runtime unavailable: {error}") from error
        try:
            root.tk.call("tk", "scaling", float(os.environ.get("PROJECT_NAV_TK_SCALING", "1.33")))
            root.geometry(os.environ.get("PROJECT_NAV_WINDOW_SIZE", "1200x800"))
            app = SupplierManagerApp(root)
            app.page_transition.set_enabled(False)
            root.update()
            self.assertNotIn("workspace", app.nav_buttons)
            self.assertIn("workspace", app.page_commands)

            def capture(label):
                directory = os.environ.get("PROJECT_NAV_SCREENSHOT_DIR")
                if directory:
                    from PIL import ImageGrab

                    ImageGrab.grab(window=root.winfo_id()).save(
                        Path(directory) / f"project-navigation-{label}.png"
                    )

            project_id = project_service.list_projects()[0]["id"]
            calls = []

            def find_button(widget, label):
                for child in widget.winfo_children():
                    if isinstance(child, ttk.Button) and child.cget("text") == label:
                        return child
                    found = find_button(child, label)
                    if found:
                        return found
                return None

            self.assertIsNone(find_button(app.content_frame, "项目工作空间"))
            app.clear_content()
            host = app.content_frame
            profit = ProjectProfitPage(
                host, navigate=lambda key, **kw: calls.append((key, kw)),
                initial_project_id=project_id,
            )
            root.update()
            self.assertEqual(profit.selected_project_id(), project_id)
            self.assertIn("经营阶段", profit.guidance_var.get())
            capture("profit")

            for label, key in (("合同与结算", "contract"), ("开票与回款", "finance")):
                button = find_button(host, label)
                self.assertIsNotNone(button)
                self.assertTrue(button.winfo_ismapped())
                self.assertLessEqual(
                    button.winfo_rootx() + button.winfo_width(),
                    root.winfo_rootx() + root.winfo_width(),
                )
                button.invoke()
                self.assertEqual(calls[-1], (key, {"project_id": project_id}))

            app.clear_content()
            host = app.content_frame
            contracts = ContractManagementPage(host, initial_project_id=project_id)
            root.update()
            self.assertEqual(contracts.selected_project_id(), project_id)
            self.assertTrue(contracts.project_combo.winfo_ismapped())
            capture("contract")

            app.clear_content()
            host = app.content_frame
            finance = ReceivablePage(host, initial_project_id=project_id)
            root.update()
            self.assertEqual(finance.selected_project_id(), project_id)
            self.assertTrue(finance.project_combo.winfo_ismapped())
            capture("finance")
        finally:
            if "app" in locals():
                app.page_transition.finish()
            root.after(150, root.destroy)
            root.mainloop()


if __name__ == "__main__":
    unittest.main()
