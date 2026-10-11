"""Readable AI answers preserve exact text facts and stay within the chat viewport."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from ui.assistant_content import parse_answer_blocks, answer_as_markdown


def profit_answer(row_count=12):
    rows = [
        f"演示工程{index}（项目编号{index}）：确认收入 ¥1,000.00；已登记经营成本 ¥200.00；"
        "已登记毛利 ¥800.00；月均毛利 ¥80.00（10个自然月）（确认收入占比8.33%）"
        for index in range(1, row_count + 1)
    ]
    return "\n".join([
        "已登记收支毛利分析｜全公司｜2026-01-01至2026-10-01",
        "确认收入：¥12,000.00；已登记经营成本：¥2,400.00；已登记毛利：¥9,600.00",
        *rows, "分组仅展示前列，合计和占比分母包含全部匹配记录。",
        "口径：仅为已登记收入减成本，不等于最终利润。",
        "注意：包含1条待归集记录，不能分摊到具体项目。",
    ])


class AnswerFormattingTests(unittest.TestCase):
    def test_old_report_becomes_metrics_and_table_without_changing_amounts(self):
        blocks = parse_answer_blocks(profit_answer())
        metrics = next(block for block in blocks if block["type"] == "metrics")
        self.assertEqual(metrics["items"][0], ("确认收入", "¥12,000.00"))
        table = next(block for block in blocks if block["type"] == "table")
        self.assertEqual(len(table["rows"]), 12)
        self.assertEqual(table["headers"], ["项目", "已登记毛利", "确认收入", "已登记经营成本", "月均毛利", "确认收入占比"])
        self.assertEqual(table["rows"][0], ["演示工程1（项目编号1）", "¥800.00", "¥1,000.00", "¥200.00", "¥80.00", "8.33%"])
        copied = answer_as_markdown(profit_answer())
        for note in ("全公司", "2026-10-01", "10个自然月", "前列", "1条待归集", "最终利润"):
            self.assertIn(note, copied)

    def test_negative_and_unknown_values_are_not_recalculated(self):
        text = profit_answer(1).replace("¥800.00", "¥-120.01").replace("¥80.00", "¥-12.00")
        table = next(block for block in parse_answer_blocks(text) if block["type"] == "table")
        self.assertEqual(table["rows"][0][1], "¥-120.01")
        self.assertEqual(table["rows"][0][4], "¥-12.00")
        text = text.replace("¥-120.01", "未知").replace("月均毛利 ¥-12.00（10个自然月）", "月均毛利无法可靠计算（存在未知金额）")
        table = next(block for block in parse_answer_blocks(text) if block["type"] == "table")
        self.assertEqual(table["rows"][0][1], "未知")
        self.assertIn("无法可靠计算", table["rows"][0][4])

    def test_unrecognized_report_keeps_every_original_line(self):
        text = profit_answer(1).replace("已登记经营成本 ¥200.00", "其他格式：不得丢失的备注")
        blocks = parse_answer_blocks(text)
        self.assertFalse(any(block["type"] == "table" for block in blocks))
        self.assertEqual(blocks[0]["text"], text)

    def test_markdown_table_and_escaped_pipe(self):
        text = "## 经营结论\n\n**没有修改业务数据**\n\n| 项目 | 毛利 |\n| --- | ---: |\n| 甲\\|乙 | -¥20.10 |\n\n- 金额以台账为准"
        blocks = parse_answer_blocks(text)
        table = next(block for block in blocks if block["type"] == "table")
        self.assertEqual(table["rows"], [["甲|乙", "-¥20.10"]])
        self.assertIn(r"甲\|乙", answer_as_markdown(text))

    def test_code_is_displayed_not_interpreted(self):
        text = "```\n|不是表格|\n|---|\nprint('不执行')\n```"
        self.assertEqual([block["type"] for block in parse_answer_blocks(text)], ["code"])
        self.assertIn("print('不执行')", answer_as_markdown(text))

    def test_markdown_cell_formatting_does_not_leak_markers_into_table(self):
        text = "| **项目** | 毛利 |\n| --- | ---: |\n| `演示工程` | **¥100.00** |"
        table = parse_answer_blocks(text)[0]
        self.assertEqual(table["headers"], ["项目", "毛利"])
        self.assertEqual(table["rows"], [["演示工程", "¥100.00"]])

    def test_multiple_query_sections_preserve_extra_conclusion(self):
        text = "毛利率：80.00%；来源：查询1\n\n" + profit_answer(1)
        blocks = parse_answer_blocks(text)
        self.assertEqual(blocks[0]["text"], "毛利率：80.00%；来源：查询1")
        self.assertTrue(any(block["type"] == "table" for block in blocks))

    def test_copy_action_uses_formatted_answer(self):
        from pages.ai_page import AIAssistantPage
        page = AIAssistantPage.__new__(AIAssistantPage)
        page.parent = Mock()
        button = Mock()
        page.copy_answer(profit_answer(1), button)
        page.parent.clipboard_clear.assert_called_once()
        self.assertIn("| 项目 |", page.parent.clipboard_append.call_args.args[0])
        button.configure.assert_called_once_with(text="已复制")


@unittest.skipUnless(os.environ.get("SUPPLY_CHAIN_GUI_TESTS") == "1", "Opt-in Tk checks")
class AnswerFormattingGuiTests(unittest.TestCase):
    def test_long_question_and_actions_fit_narrow_and_high_dpi_windows(self):
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='chat_bounds_') as temporary:
            env = dict(os.environ, SUPPLY_CHAIN_DB_PATH=str(Path(temporary) / 'test.db'),
                       SUPPLY_CHAIN_ATTACHMENTS_PATH=str(Path(temporary) / 'attachments'),
                       PYTHONIOENCODING='utf-8')
            for scaling in (4 / 3, 2):
                with self.subTest(scaling=scaling):
                    result = subprocess.run(
                        [sys.executable, '-c', 'from tests.test_ai_answer_formatting import AnswerFormattingGuiTests; '
                         f'AnswerFormattingGuiTests()._exercise_chat_bounds({scaling!r})'],
                        cwd=repository, env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _exercise_chat_bounds(self, scaling):
        import ctypes
        import ttkbootstrap as ttk
        from pages.ai_page import AIAssistantPage
        from ui.desktop_style import install_desktop_style
        from ui.theme import configure_design_system

        ctypes.windll.kernel32.SetErrorMode(2)
        root = ttk.Window(themename='flatly')
        errors = []
        root.report_callback_exception = lambda *error: errors.append(error)
        def settle():
            root.update_idletasks()
            root.after(180, root.quit)
            root.mainloop()
        try:
            root.tk.call('tk', 'scaling', scaling)
            configure_design_system(root)
            install_desktop_style(root)
            host = ttk.Frame(root, padding=20)
            host.pack(fill='both', expand=True)
            with patch('pages.ai_page.safe_init_loaders'):
                page = AIAssistantPage(host)
            question = '今年业务做得最多的是哪个客户？请按已经确认的收入金额比较，不用合同或回款代替。' * 4
            page.render_messages([
                {'role': 'user', 'content': question},
                {'role': 'assistant', 'message_type': 'answer', 'content':
                 '收入最多的是 **示例客户甲**，金额 **¥1,000.00**。\n\n范围：今年\n口径：确认收入。',
                 'metadata': {'question': question, 'sources': [{'module': '确认收入', 'label': '收入明细'}]}},
            ])
            for size, side in (('1200x800', None), ('980x760', 'history'), ('980x760', 'context')):
                root.geometry(size)
                if side:
                    page._toggle_side_panel(side)
                settle()
                bubble = page._user_bubbles[0]
                label = bubble.winfo_children()[0]
                self.assertEqual(label.cget('text'), question)
                self.assertLessEqual(label.winfo_width(), bubble.winfo_width())
                self.assertLessEqual(label.winfo_height(), bubble.winfo_height())
                self.assertLessEqual(int(label.cget('wraplength')), label.winfo_width())
                self.assertGreater(label.winfo_reqheight(), 40, 'Long question failed to wrap')
                self.assertLessEqual(bubble.winfo_x() + bubble.winfo_width(), bubble.master.winfo_width())
                for widget in (page.input_text, page.send_btn):
                    self.assertTrue(widget.winfo_ismapped())
                    self.assertGreaterEqual(widget.winfo_rootx(), root.winfo_rootx())
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), root.winfo_rootx() + root.winfo_width())
                    self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), root.winfo_rooty() + root.winfo_height())
                page.thread.yview_moveto(1)
                settle()
                outer = page.thread.winfo_children()[-1]
                actions = outer.winfo_children()[-1].winfo_children()[-1]
                viewport = page.thread.container
                self.assertGreaterEqual(actions.winfo_rooty(), viewport.winfo_rooty())
                self.assertLessEqual(actions.winfo_rooty() + actions.winfo_height(), viewport.winfo_rooty() + viewport.winfo_height())
                self.assertFalse(errors, errors)
        finally:
            root.destroy()

    def test_report_table_scrolls_and_input_remains_accessible(self):
        import ctypes
        import ttkbootstrap as ttk
        from pages.ai_page import AIAssistantPage
        from ui.assistant_content import AnswerText, MetricStrip
        from ui.theme import configure_design_system

        ctypes.windll.kernel32.SetErrorMode(2)
        root = ttk.Window(themename="flatly")
        errors = []
        root.report_callback_exception = lambda *error: errors.append(error)
        def settle():
            root.update()
            root.after(100, root.quit)
            root.mainloop()
        try:
            root.geometry("1200x800")
            configure_design_system(root)
            host = ttk.Frame(root, padding=20)
            host.pack(fill="both", expand=True)
            with patch("pages.ai_page.safe_init_loaders"):
                page = AIAssistantPage(host)
            settle()
            page.render_messages([{"role": "assistant", "message_type": "answer",
                                   "content": profit_answer(), "metadata": {"answer_mode": "local"}}])
            settle()
            self.assertEqual(len(page._answer_tables), 1)
            table = page._answer_tables[0]
            self.assertEqual(len(table.tree.get_children()), 12)
            self.assertEqual(int(table.tree.cget("height")), 8)
            table.tree.yview_moveto(1)
            self.assertGreater(table.tree.yview()[0], 0)
            self.assertLess(table.tree.xview()[1], 1)
            table.tree.xview_moveto(1)
            self.assertGreater(table.tree.xview()[0], 0)
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            strips = [widget for widget in descendants(page.thread) if isinstance(widget, MetricStrip)]
            for size, side in (("1200x800", None), ("980x760", "history"), ("980x760", "context")):
                root.geometry(size)
                if side:
                    page._toggle_side_panel(side)
                settle()
                self.assertTrue(page.input_text.winfo_ismapped())
                self.assertLessEqual(page.input_text.winfo_rooty() + page.input_text.winfo_height(), root.winfo_rooty() + root.winfo_height())
                for strip in strips:
                    for card in strip._cards:
                        self.assertLessEqual(card.winfo_x() + card.winfo_width(), strip.winfo_width())
                for widget in descendants(page.thread):
                    if isinstance(widget, AnswerText):
                        self.assertEqual(widget.cget("state"), "disabled")
                        self.assertEqual(int(widget.cget("height")), (widget.count("1.0", "end-1c", "displaylines") or (0,))[0] + 1)
            self.assertFalse(errors, errors)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
