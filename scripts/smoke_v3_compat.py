import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Smoke-test legacy UI writes against V3 data")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="supply_chain_v3_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database as db

        db.init_db()
        supplier_id = db.add_supplier({
            "name": "V3兼容测试供应商",
            "category": "测试",
            "contact": "测试联系人",
            "price_level": "正常",
            "delivery": "正常",
            "quality": "正常",
            "export": "否",
            "notes": "仅在临时数据库中创建",
        })
        db.update_supplier(supplier_id, {
            "name": "V3兼容测试供应商（更新）",
            "category": "测试",
            "contact": "新联系人",
            "price_level": "正常",
            "delivery": "正常",
            "quality": "正常",
            "export": "否",
            "notes": "更新测试",
        })
        product_id = db.add_product({
            "supplier_id": supplier_id,
            "name": "V3测试材料",
            "specification": "T-01",
            "unit": "件",
            "price": 12.34,
            "notes": "兼容写入测试",
        })
        db.update_product(product_id, {
            "supplier_id": supplier_id,
            "name": "V3测试材料",
            "specification": "T-02",
            "unit": "件",
            "price": 56.78,
            "notes": "兼容更新测试",
        })
        project_id = db.add_project({
            "name": "V3兼容测试项目",
            "customer_name": "V3兼容测试客户",
            "manager": "V3测试经理",
            "address": "临时数据库",
        })
        order_id = db.add_purchase_order({
            "purchase_type": "正式采购",
            "project_id": project_id,
            "supplier_id": supplier_id,
            "merchant_name_snapshot": "V3兼容测试供应商（更新）",
            "purchase_date": "2026-07-19",
            "payment_method": "银行转账",
            "payment_status": "未付款",
            "invoice_status": "未确认",
            "purchaser": "测试员",
            "notes": "V3采购关系测试",
        }, {
            "product_id": product_id,
            "material_name_snapshot": "V3测试材料",
            "specification_snapshot": "T-02",
            "unit_snapshot": "件",
            "cost_category": "材料费",
            "quantity": 2,
            "unit_price_cents": 5678,
            "line_amount_cents": 11356,
            "purpose": "测试",
            "notes": "",
        })
        loaded_order = db.get_purchase_order(order_id)
        assert loaded_order["supplier_id"] == supplier_id
        assert loaded_order["product_id"] == product_id

        conn = db.get_connection()
        try:
            assert conn.execute(
                "SELECT 1 FROM business_partners WHERE id=? AND legal_name=?",
                (supplier_id, "V3兼容测试供应商（更新）"),
            ).fetchone()
            stored_order = conn.execute(
                "SELECT supplier_id, supplier_partner_id, organization_id, public_id FROM purchase_orders WHERE id=?",
                (order_id,),
            ).fetchone()
            assert stored_order["supplier_id"] is None
            assert stored_order["supplier_partner_id"] == supplier_id
            assert stored_order["organization_id"] and stored_order["public_id"]
            stored_item = conn.execute(
                "SELECT product_id, material_id, supplier_offer_id, public_id FROM purchase_order_items WHERE purchase_order_id=?",
                (order_id,),
            ).fetchone()
            assert stored_item["product_id"] is None
            assert stored_item["material_id"] and stored_item["supplier_offer_id"] == product_id
            assert stored_item["public_id"]
            offer = conn.execute(
                "SELECT price_minor FROM supplier_offers WHERE id=?",
                (product_id,),
            ).fetchone()
            assert offer and offer["price_minor"] == 5678
            project = conn.execute(
                "SELECT * FROM projects WHERE id=?", (project_id,)
            ).fetchone()
            assert project["organization_id"] and project["public_id"]
            assert project["customer_partner_id"] and project["manager_employee_id"]
            assert conn.execute(
                "SELECT 1 FROM wbs_nodes WHERE project_id=? AND wbs_code='ROOT'",
                (project_id,),
            ).fetchone()
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()
    print("V3 compatibility smoke test passed")


if __name__ == "__main__":
    main()
