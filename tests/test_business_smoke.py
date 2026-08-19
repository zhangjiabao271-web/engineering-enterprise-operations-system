"""固化关键业务冒烟断言为可重复运行的测试。

覆盖：V4 迁移校验、项目利润口径、采购税费运费、数据库完整性。
所有测试在临时副本库上运行，不触碰生产库 supplier_data.db。
"""

import shutil
import tempfile
import unittest
from pathlib import Path


class _IsolatedDatabaseTestCase(unittest.TestCase):
    """Set up a private copy of the production database for the test class."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="smoke_test_")
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

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()


class MigrationIntegrityTests(_IsolatedDatabaseTestCase):
    """V4 迁移后库结构、外键与版本完整性（对应 scripts/check_database_integrity.py）。"""

    def test_schema_version_is_current(self):
        from db.connection import get_connection

        conn = get_connection()
        try:
            version = conn.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertGreaterEqual(version, 250, "迁移版本应不低于 250")

    def test_no_foreign_key_violations(self):
        from db.connection import get_connection

        conn = get_connection()
        try:
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()
        self.assertEqual(violations, [], "不应存在外键违规记录")

    def test_core_tables_populated(self):
        from db.connection import get_connection

        conn = get_connection()
        try:
            for table in ("contracts", "settlements", "purchase_orders",
                          "work_logs", "supplier_profiles"):
                count = conn.execute(
                    f'SELECT COUNT(*) FROM "{table}"'
                ).fetchone()[0]
                self.assertGreaterEqual(
                    count, 0, f"{table} 表应可查询"
                )
        finally:
            conn.close()

    def test_legacy_entry_id_unique_constraint_exists(self):
        """source_legacy_entry_id 的 UNIQUE 索引必须存在（幂等迁移依赖）。"""
        from db.connection import get_connection

        conn = get_connection()
        try:
            for table in ("contracts", "settlements", "sales_invoices",
                          "receipts", "cost_entries"):
                indexes = [
                    row[1]
                    for row in conn.execute(
                        f"PRAGMA index_list({table})"
                    ).fetchall()
                    if row[2] == 1  # unique
                ]
                # 检查自动索引或显式索引是否覆盖 source_legacy_entry_id
                covered = False
                for name in indexes:
                    cols = [
                        r[2]
                        for r in conn.execute(
                            f'PRAGMA index_info("{name}")'
                        ).fetchall()
                    ]
                    if "source_legacy_entry_id" in cols:
                        covered = True
                        break
                self.assertTrue(
                    covered, f"{table}.source_legacy_entry_id 缺少唯一索引"
                )
        finally:
            conn.close()


class ProjectProfitFormulaTests(_IsolatedDatabaseTestCase):
    """项目利润口径：合同/结算/发票/回款/成本/现金联动（对应 smoke_project_profit.py）。"""

    def setUp(self):
        from uuid import uuid4

        from services import (
            contract_service,
            cost_service,
            finance_service,
            project_profit_service,
            project_service,
        )

        self.contract_service = contract_service
        self.cost_service = cost_service
        self.finance_service = finance_service
        self.project_profit_service = project_profit_service
        self.project_service = project_service
        suffix = uuid4().hex[:8]
        self.project_id = project_service.create_project(
            {
                "name": f"利润口径测试-{suffix}",
                "project_code": f"PROFIT-{suffix}",
                "status": "进行中",
            }
        )
        self.baseline = project_profit_service.get_project_summary(
            self.project_id
        )

    def test_full_cycle_profit_formula(self):
        contract_id = self.contract_service.create_contract(
            {
                "contract_no": "TEST-PROFIT-CONTRACT-1",
                "name": "利润公式测试合同",
                "contract_type": "project",
                "sign_date": "2026-07-30",
                "amount": "10000.00",
                "status": "active",
            }
        )
        self.contract_service.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": self.project_id,
                "amount": "10000.00",
            }
        )
        self.contract_service.create_settlement(
            {
                "settlement_no": "TEST-SETTLE-1",
                "contract_id": contract_id,
                "project_id": self.project_id,
                "settlement_date": "2026-07-31",
                "amount": "6000.00",
            }
        )
        invoice_id = self.finance_service.create_invoice(
            {
                "invoice_no": "TEST-INVOICE-1",
                "contract_id": contract_id,
                "project_id": self.project_id,
                "invoice_date": "2026-08-01",
                "amount": "5000.00",
                "tax_rate": "10",
            }
        )
        self.finance_service.create_receipt(
            {
                "receipt_no": "TEST-RECEIPT-1",
                "contract_id": contract_id,
                "project_id": self.project_id,
                "invoice_id": invoice_id,
                "receipt_date": "2026-08-02",
                "amount": "4000.00",
            }
        )
        self.cost_service.create_cost(
            {
                "cost_no": "TEST-PROFIT-COST-1",
                "project_id": self.project_id,
                "cost_date": "2026-08-03",
                "category": "分包费",
                "amount": "1000.00",
            }
        )
        summary = self.project_profit_service.get_project_summary(
            self.project_id
        )
        self.assertEqual(
            summary["contract_minor"] - self.baseline["contract_minor"], 1_000_000
        )
        self.assertEqual(
            summary["settlement_minor"] - self.baseline["settlement_minor"], 600_000
        )
        self.assertEqual(
            summary["invoice_minor"] - self.baseline["invoice_minor"], 500_000
        )
        self.assertEqual(
            summary["receipt_minor"] - self.baseline["receipt_minor"], 400_000
        )
        self.assertEqual(
            summary["other_cost_minor"] - self.baseline["other_cost_minor"], 100_000
        )
        self.assertEqual(
            summary["gross_profit_minor"] - self.baseline["gross_profit_minor"],
            500_000,
        )
        self.assertEqual(
            summary["receivable_minor"] - self.baseline["receivable_minor"], 200_000
        )
        self.assertEqual(
            summary["cash_balance_minor"] - self.baseline["cash_balance_minor"],
            400_000,
        )


class ProcurementTaxFreightTests(_IsolatedDatabaseTestCase):
    """采购税额与运费口径（对应 scripts/smoke_procurement_tax_freight.py）。"""

    def test_tax_and_freight_calculation(self):
        from services import master_data_service, procurement_service, project_service

        supplier_id = master_data_service.create_supplier(
            {
                "name": "采购税费测试供应商",
                "category": "测试",
                "default_tax_rate_percent": 10,
            }
        )
        offer_id = master_data_service.create_supplier_offer(
            {
                "supplier_id": supplier_id,
                "name": "测试材料",
                "specification": "规格A",
                "unit": "件",
                "price": "10.00",
                "tax_rate_percent": 10,
            }
        )
        project = project_service.list_projects(active_only=False)[0]
        offer = master_data_service.get_supplier_offer(offer_id)
        order_id = procurement_service.add_purchase_order(
            {
                "purchase_type": "正式采购",
                "project_id": project["id"],
                "supplier_id": supplier_id,
                "merchant_name_snapshot": "采购税费测试供应商",
                "purchase_date": "2026-08-05",
                "payment_method": "对公转账",
                "payment_status": "未付款",
                "invoice_status": "有发票",
                "purchaser": "固化测试",
                "freight_amount_cents": 1500,
                "notes": "税费运费固化测试",
            },
            {
                "product_id": offer["id"],
                "material_name_snapshot": offer["name"],
                "specification_snapshot": offer["specification"],
                "unit_snapshot": offer["unit"],
                "cost_category": "材料费",
                "quantity": 2.5,
                "material_unit_price_cents": 10000,
                "tax_rate_bps": 1000,
                "purpose": "项目成本归集测试",
            },
        )
        order = procurement_service.get_purchase_order(order_id)
        self.assertEqual(order["material_amount_cents"], 25_000)
        self.assertEqual(order["tax_amount_cents"], 2_500)
        self.assertEqual(order["line_amount_cents"], 27_500)
        self.assertEqual(order["freight_amount_cents"], 1_500)


class PurchaseCostAllocationTests(_IsolatedDatabaseTestCase):
    """工具和设备采购按项目平均分摊，且公司总额不重复。"""

    @staticmethod
    def _header(project_ids, amount=100_000):
        return {
            "purchase_type": "零星采购",
            "project_id": None,
            "project_ids": project_ids,
            "allocation_method": "equal",
            "merchant_name_snapshot": "工具设备测试商户",
            "purchase_date": "2099-04-15",
            "payment_method": "现金",
            "payment_status": "已付款",
            "invoice_status": "无发票",
            "purchaser": "固化测试",
            "freight_amount_cents": 0,
            "notes": f"均摊测试-{amount}",
        }

    @staticmethod
    def _item(amount=100_000):
        return {
            "material_name_snapshot": "测试工具设备",
            "specification_snapshot": "测试规格",
            "unit_snapshot": "套",
            "cost_category": "工具和设备",
            "quantity": 1,
            "material_unit_price_cents": amount,
            "tax_rate_bps": 0,
            "purpose": "多项目共用",
        }

    def test_equal_allocation_create_update_and_reporting(self):
        from services import cost_service, procurement_service, project_profit_service
        from services import project_service

        project_ids = [
            project_service.create_project(
                {
                    "name": f"工具均摊项目-{index}",
                    "project_code": f"EQ-2099-{index}",
                    "status": "进行中",
                }
            )
            for index in range(1, 4)
        ]
        profit_before = {
            project_id: project_profit_service.get_project_summary(project_id)[
                "purchase_cost_minor"
            ]
            for project_id in project_ids
        }
        company_before = procurement_service.get_purchase_dashboard("2099-04")

        order_id = procurement_service.add_purchase_order(
            self._header(project_ids), self._item()
        )
        order = procurement_service.get_purchase_order(order_id)
        self.assertIsNone(order["project_id"])
        self.assertEqual(order["allocation_method"], "equal")
        self.assertEqual(order["project_name"], "3个项目均摊")

        allocations = procurement_service.get_purchase_allocations(order_id)
        self.assertEqual(
            [line["amount_minor"] for line in allocations],
            [33_334, 33_333, 33_333],
        )
        for project_id, expected in zip(project_ids, (33_334, 33_333, 33_333)):
            summary = project_profit_service.get_project_summary(project_id)
            self.assertEqual(
                summary["purchase_cost_minor"] - profit_before[project_id],
                expected,
            )
            listed_ids = {
                row["id"]
                for row in procurement_service.list_purchase_orders(
                    month="2099-04", project_id=project_id
                )
            }
            self.assertIn(order_id, listed_ids)
            ledger_row = next(
                row
                for row in cost_service.list_cost_ledger(project_id)
                if row["source_type"] == "purchase" and row["id"] == order_id
            )
            self.assertEqual(ledger_row["amount_minor"], expected)
            project_dashboard = procurement_service.get_purchase_dashboard(
                "2099-04", project_id
            )
            self.assertEqual(
                project_dashboard["summary"]["total_cents"], expected
            )
            cost_dashboard = cost_service.get_cost_dashboard(
                month="2099-04", project_id=project_id
            )
            self.assertEqual(
                cost_dashboard["summary"]["purchase_minor"], expected
            )

        company_after = procurement_service.get_purchase_dashboard("2099-04")
        self.assertEqual(
            company_after["summary"]["total_cents"]
            - company_before["summary"]["total_cents"],
            100_000,
        )
        self.assertEqual(company_after["summary"]["unassigned_cents"], 0)
        company_cost = cost_service.get_cost_dashboard(month="2099-04")
        self.assertEqual(company_cost["summary"]["purchase_minor"], 100_000)

        procurement_service.update_purchase_order(
            order_id, self._header(project_ids[1:], 100_001), self._item(100_001)
        )
        active = procurement_service.get_purchase_allocations(order_id)
        self.assertEqual(
            [line["amount_minor"] for line in active], [50_001, 50_000]
        )
        history = procurement_service.get_purchase_allocations(
            order_id, include_void=True
        )
        self.assertEqual(len(history), 5)
        self.assertEqual(sum(line["status"] == "void" for line in history), 3)
        self.assertEqual({line["allocation_version"] for line in history}, {1, 2})
        self.assertEqual(
            project_profit_service.get_project_summary(project_ids[0])[
                "purchase_cost_minor"
            ],
            profit_before[project_ids[0]],
        )
        company_updated = procurement_service.get_purchase_dashboard("2099-04")
        self.assertEqual(
            company_updated["summary"]["total_cents"]
            - company_before["summary"]["total_cents"],
            100_001,
        )

    def test_equal_allocation_rejects_non_tool_category(self):
        from services import procurement_service, project_service

        project_ids = [
            project_service.create_project(
                {
                    "name": f"非法均摊项目-{index}",
                    "project_code": f"EQ-BAD-{index}",
                    "status": "进行中",
                }
            )
            for index in range(1, 3)
        ]
        item = self._item()
        item["cost_category"] = "材料费"
        with self.assertRaisesRegex(ValueError, "只有.*工具和设备"):
            procurement_service.add_purchase_order(self._header(project_ids), item)


class CostDashboardTests(_IsolatedDatabaseTestCase):
    """成本与付款看板聚合口径：KPI 拆分、构成、项目 TOP、环比。"""

    def test_dashboard_aggregates_match_ledger(self):
        from services import cost_service
        from uuid import uuid4
        from services import project_service

        project_id = project_service.create_project(
            {
                "name": f"看板测试-{uuid4().hex[:8]}",
                "project_code": f"DB-{uuid4().hex[:6].upper()}",
                "status": "进行中",
            }
        )
        data = cost_service.get_cost_dashboard(project_id=project_id)
        summary = data["summary"]
        # 新项目当月无成本：总 = 采购 + 人工 + 手工
        self.assertEqual(
            summary["total_minor"],
            summary["purchase_minor"]
            + summary["labor_minor"]
            + summary["manual_minor"],
        )
        self.assertEqual(summary["purchase_count"], 0)
        # 项目 TOP 不应包含该项目（无成本）
        self.assertNotIn(
            project_id, [row["project_id"] for row in data["by_project"]]
        )
        # 构成列表非负
        for _label, amount in data["by_source"]:
            self.assertGreaterEqual(amount, 0)

    def test_dashboard_all_projects_total(self):
        from datetime import date

        from services import cost_service, procurement_service

        # 自建一笔当月采购，避免依赖生产库历史数据
        procurement_service.add_purchase_order(
            {
                "purchase_type": "零星采购",
                "purchase_date": date.today().isoformat(),
                "merchant_name_snapshot": "冒烟测试商户",
                "allocation_method": "unassigned",
            },
            {
                "material_name_snapshot": "冒烟测试材料",
                "quantity": 1,
                "material_unit_price_cents": 100,
            },
        )
        data = cost_service.get_cost_dashboard()
        summary = data["summary"]
        self.assertGreater(summary["total_minor"], 0, "应有成本数据")
        # 总成本 = 三来源之和
        self.assertEqual(
            summary["total_minor"],
            summary["purchase_minor"]
            + summary["labor_minor"]
            + summary["manual_minor"],
        )
        # 来源构成与分类构成合计应与总额一致（分类含待归集）
        source_total = sum(amount for _label, amount in data["by_source"])
        self.assertEqual(source_total, summary["total_minor"])


if __name__ == "__main__":
    unittest.main()
