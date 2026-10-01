"""Inclusive period attendance, month drill-down, and overtime accounting."""

import tempfile
import unittest
import os
import ctypes
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


class WorkdayMonthlyServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="workday_monthly_")
        cls.test_db = Path(cls.temp_dir.name) / "supplier_data.db"
        source_db = Path(__file__).resolve().parent.parent / "supplier_data.db"
        from db.backup import backup_database
        from db.migration_runner import run_migrations
        import db.connection as connection

        backup_database(connection.DB_PATH, cls.test_db)
        cls.original_db_path = connection.DB_PATH
        run_migrations(cls.test_db)
        connection.DB_PATH = cls.test_db
        from services import labor_service

        cls.labor = labor_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def _add_log(self, worker_id, work_date, days, *, overtime=False):
        return self.labor.add_work_log({
            "worker_id": worker_id,
            "work_date": work_date,
            "construction_site": "月度汇总测试工地",
            "work_type": "安装",
            "work_days": days,
            "daily_rate": 300,
            "is_overtime": overtime,
            "allow_unassigned": True,
        })

    def test_cross_year_same_name_retired_worker_and_void_log(self):
        name = f"月度同名工人-{uuid4().hex[:8]}"
        first = self.labor.add_worker({"name": name, "status": "离职"})
        second = self.labor.add_worker({"name": name, "status": "在职"})
        self.labor.add_worker({"name": f"无记录工人-{uuid4().hex[:8]}"})
        dec_log = self._add_log(first, "2099-12-31", 0.5, overtime=True)
        jan_log = self._add_log(first, "2100-01-01", 1)
        self._add_log(second, "2100-01-01", 0.5)
        void_log = self._add_log(first, "2100-01-02", 1)
        self.labor.delete_work_logs([void_log])
        self._add_log(first, "2100-02-01", 1)

        summary = self.labor.get_worker_monthly_workdays(
            "2099-12-31", "2100-01-01"
        )
        self.assertEqual(summary["months"], ["2099-12", "2100-01"])
        self.assertEqual(summary["worker_count"], 2)
        self.assertEqual(summary["record_count"], 3)
        self.assertEqual(summary["work_days"], 2)
        self.assertEqual(summary["overtime_days"], 0.5)
        self.assertEqual(summary["month_totals"], {"2099-12": 0.5, "2100-01": 1.5})
        by_id = {row["worker_id"]: row for row in summary["workers"]}
        self.assertEqual(by_id[first]["worker_status"], "离职")
        self.assertEqual(by_id[first]["months"], {"2099-12": 0.5, "2100-01": 1})
        self.assertEqual(by_id[second]["months"], {"2100-01": 0.5})

        dec_details = self.labor.get_worker_month_work_logs(
            first, "2099-12", "2099-12-31", "2100-01-01"
        )
        self.assertEqual([row["id"] for row in dec_details["details"]], [dec_log])
        self.assertEqual(dec_details["work_days"], 0.5)
        self.assertEqual(dec_details["overtime_days"], 0.5)
        jan_details = self.labor.get_worker_month_work_logs(
            first, "2100-01", "2099-12-31", "2100-01-01"
        )
        self.assertEqual([row["id"] for row in jan_details["details"]], [jan_log])
        self.assertEqual(jan_details["overtime_days"], 0)

    def test_invalid_period_and_outside_month(self):
        with self.assertRaisesRegex(ValueError, "开始日期不能晚于结束日期"):
            self.labor.get_worker_monthly_workdays("2100-02-01", "2100-01-01")
        with self.assertRaisesRegex(ValueError, "月份必须是 YYYY-MM"):
            self.labor.get_worker_month_work_logs(1, "2100-13", "2100-01-01", "2100-01-31")
        result = self.labor.get_worker_month_work_logs(
            1, "2099-12", "2100-01-01", "2100-01-31"
        )
        self.assertEqual(result["record_count"], 0)


