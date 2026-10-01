import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from db import connection
from db.backup import backup_database
from db.migration_runner import run_migrations
from services import (
    cost_service,
    procurement_service,
    project_cost_detail_service,
    project_profit_service,
    project_service,
)


class ProjectCostDetailTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="project_cost_detail_")
        self.addCleanup(folder.cleanup)
        target = Path(folder.name) / "supplier_data.db"
        backup_database(connection.DB_PATH, target)
        run_migrations(target)
        db_patch = patch.object(connection, "DB_PATH", target)
        db_patch.start()
        self.addCleanup(db_patch.stop)
        self.projects = [
            project_service.create_project({"name": f"成本下钻-{uuid4().hex[:8]}"})
            for _ in range(2)
        ]

    def _order(self, *, project_id=None, project_ids=None, date="2026-01-31"):
        header = {
            "purchase_type": "零星采购",
            "project_id": project_id,
            "merchant_name_snapshot": "测试材料店",
            "purchase_date": date,
            "payment_status": "未付款",
            "invoice_status": "无发票",
            "freight_amount_cents": 7,
        }
        if project_ids:
            header.update({"project_ids": project_ids, "allocation_method": "equal"})
        return procurement_service.add_purchase_order(header, [
            {"material_name_snapshot": "钢管", "quantity": 1,
             "material_unit_price_cents": 101, "tax_rate_bps": 1300,
             "cost_category": "工具和设备", "purpose": "安装"},
            {"material_name_snapshot": "螺栓", "quantity": 1,
             "material_unit_price_cents": 203, "tax_rate_bps": 1300,
             "cost_category": "工具和设备"},
        ])

    def test_full_lifecycle_and_shared_costs_reconcile(self):
        first, second = self.projects
        direct_order = self._order(project_id=first)
        shared_order = self._order(project_ids=self.projects, date="2026-02-01")
        cost_service.create_cost({
            "project_id": first, "cost_date": "2026-02-03",
            "category": "管理费", "amount": "20.01",
            "counterparty_name": "测试会计", "notes": "代理记账",
        })
        cost_service.create_cost({
            "cost_date": "2026-03-01", "category": "用车",
            "amount": "10.01", "allocation_method": "equal",
            "project_ids": self.projects,
        })
        with connection.db_transaction() as conn:
            worker = conn.execute("SELECT id FROM workers ORDER BY id LIMIT 1").fetchone()
            if worker is None:
                worker_id = conn.execute(
                    "INSERT INTO workers(name) VALUES ('成本明细测试工人')"
                ).lastrowid
            else:
                worker_id = worker[0]
            conn.execute(
                """INSERT INTO work_logs(worker_id, work_date, construction_site,
                       project_id, work_days, amount, amount_minor, status)
                   VALUES (?, '2026-02-04', '测试现场', ?, 0.5, 12.35, NULL, 'active')""",
                (worker_id, first),
            )
            conn.execute(
                """INSERT INTO work_logs(worker_id, work_date, construction_site,
                       project_id, work_days, amount, amount_minor, status)
                   VALUES (?, '2026-02-05', '测试现场', ?, 1, 50, 5000, 'void')""",
                (worker_id, first),
            )

        for project_id in self.projects:
            detail = project_cost_detail_service.get_project_cost_details(project_id)
            summary = project_profit_service.get_project_summary(project_id)
            for key, summary_key in (
                ("purchase", "purchase_cost_minor"),
                ("labor", "labor_cost_minor"),
                ("other", "other_cost_minor"),
            ):
                self.assertEqual(detail["totals"][key], summary[summary_key])
            self.assertEqual(detail["total_cost_minor"], summary["total_cost_minor"])
            self.assertEqual(
                sum(row["amount_minor"] for row in detail["purchase"]),
                detail["totals"]["purchase"],
            )
        first_detail = project_cost_detail_service.get_project_cost_details(first)
        self.assertEqual({row["date"] for row in first_detail["purchase"]},
                         {"2026-01-31", "2026-02-01"})
        self.assertEqual(first_detail["labor"][0]["amount_minor"], 1235)
        self.assertEqual(len(first_detail["labor"]), 1)
        self.assertTrue(any(row["notes"] == "代理记账" for row in first_detail["other"]))
        self.assertTrue(any(row["allocation_method"] == "equal" for row in first_detail["other"]))
        shared_rows = [row for row in first_detail["purchase"]
                       if row["source_no"] == procurement_service.get_purchase_order(shared_order)["order_no"]]
        self.assertEqual(len(shared_rows), 3)
        self.assertEqual(sum(row["amount_minor"] for row in shared_rows),
                         procurement_service.get_purchase_allocations(shared_order)[0]["amount_minor"])
        with connection.db_read() as conn:
            share = conn.execute(
                """SELECT material_minor, tax_minor, freight_minor
                   FROM purchase_project_costs
                   WHERE purchase_order_id=? AND project_id=?""",
                (shared_order, first),
            ).fetchone()
        for component in ("material", "tax", "freight"):
            self.assertEqual(
                sum(row[f"{component}_minor"] for row in shared_rows),
                share[f"{component}_minor"],
            )
        self.assertTrue(all(row["payment_status"] == "未付款" for row in shared_rows))
        self.assertEqual(len([row for row in first_detail["purchase"]
                              if row["source_no"] == procurement_service.get_purchase_order(direct_order)["order_no"]]), 3)

    def test_void_purchase_not_in_details(self):
        order_id = self._order(project_id=self.projects[0])
        procurement_service.void_purchase_orders([order_id])
        cost_id = cost_service.create_cost({
            "project_id": self.projects[0], "cost_date": "2026-02-03",
            "category": "管理费", "amount": "1.00",
        })
        cost_service.void_costs([cost_id])
        detail = project_cost_detail_service.get_project_cost_details(self.projects[0])
        self.assertFalse(detail["purchase"])
        self.assertFalse(detail["other"])

    def test_unknown_project(self):
        with self.assertRaisesRegex(ValueError, "项目不存在"):
            project_cost_detail_service.get_project_cost_details(-1)


