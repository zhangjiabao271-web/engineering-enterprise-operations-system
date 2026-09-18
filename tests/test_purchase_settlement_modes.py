import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db import connection
from db.backup import backup_database
from db.migration_runner import run_migrations
from services import procurement_service as purchase, project_service, attachment_service


class SettlementCalculationTests(unittest.TestCase):
    def test_weight_units_precision_and_total(self):
        for weight, unit, price in (("5.26", "吨", "3800"), ("5260", "公斤", "3.8")):
            result = purchase.calculate_purchase_item_amounts({"freight_amount_cents": 10000}, {
                "settlement_mode": "weight", "net_weight": weight, "weight_unit": unit,
                "weight_unit_price": price, "tax_rate_bps": 1300, "price_basis": "inclusive"})
            self.assertEqual(result["line_amount_cents"], 1998800)
            self.assertEqual(result["project_cost_cents"], 2008800)
            self.assertEqual(result["material_amount_cents"] + result["tax_amount_cents"], 1998800)
        result = purchase.calculate_purchase_item_amounts({}, {"settlement_mode": "weight", "net_weight": "1000.001", "weight_unit": "公斤", "weight_unit_price": "3.456789", "price_basis": "inclusive"})
        self.assertEqual(result["line_amount_cents"], 345679)
        for basis, expected in (("inclusive", 1000000), ("exclusive", 1130000)):
            result = purchase.calculate_purchase_item_amounts({}, {"settlement_mode": "total", "settlement_total_cents": 1000000, "price_basis": basis, "tax_rate_bps": 1300})
            self.assertEqual(result["line_amount_cents"], expected)

    def test_reject_invalid_weight_and_mode(self):
        base = {"settlement_mode": "weight", "net_weight": "1", "weight_unit": "吨", "weight_unit_price": "3800"}
        for changes in ({"net_weight": "0"}, {"net_weight": "NaN"}, {"weight_unit_price": "-1"}, {"weight_unit": "件"}, {"settlement_mode": "invalid"}):
            with self.assertRaises(ValueError):
                purchase.calculate_purchase_item_amounts({}, {**base, **changes})

    def test_roundtrip_cost_history_and_attachments(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "test.db"
            backup_database(connection.DB_PATH, target)
            run_migrations(target)
            with patch.object(connection, "DB_PATH", target), patch.dict(os.environ, {"SUPPLY_CHAIN_ATTACHMENTS_PATH": str(Path(folder)/"attachments")}):
                project = project_service.create_project({"name": "过磅回归项目"})
                header = {"purchase_type": "零星采购", "project_id": project, "merchant_name_snapshot": "测试钢材商", "purchase_date": "2026-09-09"}
                item = {"material_name_snapshot": "混合规格钢材", "settlement_mode": "weight", "net_weight": "5.260001", "weight_unit": "吨", "weight_unit_price": "3800", "price_basis": "inclusive", "tax_rate_bps": 1300, "weigh_ticket_no": "TEST-WEIGH-1"}
                order = purchase.add_purchase_order(header, item)
                record = purchase.get_purchase_order(order)
                self.assertEqual(record["net_weight"], "5.260001")
                self.assertEqual(record["project_cost_cents"], 1998800)
                purchase.update_purchase_order(order, header, {**item, "net_weight": "6"})
                self.assertEqual(purchase.get_purchase_order(order)["project_cost_cents"], 2280000)
                with closing(connection.get_connection()) as conn:
                    self.assertEqual(conn.execute("SELECT cost_minor FROM purchase_project_costs WHERE purchase_order_id=?", (order,)).fetchone()[0], 2280000)
                    self.assertEqual(conn.execute("SELECT COUNT(*) FROM purchase_price_revisions WHERE entity_type='purchase' AND entity_id=?", (order,)).fetchone()[0], 1)
                source = Path(folder)/"weigh.txt"
                source.write_text("test weigh ticket", encoding="utf-8")
                attachment_service.add_attachment("purchase", order, source, category="磅单")
                self.assertTrue(attachment_service.list_attachments("purchase", order)[0]["file_exists"])
                purchase.update_purchase_order(order, header, {"material_name_snapshot": "整批钢材", "settlement_mode": "total", "settlement_total_cents": 888888, "price_basis": "inclusive"})
                record = purchase.get_purchase_order(order)
                self.assertEqual(record["project_cost_cents"], 888888)
                self.assertIsNone(record["net_weight"])
                with closing(connection.get_connection()) as conn:
                    self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())


if __name__ == "__main__":
    unittest.main()
