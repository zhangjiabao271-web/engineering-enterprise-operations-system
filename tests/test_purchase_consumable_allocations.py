"""Order-level consumable allocation on fresh, disposable ledgers."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db import connection, migration_runner
from db.connection import db_read
from db.schema import init_db
from services import procurement_service as purchases, project_service as projects
from services import project_cost_detail_service as details, project_profit_service as profit
from services import web_workbench_service as business, web_workbench_operations as operations


class PurchaseConsumableAllocationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="purchase_consumables_")
        self.addCleanup(folder.cleanup)
        for module in (connection, migration_runner):
            override = patch.object(module, "DB_PATH", Path(folder.name) / "synthetic.db")
            override.start()
            self.addCleanup(override.stop)
        init_db()
        self.projects = [projects.create_project({"name": f"合成均摊项目{i}"}) for i in range(3)]
        self.data = {
            "purchase_type": "零星采购", "merchant_name_snapshot": "合成耗材店",
            "purchase_date": "2026-10-17", "allocation_method": "equal",
            "project_ids": self.projects, "freight_amount": "3.01",
            "payment_status": "已付款", "payment_method": "现金", "invoice_status": "有发票",
            "items": [
                {"material_name_snapshot": "合成焊条", "quantity": "1", "unit_snapshot": "箱",
                 "unit_price": "100.01", "price_basis": "inclusive", "tax_percent": "13", "cost_category": "材料费"},
                {"material_name_snapshot": "合成砂轮片", "quantity": "2", "unit_snapshot": "片",
                 "unit_price": "7.15", "price_basis": "inclusive", "tax_percent": "9", "cost_category": "工具和设备"},
                {"material_name_snapshot": "合成杂件", "quantity": "1", "unit_snapshot": "包",
                 "unit_price": "2.99", "price_basis": "inclusive", "tax_percent": "0", "cost_category": "其他"},
            ],
        }

    def ledger(self):
        with db_read() as conn:
            return list(conn.iterdump())

    def test_mixed_consumables_share_total_once_and_project_details_reconcile(self):
        order_id = business.save_record("purchases", self.data)
        order = business.get_record("purchases", order_id)
        self.assertIsNone(order["project_id"])
        self.assertEqual(order["total_amount_cents"], 12031)
        self.assertEqual([row["amount_minor"] for row in order["allocations"]], [4011, 4010, 4010])
        self.assertEqual([row["cost_category"] for row in order["items"]], ["材料费", "工具和设备", "其他"])
        for line in order["allocations"]:
            project_id = line["project_id"]
            self.assertEqual(profit.get_project_summary(project_id)["purchase_cost_minor"], line["amount_minor"])
            detail = details.get_project_cost_details(project_id)
            self.assertEqual(sum(row["amount_minor"] for row in detail["purchase"]), line["amount_minor"])
        self.assertEqual(purchases.get_purchase_dashboard("2026-10")["summary"]["total_cents"], 12031)
        with db_read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM fund_transactions").fetchone()[0], 0)

    def test_preview_matches_saved_shares_and_never_writes(self):
        before = self.ledger()
        preview = business.purchase_preview(self.data["items"], "3.01",
            allocation_method="equal", project_ids=self.projects)
        self.assertEqual(self.ledger(), before)
        order_id = business.save_record("purchases", self.data)
        saved = purchases.get_purchase_allocations(order_id)
        self.assertEqual([row["amount_minor"] for row in preview["allocations"]],
                         [row["amount_minor"] for row in saved])
        self.assertTrue(all(row["project_name"] and row["project_code"] for row in preview["allocations"]))

    def test_invalid_project_sets_leave_entire_order_unchanged(self):
        before = self.ledger()
        for project_ids in ([], self.projects[:1], [self.projects[0]] * 2, [0, self.projects[0]], [self.projects[0], 999999]):
            with self.subTest(project_ids=project_ids):
                with self.assertRaises(ValueError):
                    business.purchase_preview(self.data["items"], "3.01", allocation_method="equal", project_ids=project_ids)
                with self.assertRaises(ValueError):
                    business.save_record("purchases", dict(self.data, project_ids=project_ids))
                self.assertEqual(self.ledger(), before)

    def test_edit_recalculates_preserves_categories_and_keeps_allocation_history(self):
        order_id = business.save_record("purchases", self.data)
        original = business.get_record("purchases", order_id)
        self.assertEqual(original["project_ids"], self.projects)
        revised = dict(self.data, freight_amount="3.02", project_ids=self.projects[:2])
        business.save_record("purchases", revised, order_id, original["_version"])
        current = business.get_record("purchases", order_id)
        self.assertEqual(current["project_ids"], self.projects[:2])
        self.assertEqual([row["amount_minor"] for row in current["allocations"]], [6016, 6016])
        self.assertEqual(profit.get_project_summary(self.projects[2])["purchase_cost_minor"], 0)
        with db_read() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM purchase_cost_allocation_lines WHERE status='void'").fetchone()[0], 3)
            revision = json.loads(conn.execute("SELECT after_json FROM purchase_price_revisions").fetchone()[0])
            self.assertEqual(sum(row["amount_minor"] for row in revision["allocations"]), 12032)
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())

    def test_switching_to_direct_and_unassigned_removes_active_shares(self):
        order_id = business.save_record("purchases", self.data)
        current = business.get_record("purchases", order_id)
        business.save_record("purchases", dict(self.data, allocation_method="direct", project_id=self.projects[0]),
                             order_id, current["_version"])
        current = business.get_record("purchases", order_id)
        self.assertEqual(current["project_id"], self.projects[0])
        self.assertEqual(current["allocations"], [])
        self.assertEqual(profit.get_project_summary(self.projects[0])["purchase_cost_minor"], 12031)
        self.assertEqual(profit.get_project_summary(self.projects[1])["purchase_cost_minor"], 0)
        business.save_record("purchases", dict(self.data, allocation_method="unassigned"), order_id, current["_version"])
        self.assertEqual(profit.get_project_summary(self.projects[0])["purchase_cost_minor"], 0)

    def test_existing_material_purchase_can_be_reallocated_without_reentering_it(self):
        order_id = business.save_record("purchases", dict(self.data, allocation_method="direct", project_id=self.projects[0]))
        values, review, _ = operations.prepare("purchase_allocate", {
            "id": order_id, "method": "equal", "project_ids": self.projects, "reason": "耗材共同使用",
        })
        self.assertEqual(sum(row["amount_minor"] for row in review["rows"]), 12031)
        operations.apply("purchase_allocate", values, "synthetic-allocation")
        self.assertEqual(business.get_record("purchases", order_id)["project_ids"], self.projects)

    def test_historical_completed_project_is_preserved_but_not_a_new_default(self):
        order_id = business.save_record("purchases", self.data)
        original = projects.get_project(self.projects[0])
        projects.update_project(self.projects[0], dict(original, status="已完工"))
        choices = business.options()
        self.assertNotIn(self.projects[0], [row["value"] for row in choices["active_projects"]])
        current = business.get_record("purchases", order_id)
        self.assertIn(self.projects[0], current["project_ids"])
        business.save_record("purchases", dict(self.data, notes="补充说明"), order_id, current["_version"])
        self.assertEqual(business.get_record("purchases", order_id)["project_ids"], self.projects)


if __name__ == "__main__":
    unittest.main()
