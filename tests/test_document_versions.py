"""Documented migration versions must follow the code registry."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class DocumentVersionTests(unittest.TestCase):
    def test_declared_migration_versions_match_registry(self):
        from db.migrations import MIGRATIONS

        expected = max(number for number, _name, _migrate in MIGRATIONS)
        architecture = (ROOT / "ARCHITECTURE_V4.md").read_text(encoding="utf-8")
        database = (ROOT / "DATABASE_ARCHITECTURE.md").read_text(encoding="utf-8")

        declared = re.search(
            r"版本：\d+\.\d+（数据库迁移 (\d+)）", architecture
        )
        self.assertIsNotNone(
            declared, "ARCHITECTURE_V4.md 缺少“版本：x.y（数据库迁移 N）”声明"
        )
        self.assertEqual(int(declared.group(1)), expected)

        target = re.search(
            r"当前代码目标迁移版本为 `(\d+)`", database
        )
        self.assertIsNotNone(
            target, "DATABASE_ARCHITECTURE.md 缺少“当前代码目标迁移版本为 N”声明"
        )
        self.assertEqual(int(target.group(1)), expected)

    def test_migration_numbers_are_unique_and_ascending(self):
        from db.migrations import MIGRATIONS

        numbers = [number for number, _name, _migrate in MIGRATIONS]
        self.assertEqual(numbers, sorted(numbers))
        self.assertEqual(len(numbers), len(set(numbers)))

    def test_product_version_has_a_delivered_section(self):
        prd = (ROOT / "PRODUCT_PRD_V4.md").read_text(encoding="utf-8")
        version = re.search(r"版本：(\d+\.\d+)", prd)
        self.assertIsNotNone(version, "PRODUCT_PRD_V4.md 缺少版本声明")
        major_minor = version.group(1)
        self.assertIn(
            f"### V{major_minor} ",
            prd,
            f"PRD 声明版本 {major_minor}，但“已交付范围”缺少 ### V{major_minor} 小节",
        )


if __name__ == "__main__":
    unittest.main()
