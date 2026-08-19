import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


class CashProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="cash_projects_")
        cls.test_db = Path(cls.temp_dir.name) / "supplier_data.db"
        source_db = Path(__file__).resolve().parent.parent / "supplier_data.db"
        if source_db.exists():
            shutil.copy2(source_db, cls.test_db)
        else:
            # 开源环境无生产库时，从空库初始化基础表并跑全量迁移构建测试库
            import db.connection as _conn_module
            import db.migration_runner as _runner_module
            _saved_conn_path = _conn_module.DB_PATH
            _saved_runner_path = _runner_module.DB_PATH
            _conn_module.DB_PATH = cls.test_db
            _runner_module.DB_PATH = cls.test_db
            try:
                import database as _database
                _database.init_db()  # 建基础表 + run_migrations()
            finally:
                _conn_module.DB_PATH = _saved_conn_path
                _runner_module.DB_PATH = _saved_runner_path

        import db.connection as connection
        from db.migration_runner import run_migrations

        cls.original_db_path = connection.DB_PATH
        run_migrations(cls.test_db)
        connection.DB_PATH = cls.test_db

        from services import (
            contract_service,
            data_governance_service,
            finance_service,
            project_profit_service,
            project_service,
        )

        cls.contracts = contract_service
        cls.finance = finance_service
        cls.governance = data_governance_service
        cls.profit = project_profit_service
        cls.projects = project_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def test_cash_project_income_receipt_profit_and_governance_chain(self):
        suffix = uuid4().hex[:8]
        before = self.governance.get_governance_summary()
        project_id = self.projects.create_project(
            {
                "name": f"私人厂房零星工程-{suffix}",
                "project_code": f"CASH-{suffix}",
                "customer_name": f"私人加工厂-{suffix}",
                "customer_entity_type": "individual_business",
                "business_mode": "cash",
                "invoice_policy": "not_required",
                "status": "进行中",
            }
        )
        project = self.projects.get_project(project_id)
        self.assertEqual(project["business_mode"], "cash")
        self.assertEqual(project["invoice_policy"], "not_required")
        self.assertEqual(project["customer_entity_type"], "individual_business")

        after_project = self.governance.get_governance_summary()
        self.assertEqual(
            after_project["missing_contract_count"],
            before["missing_contract_count"],
        )

        settlement_id = self.contracts.create_settlement(
            {
                "project_id": project_id,
                "settlement_date": "2026-08-14",
                "amount": "3500.00",
                "basis": "现场完工并由客户确认",
            }
        )
        settlement = self.contracts.get_settlement(settlement_id)
        self.assertIsNone(settlement["contract_id"])
        self.assertEqual(settlement["source_type"], "cash_job")
        self.assertEqual(settlement["uninvoiced_minor"], 0)
        self.assertEqual(settlement["unreceived_minor"], 350_000)

        with self.assertRaisesRegex(ValueError, "先为该项目登记收入确认"):
            self.finance.create_invoice(
                {
                    "project_id": project_id,
                    "invoice_date": "2026-08-14",
                    "amount": "1.00",
                }
            )

        receipt_id = self.finance.create_receipt(
            {
                "project_id": project_id,
                "settlement_id": settlement_id,
                "receipt_date": "2026-08-14",
                "amount": "3000.00",
            }
        )
        receipt = next(
            row for row in self.finance.list_receipts(project_id)
            if row["id"] == receipt_id
        )
        self.assertIsNone(receipt["contract_id"])
        self.assertIsNone(receipt["invoice_id"])
        self.assertEqual(receipt["settlement_id"], settlement_id)
        self.assertEqual(receipt["payment_method"], "现金")

        dashboard = self.finance.get_finance_dashboard(project_id)
        summary = dashboard["summary"]
        self.assertEqual(summary["settlement_minor"], 350_000)
        self.assertEqual(summary["invoice_minor"], 0)
        self.assertEqual(summary["uninvoiced_minor"], 0)
        self.assertEqual(summary["unlinked_receipt_minor"], 0)
        self.assertEqual(summary["receipt_minor"], 300_000)
        self.assertEqual(summary["receivable_minor"], 50_000)

        profit = self.profit.get_project_summary(project_id)
        self.assertEqual(profit["settlement_minor"], 350_000)
        self.assertEqual(profit["receipt_minor"], 300_000)
        self.assertEqual(profit["receivable_minor"], 50_000)

        gaps = self.governance.list_fulfillment_gaps()
        self.assertTrue(
            any(
                row["issue_type"] == "零星工程待收款"
                and row["id"] == settlement_id
                and row["amount_minor"] == 50_000
                for row in gaps
            )
        )
        self.assertTrue(
            any(
                row["issue_type"] == "现金回款缺凭证"
                and row["id"] == receipt_id
                for row in gaps
            )
        )

        with self.assertRaisesRegex(ValueError, "不能超过已确认收入金额"):
            self.finance.create_receipt(
                {
                    "project_id": project_id,
                    "settlement_id": settlement_id,
                    "receipt_date": "2026-08-14",
                    "amount": "500.01",
                }
            )
        with self.assertRaisesRegex(ValueError, "不能低于已回款金额"):
            self.contracts.update_settlement(
                settlement_id,
                {
                    "project_id": project_id,
                    "settlement_date": "2026-08-14",
                    "amount": "2999.99",
                },
            )
        with self.assertRaisesRegex(ValueError, "已有发票或回款"):
            self.contracts.void_settlements([settlement_id])

    def test_contract_project_still_requires_contract_allocation(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project(
            {
                "name": f"正式合同回归-{suffix}",
                "project_code": f"FORMAL-{suffix}",
                "business_mode": "contract",
                "invoice_policy": "required",
                "status": "进行中",
            }
        )
        with self.assertRaisesRegex(ValueError, "必须选择合同项目分配"):
            self.contracts.create_settlement(
                {
                    "project_id": project_id,
                    "settlement_date": "2026-08-14",
                    "amount": "100.00",
                }
            )

    def test_receipt_can_atomically_create_cash_income_confirmation(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project(
            {
                "name": f"历史零星现金补录-{suffix}",
                "project_code": f"CASH-HISTORY-{suffix}",
                "customer_name": f"历史现金客户-{suffix}",
                "customer_entity_type": "individual_business",
                "business_mode": "cash",
                "invoice_policy": "not_required",
                "status": "已完工",
            }
        )

        receipt_id = self.finance.create_receipt(
            {
                "project_id": project_id,
                "receipt_date": "2026-08-13",
                "settlement_date": "2026-08-12",
                "settlement_amount": "5000.00",
                "amount": "3200.00",
                "settlement_basis": "历史回款补录同步确认",
            }
        )

        settlements = self.contracts.list_settlements(project_id=project_id)
        self.assertEqual(len(settlements), 1)
        settlement = settlements[0]
        self.assertEqual(settlement["source_type"], "cash_job")
        self.assertEqual(settlement["settlement_date"], "2026-08-12")
        self.assertEqual(settlement["amount_minor"], 500_000)
        self.assertEqual(settlement["unreceived_minor"], 180_000)

        receipt = next(
            row for row in self.finance.list_receipts(project_id)
            if row["id"] == receipt_id
        )
        self.assertEqual(receipt["settlement_id"], settlement["id"])
        self.assertEqual(receipt["allocated_amount_minor"], 320_000)
        self.assertEqual(receipt["payment_method"], "现金")

        summary = self.finance.get_finance_dashboard(project_id)["summary"]
        self.assertEqual(summary["settlement_minor"], 500_000)
        self.assertEqual(summary["invoice_minor"], 0)
        self.assertEqual(summary["receipt_minor"], 320_000)
        self.assertEqual(summary["receivable_minor"], 180_000)

    def test_failed_cash_receipt_rolls_back_new_income_confirmation(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project(
            {
                "name": f"零星现金原子性-{suffix}",
                "project_code": f"CASH-ATOMIC-{suffix}",
                "business_mode": "cash",
                "invoice_policy": "not_required",
                "status": "已完工",
            }
        )

        with self.assertRaisesRegex(ValueError, "不能超过已确认收入金额"):
            self.finance.create_receipt(
                {
                    "project_id": project_id,
                    "receipt_date": "2026-08-14",
                    "settlement_date": "2026-08-14",
                    "settlement_amount": "100.00",
                    "amount": "100.01",
                }
            )

        self.assertEqual(
            self.contracts.list_settlements(project_id=project_id), []
        )
        self.assertEqual(self.finance.list_receipts(project_id), [])

    def test_cash_receipt_can_be_modified_within_original_confirmation(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project(
            {
                "name": f"零星现金回款修改-{suffix}",
                "project_code": f"CASH-EDIT-{suffix}",
                "business_mode": "cash",
                "invoice_policy": "not_required",
                "status": "已完工",
            }
        )
        settlement_id = self.contracts.create_settlement(
            {
                "project_id": project_id,
                "settlement_date": "2026-08-14",
                "amount": "1000.00",
            }
        )
        receipt_id = self.finance.create_receipt(
            {
                "project_id": project_id,
                "settlement_id": settlement_id,
                "receipt_date": "2026-08-14",
                "amount": "600.00",
            }
        )

        self.finance.update_receipt(
            receipt_id,
            {
                "receipt_no": "CASH-EDIT-RECEIPT",
                "receipt_date": "2026-08-13",
                "amount": "800.00",
                "payer_name": "修改后的现金客户",
                "payment_method": "现金",
            },
        )
        receipt = self.finance.get_receipt(receipt_id)
        self.assertEqual(receipt["receipt_date"], "2026-08-13")
        self.assertEqual(receipt["allocated_amount_minor"], 80_000)
        self.assertEqual(receipt["settlement_id"], settlement_id)
        self.assertEqual(receipt["invoice_id"], None)
        self.assertEqual(
            self.contracts.get_settlement(settlement_id)["unreceived_minor"],
            20_000,
        )
        with self.assertRaisesRegex(ValueError, "不能超过已确认收入金额"):
            self.finance.update_receipt(
                receipt_id,
                {
                    "receipt_no": "CASH-EDIT-RECEIPT",
                    "receipt_date": "2026-08-13",
                    "amount": "1000.01",
                },
            )
        self.assertEqual(
            self.finance.get_receipt(receipt_id)["allocated_amount_minor"],
            80_000,
        )


if __name__ == "__main__":
    unittest.main()
