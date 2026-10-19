"""Project ledger acceptance uses fresh synthetic ledgers, never production writes."""

import tempfile
import threading
import unittest
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

from db import connection, migration_runner
from db.schema import init_db
from services import project_ledger_service as ledger, web_workbench_service as business
from services.web_workbench_operations import database_fingerprint
from web_workbench.synthetic import seed


class ProjectLedgerTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="project_ledger_")
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "synthetic.db"
        for module in (connection, migration_runner):
            db_patch = patch.object(module, "DB_PATH", self.path)
            db_patch.start()
            self.addCleanup(db_patch.stop)
        init_db()
        seed()

    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_scope_blur_does_not_swallow_ledger_clicks(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([shutil.which("node"), "scripts/test_search_choice.mjs"],
                                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js unavailable")
    def test_floating_header_geometry_and_lifecycle(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([shutil.which("node"), "scripts/test_project_ledger_header.mjs"],
                                cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def receipt(self, amount="25000.01"):
        return business.save_record("receipts", {
            "project_id": 1, "contract_id": 1, "settlement_id": 1,
            "amount": amount, "receipt_date": "2026-10-10",
            "payment_method": "银行转账", "payer_name": "合成到账客户",
        })

    def test_list_and_details_reconcile_without_writes(self):
        self.receipt()
        with connection.db_read() as conn:
            before = database_fingerprint(conn)
        rows = business.rows("projects")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["settlement_minor"], row["invoice_minor"], row["receipt_minor"],
                          row["receivable_minor"]), (10_000_000, 10_000_000, 2_500_001, 7_499_999))
        self.assertEqual((row["settlement_count"], row["invoice_count"], row["receipt_count"]), (1, 1, 1))
        detail = ledger.get_details(1)
        for kind, values in detail["details"].items():
            with self.subTest(kind=kind):
                self.assertTrue(values["reconciled"])
                self.assertEqual(values["total_minor"], sum(r[values["amount_key"]] for r in values["rows"]))
        with connection.db_read() as conn:
            self.assertEqual(database_fingerprint(conn), before)

    def test_scoped_search_and_completed_project_remain_available(self):
        second = business.save_record("projects", {"name": "合成已完工项目", "status": "已完工"})
        self.assertEqual([r["id"] for r in business.rows("projects", second)], [second])
        self.assertEqual([r["id"] for r in business.rows("projects", keyword="已完工")], [second])
        empty = ledger.get_details(second)
        self.assertTrue(all(not d["rows"] and d["total_minor"] == 0 for d in empty["details"].values()))
        with self.assertRaisesRegex(ValueError, "项目不存在"):
            ledger.get_details(99999)

    def test_shared_receipt_uses_project_share_and_counts_one_receipt(self):
        receipt_id = self.receipt("100.00")
        second = business.save_record("projects", {"name": "合成另一项目"})
        with connection.db_transaction() as conn:
            original = dict(conn.execute("SELECT * FROM receipt_allocations WHERE receipt_id=?", (receipt_id,)).fetchone())
            conn.execute("UPDATE receipt_allocations SET allocated_amount_minor=3000 WHERE id=?", (original["id"],))
            columns = [key for key in original if key != "id"]
            values = {**original, "public_id": "synthetic-second-share", "project_id": second,
                      "contract_id": None, "settlement_id": None, "allocated_amount_minor": 7000}
            conn.execute("INSERT INTO receipt_allocations (" + ",".join(columns) + ") VALUES (" +
                         ",".join("?" for _ in columns) + ")", [values[key] for key in columns])
        first = ledger.get_details(1)
        other = ledger.get_details(second)
        self.assertEqual(first["summary"]["receipt_minor"], 3000)
        self.assertEqual(other["summary"]["receipt_minor"], 7000)
        for data, amount in ((first, 3000), (other, 7000)):
            self.assertEqual(data["project"]["receipt_count"], 1)
            rows = data["details"]["receipts"]["rows"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["amount_minor"], 10000)
            self.assertEqual(rows[0]["project_amount_minor"], amount)
            self.assertTrue(data["details"]["receipts"]["reconciled"])

    def test_voided_facts_and_allocations_are_excluded(self):
        self.receipt("100.00")
        with connection.db_transaction() as conn:
            conn.execute("UPDATE receipt_allocations SET status='void'")
        self.assertEqual(ledger.get_details(1)["summary"]["receipt_minor"], 0)
        self.assertEqual(ledger.get_details(1)["details"]["receipts"]["rows"], [])
        with connection.db_transaction() as conn:
            conn.execute("UPDATE receipt_allocations SET status='active'")
            conn.execute("UPDATE receipts SET status='void'")
            conn.execute("UPDATE sales_invoices SET status='void'")
            conn.execute("UPDATE settlements SET status='void'")
        project = ledger.list_projects()[0]
        for key in ('settlement_minor','invoice_minor','receipt_minor','receivable_minor',
                    'settlement_count','invoice_count','receipt_count'):
            self.assertEqual(project[key], 0)
        details = ledger.get_details(1)['details']
        self.assertTrue(all(not details[key]['rows'] for key in ('settlements','invoices','receipts','outstanding')))
        self.assertEqual(project['allocated_minor'], 50000000)
        self.assertEqual(project['gross_profit_minor'], -project['cost_minor'])

    def test_multiple_allocations_count_a_receipt_once(self):
        receipt_id = self.receipt("100.00")
        with connection.db_transaction() as conn:
            original = dict(conn.execute("SELECT * FROM receipt_allocations WHERE receipt_id=?",
                                         (receipt_id,)).fetchone())
            conn.execute("UPDATE receipt_allocations SET allocated_amount_minor=3000 WHERE id=?",
                         (original["id"],))
            columns = [key for key in original if key != "id"]
            values = {**original, "public_id": "synthetic-additional-share",
                      "allocated_amount_minor": 7000}
            conn.execute("INSERT INTO receipt_allocations (" + ",".join(columns) + ") VALUES (" +
                         ",".join("?" for _ in columns) + ")", [values[key] for key in columns])
        result = ledger.get_details(1)
        self.assertEqual(result["project"]["receipt_count"], 1)
        self.assertEqual(result["summary"]["receipt_minor"], 10000)
        self.assertEqual(len(result["details"]["receipts"]["rows"]), 1)
        self.assertTrue(all(detail["reconciled"] for detail in result["details"].values()))

    def test_unallocated_receipts_do_not_hide_outstanding_income(self):
        self.receipt("100.00")
        with connection.db_transaction() as conn:
            conn.execute("UPDATE receipt_allocations SET settlement_id=NULL")
        result = ledger.get_details(1)
        self.assertEqual(result["summary"]["receipt_minor"], 10000)
        self.assertEqual(result["summary"]["receivable_minor"], 10_000_000)
        self.assertEqual(result["project"]["pending_receipt_minor"], 10000)
        self.assertEqual(result["details"]["outstanding"]["rows"][0]["unreceived_minor"], 10_000_000)
        self.assertTrue(all(detail["reconciled"] for detail in result["details"].values()))

    def test_cash_receipt_without_invoice_does_not_invent_income(self):
        cash = business.save_record("projects", {"name": "合成现金项目", "business_mode": "cash"})
        business.save_record("receipts", {"project_id": cash, "amount": "80.01",
            "receipt_date": "2026-10-10", "payment_method": "现金", "allow_advance": True})
        result = ledger.get_details(cash)
        self.assertEqual(result["summary"]["receipt_minor"], 8001)
        self.assertEqual(result["summary"]["invoice_minor"], 0)
        self.assertEqual(result["summary"]["settlement_minor"], 0)

    def test_http_assets_and_drilldown_are_readonly(self):
        import json
        import urllib.request
        from web_workbench.server import WorkbenchServer
        server = WorkbenchServer(0, read_only=True, demo=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with connection.db_read() as conn:
                before = database_fingerprint(conn)
            for path in ("/project-ledger.js", "/project-ledger.css"):
                with urllib.request.urlopen(server.origin + path) as response:
                    self.assertEqual(response.status, 200)
            with urllib.request.urlopen(server.origin + "/api/project_ledger?id=1") as response:
                self.assertTrue(json.load(response)["details"]["invoices"]["reconciled"])
            with connection.db_read() as conn:
                self.assertEqual(database_fingerprint(conn), before)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
