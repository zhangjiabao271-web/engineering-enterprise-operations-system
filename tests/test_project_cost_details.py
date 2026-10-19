import tempfile
import unittest
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from db import connection
from db.backup import backup_database
from db.migration_runner import run_migrations
from services import (
    cost_service,
    master_data_service,
    procurement_service,
    project_cost_detail_service,
    project_profit_service,
    project_service,
)


class ProjectCostDetailTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="project_cost_detail_")
        self.addCleanup(folder.cleanup)
        target = Path(folder.name) / "supplier_data.db"
        backup_database(connection.DB_PATH, target)
        run_migrations(target)
        db_patch = patch.object(connection, "DB_PATH", target)
        db_patch.start()
        self.addCleanup(db_patch.stop)
        self.projects = [
            project_service.create_project({"name": f"成本下钻-{uuid4().hex[:8]}"})
            for _ in range(2)
        ]

    def _order(self, *, project_id=None, project_ids=None, date="2026-01-31"):
        header = {
            "purchase_type": "零星采购",
            "project_id": project_id,
            "merchant_name_snapshot": "测试材料店",
            "purchase_date": date,
            "payment_status": "未付款",
            "invoice_status": "无发票",
            "freight_amount_cents": 7,
        }
        if project_ids:
            header.update({"project_ids": project_ids, "allocation_method": "equal"})
        return procurement_service.add_purchase_order(header, [
            {"material_name_snapshot": "钢管", "quantity": 1,
             "material_unit_price_cents": 101, "tax_rate_bps": 1300,
             "cost_category": "工具和设备", "purpose": "安装"},
            {"material_name_snapshot": "螺栓", "quantity": 1,
             "material_unit_price_cents": 203, "tax_rate_bps": 1300,
             "cost_category": "工具和设备"},
        ])

    def test_full_lifecycle_and_shared_costs_reconcile(self):
        first, second = self.projects
        direct_order = self._order(project_id=first)
        shared_order = self._order(project_ids=self.projects, date="2026-02-01")
        cost_service.create_cost({
            "project_id": first, "cost_date": "2026-02-03",
            "category": "管理费", "amount": "20.01",
            "counterparty_name": "测试会计", "notes": "代理记账",
        })
        cost_service.create_cost({
            "cost_date": "2026-03-01", "category": "用车",
            "amount": "10.01", "allocation_method": "equal",
            "project_ids": self.projects,
        })
        with connection.db_transaction() as conn:
            worker = conn.execute("SELECT id FROM workers ORDER BY id LIMIT 1").fetchone()
            if worker is None:
                worker_id = conn.execute(
                    "INSERT INTO workers(name) VALUES ('成本明细测试工人')"
                ).lastrowid
            else:
                worker_id = worker[0]
            conn.execute(
                """INSERT INTO work_logs(worker_id, work_date, construction_site,
                       project_id, work_days, amount, amount_minor, status)
                   VALUES (?, '2026-02-04', '测试现场', ?, 0.5, 12.35, NULL, 'active')""",
                (worker_id, first),
            )
            conn.execute(
                """INSERT INTO work_logs(worker_id, work_date, construction_site,
                       project_id, work_days, amount, amount_minor, status)
                   VALUES (?, '2026-02-05', '测试现场', ?, 1, 50, 5000, 'void')""",
                (worker_id, first),
            )

        for project_id in self.projects:
            detail = project_cost_detail_service.get_project_cost_details(project_id)
            summary = project_profit_service.get_project_summary(project_id)
            for key, summary_key in (
                ("purchase", "purchase_cost_minor"),
                ("labor", "labor_cost_minor"),
                ("other", "other_cost_minor"),
            ):
                self.assertEqual(detail["totals"][key], summary[summary_key])
            self.assertEqual(detail["total_cost_minor"], summary["total_cost_minor"])
            self.assertEqual(
                sum(row["amount_minor"] for row in detail["purchase"]),
                detail["totals"]["purchase"],
            )
        first_detail = project_cost_detail_service.get_project_cost_details(first)
        self.assertEqual({row["date"] for row in first_detail["purchase"]},
                         {"2026-01-31", "2026-02-01"})
        self.assertEqual(first_detail["labor"][0]["amount_minor"], 1235)
        self.assertEqual(len(first_detail["labor"]), 1)
        self.assertTrue(any(row["notes"] == "代理记账" for row in first_detail["other"]))
        self.assertTrue(any(row["allocation_method"] == "equal" for row in first_detail["other"]))
        shared_rows = [row for row in first_detail["purchase"]
                       if row["source_no"] == procurement_service.get_purchase_order(shared_order)["order_no"]]
        self.assertEqual(len(shared_rows), 3)
        self.assertEqual(sum(row["amount_minor"] for row in shared_rows),
                         procurement_service.get_purchase_allocations(shared_order)[0]["amount_minor"])
        with connection.db_read() as conn:
            share = conn.execute(
                """SELECT material_minor, tax_minor, freight_minor
                   FROM purchase_project_costs
                   WHERE purchase_order_id=? AND project_id=?""",
                (shared_order, first),
            ).fetchone()
        for component in ("material", "tax", "freight"):
            self.assertEqual(
                sum(row[f"{component}_minor"] for row in shared_rows),
                share[f"{component}_minor"],
            )
        self.assertTrue(all(row["payment_status"] == "未付款" for row in shared_rows))
        self.assertEqual(len([row for row in first_detail["purchase"]
                              if row["source_no"] == procurement_service.get_purchase_order(direct_order)["order_no"]]), 3)

    def test_void_purchase_not_in_details(self):
        order_id = self._order(project_id=self.projects[0])
        procurement_service.void_purchase_orders([order_id])
        cost_id = cost_service.create_cost({
            "project_id": self.projects[0], "cost_date": "2026-02-03",
            "category": "管理费", "amount": "1.00",
        })
        cost_service.void_costs([cost_id])
        detail = project_cost_detail_service.get_project_cost_details(self.projects[0])
        self.assertFalse(detail["purchase"])
        self.assertFalse(detail["other"])

    def test_unknown_project(self):
        with self.assertRaisesRegex(ValueError, "项目不存在"):
            project_cost_detail_service.get_project_cost_details(-1)

    def test_original_prices_and_line_amounts_are_not_project_shares(self):
        order_id = procurement_service.add_purchase_order({
            "purchase_type": "零星采购", "project_ids": self.projects,
            "merchant_name_snapshot": "测试材料店",
            "allocation_method": "equal", "purchase_date": "2026-10-10",
            "payment_status": "已付款", "freight_amount_cents": 7711,
        }, [
            {"material_name_snapshot": "彩钢瓦", "quantity": 996,
             "unit_snapshot": "米", "price_basis": "inclusive",
             "tax_inclusive_unit_price_cents": 189, "tax_rate_bps": 900,
             "cost_category": "工具和设备"},
            {"material_name_snapshot": "螺杆", "quantity": 24,
             "unit_snapshot": "支", "price_basis": "exclusive",
             "material_unit_price_cents": 970, "tax_rate_bps": 1000,
             "cost_category": "工具和设备"},
        ])
        snapshots = procurement_service.get_purchase_order(order_id)
        shares = []
        for project_id in self.projects:
            detail = project_cost_detail_service.get_project_cost_details(project_id)
            rows = detail["purchase"]
            self.assertEqual([row["unit_price_cents"] for row in rows[:2]], [189, 970])
            self.assertEqual([row["price_basis"] for row in rows[:2]], ["inclusive", "exclusive"])
            self.assertEqual([row["line_amount_minor"] for row in rows], [188244, 25608, 7711])
            self.assertTrue(all(row["purchase_order_id"] == order_id for row in rows))
            self.assertTrue(all(row["allocation_method"] == "equal" for row in rows))
            self.assertTrue(all(row["amount_minor"] < row["line_amount_minor"] for row in rows))
            self.assertEqual(rows[-1]["settlement_mode"], "freight")
            self.assertIsNone(rows[-1]["quantity"])
            shares.append(detail["totals"]["purchase"])
        self.assertEqual(sum(shares), 188244 + 25608 + 7711)
        self.assertEqual(procurement_service.get_purchase_order(order_id), snapshots)

    def test_weight_and_total_prices_keep_original_precision_and_basis(self):
        order_id = procurement_service.add_purchase_order({
            "purchase_type": "零星采购", "project_id": self.projects[0],
            "merchant_name_snapshot": "测试材料店",
            "purchase_date": "2026-10-10", "payment_status": "未付款",
        }, [
            {"material_name_snapshot": "H型钢", "settlement_mode": "weight",
             "net_weight": "1.257", "weight_unit": "吨", "weight_unit_price": "3568.1234",
             "price_basis": "exclusive", "tax_rate_bps": 1300, "weigh_ticket_no": "B-001"},
            {"material_name_snapshot": "整批钢板", "settlement_mode": "total",
             "settlement_total_cents": 988888, "price_basis": "inclusive", "tax_rate_bps": 1300},
        ])
        with connection.db_read() as conn:
            conn.execute("PRAGMA query_only=ON")
            rows = project_cost_detail_service._purchase_rows(conn, self.projects[0])
            self.assertEqual(conn.total_changes, 0)
        weight, total = rows
        self.assertEqual(weight["quantity"], "1.257")
        self.assertEqual(weight["unit"], "吨")
        self.assertEqual(weight["unit_price_yuan"], "3568.1234")
        self.assertIsNone(weight["unit_price_cents"])
        self.assertEqual(weight["weigh_ticket_no"], "B-001")
        self.assertIsNone(total["unit_price_cents"])
        self.assertIsNone(total["unit_price_yuan"])
        self.assertEqual(total["settlement_mode"], "total")
        self.assertEqual(total["line_amount_minor"], 988888)
        self.assertEqual([row["line_amount_minor"] for row in rows],
                         [item["line_amount_cents"] for item in procurement_service.get_purchase_order(order_id)["items"]])
        self.assertTrue(all(row["line_amount_minor"] == row["amount_minor"] for row in rows))

    def test_linked_supplier_name_is_shown_without_merchant_snapshot(self):
        name = f"成本明细供应商-{uuid4().hex[:8]}"
        supplier = master_data_service.create_supplier({"name": name})
        procurement_service.add_purchase_order({
            "purchase_type": "正式采购", "project_id": self.projects[0],
            "supplier_id": supplier, "merchant_name_snapshot": "",
            "purchase_date": "2026-10-10", "payment_status": "未付款",
        }, [{"material_name_snapshot": "螺栓", "quantity": 1, "unit_price_cents": 100}])
        detail = project_cost_detail_service.get_project_cost_details(self.projects[0])
        self.assertEqual(detail["purchase"][0]["counterparty"], name)


class ProjectPurchaseRenderingTests(unittest.TestCase):
    def test_purchasing_figures_render_without_database_or_network(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node runtime unavailable")
        script = Path(__file__).resolve().parents[1] / "scripts/test_project_purchase_details.mjs"
        result = subprocess.run([node, str(script)], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)




if __name__ == "__main__":
    unittest.main()
