import unittest

from pages.ai_page import AIAssistantPage


class AISourceRenderingTests(unittest.TestCase):
    def setUp(self):
        self.page = AIAssistantPage.__new__(AIAssistantPage)

    def test_custom_source_money_uses_page_formatter(self):
        source = {
            "columns": [
                {"key": "label", "label": "项目"},
                {"key": "amount", "label": "回款", "kind": "money"},
            ],
            "kpis": [["回款合计", "¥123.45"]],
        }
        self.assertEqual(self.page._source_kpis(source), (("回款合计", "¥123.45"),))
        columns, _headings, _widths, numeric, values = self.page._source_table_spec(source)
        self.assertEqual(columns, ("label", "amount"))
        self.assertEqual(numeric, {"amount"})
        self.assertEqual(values({"label": "项目甲", "amount": 12345}), ("项目甲", "¥123.45"))

    def test_labor_source_rows_and_summary(self):
        source = {"view_type": "labor", "summary": {"total_minor": 25000, "work_days": 1.5}}
        self.assertEqual(self.page._source_kpis(source)[0], ("人工成本", "¥250.00"))
        columns, _headings, _widths, _numeric, values = self.page._source_table_spec(source)
        self.assertIn("amount", columns)
        row = values({"worker_name": "工人甲", "work_days": 0.5, "amount_minor": 12500})
        self.assertEqual(row[-1], "¥125.00")


if __name__ == "__main__":
    unittest.main()
