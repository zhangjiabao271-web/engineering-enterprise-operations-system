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
            finance_service,
            operations_service,
            project_service,
        )

        cls.contract_service = contract_service
        cls.finance_service = finance_service
        cls.operations_service = operations_service
        cls.project_service = project_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def _create_fifo_case(self, suffix, customer_name, amount="5000.00"):
        project_id = self.project_service.create_project(
            {
                "name": f"发票回款核销-{suffix}",
                "project_code": f"FIFO-{suffix}",
                "customer_name": customer_name,
                "status": "进行中",
            }
        )
        contract_id = self.contract_service.create_contract(
            {
                "contract_no": f"FIFO-CONTRACT-{suffix}",
                "name": f"发票回款核销合同-{suffix}",
                "contract_type": "project",
                "sign_date": "2026-08-01",
                "amount": amount,
                "status": "active",
            }
        )
        self.contract_service.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "amount": amount,
            }
        )
        self.contract_service.create_settlement(
            {
                "settlement_no": f"FIFO-SETTLEMENT-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-01",
                "amount": amount,
            }
        )
        return project_id, contract_id

    def _create_fifo_invoice(
        self, suffix, project_id, contract_id, invoice_date, amount
    ):
        invoice_id = self.finance_service.create_invoice(
            {
                "invoice_no": f"FIFO-INVOICE-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "invoice_date": invoice_date,
                "amount": amount,
            }
        )
        from services.operating_entity_service import assign_records
        assign_records('sales_invoices', [invoice_id], 1, '测试确认主体')
        return invoice_id

    def _create_fifo_receipt(
        self,
        suffix,
        project_id,
        contract_id,
        receipt_date,
        amount,
        invoice_id=None,
    ):
        receipt_id = self.finance_service.create_receipt(
            {
                "receipt_no": f"FIFO-RECEIPT-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "invoice_id": invoice_id,
                "receipt_date": receipt_date,
                "amount": amount,
            }
        )
        from services.operating_entity_service import assign_records
        assign_records('receipts', [receipt_id], 1, '测试确认主体')
        return receipt_id

    def test_entity_change_reconciles_and_never_cross_matches(self):
        from services.operating_entity_service import assign_records
        suffix = uuid4().hex[:8]
        project, contract = self._create_fifo_case(suffix, f'主体测试-{suffix}')
        first = self._create_fifo_invoice(suffix+'A', project, contract, '2026-08-01', '100')
        second = self._create_fifo_invoice(suffix+'B', project, contract, '2026-08-02', '100')
        assign_records('sales_invoices', [second], 2)
        receipt = self._create_fifo_receipt(suffix, project, contract, '2026-08-03', '100', invoice_id=first)
        assign_records('receipts', [receipt], 2)
        self.assertEqual(self.finance_service.get_invoice(first)['received_minor'], 0)
        self.assertEqual(self.finance_service.get_invoice(second)['received_minor'], 10000)
        result = self.finance_service.get_receipt(receipt)
        self.assertEqual([m['invoice_id'] for m in result['invoice_matches']], [second])
        self.assertEqual(result['invoice_unmatched_minor'], 0)
        assign_records('receipts', [receipt], 1)
        self.assertEqual(self.finance_service.get_invoice(second)['received_minor'], 0)
        self.assertEqual(self.finance_service.get_invoice(first)['received_minor'], 10000)

    def test_unknown_entity_does_not_use_old_or_manual_matches(self):
        from db.connection import db_transaction
        from services.operating_entity_service import assign_records
        suffix = uuid4().hex[:8]
        project, contract = self._create_fifo_case(suffix, f'待确认测试-{suffix}')
        invoice = self._create_fifo_invoice(suffix, project, contract, '2026-08-01', '100')
        receipt = self._create_fifo_receipt(suffix, project, contract, '2026-08-02', '100', invoice_id=invoice)
        with db_transaction() as conn:
            conn.execute("DELETE FROM record_entities WHERE record_type='receipts' AND record_id=?", (receipt,))
        self.assertEqual(self.finance_service.get_invoice(invoice)['received_minor'], 0)
        self.assertEqual(self.finance_service.get_receipt(receipt)['invoice_matches'], [])
        row = next(r for r in self.finance_service.list_invoices(project) if r['id'] == invoice)
        self.assertEqual(row['received_minor'], 0)
        assign_records('receipts', [receipt], 1)
        self.assertEqual(self.finance_service.get_invoice(invoice)['received_minor'], 10000)

    def test_invoice_list_distinguishes_missing_and_active_attachments(self):
        suffix = uuid4().hex[:8]
        project_id, contract_id = self._create_fifo_case(
            suffix, f"附件标记客户-{suffix}", amount="1000.00"
        )
        invoice_id = self._create_fifo_invoice(
            suffix, project_id, contract_id, "2026-08-01", "1000.00"
        )

        invoice = next(
            row
            for row in self.finance_service.list_invoices(project_id)
            if row["id"] == invoice_id
        )
        self.assertEqual(invoice["attachment_count"], 0)

        from db.connection import get_connection

        conn = get_connection()
        try:
            organization_id = conn.execute(
                "SELECT organization_id FROM sales_invoices WHERE id=?",
                (invoice_id,),
            ).fetchone()[0]
            for status in ("active", "active", "void"):
                public_id = str(uuid4())
                conn.execute(
                    """INSERT INTO business_attachments (
                           public_id, organization_id, invoice_id, category,
                           file_path, original_name, description, status,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, '发票附件', ?, ?, '', ?, ?, ?)""",
                    (
                        public_id,
                        organization_id,
                        invoice_id,
                        f"attachments/{public_id}.pdf",
                        f"{public_id}.pdf",
                        status,
                        "2026-08-01T00:00:00",
                        "2026-08-01T00:00:00",
                    ),
                )
            conn.commit()
        finally:
            conn.close()

        invoice = next(
            row
            for row in self.finance_service.list_invoices(project_id)
            if row["id"] == invoice_id
        )
        self.assertEqual(invoice["attachment_count"], 2)

    def test_invoice_receipt_fifo_handles_both_business_sequences(self):
        suffix = uuid4().hex[:8]
        project_id, contract_id = self._create_fifo_case(
            suffix, f"核销客户-{suffix}"
        )
        first_invoice_id = self._create_fifo_invoice(
            f"A-{suffix}", project_id, contract_id, "2026-08-01", "1000.00"
        )
        second_invoice_id = self._create_fifo_invoice(
            f"B-{suffix}", project_id, contract_id, "2026-08-02", "1000.00"
        )
        first_receipt_id = self._create_fifo_receipt(
            f"A-{suffix}", project_id, contract_id, "2026-08-03", "1500.00"
        )

        first_receipt = self.finance_service.get_receipt(first_receipt_id)
        self.assertEqual(
            [
                (row["invoice_id"], row["allocated_amount_minor"])
                for row in first_receipt["invoice_matches"]
            ],
            [(first_invoice_id, 100_000), (second_invoice_id, 50_000)],
        )
        self.assertEqual(first_receipt["invoice_matched_minor"], 150_000)
        self.assertEqual(first_receipt["invoice_unmatched_minor"], 0)
        self.assertEqual(
            self.finance_service.get_invoice(second_invoice_id)[
                "unreceived_minor"
            ],
            50_000,
        )

        second_receipt_id = self._create_fifo_receipt(
            f"B-{suffix}", project_id, contract_id, "2026-08-04", "700.00"
        )
        second_receipt = self.finance_service.get_receipt(second_receipt_id)
        self.assertEqual(second_receipt["invoice_matched_minor"], 50_000)
        self.assertEqual(second_receipt["invoice_unmatched_minor"], 20_000)

        third_invoice_id = self._create_fifo_invoice(
            f"C-{suffix}", project_id, contract_id, "2026-08-05", "200.00"
        )
        second_receipt = self.finance_service.get_receipt(second_receipt_id)
        self.assertEqual(second_receipt["invoice_unmatched_minor"], 0)
        self.assertEqual(
            second_receipt["invoice_matches"][-1]["invoice_id"],
            third_invoice_id,
        )
        self.assertEqual(
            self.finance_service.get_invoice(third_invoice_id)[
                "collection_status"
            ],
            "已结清",
        )

    def test_receipt_before_invoice_is_retained_and_matched_later(self):
        suffix = uuid4().hex[:8]
        project_id, contract_id = self._create_fifo_case(
            suffix, f"先款后票客户-{suffix}"
        )
        receipt_id = self._create_fifo_receipt(
            suffix, project_id, contract_id, "2026-08-01", "600.00"
        )
        receipt = self.finance_service.get_receipt(receipt_id)
        self.assertEqual(receipt["invoice_matched_minor"], 0)
        self.assertEqual(receipt["invoice_unmatched_minor"], 60_000)
        self.assertEqual(receipt["invoice_matches"], [])

        invoice_id = self._create_fifo_invoice(
            suffix, project_id, contract_id, "2026-08-10", "600.00"
        )
        receipt = self.finance_service.get_receipt(receipt_id)
        self.assertEqual(receipt["invoice_matched_minor"], 60_000)
        self.assertEqual(receipt["invoice_unmatched_minor"], 0)
        self.assertEqual(receipt["invoice_matches"][0]["invoice_id"], invoice_id)

    def test_manual_invoice_override_and_fifo_recalculation_keep_history(self):
        from db.connection import get_connection

        suffix = uuid4().hex[:8]
        project_id, contract_id = self._create_fifo_case(
            suffix, f"手动例外客户-{suffix}"
        )
        first_invoice_id = self._create_fifo_invoice(
            f"A-{suffix}", project_id, contract_id, "2026-08-01", "1000.00"
        )
        second_invoice_id = self._create_fifo_invoice(
            f"B-{suffix}", project_id, contract_id, "2026-08-02", "1000.00"
        )
        automatic_receipt_id = self._create_fifo_receipt(
            f"AUTO-{suffix}", project_id, contract_id, "2026-08-10", "700.00"
        )
        manual_receipt_id = self._create_fifo_receipt(
            f"MANUAL-{suffix}",
            project_id,
            contract_id,
            "2026-08-11",
            "500.00",
            invoice_id=second_invoice_id,
        )
        self.assertEqual(
            self.finance_service.get_receipt(manual_receipt_id)[
                "invoice_matches"
            ][0]["allocation_method"],
            "manual",
        )
        self.assertEqual(
            self.finance_service.get_receipt(automatic_receipt_id)[
                "invoice_matches"
            ][0]["invoice_id"],
            first_invoice_id,
        )

        later_receipt_id = self._create_fifo_receipt(
            f"LATER-{suffix}", project_id, contract_id, "2026-08-20", "900.00"
        )
        self.finance_service.update_receipt(
            later_receipt_id,
            {
                "receipt_no": f"FIFO-RECEIPT-LATER-{suffix}",
                "receipt_date": "2026-08-05",
                "amount": "900.00",
            },
        )
        earlier_receipt = self.finance_service.get_receipt(later_receipt_id)
        self.assertEqual(
            earlier_receipt["invoice_matches"][0]["invoice_id"],
            first_invoice_id,
        )
        self.assertEqual(earlier_receipt["invoice_matched_minor"], 90_000)
        automatic_receipt = self.finance_service.get_receipt(
            automatic_receipt_id
        )
        self.assertEqual(automatic_receipt["invoice_matched_minor"], 60_000)
        self.assertEqual(automatic_receipt["invoice_unmatched_minor"], 10_000)

        conn = get_connection()
        try:
            void_history_count = conn.execute(
                """SELECT COUNT(*) FROM invoice_receipt_allocations
                   WHERE customer_partner_id=(
                       SELECT customer_partner_id FROM projects WHERE id=?
                   ) AND status='void'""",
                (project_id,),
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertGreater(void_history_count, 0)

        self.finance_service.update_receipt(
            later_receipt_id,
            {
                "receipt_no": f"FIFO-RECEIPT-LATER-{suffix}",
                "receipt_date": "2026-08-05",
                "amount": "800.00",
            },
        )
        resized_receipt = self.finance_service.get_receipt(later_receipt_id)
        self.assertEqual(resized_receipt["invoice_matched_minor"], 80_000)
        automatic_receipt = self.finance_service.get_receipt(
            automatic_receipt_id
        )
        self.assertEqual(automatic_receipt["invoice_matched_minor"], 70_000)
        self.assertEqual(automatic_receipt["invoice_unmatched_minor"], 0)

        self.finance_service.void_receipts([later_receipt_id])
        automatic_receipt = self.finance_service.get_receipt(
            automatic_receipt_id
        )
        self.assertEqual(automatic_receipt["invoice_matched_minor"], 70_000)
        self.assertEqual(automatic_receipt["invoice_unmatched_minor"], 0)

    def test_fifo_voided_invoice_reallocates_without_crossing_customers(self):
        suffix = uuid4().hex[:8]
        project_a, contract_a = self._create_fifo_case(
            f"A-{suffix}", f"独立客户甲-{suffix}", amount="2000.00"
        )
        project_b, contract_b = self._create_fifo_case(
            f"B-{suffix}", f"独立客户乙-{suffix}", amount="1000.00"
        )
        first_a = self._create_fifo_invoice(
            f"A1-{suffix}", project_a, contract_a, "2026-08-01", "500.00"
        )
        second_a = self._create_fifo_invoice(
            f"A2-{suffix}", project_a, contract_a, "2026-08-02", "500.00"
        )
        invoice_b = self._create_fifo_invoice(
            f"B-{suffix}", project_b, contract_b, "2026-08-01", "500.00"
        )
        receipt_a = self._create_fifo_receipt(
            f"A-{suffix}", project_a, contract_a, "2026-08-03", "700.00"
        )
        self.finance_service.void_invoices([first_a])

        receipt = self.finance_service.get_receipt(receipt_a)
        self.assertEqual(
            [row["invoice_id"] for row in receipt["invoice_matches"]],
            [second_a],
        )
        self.assertEqual(receipt["invoice_matched_minor"], 50_000)
        self.assertEqual(receipt["invoice_unmatched_minor"], 20_000)
        self.assertEqual(
            self.finance_service.get_invoice(invoice_b)["received_minor"], 0
        )

    def test_project_and_company_finance_totals(self):
        suffix = uuid4().hex[:8]
        project_id = self.project_service.create_project(
            {
                "name": f"财务看板测试-{suffix}",
                "customer_name": f"财务看板客户-{suffix}",
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

        from services.operating_entity_service import assign_records
        assign_records('sales_invoices', [invoice_id], 1)
        assign_records('receipts', [receipt_id], 1)
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

    def test_project_and_collection_archives_are_independent(self):
        suffix = uuid4().hex[:8]
        before = self.operations_service.get_executive_overview("2026-08")

        completed_pending_id = self.project_service.create_project(
            {
                "name": f"完工待回款-{suffix}",
                "project_code": f"DONE-PENDING-{suffix}",
                "status": "已完工",
                "business_mode": "cash",
                "invoice_policy": "not_required",
            }
        )
        self.contract_service.create_settlement(
            {
                "project_id": completed_pending_id,
                "settlement_date": "2026-08-19",
                "amount": "1000.00",
            }
        )

        active_settled_id = self.project_service.create_project(
            {
                "name": f"在建已结清-{suffix}",
                "project_code": f"ACTIVE-SETTLED-{suffix}",
                "status": "进行中",
                "business_mode": "cash",
                "invoice_policy": "not_required",
            }
        )
        active_settlement_id = self.contract_service.create_settlement(
            {
                "project_id": active_settled_id,
                "settlement_date": "2026-08-19",
                "amount": "800.00",
            }
        )
        self.finance_service.create_receipt(
            {
                "project_id": active_settled_id,
                "settlement_id": active_settlement_id,
                "receipt_date": "2026-08-19",
                "amount": "800.00",
            }
        )

        dashboard = self.finance_service.get_finance_dashboard()
        rows = {row["project_id"]: row for row in dashboard["projects"]}
        self.assertEqual(
            rows[completed_pending_id]["collection_status"], "待回款"
        )
        self.assertEqual(
            rows[active_settled_id]["collection_status"], "已结清"
        )

        overview = self.operations_service.get_executive_overview("2026-08")
        visible_ids = {row["project_id"] for row in overview["projects"]}
        self.assertNotIn(completed_pending_id, visible_ids)
        self.assertIn(active_settled_id, visible_ids)
        self.assertEqual(
            overview["summary"]["receivable_minor"]
            - before["summary"]["receivable_minor"],
            100_000,
        )

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

        auto_invoice_id = self.finance_service.create_invoice(
            {
                "invoice_no": f"LINK-UNSELECTED-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "invoice_date": "2026-08-11",
                "amount": "1.00",
            }
        )
        auto_invoice = self.finance_service.get_invoice(auto_invoice_id)
        self.assertEqual(auto_invoice["settlement_id"], settlement_id)
        self.finance_service.void_invoices([auto_invoice_id])

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
        self.assertEqual(linked_invoice["settlement_count"], 1)
        self.assertIn("LINK-SETTLEMENT-A", linked_invoice["settlement_no"])

        second_settlement = self.contract_service.get_settlement(
            second_settlement_id
        )
        self.assertEqual(second_settlement["invoiced_minor"], 0)
        with self.assertRaisesRegex(ValueError, "已有发票或回款"):
            self.contract_service.void_settlements([settlement_id])

    def test_invoice_automatically_covers_all_project_uninvoiced_income(self):
        from db.connection import get_connection

        suffix = uuid4().hex[:8]
        project_id = self.project_service.create_project(
            {
                "name": f"跨收入确认开票-{suffix}",
                "project_code": f"MULTI-INVOICE-{suffix}",
                "status": "进行中",
            }
        )
        contract_id = self.contract_service.create_contract(
            {
                "contract_no": f"MULTI-INVOICE-CONTRACT-{suffix}",
                "name": "跨收入确认开票测试合同",
                "contract_type": "project",
                "sign_date": "2026-08-25",
                "amount": "546000.00",
                "status": "active",
            }
        )
        self.contract_service.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "amount": "546000.00",
            }
        )
        first_settlement_id = self.contract_service.create_settlement(
            {
                "settlement_no": f"MULTI-INVOICE-A-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-05",
                "amount": "530000.00",
            }
        )
        second_settlement_id = self.contract_service.create_settlement(
            {
                "settlement_no": f"MULTI-INVOICE-B-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-25",
                "amount": "16000.00",
            }
        )
        self.finance_service.create_invoice(
            {
                "invoice_no": f"MULTI-INVOICE-OLD-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_id": first_settlement_id,
                "invoice_date": "2026-08-05",
                "amount": "476000.00",
            }
        )

        final_invoice_id = self.finance_service.create_invoice(
            {
                "invoice_no": f"MULTI-INVOICE-FINAL-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "invoice_date": "2026-08-25",
                "amount": "70000.00",
            }
        )
        final_invoice = self.finance_service.get_invoice(final_invoice_id)
        self.assertIsNone(final_invoice["settlement_id"])
        self.assertEqual(final_invoice["settlement_count"], 2)
        self.assertIn("MULTI-INVOICE-A", final_invoice["settlement_no"])
        self.assertIn("MULTI-INVOICE-B", final_invoice["settlement_no"])

        conn = get_connection()
        try:
            allocations = conn.execute(
                """SELECT settlement_id, allocated_amount_minor
                   FROM invoice_settlement_allocations
                   WHERE invoice_id=? ORDER BY settlement_id""",
                (final_invoice_id,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(
            [tuple(row) for row in allocations],
            [
                (first_settlement_id, 5_400_000),
                (second_settlement_id, 1_600_000),
            ],
        )
        self.assertEqual(
            self.contract_service.get_settlement(first_settlement_id)[
                "uninvoiced_minor"
            ],
            0,
        )
        self.assertEqual(
            self.contract_service.get_settlement(second_settlement_id)[
                "uninvoiced_minor"
            ],
            0,
        )

        receipt_id = self.finance_service.create_receipt(
            {
                "receipt_no": f"MULTI-INVOICE-RECEIPT-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "invoice_id": final_invoice_id,
                "receipt_date": "2026-08-25",
                "amount": "70000.00",
            }
        )
        receipt = self.finance_service.get_receipt(receipt_id)
        self.assertEqual(receipt["settlement_count"], 2)
        self.assertEqual(
            [
                (row["settlement_id"], row["allocated_amount_minor"])
                for row in receipt["allocations"]
            ],
            [
                (first_settlement_id, 5_400_000),
                (second_settlement_id, 1_600_000),
            ],
        )

        with self.assertRaisesRegex(ValueError, "累计开票金额不能超过"):
            self.finance_service.create_invoice(
                {
                    "invoice_no": f"MULTI-INVOICE-OVER-{suffix}",
                    "contract_id": contract_id,
                    "project_id": project_id,
                    "invoice_date": "2026-08-25",
                    "amount": "0.01",
                }
            )

    def test_void_receipt_marks_allocations_void(self):
        """作废回款应把其分配标记为 void，历史遗留的悬空分配由迁移 410 兜底。"""
        from db.connection import get_connection

        suffix = uuid4().hex[:8]
        project_id = self.project_service.create_project(
            {
                "name": f"回款作废测试-{suffix}",
                "project_code": f"RCV-{suffix}",
                "status": "进行中",
            }
        )
        contract_id = self.contract_service.create_contract(
            {
                "contract_no": f"RCV-CONTRACT-{suffix}",
                "name": "回款作废测试合同",
                "contract_type": "project",
                "sign_date": "2026-08-23",
                "amount": "1000.00",
                "status": "active",
            }
        )
        self.contract_service.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "amount": "1000.00",
            }
        )
        settlement_id = self.contract_service.create_settlement(
            {
                "settlement_no": f"RCV-SETTLEMENT-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-23",
                "amount": "600.00",
            }
        )
        receipt_id = self.finance_service.create_receipt(
            {
                "receipt_no": f"RCV-RECEIPT-{suffix}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_id": settlement_id,
                "receipt_date": "2026-08-23",
                "amount": "600.00",
            }
        )
        self.finance_service.void_receipts([receipt_id])

        conn = get_connection()
        try:
            statuses = [
                row[0]
                for row in conn.execute(
                    "SELECT status FROM receipt_allocations WHERE receipt_id=?",
                    (receipt_id,),
                ).fetchall()
            ]
            # 迁移 410 兜底：任何作废回款都不应残留 active 分配
            dangling = conn.execute(
                """SELECT COUNT(*) FROM receipt_allocations ra
                   JOIN receipts r ON r.id=ra.receipt_id
                   WHERE r.status='void' AND ra.status='active'"""
            ).fetchone()[0]
            # 迁移 410 补记：作废功能上线前被作废的回款也有审计快照
            missing_audit = conn.execute(
                """SELECT COUNT(*) FROM receipts r
                   WHERE r.status='void'
                     AND EXISTS (
                         SELECT 1 FROM receipt_allocations ra
                         WHERE ra.receipt_id=r.id
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM receipt_revisions rr
                         WHERE rr.receipt_id=r.id AND rr.action='void'
                     )"""
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertTrue(statuses, "作废的回款应当有分配记录")
        self.assertTrue(all(status == "void" for status in statuses))
        self.assertEqual(dangling, 0)
        self.assertEqual(missing_audit, 0)
    def test_equal_split_purchase_not_counted_as_unassigned(self):
        """已多项目均摊的采购不得再计入驾驶舱的未归集采购（双重计数回归）。"""
        from services import procurement_service

        suffix = uuid4().hex[:8]
        project_a = self.project_service.create_project(
            {
                "name": f"均摊测试A-{suffix}",
                "project_code": f"EQA-{suffix}",
                "status": "进行中",
            }
        )
        project_b = self.project_service.create_project(
            {
                "name": f"均摊测试B-{suffix}",
                "project_code": f"EQB-{suffix}",
                "status": "进行中",
            }
        )
        before = self.operations_service.get_executive_overview()
        procurement_service.add_purchase_order(
            {
                "purchase_type": "零星采购",
                "merchant_name_snapshot": f"均摊测试商户-{suffix}",
                "purchase_date": "2099-11-01",
                "allocation_method": "equal",
                "project_ids": [project_a, project_b],
            },
            {
                "material_name_snapshot": f"均摊测试设备-{suffix}",
                "cost_category": "工具和设备",
                "quantity": 1,
                "unit_price_cents": 10000,
                "line_amount_cents": 10000,
            },
        )
        after = self.operations_service.get_executive_overview()
        self.assertEqual(
            after["drivers"]["unassigned_purchase_count"],
            before["drivers"]["unassigned_purchase_count"],
        )
        self.assertEqual(
            after["drivers"]["unassigned_purchase_minor"],
            before["drivers"]["unassigned_purchase_minor"],
        )
        # 均摊金额应全额计入两个项目，各 50 元
        from services import project_profit_service

        costs = {
            row["project"]["id"]: row["purchase_cost_minor"]
            for row in project_profit_service.get_portfolio_summary()["projects"]
        }
        self.assertEqual(costs.get(project_a, 0), 5000)
        self.assertEqual(costs.get(project_b, 0), 5000)


if __name__ == "__main__":
    unittest.main()
