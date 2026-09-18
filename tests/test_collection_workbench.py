import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4


class CollectionWorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="collection_workbench_")
        cls.test_db = Path(cls.temp_dir.name) / "supplier_data.db"
        source_db = Path(__file__).resolve().parent.parent / "supplier_data.db"
        from db.backup import backup_database
        _oss_source_db = source_db
        if _oss_source_db.exists():
            backup_database(_oss_source_db, cls.test_db)
        else:
            # 开源环境无生产库：从空库初始化基础表并跑全量迁移构建测试库
            import db.connection as _conn_module
            import db.migration_runner as _runner_module
            _saved_paths = (_conn_module.DB_PATH, _runner_module.DB_PATH)
            _conn_module.DB_PATH = cls.test_db
            _runner_module.DB_PATH = cls.test_db
            try:
                from db.schema import init_db as _init_db
                _init_db()
            finally:
                _conn_module.DB_PATH, _runner_module.DB_PATH = _saved_paths

        import db.connection as connection
        from db.migration_runner import run_migrations

        cls.original_db_path = connection.DB_PATH
        run_migrations(cls.test_db)
        connection.DB_PATH = cls.test_db

        from services import (
            collection_service,
            contract_service,
            finance_service,
            operations_service,
            project_service,
        )

        cls.collection = collection_service
        cls.contracts = contract_service
        cls.finance = finance_service
        cls.operations = operations_service
        cls.projects = project_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def _cash_receivable(self, suffix, *, customer="现金客户", amount="1000.00"):
        project_id = self.projects.create_project(
            {
                "name": f"现金催款项目-{suffix}",
                "project_code": f"COLLECT-{suffix}",
                "customer_name": customer,
                "business_mode": "cash",
                "invoice_policy": "not_required",
                "status": "进行中",
            }
        )
        settlement_id = self.contracts.create_settlement(
            {
                "project_id": project_id,
                "settlement_date": (date.today() - timedelta(days=45)).isoformat(),
                "amount": amount,
            }
        )
        return project_id, settlement_id

    def test_cash_project_is_accountable_without_contract_or_invoice(self):
        suffix = uuid4().hex[:8]
        before = self.operations.get_executive_overview()
        project_id, _settlement_id = self._cash_receivable(suffix)

        overview = self.operations.get_executive_overview()
        row = next(
            item for item in overview["projects"]
            if item["project_id"] == project_id
        )
        self.assertTrue(row["is_accountable"])
        self.assertEqual(row["stage_code"], "accountable")
        self.assertNotIn("缺合同分配", row["gaps"])
        self.assertNotIn("缺开票记录", row["gaps"])
        self.assertEqual(
            overview["north_star"]["accountable_project_count"],
            before["north_star"]["accountable_project_count"] + 1,
        )

    def test_followup_plan_and_history_do_not_change_financial_facts(self):
        suffix = uuid4().hex[:8]
        project_id, settlement_id = self._cash_receivable(suffix)
        before = self.finance.get_finance_dashboard(project_id)["summary"]
        today = date.today().isoformat()
        promised = (date.today() - timedelta(days=1)).isoformat()

        self.collection.save_project_case(
            project_id,
            {
                "due_date": (date.today() - timedelta(days=45)).isoformat(),
                "promised_date": promised,
                "next_followup_date": today,
                "owner_name": "本人",
                "status": "promised",
                "overdue_reason": "客户付款流程延后",
                "next_action": "今天电话确认付款时间",
                "followup_date": today,
                "followup_content": "客户承诺昨天安排付款",
            },
        )

        case = self.collection.get_project_case(project_id)
        self.assertEqual(case["attention_status"], "承诺逾期")
        self.assertEqual(case["aging_bucket"], "31—60天")
        self.assertTrue(case["needs_action_today"])
        self.assertEqual(case["owner_name"], "本人")
        logs = self.collection.list_followup_logs(project_id)
        self.assertEqual(len(logs), 1)
        self.assertIn("承诺", logs[0]["content"])

        after = self.finance.get_finance_dashboard(project_id)["summary"]
        for key in ("settlement_minor", "invoice_minor", "receipt_minor"):
            self.assertEqual(after[key], before[key])
        settlement = self.contracts.get_settlement(settlement_id)
        self.assertEqual(settlement["amount_minor"], 100_000)

    def test_customer_view_aggregates_without_merging_project_cases(self):
        suffix = uuid4().hex[:8]
        customer = f"同客户-{suffix}"
        first_id, _ = self._cash_receivable(
            f"A-{suffix}", customer=customer, amount="600.00"
        )
        second_id, _ = self._cash_receivable(
            f"B-{suffix}", customer=customer, amount="400.00"
        )
        rows = [
            row for row in self.collection.list_project_cases()
            if row["project_id"] in {first_id, second_id}
        ]
        self.assertEqual(len(rows), 2)
        customers = self.collection.list_customer_cases(rows)
        self.assertEqual(len(customers), 1)
        self.assertEqual(customers[0]["project_count"], 2)
        self.assertEqual(customers[0]["receivable_minor"], 100_000)

    def test_project_without_income_cannot_create_collection_case(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project(
            {
                "name": f"无收入催款-{suffix}",
                "project_code": f"NO-INCOME-{suffix}",
                "status": "进行中",
            }
        )
        with self.assertRaisesRegex(ValueError, "尚无有效收入确认"):
            self.collection.save_project_case(
                project_id,
                {"status": "pending", "next_action": "不应保存"},
            )


if __name__ == "__main__":
    unittest.main()
