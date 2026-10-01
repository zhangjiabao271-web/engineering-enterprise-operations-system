import sqlite3
import unittest

from services._constraint_errors import translate_constraints


class ConstraintTranslationTests(unittest.TestCase):
    def setUp(self):
        @translate_constraints(
            duplicate="重复记录",
            related="关联记录已变化",
            invalid="输入不符合规则",
        )
        def fail(detail, error_type=sqlite3.IntegrityError):
            raise error_type(detail)

        self.fail = fail

    def test_expected_constraints_are_business_errors(self):
        cases = (
            ("UNIQUE constraint failed: fund_accounts.name", "重复记录"),
            ("FOREIGN KEY constraint failed", "关联记录已变化"),
            ("CHECK constraint failed: amount_minor>0", "输入不符合规则"),
            ("NOT NULL constraint failed: workers.name", "输入不符合规则"),
            ("该月已有工资付款，不能删除工天", "该月已有工资付款，不能删除工天"),
        )
        for detail, expected in cases:
            with self.subTest(detail=detail), self.assertRaisesRegex(ValueError, expected):
                self.fail(detail)

    def test_unexpected_database_errors_keep_original_type(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.fail("unrecognized trigger failure")
        with self.assertRaises(sqlite3.OperationalError):
            self.fail("database is locked", sqlite3.OperationalError)


if __name__ == "__main__":
    unittest.main()