@unittest.skipUnless(
    os.environ.get("SUPPLY_CHAIN_GUI_TESTS") == "1",
    "GUI validation requires an interactive Windows desktop",
)
class WorkdayMonthlyGuiTests(unittest.TestCase):
    def test_month_cell_opens_amount_free_details_and_table_scrolls(self):
        import tkinter as tk
        import tkinter.font as tkfont
        import ttkbootstrap as ttk

        from pages import workday_monthly_panel
        from ui.theme import configure_design_system
        from ui.scaling import scale_px

        ctypes.windll.kernel32.SetErrorMode(2)
        try:
            root = ttk.Window(themename="flatly")
        except tk.TclError as error:
            raise unittest.SkipTest(f"Tk runtime unavailable: {error}") from error
        try:
            root.tk.call("tk", "scaling", 1.70)
            root.geometry("850x650")
            configure_design_system(root)
            months = [f"2099-{month:02d}" for month in range(1, 13)]
            summary = {
                "start_date": "2099-01-01", "end_date": "2099-12-31",
                "months": months, "worker_count": 2, "record_count": 2,
                "work_days": 1, "overtime_days": 0.5,
                "workers": [
                    {
                        "worker_id": 42, "worker_name": "测试工人", "worker_status": "离职",
                        "work_days": 0.5, "overtime_days": 0.5,
                        "months": {"2099-01": 0.5},
                    },
                    {
                        "worker_id": 43, "worker_name": "测试工人", "worker_status": "在职",
                        "work_days": 0.5, "overtime_days": 0,
                        "months": {"2099-01": 0.5},
                    },
                ],
            }
            details = {
                "record_count": 1, "work_days": 0.5, "overtime_days": 0.5,
                "details": [{
                    "id": 101, "work_date": "2099-01-01", "project_name": "测试项目",
                    "construction_site": "测试工地", "work_type": "安装",
                    "is_overtime": 1, "work_days": 0.5,
                }],
            }
            with patch.object(workday_monthly_panel.labor_service, "get_worker_monthly_workdays", return_value=summary), \
                 patch.object(workday_monthly_panel.labor_service, "get_worker_month_work_logs", return_value=details):
                panel = workday_monthly_panel.WorkdayMonthlyPanel(root)
                panel.refresh()
                root.update()
                tree = panel.table.tree
                self.assertEqual(tree.set("42", "2099-01"), "0.5")
                self.assertNotEqual(tree.set("42", "worker"), tree.set("43", "worker"))
                tree.xview_moveto(1)
                root.update()
                self.assertGreater(tree.xview()[0], 0)
                tree.xview_moveto(0)
                root.update()
                x, y, width, height = tree.bbox("42", "2099-01")
                self.assertGreater(width, 0)
                tree.event_generate("<ButtonRelease-1>", x=x + 10, y=y + height // 2)
                root.update()
                dialogs = [child for child in root.winfo_children() if isinstance(child, ttk.Toplevel)]
                self.assertEqual(len(dialogs), 1)
                dialog = dialogs[0]
                self.assertIn("测试工人", dialog.title())
                trees = [widget for widget in dialog.winfo_children()]
                while trees and not any(isinstance(widget, ttk.Treeview) for widget in trees):
                    trees = [child for widget in trees for child in widget.winfo_children()]
                detail_tree = next(widget for widget in trees if isinstance(widget, ttk.Treeview))
                self.assertNotIn("amount", detail_tree["columns"])
                self.assertEqual(detail_tree.set("101", "date"), "2099-01-01")
                body_font = tkfont.Font(root=root, font=root.style.lookup("Treeview", "font"))
                self.assertGreaterEqual(
                    detail_tree.column("date", "width"),
                    body_font.measure("2099-01-01") + scale_px(root, 24),
                )
                dialog.destroy()
                panel._mark_dates_changed(None)
                self.assertIsNone(panel.summary)
                self.assertFalse(tree.get_children())
                self.assertIn("点击", panel.summary_var.get())
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
