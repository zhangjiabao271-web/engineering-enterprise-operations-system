import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


class FinanceDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="finance_dashboard_")
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
            finance_service,
            project_service,
        )

        cls.contract_service = contract_service
        cls.finance_service = finance_service
        cls.project_service = project_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def test_project_and_company_finance_totals(self):
        suffix = uuid4().hex[:8]
        project_id = self.project_service.create_project(
            {
                "name": f"财务看板测试-{suffix}",
                "project_code": f"FIN-{suffix}",
                "status": "进行中",
            }
        )
        contract_id = self.contract_service.create_contract(
            {
                "contract_no": f"FIN-CONTRACT-{suffix}",
                "name": "财务看板测试合同",
                "contract_type": "project",
                "sign_date": "2026-08-05",
                "amount": "10000.00",
                "status": "active",
            }
        )
        self.contract_service.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "amount": "8000.00",
            }
        )
        settlement_id = self.contract_service.create_settlement(
            {
                "settlement_no": f"FIN-SETTLEMENT-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-05",
                "amount": "6000.00",
            }
        )
        invoice_id = self.finance_service.create_invoice(
            {
                "invoice_no": f"FIN-INVOICE-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "invoice_date": "2026-08-05",
                "amount": "5000.00",
                "tax_rate": "10",
            }
        )
        receipt_id = self.finance_service.create_receipt(
            {
                "receipt_no": f"FIN-RECEIPT-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "invoice_id": invoice_id,
                "receipt_date": "2026-08-05",
                "amount": "4000.00",
            }
        )

        invoice = self.finance_service.get_invoice(invoice_id)
        self.assertEqual(invoice["received_minor"], 400_000)
        self.assertEqual(invoice["unreceived_minor"], 100_000)
        self.assertEqual(invoice["collection_status"], "部分回款")
        settlement = self.contract_service.get_settlement(settlement_id)
        self.assertEqual(settlement["received_minor"], 400_000)
        self.assertEqual(settlement["unreceived_minor"], 200_000)
        self.assertAlmostEqual(settlement["receipt_rate_percent"], 66.6667, 3)
        self.assertEqual(settlement["collection_status"], "部分回款")

        dashboard = self.finance_service.get_finance_dashboard(project_id)
        self.assertEqual(len(dashboard["projects"]), 1)
        summary = dashboard["summary"]
        self.assertEqual(summary["allocated_minor"], 800_000)
        self.assertEqual(summary["settlement_minor"], 600_000)
        self.assertEqual(summary["invoice_minor"], 500_000)
        self.assertEqual(summary["receipt_minor"], 400_000)
        self.assertEqual(summary["uninvoiced_minor"], 100_000)
        self.assertEqual(summary["receivable_minor"], 200_000)
        self.assertEqual(summary["unlinked_receipt_minor"], 0)
        self.assertAlmostEqual(summary["invoice_rate_percent"], 83.3333, 3)
        self.assertAlmostEqual(summary["receipt_rate_percent"], 66.6667, 3)

        self.finance_service.update_receipt(
            receipt_id,
            {
                "receipt_no": f"FIN-RECEIPT-{suffix}",
                "receipt_date": "2026-08-06",
                "invoice_id": invoice_id,
                "amount": "5000.00",
                "payer_name": "修改后的付款方",
                "payment_method": "票据",
                "notes": "回款修改测试",
            },
        )
        updated_receipt = self.finance_service.get_receipt(receipt_id)
        self.assertEqual(updated_receipt["receipt_date"], "2026-08-06")
        self.assertEqual(updated_receipt["allocated_amount_minor"], 500_000)
        self.assertEqual(updated_receipt["payer_name_snapshot"], "修改后的付款方")
        self.assertEqual(updated_receipt["payment_method"], "票据")
        settled_invoice = self.finance_service.get_invoice(invoice_id)
        self.assertEqual(settled_invoice["received_minor"], 500_000)
        self.assertEqual(settled_invoice["unreceived_minor"], 0)
        self.assertEqual(settled_invoice["collection_status"], "已结清")
        settlement = self.contract_service.get_settlement(settlement_id)
        self.assertEqual(settlement["received_minor"], 500_000)
        self.assertEqual(settlement["unreceived_minor"], 100_000)
        with self.assertRaisesRegex(ValueError, "不能超过发票金额"):
            self.finance_service.update_receipt(
                receipt_id,
                {
                    "receipt_no": f"FIN-RECEIPT-{suffix}",
                    "receipt_date": "2026-08-06",
                    "invoice_id": invoice_id,
                    "amount": "5000.01",
                },
            )
        self.assertEqual(
            self.finance_service.get_receipt(receipt_id)["allocated_amount_minor"],
            500_000,
        )

        with self.assertRaisesRegex(
            ValueError, "发票号码.*已经登记过.*财务看板测试"
        ):
            self.finance_service.create_invoice(
                {
                    "invoice_no": f"  FIN-INVOICE-{suffix}  ",
                    "contract_id": contract_id,
                    "project_id": project_id,
                    "invoice_date": "2026-08-06",
                    "amount": "1.00",
                    "tax_rate": "10",
                }
            )

        self.finance_service.update_invoice(
            invoice_id,
            {
                "invoice_no": f"FIN-INVOICE-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "invoice_date": "2026-08-06",
                "amount": "5000.00",
                "tax_rate": "10",
                "buyer_name": "修改后的购买方",
                "notes": "修改记录测试",
            },
        )
        updated = self.finance_service.get_invoice(invoice_id)
        self.assertEqual(updated["invoice_date"], "2026-08-06")
        self.assertEqual(updated["buyer_name_snapshot"], "修改后的购买方")
        self.assertEqual(updated["status"], "active")
        with self.assertRaisesRegex(ValueError, "不能低于已关联回款金额"):
            self.finance_service.update_invoice(
                invoice_id,
                {
                    "invoice_no": f"FIN-INVOICE-{suffix}",
                    "contract_id": contract_id,
                    "project_id": project_id,
                    "invoice_date": "2026-08-06",
                    "amount": "3999.99",
                    "tax_rate": "10",
                },
            )

        void_invoice_no = f"FIN-VOID-{suffix}"
        void_invoice_id = self.finance_service.create_invoice(
            {
                "invoice_no": void_invoice_no,
                "contract_id": contract_id,
                "project_id": project_id,
                "invoice_date": "2026-08-06",
                "amount": "1.00",
                "tax_rate": "10",
            }
        )
        self.finance_service.void_invoices([void_invoice_id])
        with self.assertRaisesRegex(ValueError, "已有作废记录.*显示已作废"):
            self.finance_service.create_invoice(
                {
                    "invoice_no": void_invoice_no,
                    "contract_id": contract_id,
                    "project_id": project_id,
                    "invoice_date": "2026-08-06",
                    "amount": "1.00",
                    "tax_rate": "10",
                }
            )

        self.finance_service.update_invoice(
            void_invoice_id,
            {
                "invoice_no": void_invoice_no,
                "contract_id": contract_id,
                "project_id": project_id,
                "invoice_date": "2026-08-06",
                "amount": "2.00",
                "tax_rate": "10",
                "buyer_name": "恢复后的购买方",
            },
        )
        restored = self.finance_service.get_invoice(void_invoice_id)
        self.assertEqual(restored["status"], "active")
        self.assertEqual(restored["amount_minor"], 200)
        self.assertEqual(restored["buyer_name_snapshot"], "恢复后的购买方")
        visible_ids = {
            row["id"] for row in self.finance_service.list_invoices(project_id)
        }
        self.assertIn(void_invoice_id, visible_ids)

        from db.connection import get_connection

        conn = get_connection()
        try:
            actions = [
                row[0]
                for row in conn.execute(
                    """SELECT action FROM sales_invoice_revisions
                       WHERE invoice_id=? ORDER BY id""",
                    (void_invoice_id,),
                ).fetchall()
            ]
            receipt_actions = [
                row[0]
                for row in conn.execute(
                    """SELECT action FROM receipt_revisions
                       WHERE receipt_id=? ORDER BY id""",
                    (receipt_id,),
                ).fetchall()
            ]
        finally:
            conn.close()
        self.assertEqual(actions, ["void", "restore"])
        self.assertEqual(receipt_actions, ["update"])

        self.finance_service.void_receipts([receipt_id])
        reopened_invoice = self.finance_service.get_invoice(invoice_id)
        self.assertEqual(reopened_invoice["received_minor"], 0)
        self.assertEqual(reopened_invoice["unreceived_minor"], 500_000)
        self.assertEqual(reopened_invoice["collection_status"], "待回款")
        settlement = self.contract_service.get_settlement(settlement_id)
        self.assertEqual(settlement["received_minor"], 0)
        self.assertEqual(settlement["collection_status"], "待回款")
        conn = get_connection()
        try:
            receipt_actions = [
                row[0]
                for row in conn.execute(
                    """SELECT action FROM receipt_revisions
                       WHERE receipt_id=? ORDER BY id""",
                    (receipt_id,),
                ).fetchall()
            ]
        finally:
            conn.close()
        self.assertEqual(receipt_actions, ["update", "void"])

    def test_receipt_auto_distribution_and_manual_adjustment(self):
        suffix = uuid4().hex[:8]
        project_id = self.project_service.create_project(
            {
                "name": f"回款分配测试-{suffix}",
                "project_code": f"DIST-{suffix}",
                "status": "进行中",
            }
        )
        contract_id = self.contract_service.create_contract(
            {
                "contract_no": f"DIST-CONTRACT-{suffix}",
                "name": "回款分配测试合同",
                "contract_type": "project",
                "sign_date": "2026-08-18",
                "amount": "3000.00",
                "status": "active",
            }
        )
        self.contract_service.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "amount": "3000.00",
            }
        )
        first_settlement_id = self.contract_service.create_settlement(
            {
                "settlement_no": f"DIST-A-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-01",
                "amount": "1000.00",
            }
        )
        second_settlement_id = self.contract_service.create_settlement(
            {
                "settlement_no": f"DIST-B-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-02",
                "amount": "1000.00",
            }
        )

        receipt_id = self.finance_service.create_receipt(
            {
                "receipt_no": f"DIST-RECEIPT-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "receipt_date": "2026-08-18",
                "amount": "1500.00",
            }
        )
        receipt = self.finance_service.get_receipt(receipt_id)
        self.assertEqual(receipt["allocated_amount_minor"], 150_000)
        self.assertEqual(receipt["settlement_count"], 2)
        self.assertEqual(len(receipt["allocations"]), 2)
        self.assertEqual(
            [row["allocated_amount_minor"] for row in receipt["allocations"]],
            [100_000, 50_000],
        )
        self.assertEqual(
            self.contract_service.get_settlement(first_settlement_id)[
                "collection_status"
            ],
            "已结清",
        )
        self.assertEqual(
            self.contract_service.get_settlement(second_settlement_id)[
                "received_minor"
            ],
            50_000,
        )

        self.finance_service.update_receipt(
            receipt_id,
            {
                "receipt_no": f"DIST-RECEIPT-{suffix}",
                "receipt_date": "2026-08-18",
                "amount": "1000.00",
                "settlement_allocations": [
                    {
                        "settlement_id": second_settlement_id,
                        "amount_minor": 100_000,
                    }
                ],
            },
        )
        receipt = self.finance_service.get_receipt(receipt_id)
        self.assertEqual(receipt["settlement_count"], 1)
        self.assertEqual(receipt["settlement_id"], second_settlement_id)
        self.assertEqual(
            self.contract_service.get_settlement(first_settlement_id)[
                "received_minor"
            ],
            0,
        )
        self.assertEqual(
            self.contract_service.get_settlement(second_settlement_id)[
                "collection_status"
            ],
            "已结清",
        )

        from db.connection import get_connection

        conn = get_connection()
        try:
            revision_allocations = conn.execute(
                """SELECT rar.previous_settlement_id,
                          rar.previous_allocated_amount_minor
                   FROM receipt_allocation_revisions rar
                   JOIN receipt_revisions rr
                     ON rr.id=rar.receipt_revision_id
                   WHERE rr.receipt_id=?
                   ORDER BY rar.id""",
                (receipt_id,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            [tuple(row) for row in revision_allocations],
            [
                (first_settlement_id, 100_000),
                (second_settlement_id, 50_000),
            ],
        )

    def test_settlement_invoice_link_supports_partial_invoicing(self):
        suffix = uuid4().hex[:8]
        project_id = self.project_service.create_project(
            {
                "name": f"结算开票联动-{suffix}",
                "project_code": f"LINK-{suffix}",
                "status": "进行中",
            }
        )
        contract_id = self.contract_service.create_contract(
            {
                "contract_no": f"LINK-CONTRACT-{suffix}",
                "name": "结算开票联动测试合同",
                "contract_type": "project",
                "sign_date": "2026-08-11",
                "amount": "10000.00",
                "status": "active",
            }
        )
        self.contract_service.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "amount": "10000.00",
            }
        )
        settlement_id = self.contract_service.create_settlement(
            {
                "settlement_no": f"LINK-SETTLEMENT-A-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-11",
                "amount": "1000.00",
            }
        )
        second_settlement_id = self.contract_service.create_settlement(
            {
                "settlement_no": f"LINK-SETTLEMENT-B-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-11",
                "amount": "500.00",
            }
        )

        with self.assertRaisesRegex(ValueError, "多笔收入确认.*请选择"):
            self.finance_service.create_invoice(
                {
                    "invoice_no": f"LINK-UNSELECTED-{suffix}",
                    "contract_id": contract_id,
                    "project_id": project_id,
                    "invoice_date": "2026-08-11",
                    "amount": "1.00",
                }
            )

        first_invoice_id = self.finance_service.create_invoice(
            {
                "invoice_no": f"LINK-INVOICE-60-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_id": settlement_id,
                "invoice_date": "2026-08-11",
                "amount": "600.00",
            }
        )
        settlement = self.contract_service.get_settlement(settlement_id)
        self.assertEqual(settlement["invoiced_minor"], 60_000)
        self.assertEqual(settlement["uninvoiced_minor"], 40_000)
        self.assertEqual(settlement["invoice_count"], 1)
        self.assertAlmostEqual(settlement["invoice_rate_percent"], 60.0)

        second_invoice_id = self.finance_service.create_invoice(
            {
                "invoice_no": f"LINK-INVOICE-40-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_id": settlement_id,
                "invoice_date": "2026-08-11",
                "amount": "400.00",
            }
        )
        settlement = self.contract_service.get_settlement(settlement_id)
        self.assertEqual(settlement["invoiced_minor"], 100_000)
        self.assertEqual(settlement["uninvoiced_minor"], 0)
        self.assertEqual(settlement["invoice_count"], 2)
        self.assertAlmostEqual(settlement["invoice_rate_percent"], 100.0)

        with self.assertRaisesRegex(ValueError, "待开票金额.*0.00"):
            self.finance_service.create_invoice(
                {
                    "invoice_no": f"LINK-OVER-{suffix}",
                    "contract_id": contract_id,
                    "project_id": project_id,
                    "settlement_id": settlement_id,
                    "invoice_date": "2026-08-11",
                    "amount": "1.00",
                }
            )
        with self.assertRaisesRegex(ValueError, "不能低于已关联开票金额"):
            self.contract_service.update_settlement(
                settlement_id,
                {
                    "contract_id": contract_id,
                    "project_id": project_id,
                    "settlement_date": "2026-08-11",
                    "amount": "999.99",
                },
            )

        self.finance_service.void_invoices([second_invoice_id])
        settlement = self.contract_service.get_settlement(settlement_id)
        self.assertEqual(settlement["invoiced_minor"], 60_000)
        self.assertEqual(settlement["uninvoiced_minor"], 40_000)

        self.finance_service.update_invoice(
            first_invoice_id,
            {
                "invoice_no": f"LINK-INVOICE-60-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_id": settlement_id,
                "invoice_date": "2026-08-11",
                "amount": "700.00",
            },
        )
        self.finance_service.update_invoice(
            second_invoice_id,
            {
                "invoice_no": f"LINK-INVOICE-40-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_id": settlement_id,
                "invoice_date": "2026-08-11",
                "amount": "300.00",
            },
        )
        settlement = self.contract_service.get_settlement(settlement_id)
        self.assertEqual(settlement["invoiced_minor"], 100_000)
        self.assertEqual(settlement["uninvoiced_minor"], 0)
        linked_invoice = self.finance_service.get_invoice(first_invoice_id)
        self.assertEqual(linked_invoice["settlement_id"], settlement_id)
        self.assertIn("LINK-SETTLEMENT-A", linked_invoice["settlement_no"])

        second_settlement = self.contract_service.get_settlement(
            second_settlement_id
        )
        self.assertEqual(second_settlement["invoiced_minor"], 0)
        with self.assertRaisesRegex(ValueError, "已有发票或回款"):
            self.contract_service.void_settlements([settlement_id])


if __name__ == "__main__":
    unittest.main()
