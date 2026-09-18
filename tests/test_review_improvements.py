import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from db.backup import backup_database


class BackupAndSortTests(unittest.TestCase):
    def test_backup_includes_committed_wal_and_does_not_overwrite(self):
        with tempfile.TemporaryDirectory() as folder:
            source, target = Path(folder)/"source.db", Path(folder)/"backup.db"
            conn = sqlite3.connect(source)
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA wal_autocheckpoint=0")
                conn.execute("CREATE TABLE facts(amount INTEGER)")
                conn.execute("INSERT INTO facts VALUES(12345)")
                conn.commit()
                backup_database(source, target)
                with closing(sqlite3.connect(target)) as snapshot:
                    self.assertEqual(snapshot.execute("SELECT amount FROM facts").fetchone()[0], 12345)
                with self.assertRaises(FileExistsError):
                    backup_database(source, target)
                with self.assertRaises(ValueError):
                    backup_database(source, source)
            finally:
                conn.close()

    def test_sort_money_percent_and_identifiers(self):
        from ui.components.table import display_sort_key
        self.assertEqual(sorted(["¥900.00", "¥10,000.00", "-¥200.00"], key=display_sort_key),
                         ["-¥200.00", "¥900.00", "¥10,000.00"])
        self.assertLess(display_sort_key("9.0%"), display_sort_key("80.0%"))
        self.assertEqual(display_sort_key("FP-2026-01")[0], 1)


class ReviewBusinessTests(unittest.TestCase):
    def setUp(self):
        import db.connection as connection
        from db.migration_runner import run_migrations
        from services import project_service, cost_service, procurement_service
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name)/"test.db"
        backup_database(connection.DB_PATH, self.database)
        run_migrations(self.database)
        self.original = connection.DB_PATH
        connection.DB_PATH = self.database
        self.addCleanup(setattr, connection, "DB_PATH", self.original)
        self.addCleanup(self.temp.cleanup)
        self.project = project_service
        self.cost = cost_service
        self.purchase = procurement_service
        self.ids = [self.project.create_project({"name": f"审计回归项目{i}"}) for i in range(3)]

    def test_cost_resize_preserves_total_and_history_and_rolls_back(self):
        cost_id = self.cost.create_cost({"category": "管理费", "cost_date": "2026-09-06", "amount": "10.00",
                                        "allocation_method": "equal", "project_ids": self.ids})
        data = {"category": "管理费", "cost_date": "2026-09-06", "amount": "10.01"}
        self.cost.update_cost_details(cost_id, data)
        self.assertEqual([line["amount_minor"] for line in self.cost.get_cost_allocations(cost_id)["lines"]], [334,334,333])
        with self.assertRaises(ValueError):
            self.cost.update_cost_details(cost_id, {**data, "amount": "0.01"})
        self.assertEqual(self.cost.get_cost_entry(cost_id)["amount_minor"], 1001)
        with closing(sqlite3.connect(self.database)) as conn:
            rows = conn.execute("SELECT previous_values_json, updated_values_json FROM cost_entry_revisions WHERE cost_entry_id=?", (cost_id,)).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0][0])["amount_minor"], 1000)
        self.assertEqual(json.loads(rows[0][1])["amount_minor"], 1001)

    def test_manual_direct_unassigned_cost_resize(self):
        for method in ("manual", "direct", "unassigned"):
            cost_id = self.cost.create_cost({"category":"用车", "cost_date":"2026-09-06", "amount":"10.00",
                "allocation_method":method, "project_ids":self.ids[:1],
                "allocations":[{"project_id":self.ids[0],"amount":"3.00"},{"project_id":self.ids[1],"amount":"7.00"}]})
            self.cost.update_cost_details(cost_id, {"category":"用车","cost_date":"2026-09-06","amount":"10.01"})
            amounts = [row["amount_minor"] for row in self.cost.get_cost_allocations(cost_id)["lines"]]
            self.assertEqual(amounts, {"manual":[300,701], "direct":[1001], "unassigned":[]}[method])

    def test_failed_import_leaves_no_project_and_success_reuses_project(self):
        header = {"purchase_type":"零星采购", "purchase_date":"2026-09-06", "merchant_name_snapshot":"测试商户", "project_name":"导入事务项目", "order_no":"REVIEW-IMPORT"}
        item = {"material_name_snapshot":"测试材料", "quantity":1, "unit_price_cents":100}
        first = self.purchase.add_purchase_order(header, item)
        with self.assertRaises(sqlite3.IntegrityError):
            self.purchase.add_purchase_order({**header, "project_name":"失败不留空项目"}, item)
        self.assertNotIn("失败不留空项目", [row["name"] for row in self.project.list_projects()])
        self.purchase.add_purchase_order({**header, "order_no":"REVIEW-IMPORT-2"}, item)
        self.assertEqual(sum(row["name"]=="导入事务项目" for row in self.project.list_projects()), 1)

    def test_attachment_missing_file_is_not_reported_available(self):
        from services import attachment_service, finance_service, contract_service
        project_id = self.ids[0]
        contract_id = contract_service.create_contract({"name":"附件测试合同","contract_type":"project","sign_date":"2026-09-06","amount":"100","status":"active"})
        contract_service.create_allocation({"contract_id":contract_id,"project_id":project_id,"amount":"100"})
        contract_service.create_settlement({"contract_id":contract_id,"project_id":project_id,"settlement_date":"2026-09-06","amount":"100"})
        invoice_id = finance_service.create_invoice({"contract_id":contract_id,"project_id":project_id,"invoice_date":"2026-09-06","amount":"100"})
        from unittest.mock import patch
        source = Path(self.temp.name)/"receipt.txt"
        source.write_text("test", encoding="utf-8")
        with patch.dict("os.environ", {"SUPPLY_CHAIN_ATTACHMENTS_PATH":str(Path(self.temp.name)/"files")}):
            attachment_id = attachment_service.add_attachment("invoice", invoice_id, source)
        self.assertFalse(finance_service.list_invoices(project_id)[0]["attachment_needs_attention"])
        stored = Path(attachment_service.list_attachments("invoice", invoice_id)[0]["absolute_path"])
        stored.unlink()
        invoice = finance_service.list_invoices(project_id)[0]
        self.assertEqual(invoice["missing_attachment_count"], 1)
        self.assertTrue(invoice["attachment_needs_attention"])
        attachment_service.void_attachments([attachment_id])
        self.assertEqual(finance_service.list_invoices(project_id)[0]["attachment_status"], "未上传")
