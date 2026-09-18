import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


class CustomerDashboardSummaryTests(unittest.TestCase):
    def setUp(self):
        from services import master_data_service

        self.summarize = master_data_service.summarize_customer_business

    def test_summary_reconciles_totals_shares_counts_and_rankings(self):
        summary = self.summarize(
            [
                {
                    "id": 1,
                    "name": "甲客户",
                    "yearly_business_minor": 60_000,
                    "current_receivable_minor": 10_000,
                },
                {
                    "id": 2,
                    "name": "乙客户",
                    "yearly_business_minor": 40_000,
                    "current_receivable_minor": 30_000,
                },
                {
                    "id": 3,
                    "name": "无业务客户",
                    "yearly_business_minor": 0,
                    "current_receivable_minor": 0,
                },
            ]
        )

        self.assertEqual(summary["total_income_minor"], 100_000)
        self.assertEqual(summary["total_receivable_minor"], 40_000)
        self.assertEqual(summary["income_customer_count"], 2)
        self.assertEqual(summary["receivable_customer_count"], 2)
        self.assertEqual(
            [row["label"] for row in summary["income_rows"]],
            ["甲客户", "乙客户"],
        )
        self.assertEqual(
            [row["label"] for row in summary["receivable_rows"]],
            ["乙客户", "甲客户"],
        )
        self.assertAlmostEqual(
            sum(row["share_percent"] for row in summary["income_rows"]),
            100.0,
        )
        self.assertAlmostEqual(
            sum(row["share_percent"] for row in summary["receivable_rows"]),
            100.0,
        )

    def test_summary_handles_zero_denominators(self):
        summary = self.summarize(
            [{"id": 1, "name": "零业务客户"}]
        )

        self.assertEqual(summary["total_income_minor"], 0)
        self.assertEqual(summary["total_receivable_minor"], 0)
        self.assertEqual(summary["income_rows"], [])
        self.assertEqual(summary["receivable_rows"], [])

    def test_summary_prefers_customer_short_name_for_chart_labels(self):
        summary = self.summarize(
            [{
                "id": 1,
                "name": "完整客户主体名称有限公司",
                "short_name": "客户简称",
                "yearly_business_minor": 10_000,
            }]
        )

        self.assertEqual(summary["income_rows"][0]["label"], "客户简称")

    def test_donut_condenses_minor_customers_without_losing_amount(self):
        from ui.charts import DonutBreakdown

        items = [(f"客户{index}", index * 100) for index in range(1, 8)]
        result = DonutBreakdown._condense_items(items, 5, "其他客户")

        self.assertEqual(len(result), 6)
        self.assertEqual(result[-1][0], "其他客户")
        self.assertEqual(sum(amount for _label, amount in result), 2_800)


class CustomerBusinessMetricTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="customer_metrics_")
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
                import database as _database
                _database.init_db()
            finally:
                _conn_module.DB_PATH, _runner_module.DB_PATH = _saved_paths

        import db.connection as connection
        from db.migration_runner import run_migrations

        cls.original_db_path = connection.DB_PATH
        run_migrations(cls.test_db)
        connection.DB_PATH = cls.test_db

        from services import (
            contract_service,
            finance_service,
            master_data_service,
            project_service,
        )

        cls.contracts = contract_service
        cls.finance = finance_service
        cls.partners = master_data_service
        cls.projects = project_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def test_customer_overview_separates_selected_year_from_current_balance(self):
        suffix = uuid4().hex[:8]
        customer_name = f"客户经营指标-{suffix}"
        customer_id = self.partners.create_business_partner(
            {
                "legal_name": customer_name,
                "roles": {"customer"},
                "status": "active",
                "entity_type": "individual_business",
                "contact_name": "经营联系人",
                "contact_phone": "13800000000",
            }
        )
        project_id = self.projects.create_project(
            {
                "name": f"客户经营项目-{suffix}",
                "project_code": f"CUSTOMER-{suffix}",
                "customer_name": customer_name,
                "customer_partner_id": customer_id,
                "business_mode": "cash",
                "invoice_policy": "not_required",
                "status": "进行中",
            }
        )
        self.contracts.create_settlement(
            {
                "project_id": project_id,
                "settlement_date": "2098-12-31",
                "amount": "500.00",
                "basis": "以前年度业务",
            }
        )
        current_settlement_id = self.contracts.create_settlement(
            {
                "project_id": project_id,
                "settlement_date": "2099-02-01",
                "amount": "1000.00",
                "basis": "本年度业务",
            }
        )
        self.finance.create_receipt(
            {
                "project_id": project_id,
                "settlement_id": current_settlement_id,
                "receipt_date": "2099-03-05",
                "amount": "400.00",
            }
        )

        overview = next(
            row for row in self.partners.list_customers(year=2099)
            if row["id"] == customer_id
        )
        self.assertEqual(overview["yearly_business_minor"], 100_000)
        self.assertEqual(overview["yearly_receipt_minor"], 40_000)
        self.assertEqual(overview["current_receivable_minor"], 110_000)
        self.assertEqual(overview["active_project_count"], 1)
        self.assertEqual(overview["latest_business_date"], "2099-03-05")

        prior_year = next(
            row for row in self.partners.list_customers(year=2098)
            if row["id"] == customer_id
        )
        self.assertEqual(prior_year["yearly_business_minor"], 50_000)
        self.assertEqual(prior_year["yearly_receipt_minor"], 0)
        self.assertEqual(prior_year["current_receivable_minor"], 110_000)

        detail = self.partners.get_customer_business_detail(customer_id, 2099)
        self.assertEqual(detail["summary"]["yearly_business_minor"], 100_000)
        self.assertEqual(detail["summary"]["yearly_receipt_minor"], 40_000)
        self.assertEqual(detail["summary"]["current_receivable_minor"], 110_000)
        project = next(
            row for row in detail["projects"]
            if row["project_id"] == project_id
        )
        self.assertEqual(project["current_receivable_minor"], 110_000)
        self.assertIn(2099, self.partners.list_customer_business_years())

    def test_invalid_business_year_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "经营年度"):
            self.partners.list_customers(year="不是年份")


if __name__ == "__main__":
    unittest.main()
