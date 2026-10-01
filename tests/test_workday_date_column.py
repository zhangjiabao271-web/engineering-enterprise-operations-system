"""Guard against clipping a full workday date at desktop DPI scaling."""

import ctypes
import os
import tkinter as tk
import tkinter.font as tkfont
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import ttkbootstrap as ttk

from ui.scaling import scale_px
from ui.theme import configure_design_system


@unittest.skipUnless(
    os.environ.get("SUPPLY_CHAIN_GUI_TESTS") == "1",
    "GUI validation requires an interactive Windows desktop",
)
class WorkdayDateColumnTests(unittest.TestCase):
    def test_full_date_fits_and_clipping_audit_detects_regression(self):
        from pages import workday_page
        from scripts.audit_text_clipping import inspect_page

        ctypes.windll.kernel32.SetErrorMode(2)
        try:
            root = ttk.Window(themename="flatly")
        except tk.TclError as error:
            raise unittest.SkipTest(f"Tk runtime unavailable: {error}") from error
        try:
            root.tk.call("tk", "scaling", 1.70)
            root.geometry("1200x800")
            configure_design_system(root)
            with patch.object(workday_page, "safe_init_loaders"):
                page = workday_page.WorkdayDashboardPage(root)
            date_text = "2026-09-29"
            page.log_table.insert_row(
                "1", (date_text, "测试工人", "普工", "测试工地", "测试", "正常",
                      "1", "200", "200", "未锁定", ""),
            )
            root.update()

            tree = page.log_tree
            tree_style = tree.cget("style") or "Treeview"
            body_font = tkfont.Font(root=root, font=root.style.lookup(tree_style, "font"))
            required_width = body_font.measure(date_text) + scale_px(root, 24)
            self.assertGreaterEqual(tree.column("date", "width"), required_width)
            self.assertEqual(tree.set("1", "date"), date_text)

            tree.column("date", width=required_width - 1, minwidth=1)
            app = SimpleNamespace(content_frame=root, navigate_to=lambda _key: None)
            issues = inspect_page(root, app, "workday")
            self.assertTrue(
                any("列“日期”" in issue and date_text in issue for issue in issues),
                issues,
            )
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
