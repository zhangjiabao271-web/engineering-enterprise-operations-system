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

    def test_no_invoice_project_can_enter_contract_income_and_receipt_chain(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project({
            "name": f"不开票合同-{suffix}", "business_mode": "cash",
            "invoice_policy": "not_required", "cash_agreed_amount": "86000",
            "status": "进行中", "customer_name": f"客户-{suffix}",
        })
        project = self.projects.get_project(project_id)
        contract_id = self.contracts.create_contract({
            "name": f"不开票合同-{suffix}", "sign_date": "2026-09-11",
            "amount": "86000", "customer_partner_id": project["customer_partner_id"],
        })
        self.contracts.create_allocation({
            "contract_id": contract_id, "project_id": project_id, "amount": "86000",
        })
        updated = self.projects.get_project(project_id)
        self.assertEqual(updated["business_mode"], "contract")
        self.assertEqual(updated["invoice_policy"], "not_required")
        self.assertEqual(updated["cash_agreed_amount_minor"], 8600000)
        settlement_id = self.contracts.create_settlement({
            "contract_id": contract_id, "project_id": project_id,
            "amount": "86000", "settlement_date": "2026-09-11",
        })
        with self.assertRaises(ValueError):
            self.contracts.create_settlement({
                "contract_id": contract_id, "project_id": project_id,
                "amount": "1", "settlement_date": "2026-09-11",
            })
        self.finance.create_receipt({
            "contract_id": contract_id, "project_id": project_id,
            "settlement_id": settlement_id, "amount": "60000",
            "receipt_date": "2026-09-11", "payment_method": "银行转账",
        })
        settlement = self.contracts.get_settlement(settlement_id)
        self.assertEqual(settlement["source_type"], "contract")
        self.assertEqual(settlement["invoice_policy"], "not_required")

    def test_contract_link_does_not_reassign_existing_cash_income(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project({
            "name": f"保留历史-{suffix}", "business_mode": "cash",
            "invoice_policy": "not_required", "status": "进行中",
        })
        settlement_id = self.contracts.create_settlement({
            "project_id": project_id, "amount": "100", "settlement_date": "2026-09-11",
        })
        contract_id = self.contracts.create_contract({
            "name": f"待核对-{suffix}", "amount": "100", "sign_date": "2026-09-11",
        })
        with self.assertRaisesRegex(ValueError, "历史记录的合同归属"):
            self.contracts.create_allocation({
                "contract_id": contract_id, "project_id": project_id, "amount": "100",
            })
        self.assertEqual(self.projects.get_project(project_id)["business_mode"], "cash")
        self.assertIsNone(self.contracts.get_settlement(settlement_id)["contract_id"])
        self.assertFalse([r for r in self.contracts.list_allocations() if r["contract_id"] == contract_id])

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

    def test_cash_project_agreed_amount_limits_create_and_update(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project(
            {
                "name": f"现金约定总额-{suffix}",
                "project_code": f"CASH-LIMIT-{suffix}",
                "business_mode": "cash",
                "invoice_policy": "not_required",
                "cash_agreed_amount": "1000.00",
                "status": "进行中",
            }
        )
        first_id = self.contracts.create_settlement(
            {
                "project_id": project_id,
                "settlement_date": "2026-08-20",
                "amount": "600.00",
            }
        )
        self.contracts.create_settlement(
            {
                "project_id": project_id,
                "settlement_date": "2026-08-21",
                "amount": "400.00",
            }
        )

        project = self.projects.get_project(project_id)
        self.assertEqual(project["cash_agreed_amount_minor"], 100_000)
        self.assertEqual(project["cash_confirmed_minor"], 100_000)
        with self.assertRaisesRegex(ValueError, "约定总额"):
            self.contracts.create_settlement(
                {
                    "project_id": project_id,
                    "settlement_date": "2026-08-22",
                    "amount": "0.01",
                }
            )
        with self.assertRaisesRegex(ValueError, "约定总额"):
            self.contracts.update_settlement(
                first_id,
                {
                    "project_id": project_id,
                    "settlement_date": "2026-08-20",
                    "amount": "600.01",
                },
            )

    def test_cash_project_without_agreed_amount_allows_multiple_confirmations(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project(
            {
                "name": f"现金无上限-{suffix}",
                "project_code": f"CASH-OPEN-{suffix}",
                "business_mode": "cash",
                "invoice_policy": "not_required",
                "status": "进行中",
            }
        )
        for day in ("2026-08-20", "2026-08-21"):
            self.contracts.create_settlement(
                {
                    "project_id": project_id,
                    "settlement_date": day,
                    "amount": "750.00",
                }
            )
        project = self.projects.get_project(project_id)
        self.assertIsNone(project["cash_agreed_amount_minor"])
        self.assertEqual(project["cash_confirmed_minor"], 150_000)

    def test_cash_project_limit_cannot_be_reduced_below_confirmed_income(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project(
            {
                "name": f"现金调整上限-{suffix}",
                "project_code": f"CASH-ADJUST-{suffix}",
                "business_mode": "cash",
                "invoice_policy": "not_required",
                "cash_agreed_amount": "1000.00",
                "status": "进行中",
            }
        )
        self.contracts.create_settlement(
            {
                "project_id": project_id,
                "settlement_date": "2026-08-20",
                "amount": "750.00",
            }
        )
        current = self.projects.get_project(project_id)
        with self.assertRaisesRegex(ValueError, "不能低于当前有效完工确认"):
            self.projects.update_project(
                project_id,
                {
                    "project_code": current["project_code"],
                    "name": current["name"],
                    "customer_name": current["customer_name"],
                    "customer_partner_id": current["customer_partner_id"],
                    "status": current["status"],
                    "business_mode": "cash",
                    "invoice_policy": "not_required",
                    "cash_agreed_amount": "749.99",
                },
            )
        self.assertEqual(
            self.projects.get_project(project_id)["cash_agreed_amount_minor"],
            100_000,
        )

    def test_contract_project_without_invoice_can_convert_to_cash(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project(
            {
                "name": f"正式转零星现金-{suffix}",
                "project_code": f"CONVERT-CASH-{suffix}",
                "business_mode": "contract",
                "invoice_policy": "required",
                "status": "进行中",
            }
        )
        contract_id = self.contracts.create_contract(
            {
                "contract_no": f"CONVERT-CONTRACT-{suffix}",
                "name": f"待解除合同-{suffix}",
                "contract_type": "project",
                "pricing_mode": "fixed",
                "sign_date": "2026-08-22",
                "amount": "1000.00",
                "status": "active",
            }
        )
        allocation_id = self.contracts.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "amount": "1000.00",
            }
        )
        settlement_id = self.contracts.create_settlement(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-22",
                "amount": "1000.00",
            }
        )
        receipt_id = self.finance.create_receipt(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_id": settlement_id,
                "receipt_date": "2026-08-22",
                "amount": "600.00",
            }
        )

        current = self.projects.get_project(project_id)
        self.projects.update_project(
            project_id,
            {
                "project_code": current["project_code"],
                "name": current["name"],
                "customer_name": current["customer_name"],
                "customer_partner_id": current["customer_partner_id"],
                "status": current["status"],
                "business_mode": "cash",
                "invoice_policy": "not_required",
            },
        )

        project = self.projects.get_project(project_id)
        self.assertEqual(project["business_mode"], "cash")
        self.assertEqual(project["invoice_policy"], "not_required")
        self.assertEqual(project["cash_agreed_amount_minor"], 100_000)
        settlement = self.contracts.get_settlement(settlement_id)
        self.assertIsNone(settlement["contract_id"])
        self.assertEqual(settlement["source_type"], "cash_job")
        receipt = self.finance.get_receipt(receipt_id)
        self.assertIsNone(receipt["contract_id"])
        self.assertIsNone(receipt["invoice_id"])
        self.assertEqual(receipt["settlement_id"], settlement_id)
        self.assertEqual(
            self.contracts.list_allocations(project_id=project_id), []
        )
        import db.connection as connection

        conn = connection.get_connection()
        try:
            statuses = conn.execute(
                """SELECT a.status AS allocation_status,
                          c.status AS contract_status
                   FROM contract_project_allocations a
                   JOIN contracts c ON c.id=a.contract_id
                   WHERE a.id=?""",
                (allocation_id,),
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(statuses["allocation_status"], "void")
        self.assertEqual(statuses["contract_status"], "active")
        summary = self.finance.get_finance_dashboard(project_id)["summary"]
        self.assertEqual(summary["settlement_minor"], 100_000)
        self.assertEqual(summary["invoice_minor"], 0)
        self.assertEqual(summary["receipt_minor"], 60_000)
        self.assertEqual(summary["receivable_minor"], 40_000)

    def test_annual_contract_remains_active_after_project_converts_to_cash(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project(
            {
                "name": f"框架项目转现金-{suffix}",
                "project_code": f"ANNUAL-CASH-{suffix}",
                "business_mode": "contract",
                "invoice_policy": "required",
                "status": "进行中",
            }
        )
        contract_id = self.contracts.create_contract(
            {
                "contract_no": f"ANNUAL-CONTRACT-{suffix}",
                "name": f"保留年度框架-{suffix}",
                "contract_type": "annual",
                "pricing_mode": "actual",
                "sign_date": "2026-08-23",
                "status": "active",
            }
        )
        self.contracts.create_allocation(
            {"contract_id": contract_id, "project_id": project_id}
        )

        current = self.projects.get_project(project_id)
        self.projects.update_project(
            project_id,
            {
                "project_code": current["project_code"],
                "name": current["name"],
                "customer_name": current["customer_name"],
                "customer_partner_id": current["customer_partner_id"],
                "status": current["status"],
                "business_mode": "cash",
                "invoice_policy": "not_required",
            },
        )

        import db.connection as connection

        conn = connection.get_connection()
        try:
            contract_status = conn.execute(
                "SELECT status FROM contracts WHERE id=?", (contract_id,)
            ).fetchone()["status"]
        finally:
            conn.close()
        self.assertEqual(contract_status, "active")

    def test_contract_project_with_invoice_cannot_convert_to_cash(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project(
            {
                "name": f"已开票正式工程-{suffix}",
                "project_code": f"INVOICED-FORMAL-{suffix}",
                "business_mode": "contract",
                "invoice_policy": "required",
                "status": "进行中",
            }
        )
        contract_id = self.contracts.create_contract(
            {
                "contract_no": f"INVOICED-CONTRACT-{suffix}",
                "name": f"已开票合同-{suffix}",
                "contract_type": "project",
                "pricing_mode": "fixed",
                "sign_date": "2026-08-22",
                "amount": "1000.00",
                "status": "active",
            }
        )
        self.contracts.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "amount": "1000.00",
            }
        )
        settlement_id = self.contracts.create_settlement(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-22",
                "amount": "1000.00",
            }
        )
        self.finance.create_invoice(
            {
                "invoice_no": f"INVOICED-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_id": settlement_id,
                "invoice_date": "2026-08-22",
                "amount": "1000.00",
            }
        )
        current = self.projects.get_project(project_id)

        with self.assertRaisesRegex(ValueError, "已有有效发票"):
            self.projects.update_project(
                project_id,
                {
                    "project_code": current["project_code"],
                    "name": current["name"],
                    "customer_name": current["customer_name"],
                    "customer_partner_id": current["customer_partner_id"],
                    "status": current["status"],
                    "business_mode": "cash",
                    "invoice_policy": "not_required",
                },
            )
        self.assertEqual(
            self.projects.get_project(project_id)["business_mode"], "contract"
        )
        settlement = self.contracts.get_settlement(settlement_id)
        self.assertEqual(settlement["contract_id"], contract_id)
        self.assertEqual(settlement["source_type"], "contract")

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

    def test_cash_receipt_cannot_create_confirmation_above_agreed_amount(self):
        suffix = uuid4().hex[:8]
        project_id = self.projects.create_project(
            {
                "name": f"现金回款上限-{suffix}",
                "project_code": f"CASH-RECEIPT-LIMIT-{suffix}",
                "business_mode": "cash",
                "invoice_policy": "not_required",
                "cash_agreed_amount": "1000.00",
                "status": "已完工",
            }
        )
        self.contracts.create_settlement(
            {
                "project_id": project_id,
                "settlement_date": "2026-08-20",
                "amount": "800.00",
            }
        )

        with self.assertRaisesRegex(ValueError, "约定总额"):
            self.finance.create_receipt(
                {
                    "project_id": project_id,
                    "receipt_date": "2026-08-21",
                    "settlement_date": "2026-08-21",
                    "settlement_amount": "200.01",
                    "amount": "100.00",
                }
            )

        settlements = self.contracts.list_settlements(project_id=project_id)
        self.assertEqual(len(settlements), 1)
        self.assertEqual(self.finance.list_receipts(project_id), [])

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
