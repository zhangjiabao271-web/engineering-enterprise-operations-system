import json
import tempfile
import unittest
from pathlib import Path
from contextlib import closing

from db import connection
from db.backup import backup_database

if not connection.DB_PATH.exists():
    # 开源环境无生产库：构建空库（基础表 + 全量迁移）作为测试基准
    import db.migration_runner as _runner_module

    _base_dir = tempfile.TemporaryDirectory(prefix="oss_base_")
    connection.DB_PATH = Path(_base_dir.name) / "supplier_data.db"
    _runner_module.DB_PATH = connection.DB_PATH
    import database as _database

    _database.init_db()
from db.migration_runner import run_migrations
from services import procurement_service as purchase, master_data_service as master, project_service


class InclusivePriceTests(unittest.TestCase):
    def test_user_prices_and_legacy_amounts(self):
        for price, qty, total in [(184, 2880, 529920), (189, 996, 188244), (192, 1, 192), (820, 588, 482160)]:
            with self.subTest(price=price):
                result = purchase.calculate_purchase_amounts(qty, 0, 1300, price_basis="inclusive", tax_inclusive_unit_price_cents=price)
                self.assertEqual(result["line_amount_cents"], total)
                self.assertEqual(result["tax_amount_cents"] + result["material_amount_cents"], total)
        self.assertEqual(purchase.calculate_purchase_amounts(996, 167, 1300)["line_amount_cents"], 187955)

    def test_fraction_freight_and_invalid_values(self):
        result = purchase.calculate_purchase_amounts("0.5", 0, 0, 100, price_basis="inclusive", tax_inclusive_unit_price_cents=189)
        self.assertEqual(result["project_cost_cents"], 195)
        self.assertEqual(purchase.decimal_minor("1.005"), 101)
        for value in (None, -1, "NaN", "Infinity", "1.5"):
            with self.assertRaises(ValueError):
                purchase.calculate_purchase_amounts(1, 0, 1300, price_basis="inclusive", tax_inclusive_unit_price_cents=value)

    def test_offer_purchase_edit_and_allocation_history(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.db"
            backup_database(connection.DB_PATH, path)
            run_migrations(path)
            original = connection.DB_PATH
            connection.DB_PATH = path
            try:
                supplier = master.create_supplier({"name": "含税回归供应商"})
                data = {"supplier_id": supplier, "name": "螺栓", "specification": "20*55", "price": "1.89", "price_basis": "inclusive", "tax_rate_percent": 13}
                offer = master.create_supplier_offer(data)
                self.assertEqual(master.get_supplier_offer(offer)["quoted_price"], 1.89)
                self.assertEqual(master.list_supplier_offers(supplier_id=supplier)[0]["tax_inclusive_price_minor"], 189)
                master.update_supplier_offer(offer, {**data, "price": "1.92"})
                projects = [project_service.create_project({"name": f"含税均摊测试{i}"}) for i in range(3)]
                header = {"purchase_type": "正式采购", "supplier_id": supplier, "merchant_name_snapshot": "含税回归供应商", "purchase_date": "2026-09-06", "allocation_method": "equal", "project_ids": projects}
                item = {"product_id": offer, "material_name_snapshot": "螺栓", "quantity": 996, "material_unit_price_cents": 0, "tax_rate_bps": 1300, "price_basis": "inclusive", "tax_inclusive_unit_price_cents": 189, "cost_category": "工具和设备"}
                order = purchase.add_purchase_order(header, item)
                stored = purchase.get_purchase_order(order)
                self.assertEqual(stored["price_basis"], "inclusive")
                self.assertEqual(stored["project_cost_cents"], 188244)
                purchase.update_purchase_order(order, header, item)
                self.assertEqual(purchase.get_purchase_order(order)["project_cost_cents"], 188244)
                self.assertEqual(sum(r["amount_minor"] for r in purchase.get_purchase_allocations(order)), 188244)
                with closing(connection.get_connection()) as conn:
                    revision = conn.execute("SELECT after_json FROM purchase_price_revisions WHERE entity_type='purchase' AND entity_id=?", (order,)).fetchone()
                    self.assertEqual(sum(r["amount_minor"] for r in json.loads(revision[0])["allocations"]), 188244)
                    self.assertFalse(conn.execute("PRAGMA foreign_key_check").fetchall())
            finally:
                connection.DB_PATH = original


if __name__ == "__main__":
    unittest.main()
