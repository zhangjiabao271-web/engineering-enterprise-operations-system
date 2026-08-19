import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test procurement tax and freight migration"
    )
    parser.add_argument(
        "database",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "supplier_data.db",
    )
    args = parser.parse_args()
    source_database = args.database
    with tempfile.TemporaryDirectory(prefix="procurement_tax_freight_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(source_database, test_database)

        before = sqlite3.connect(test_database)
        historical_order_total = scalar(
            before, "SELECT COALESCE(SUM(total_amount_cents), 0) FROM purchase_orders"
        )
        historical_line_total = scalar(
            before,
            "SELECT COALESCE(SUM(line_amount_cents), 0) FROM purchase_order_items",
        )
        historical_item_count = scalar(
            before, "SELECT COUNT(*) FROM purchase_order_items"
        )
        historical_tax_item_count = scalar(
            before,
            """SELECT COUNT(*) FROM purchase_order_items
               WHERE tax_rate_bps<>0 OR tax_amount_cents<>0""",
        )
        historical_freight_order_count = scalar(
            before,
            """SELECT COUNT(*) FROM purchase_orders
               WHERE freight_amount_cents<>0""",
        )
        before.close()

        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from db.connection import get_connection
        from db.migration_runner import run_migrations

        run_migrations()

        conn = get_connection()
        try:
            assert scalar(
                conn, "SELECT COUNT(*) FROM schema_migrations WHERE version=140"
            ) == 1
            assert scalar(
                conn,
                """SELECT default_tax_rate_bps
                   FROM supplier_profiles
                   WHERE partner_id=(
                       SELECT id FROM business_partners WHERE legal_name='砺锋钢铁'
                   )""",
            ) == 1000
            assert scalar(
                conn,
                """SELECT COUNT(*) FROM supplier_offers
                   WHERE supplier_partner_id=(
                       SELECT id FROM business_partners WHERE legal_name='砺锋钢铁'
                   ) AND tax_rate_bps<>1000""",
            ) == 0
            assert scalar(
                conn,
                """SELECT COALESCE(SUM(total_amount_cents), 0)
                   FROM purchase_orders""",
            ) == historical_order_total
            assert scalar(
                conn,
                """SELECT COALESCE(SUM(line_amount_cents), 0)
                   FROM purchase_order_items""",
            ) == historical_line_total
            assert scalar(
                conn,
                """SELECT COUNT(*) FROM purchase_order_items
                   WHERE tax_rate_bps<>0 OR tax_amount_cents<>0""",
            ) == historical_tax_item_count
            assert scalar(
                conn,
                """SELECT COUNT(*) FROM purchase_orders
                   WHERE freight_amount_cents<>0""",
            ) == historical_freight_order_count
            assert scalar(
                conn, "SELECT COUNT(*) FROM purchase_order_items"
            ) == historical_item_count
        finally:
            conn.close()

        from services import (
            master_data_service,
            procurement_service,
            project_profit_service,
            project_service,
        )

        meifeng = next(
            row
            for row in master_data_service.list_suppliers()
            if row["name"] == "砺锋钢铁"
        )
        assert meifeng["default_tax_rate_percent"] == 10
        offer = master_data_service.list_supplier_offers(
            supplier_id=meifeng["id"]
        )[0]
        assert offer["tax_rate_bps"] == 1000
        assert offer["tax_inclusive_price_minor"] >= offer["price_minor"]

        projects = project_service.list_projects(active_only=False)
        project = projects[0]
        other_project = projects[1]
        baseline = project_profit_service.get_project_summary(project["id"])
        other_baseline = project_profit_service.get_project_summary(
            other_project["id"]
        )

        order_id = procurement_service.add_purchase_order(
            {
                "purchase_type": "正式采购",
                "project_id": project["id"],
                "supplier_id": meifeng["id"],
                "merchant_name_snapshot": meifeng["name"],
                "purchase_date": "2026-07-30",
                "payment_method": "对公转账",
                "payment_status": "未付款",
                "invoice_status": "有发票",
                "purchaser": "迁移测试",
                "freight_amount_cents": 1500,
                "notes": "税费运费临时库测试",
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
        assert order["material_amount_cents"] == 25000
        assert order["tax_amount_cents"] == 2500
        assert order["line_amount_cents"] == 27500
        assert order["freight_amount_cents"] == 1500
        assert order["project_cost_cents"] == 29000

        changed = project_profit_service.get_project_summary(project["id"])
        baseline_material = next(
            (
                row["amount_minor"]
                for row in baseline["purchase_material_breakdown"]
                if row["label"] == offer["name"]
            ),
            0,
        )
        changed_material = next(
            row
            for row in changed["purchase_material_breakdown"]
            if row["label"] == offer["name"]
        )
        assert changed_material["amount_minor"] - baseline_material == 27500
        assert sum(
            row["amount_minor"]
            for row in changed["purchase_material_breakdown"]
        ) == changed["purchase_tax_inclusive_material_minor"]
        assert (
            changed["purchase_material_minor"]
            - baseline["purchase_material_minor"]
            == 25000
        )
        assert changed["purchase_tax_minor"] - baseline["purchase_tax_minor"] == 2500
        assert (
            changed["purchase_freight_minor"]
            - baseline["purchase_freight_minor"]
            == 1500
        )
        assert changed["purchase_cost_minor"] - baseline["purchase_cost_minor"] == 29000
        assert changed["gross_profit_minor"] - baseline["gross_profit_minor"] == -29000

        other_changed = project_profit_service.get_project_summary(
            other_project["id"]
        )
        assert (
            other_changed["purchase_material_breakdown"]
            == other_baseline["purchase_material_breakdown"]
        )
        assert (
            other_changed["purchase_cost_minor"]
            == other_baseline["purchase_cost_minor"]
        )

        procurement_service.update_purchase_order(
            order_id,
            {
                "purchase_type": "正式采购",
                "project_id": project["id"],
                "supplier_id": meifeng["id"],
                "merchant_name_snapshot": meifeng["name"],
                "purchase_date": "2026-07-30",
                "payment_method": "对公转账",
                "payment_status": "已付款",
                "invoice_status": "有发票",
                "purchaser": "迁移测试",
                "freight_amount_cents": 2000,
                "notes": "修改税费运费测试",
            },
            {
                "product_id": offer["id"],
                "material_name_snapshot": offer["name"],
                "specification_snapshot": offer["specification"],
                "unit_snapshot": offer["unit"],
                "cost_category": "材料费",
                "quantity": 2.5,
                "material_unit_price_cents": 10000,
                "tax_rate_bps": 500,
                "purpose": "项目成本归集测试",
            },
        )
        updated = procurement_service.get_purchase_order(order_id)
        assert updated["material_amount_cents"] == 25000
        assert updated["tax_amount_cents"] == 1250
        assert updated["freight_amount_cents"] == 2000
        assert updated["project_cost_cents"] == 28250

        conn = get_connection()
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.close()

    print("Procurement tax and freight smoke test passed")


if __name__ == "__main__":
    main()