@unittest.skipUnless(
    os.environ.get("SUPPLY_CHAIN_GUI_TESTS") == "1",
    "GUI validation requires an interactive Windows desktop",
)
class ProjectCostDetailGuiTests(unittest.TestCase):
    def test_dialog_tabs_totals_and_horizontal_scroll(self):
        import ctypes
        import tkinter as tk
        import ttkbootstrap as ttk

        from pages.project_cost_detail_dialog import show_project_cost_details
        from ui.components import DataTable
        from ui.theme import configure_design_system

        ctypes.windll.kernel32.SetErrorMode(2)
        try:
            root = ttk.Window(themename="flatly")
        except tk.TclError as error:
            raise unittest.SkipTest(f"Tk runtime unavailable: {error}") from error
        try:
            root.geometry("1200x800")
            configure_design_system(root)
            with connection.db_read() as conn:
                project_id = conn.execute(
                    """SELECT project_id FROM purchase_project_costs
                       GROUP BY project_id ORDER BY COUNT(*) DESC LIMIT 1"""
                ).fetchone()[0]
            dialog = show_project_cost_details(root, project_id)
            root.update()
            self.assertTrue(dialog.winfo_ismapped())
            self.assertGreaterEqual(dialog.winfo_width(), 760)
            self.assertGreaterEqual(dialog.winfo_height(), 430)
            widgets = []

            def collect(widget):
                for child in widget.winfo_children():
                    widgets.append(child)
                    collect(child)

            collect(dialog)
            tables = [widget for widget in widgets if isinstance(widget, DataTable)]
            self.assertEqual(len(tables), 3)
            self.assertTrue(all(table.winfo_exists() for table in tables))
            purchase_tree = tables[0].tree
            self.assertTrue(purchase_tree.winfo_ismapped())
            purchase_tree.xview_moveto(1)
            root.update()
            self.assertGreater(purchase_tree.xview()[0], 0)
            close = next(widget for widget in widgets
                         if isinstance(widget, ttk.Button) and widget.cget("text") == "关闭")
            self.assertTrue(close.winfo_ismapped())
            dialog.destroy()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
