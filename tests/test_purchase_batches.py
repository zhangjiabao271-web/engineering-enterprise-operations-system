import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db import connection
from db.backup import backup_database
from db.migration_runner import run_migrations
from services import procurement_service as purchase, project_service, project_profit_service


class PurchaseBatchTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        target = Path(self.folder.name) / "test.db"
        backup_database(connection.DB_PATH, target)
        run_migrations(target)
        self.patch = patch.object(connection, "DB_PATH", target)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.project = project_service.create_project({"name": "多材料采购专项"})
        self.header = {"purchase_type": "零星采购", "project_id": self.project,
                       "merchant_name_snapshot": "测试材料店", "purchase_date": "2026-09-12",
                       "freight_amount_cents": 1234}
        self.items = [
            {"material_name_snapshot": "钢管", "quantity": "996", "price_basis": "inclusive",
             "tax_inclusive_unit_price_cents": 189, "tax_rate_bps": 1300},
            {"material_name_snapshot": "钢板", "settlement_mode": "weight", "net_weight": "2.35",
             "weight_unit": "吨", "weight_unit_price": "3800", "price_basis": "inclusive"},
            {"material_name_snapshot": "螺丝", "settlement_mode": "total", "settlement_total_cents": 10101,
             "price_basis": "inclusive"},
        ]

    def test_create_edit_totals_and_search(self):
        order = purchase.add_purchase_order(self.header, self.items)
        record = purchase.get_purchase_order(order)
        expected = 188244 + 893000 + 10101 + 1234
        self.assertEqual(record["item_count"], 3)
        self.assertEqual(record["project_cost_cents"], expected)
        found = purchase.list_purchase_orders(project_id=self.project, keyword="钢板")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["item_count"], 3)
        with closing(connection.get_connection()) as conn:
            self.assertEqual(conn.execute("SELECT cost_minor FROM purchase_project_costs WHERE purchase_order_id=?", (order,)).fetchone()[0], expected)
            details = project_profit_service._purchase_material_breakdown(conn, self.project)
            self.assertEqual(sum(row["amount_minor"] for row in details), expected - 1234)
        items = record["items"]
        ids = [item["item_id"] for item in items]
        items[0]["quantity"] = "1000"
        purchase.update_purchase_order(order, self.header, items)
        changed = purchase.get_purchase_order(order)
        self.assertEqual(changed["project_cost_cents"], expected + 756)
        self.assertEqual([item["item_id"] for item in changed["items"]], ids)
        self.assertEqual(changed["items"][1]["net_weight"], "2.35")
        self.assertEqual(changed["items"][2]["settlement_total_cents"], 10101)
        purchase.update_purchase_order(order, self.header, changed["items"][:2])
        self.assertEqual(purchase.get_purchase_order(order)["item_count"], 2)

    def test_failed_edit_is_atomic(self):
        order = purchase.add_purchase_order(self.header, self.items)
        before = purchase.get_purchase_order(order)
        with self.assertRaises(ValueError):
            purchase.update_purchase_order(order, self.header, self.items[0])
        items = [dict(item) for item in before["items"]]
        items[0]["quantity"] = "1000"
        items[1]["item_id"] = -1
        with self.assertRaises(ValueError):
            purchase.update_purchase_order(order, self.header, items)
        self.assertEqual(purchase.get_purchase_order(order), before)
        with closing(connection.get_connection()) as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())


if __name__ == "__main__":
    unittest.main()
