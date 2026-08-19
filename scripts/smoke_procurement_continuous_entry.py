import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


class FakeVariable:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def supplier_data():
    return {
        "name": "连续采购录入测试供应商",
        "category": "钢材",
        "default_tax_rate_percent": "10",
        "contact": "测试联系人",
        "price_level": "中",
        "delivery": "一般",
        "quality": "良",
        "export": "否",
        "notes": "连续采购录入测试",
    }


def offer_data(supplier_id, name, specification, price):
    return {
        "supplier_id": supplier_id,
        "name": name,
        "specification": specification,
        "unit": "吨",
        "price": price,
        "tax_rate_percent": "10",
        "notes": "连续采购录入测试",
    }


def purchase_item(offer, quantity):
    return {
        "product_id": offer["id"],
        "material_name_snapshot": offer["name"],
        "specification_snapshot": offer["specification"],
        "unit_snapshot": offer["unit"],
        "cost_category": "材料费",
        "quantity": quantity,
        "material_unit_price_cents": offer["price_minor"],
        "tax_rate_bps": offer["tax_rate_bps"],
        "purpose": "同一项目连续录入",
        "notes": "同一张供应商单据",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test repeated formal purchase entry"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="procurement_continuous_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database
        from db.connection import get_connection
        from services import master_data_service, procurement_service, project_service
        from ui.purchase_entry import reset_continuous_purchase_line

        database.init_db()
        project = project_service.list_projects(active_only=True)[0]
        supplier_id = master_data_service.create_supplier(supplier_data())
        first_offer_id = master_data_service.create_supplier_offer(
            offer_data(supplier_id, "H型钢", "H300×300", "3500")
        )
        second_offer_id = master_data_service.create_supplier_offer(
            offer_data(supplier_id, "槽钢", "10#", "3300")
        )
        offers = {
            offer["id"]: offer
            for offer in master_data_service.list_supplier_offers(
                supplier_id=supplier_id
            )
        }

        header = {
            "purchase_type": "正式采购",
            "project_id": project["id"],
            "supplier_id": supplier_id,
            "merchant_name_snapshot": "连续采购录入测试供应商",
            "purchase_date": "2026-08-01",
            "payment_method": "对公转账",
            "payment_status": "未付款",
            "invoice_status": "有发票",
            "purchaser": "连续录入测试",
            "freight_amount_cents": 1200,
            "notes": "同一供应商连续录入",
        }
        first_order_id = procurement_service.add_purchase_order(
            header, purchase_item(offers[first_offer_id], 2)
        )
        next_header = dict(header)
        next_header["freight_amount_cents"] = 0
        second_order_id = procurement_service.add_purchase_order(
            next_header, purchase_item(offers[second_offer_id], 3)
        )
        assert first_order_id != second_order_id

        first_order = procurement_service.get_purchase_order(first_order_id)
        second_order = procurement_service.get_purchase_order(second_order_id)
        for order in (first_order, second_order):
            assert order["project_id"] == project["id"]
            assert order["supplier_id"] == supplier_id
            assert order["purchase_date"] == "2026-08-01"
            assert order["payment_method"] == "对公转账"
            assert order["invoice_status"] == "有发票"
        assert first_order["freight_amount_cents"] == 1200
        assert second_order["freight_amount_cents"] == 0

        conn = get_connection()
        try:
            assert conn.execute(
                "SELECT COUNT(*) FROM purchase_order_items WHERE purchase_order_id IN (?, ?)",
                (first_order_id, second_order_id),
            ).fetchone()[0] == 2
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()

        preserved = {
            "project": "CH-001 · 澄湖药业",
            "date": "2026-08-01",
            "supplier": "连续采购录入测试供应商",
            "payment_method": "对公转账",
            "payment_status": "未付款",
            "invoice": "有发票",
            "purchaser": "张三",
            "category": "材料费",
            "purpose": "厂房一区",
            "notes": "同一张供应商单据",
        }
        variables = {
            key: FakeVariable(value) for key, value in preserved.items()
        }
        variables.update(
            {
                "product": FakeVariable("H型钢 · H300×300"),
                "material": FakeVariable("H型钢"),
                "spec": FakeVariable("H300×300"),
                "unit": FakeVariable("吨"),
                "qty": FakeVariable("2"),
                "material_unit_price": FakeVariable("3500"),
                "tax_rate": FakeVariable("10"),
                "freight": FakeVariable("12"),
                "tax_inclusive_unit_price": FakeVariable("3850.00"),
                "material_amount": FakeVariable("7000.00"),
                "tax_amount": FakeVariable("700.00"),
                "project_cost": FakeVariable("7712.00"),
            }
        )
        reset_continuous_purchase_line(variables)
        assert {key: variables[key].get() for key in preserved} == preserved
        assert variables["product"].get() == ""
        assert variables["qty"].get() == "1"
        assert variables["freight"].get() == "0.00"
        assert variables["project_cost"].get() == "--"

    print("Formal procurement continuous-entry smoke test passed")


if __name__ == "__main__":
    main()
