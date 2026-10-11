"""Cockpit checks use synthetic facts, never write to the formal ledger."""

import tempfile
import logging
import json
import threading
import urllib.request
import unittest
from pathlib import Path
from unittest.mock import patch

from db import connection, migration_runner
from db.schema import init_db
from services import cockpit_service as cockpit, web_workbench_service as business
from services.web_workbench_operations import database_fingerprint
from web_workbench.synthetic import seed


class CockpitTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="cockpit_")
        self.addCleanup(folder.cleanup)
        path = Path(folder.name) / "synthetic.db"
        self.path = path
        for module in (connection, migration_runner):
            db_patch = patch.object(module, "DB_PATH", path)
            db_patch.start()
            self.addCleanup(db_patch.stop)
        with patch.object(logging.getLogger("db.migration_runner"), "disabled", True):
            init_db()
        seed()
        business.save_record("receipts", {"project_id": 1, "contract_id": 1, "settlement_id": 1,
            "amount": "25000.01", "receipt_date": "2026-10-10", "payment_method": "银行转账"})

    def test_every_card_reconciles_and_does_not_write(self):
        with connection.db_read() as conn:
            before = database_fingerprint(conn)
        for scope in (None, 1):
            data = business.dashboards("home", scope, "2026-10")["cockpit"]
            self.assertEqual(len(data["metrics"]), 8)
            for metric in data["metrics"]:
                with self.subTest(scope=scope, metric=metric["key"]):
                    detail = cockpit.get_detail(metric["key"], scope)
                    self.assertEqual(metric["amount_minor"], detail["total_minor"])
                    self.assertEqual(detail["total_minor"], sum(row["amount_minor"] for row in detail["rows"]))
            self.assertEqual(sum(row["amount_minor"] for row in data["composition"]),
                             next(row["amount_minor"] for row in data["metrics"] if row["key"] == "costs"))
        with connection.db_read() as conn:
            self.assertEqual(database_fingerprint(conn), before)

    def test_completed_cash_income_is_visible_but_not_pending_invoice(self):
        project = business.save_record("projects", {"name": "合成已完工现金工程", "status": "已完工",
            "business_mode": "cash", "invoice_policy": "not_required"})
        business.save_record("settlements", {"project_id": project, "amount": "75000",
            "settlement_date": "2026-10-10", "basis": "合成最终结算"})
        data = cockpit.get_overview(project, "2026-10")["cockpit"]
        values = {row["key"]: row["amount_minor"] for row in data["metrics"]}
        self.assertEqual(values["income"], 7_500_000)
        self.assertEqual(values["outstanding"], 7_500_000)
        self.assertEqual(values["uninvoiced"], 0)
        self.assertEqual(data["active_count"], 0)
        self.assertEqual(data["projects"], [])
        self.assertEqual(data["priorities"][0]["project_id"], project)

    def test_cost_split_conserves_cents_and_unassigned_stays_global(self):
        other = business.save_record("projects", {"name": "合成第二项目"})
        business.save_record("costs", {"project_ids": [1, other], "allocation_method": "equal",
            "amount": "10.01", "category": "管理费", "cost_date": "2026-10-11"})
        business.save_record("costs", {"amount": "15.73", "category": "管理费", "cost_date": "2026-10-11"})
        first = cockpit.get_detail("costs", 1, "2026-10")
        second = cockpit.get_detail("costs", other, "2026-10")
        self.assertEqual(second["total_minor"], 500)
        self.assertEqual(first["rows"][0]["amount_minor"], 501)
        self.assertEqual(cockpit.get_detail("unassigned")["total_minor"], 1573)
        self.assertEqual(cockpit.get_detail("unassigned", 1)["total_minor"], 0)

    def test_trend_and_month_drilldowns_match_including_year_boundary(self):
        data = cockpit.get_overview(month="2026-10")["cockpit"]
        self.assertEqual([row["month"] for row in data["trend"]],
                         [f"2026-{month:02d}" for month in range(5, 11)])
        for row in data["trend"]:
            for metric, key in (("income", "income_minor"), ("receipts", "receipt_minor"), ("costs", "cost_minor")):
                self.assertEqual(row[key], cockpit.get_detail(metric, month=row["month"])["total_minor"])
        earlier = cockpit.get_overview(month="2026-01")["cockpit"]
        self.assertEqual(earlier["trend"][0]["month"], "2025-08")
        self.assertEqual(earlier["trend"][-1]["month"], "2026-01")

    def test_void_receipt_and_allocations_are_excluded(self):
        with connection.db_transaction() as conn:
            conn.execute("UPDATE receipt_allocations SET status='void'")
        self.assertEqual(cockpit.get_detail("receipts")["total_minor"], 0)
        data = cockpit.get_overview()["cockpit"]
        self.assertEqual(next(row["amount_minor"] for row in data["metrics"] if row["key"] == "receipts"), 0)
        self.assertEqual(cockpit.get_detail("outstanding")["total_minor"], 10_000_000)

    def test_invalid_metric_month_and_project_are_rejected(self):
        with self.assertRaises(ValueError):
            cockpit.get_detail("sqlite_master")
        with self.assertRaises(ValueError):
            cockpit.get_overview(month="2026-13")
        with self.assertRaises(ValueError):
            cockpit.get_detail("outstanding", month="2026-10")
        with self.assertRaises(ValueError):
            cockpit.get_overview(99999)

    def test_http_assets_and_detail_are_available_in_readonly_mode(self):
        from web_workbench.server import WorkbenchServer
        server = WorkbenchServer(0, read_only=True, demo=True, staging=self.path.parent / "staging")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for path in ("/cockpit.js", "/cockpit.css"):
                with urllib.request.urlopen(server.origin + path) as response:
                    self.assertEqual(response.status, 200)
                    self.assertTrue(response.read())
            with urllib.request.urlopen(server.origin + "/api/cockpit_detail?metric=receipts&project_id=1") as response:
                data = json.load(response)
            self.assertEqual(data["total_minor"], "2500001")
            self.assertEqual(data["rows"][0]["amount_minor"], "2500001")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
