"""Accounting guardrails on fresh synthetic databases, never the formal ledger."""

import copy
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch


class ProjectAccountingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="project_accounting_")
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "synthetic.db"
        environment = patch.dict(os.environ, {
            "SUPPLY_CHAIN_DB_PATH": str(self.database),
            "SUPPLY_CHAIN_ATTACHMENTS_PATH": str(Path(temporary.name) / "attachments"),
        })
        environment.start()
        self.addCleanup(environment.stop)

        from db import connection, migration_runner
        from db.schema import init_db
        from services import (
            contract_service, cost_service, finance_service, master_data_service,
            operations_service, project_accounting_service, project_ledger_service,
            project_profit_service, project_service,
        )
        from services.web_workbench_operations import database_fingerprint

        for module in (connection, migration_runner):
            override = patch.object(module, "DB_PATH", self.database)
            override.start()
            self.addCleanup(override.stop)
        init_db()
        self.connection = connection
        self.contracts = contract_service
        self.costs = cost_service
        self.finance = finance_service
        self.master = master_data_service
        self.operations = operations_service
        self.accounting = project_accounting_service
        self.ledger = project_ledger_service
        self.profit = project_profit_service
        self.projects = project_service
        self.fingerprint = database_fingerprint
        self.customer = master_data_service.create_business_partner({
            "legal_name": "合成核算客户", "roles": ["customer"],
        })

    def project(self, name, mode="contract", status="进行中", **extra):
        return self.projects.create_project({
            "name": name, "business_mode": mode, "status": status,
            "customer_partner_id": self.customer, **extra,
        })

    def contract(self, project, **extra):
        return self.contracts.create_contract({
            "project_id": project, "name": "合成核算合同", "amount": "500.00",
            "sign_date": "2026-01-01", "customer_partner_id": self.customer,
            "entity_id": 1, "status": "active", **extra,
        })

    def income(self, project, amount="100.01", contract=None):
        return self.contracts.create_settlement({
            "project_id": project, "contract_id": contract, "amount": amount,
            "settlement_date": "2026-10-01", "basis": "合成客户已确认工程价款",
        })

    def cost(self, project, amount="80.03"):
        return self.costs.create_cost({
            "project_id": project, "amount": amount, "cost_date": "2026-09-01",
            "category": "管理费", "counterparty_name": "合成费用收款方",
        })

    def receipt(self, project, settlement, amount="10.00"):
        return self.finance.create_receipt({
            "project_id": project, "settlement_id": settlement, "amount": amount,
            "contract_id": self.contracts.get_settlement(settlement)["contract_id"],
            "receipt_date": "2026-10-02", "entity_id": 1,
        })

    def test_cost_without_confirmation_remains_negative_book_profit(self):
        project = self.project("无收入但有成本")
        self.contract(project)
        self.cost(project)
        result = self.accounting.get_profit_dashboard(project)
        row = result["projects"][0]
        self.assertFalse(row["is_accountable"])
        self.assertEqual(row["accounting_status"], "待确认收入")
        self.assertIn("缺收入确认", row["accounting_note"])
        self.assertEqual(result["accounting_summary"], {
            "settlement_minor": 0, "receivable_minor": 0, "total_cost_minor": 8003,
            "gross_profit_minor": -8003, "confirmed_gross_profit_minor": 0,
            "pending_gross_profit_minor": -8003, "accountable_count": 0,
            "pending_count": 1, "project_count": 1,
        })
        profit = self.ledger.get_details(project)["details"]["profit"]
        self.assertEqual(profit["label"], "账面毛利（含待完善）")
        self.assertEqual(profit["note"], row["accounting_note"])
        self.assertEqual(profit["rows"], [{"label": "减：项目累计成本", "amount_minor": -8003}])
        self.assertTrue(profit["reconciled"])

    def test_confirmed_income_without_contract_remains_pending(self):
        project = self.project("未签合同已确认")
        self.income(project)
        self.cost(project, "20.02")
        row = self.accounting.get_profit_dashboard(project)["projects"][0]
        self.assertFalse(row["is_accountable"])
        self.assertEqual(row["accounting_status"], "待补合同")
        self.assertIn("缺有效合同分配", row["accounting_note"])
        self.assertEqual(row["gross_profit_minor"], 7999)
        self.assertEqual(row["settlement_minor"], 10001)
        self.assertEqual(self.ledger.list_projects(project)[0]["unsigned_minor"], 10001)

    def test_cash_confirmation_is_accountable_without_contract_invoice_or_receipt(self):
        project = self.project("现金完工", mode="cash")
        self.income(project)
        self.cost(project, "20.02")
        result = self.accounting.get_profit_dashboard(project)
        row = result["projects"][0]
        self.assertTrue(row["is_accountable"])
        self.assertEqual(row["accounting_status"], "已可核算")
        self.assertIn("无需合同分配", row["accounting_note"])
        self.assertEqual((row["contract_count"], row["invoice_count"], row["receipt_count"]), (0, 0, 0))
        self.assertEqual(result["accounting_summary"]["confirmed_gross_profit_minor"], 7999)
        self.assertEqual(result["accounting_summary"]["pending_gross_profit_minor"], 0)
        self.assertTrue(self.operations.get_accountability_map()[project])

    def test_receipt_does_not_substitute_for_cash_income_confirmation(self):
        project = self.project("现金预收未完工", mode="cash")
        self.finance.create_receipt({
            "project_id": project, "amount": "50.00", "receipt_date": "2026-10-02",
            "entity_id": 1, "allow_advance": True,
        })
        row = self.accounting.get_profit_dashboard(project)["projects"][0]
        self.assertFalse(row["is_accountable"])
        self.assertEqual(row["accounting_status"], "待确认收入")
        self.assertEqual((row["receipt_minor"], row["settlement_minor"]), (5000, 0))

    def test_totals_conserve_all_project_costs_including_pending_and_completed(self):
        signed = self.project("已完工已签", status="已完工")
        contract = self.contract(signed)
        self.income(signed, "100.01", contract)
        self.cost(signed, "120.02")
        cash = self.project("现金", mode="cash")
        self.income(cash, "60.02")
        self.cost(cash, "10.01")
        unsigned = self.project("待补合同")
        self.income(unsigned, "150.03")
        self.cost(unsigned, "40.04")
        no_income = self.project("待确认")
        self.cost(no_income, "80.05")
        self.project("待补全部资料")
        self.receipt(signed, self.contracts.list_settlements(project_id=signed)[0]["id"], "50.00")
        summary = self.accounting.get_profit_dashboard()["accounting_summary"]
        self.assertEqual(summary, {
            "settlement_minor": 31006, "receivable_minor": 26006, "total_cost_minor": 25012,
            "gross_profit_minor": 5994, "confirmed_gross_profit_minor": 3000,
            "pending_gross_profit_minor": 2994, "accountable_count": 2,
            "pending_count": 3, "project_count": 5,
        })
        self.assertEqual(summary["settlement_minor"] - summary["total_cost_minor"], summary["gross_profit_minor"])
        self.assertEqual(summary["confirmed_gross_profit_minor"] + summary["pending_gross_profit_minor"],
                         summary["gross_profit_minor"])
        self.assertEqual(summary, self.accounting.summarize_projects(self.ledger.list_projects()))

    def test_ledger_list_detail_and_profit_views_share_flags_notes_and_amounts(self):
        signed = self.project("有合同")
        self.income(signed, contract=self.contract(signed))
        unsigned = self.project("未签")
        self.income(unsigned)
        pending = self.project("只有成本")
        self.cost(pending)
        cash = self.project("现金", mode="cash")
        self.income(cash)
        ledger_rows = {row["id"]: row for row in self.ledger.list_projects()}
        board = self.accounting.get_profit_dashboard()
        for row in board["projects"]:
            project = row["id"]
            detail = self.ledger.get_details(project)
            single = self.accounting.get_profit_dashboard(project)
            with self.subTest(project=project):
                for key in (*self.accounting.ACCOUNTING_KEYS, "settlement_minor", "total_cost_minor", "gross_profit_minor"):
                    self.assertEqual(row[key], ledger_rows[project][key])
                    self.assertEqual(row[key], detail["project"][key])
                    self.assertEqual(row[key], detail["summary"][key])
                    self.assertEqual(row[key], single["projects"][0][key])
                    self.assertEqual(row[key], single["details"][key])
                self.assertEqual(detail["summary"]["cost_minor"], row["total_cost_minor"])
                self.assertTrue(all(values["reconciled"] for values in detail["details"].values()))
                self.assertEqual(row["gross_profit_minor"], self.profit.get_project_summary(project)["gross_profit_minor"])
                self.assertEqual(row["is_accountable"], self.operations.get_accountability_map()[project])

    def test_mixed_cost_sources_and_shared_costs_reconcile_without_pending_cost_loss(self):
        from web_workbench.synthetic import seed

        seed()
        original = self.profit.get_project_summary(1)
        self.assertGreater(original["purchase_cost_minor"], 0)
        self.assertGreater(original["labor_cost_minor"], 0)
        self.assertGreater(original["other_cost_minor"], 0)
        pending = self.project("多来源待确认项目")
        self.cost(pending, "10.01")
        self.costs.create_cost({
            "amount": "3.01", "cost_date": "2026-10-03", "category": "管理费",
            "allocation_method": "equal", "project_ids": [1, pending],
        })
        result = self.accounting.get_profit_dashboard()
        summary = result["accounting_summary"]
        self.assertEqual(summary["total_cost_minor"], original["total_cost_minor"] + 1001 + 301)
        self.assertEqual(summary, self.accounting.summarize_projects(self.ledger.list_projects()))
        for row in result["projects"]:
            detail = self.ledger.get_details(row["id"])
            self.assertEqual(detail["summary"]["cost_minor"], row["total_cost_minor"])
            self.assertTrue(detail["details"]["costs"]["reconciled"])
        self.assertLess(summary["pending_gross_profit_minor"], 0)

    def test_customer_filter_uses_real_ownership_and_totals_only_returned_projects(self):
        owned = self.project("本客户已确认", mode="cash")
        self.income(owned, "100.01")
        self.receipt(owned, self.contracts.list_settlements(project_id=owned)[0]["id"], "10.00")
        pending = self.project("本客户待确认")
        self.cost(pending, "20.02")
        other_customer = self.master.create_business_partner({
            "legal_name": "合成另一客户", "roles": ["customer"],
        })
        other = self.project("其他客户", mode="cash", customer_partner_id=other_customer)
        self.income(other, "999.99")
        unlinked = self.project("客户同名但未归属", mode="cash")
        self.income(unlinked, "777.77")
        with self.connection.db_transaction() as conn:
            conn.execute("UPDATE projects SET customer_partner_id=NULL, customer_name=? WHERE id=?",
                         ("合成核算客户", unlinked))

        result = self.accounting.get_profit_dashboard(customer_id=str(self.customer))
        self.assertEqual({row["id"] for row in result["projects"]}, {owned, pending})
        self.assertEqual(result["accounting_summary"], {
            "settlement_minor": 10001, "receivable_minor": 9001,
            "total_cost_minor": 2002, "gross_profit_minor": 7999,
            "confirmed_gross_profit_minor": 10001, "pending_gross_profit_minor": -2002,
            "accountable_count": 1, "pending_count": 1, "project_count": 2,
        })
        matching = self.accounting.get_profit_dashboard(owned, customer_id=self.customer)
        self.assertEqual(matching["details"]["project"]["id"], owned)
        self.assertEqual(matching["summary"], matching["accounting_summary"])
        for project_id, customer_id in ((other, self.customer), (owned, other_customer), (None, 999999)):
            with self.subTest(project_id=project_id, customer_id=customer_id):
                empty = self.accounting.get_profit_dashboard(project_id, customer_id=customer_id)
                self.assertEqual(empty["projects"], [])
                self.assertNotIn("details", empty)
                self.assertTrue(all(value == 0 for value in empty["accounting_summary"].values()))

    def test_legacy_portfolio_summary_and_other_keys_are_preserved(self):
        project = self.project("旧汇总兼容", mode="cash")
        self.income(project)
        portfolio = self.profit.get_portfolio_summary()
        portfolio["summary"] = {
            "contract_minor": 12345, "invoice_minor": 6789, "legacy_key": "keep",
        }
        original = copy.deepcopy(portfolio)
        with patch.object(self.profit, "get_portfolio_summary", return_value=portfolio):
            result = self.accounting.get_profit_dashboard(customer_id=self.customer)
        self.assertEqual(result["summary"], original["summary"])
        self.assertEqual(result["unassigned_labor"], original["unassigned_labor"])
        self.assertEqual(result["accounting_summary"]["gross_profit_minor"], 10001)
        self.assertEqual(portfolio, original)

    def test_decorator_supports_aliases_batches_reads_and_preserves_input_profit(self):
        project = self.project("格式兼容")
        rows = [
            {"id": project, "settlement_minor": 20, "cost_minor": 30, "gross_profit_minor": -10},
            {"project_id": project, "settlement_minor": 30, "total_cost_minor": 10, "gross_profit_minor": 777},
            {"project": {"id": project}, "settlement_minor": 0, "cost_minor": 9},
        ]
        original = copy.deepcopy(rows)
        with patch.object(self.accounting, "get_accounting_map", wraps=self.accounting.get_accounting_map) as read:
            decorated = self.accounting.decorate_projects(iter(rows))
        self.assertEqual(read.call_count, 1)
        self.assertEqual(rows, original)
        self.assertEqual([row["total_cost_minor"] for row in decorated], [30, 10, 9])
        self.assertEqual([row["gross_profit_minor"] for row in decorated], [-10, 777, -9])
        with patch.object(self.accounting, "get_accounting_map", side_effect=AssertionError("unexpected read")):
            summary = self.accounting.summarize_projects(iter(decorated))
        self.assertEqual(summary["gross_profit_minor"], 758)
        self.assertEqual(summary["total_cost_minor"], 49)

    def test_empty_rows_need_no_database_and_return_integer_zero_totals(self):
        with patch.object(self.accounting, "db_read", side_effect=AssertionError("unexpected read")):
            self.assertEqual(self.accounting.decorate_projects([]), [])
            summary = self.accounting.summarize_projects([])
        self.assertEqual(len(summary), 9)
        self.assertTrue(all(type(value) is int and value == 0 for value in summary.values()))

    def test_missing_project_cannot_silently_receive_a_profit_conclusion(self):
        with self.assertRaisesRegex(ValueError, "项目不存在"):
            self.accounting.decorate_projects([{
                "project_id": 999999, "settlement_minor": 1, "cost_minor": 0,
            }])

    def test_void_contract_or_allocation_does_not_make_confirmed_income_accountable(self):
        project = self.project("无效合同入口")
        contract = self.contract(project)
        self.income(project, contract=contract)
        self.cost(project)
        for void_target in ("allocation", "contract"):
            with self.subTest(void_target=void_target):
                with self.connection.db_transaction() as conn:
                    conn.execute("UPDATE contracts SET status=? WHERE id=?",
                                 ("void" if void_target == "contract" else "active", contract))
                    conn.execute("UPDATE contract_project_allocations SET status=? WHERE contract_id=?",
                                 ("void" if void_target == "allocation" else "active", contract))
                facts = self.accounting.get_accounting_map()[project]
                self.assertFalse(facts["is_accountable"])
                self.assertEqual(facts["contract_count"], 0)
                self.assertEqual(facts["accounting_status"], "待补合同")
                self.assertFalse(self.operations.get_accountability_map()[project])
                self.assertFalse(self.operations.get_project_guidance(project)["is_accountable"])
                row = self.ledger.list_projects(project)[0]
                self.assertFalse(row["is_accountable"])
                self.assertEqual((row["settlement_minor"], row["cost_minor"], row["gross_profit_minor"]),
                                 (10001, 8003, 1998))

    def test_completed_actual_contract_with_zero_allocation_is_valid(self):
        project = self.project("已完结据实合同")
        contract = self.contract(project, pricing_mode="actual", amount="0")
        self.income(project, contract=contract)
        with self.connection.db_transaction() as conn:
            conn.execute("UPDATE contracts SET status='completed' WHERE id=?", (contract,))
        row = self.ledger.list_projects(project)[0]
        self.assertTrue(row["is_accountable"])
        self.assertEqual((row["allocated_minor"], row["contract_count"], row["actual_contract_count"]), (0, 1, 1))

    def test_void_receipt_allocation_or_receipt_is_not_an_entry_fact(self):
        project = self.project("有效回款入口", mode="cash")
        income = self.income(project)
        receipt = self.receipt(project, income)
        for void_target in ("allocation", "receipt"):
            with self.subTest(void_target=void_target):
                with self.connection.db_transaction() as conn:
                    conn.execute("UPDATE receipts SET status=? WHERE id=?",
                                 ("void" if void_target == "receipt" else "active", receipt))
                    conn.execute("UPDATE receipt_allocations SET status=? WHERE receipt_id=?",
                                 ("void" if void_target == "allocation" else "active", receipt))
                facts = self.accounting.get_accounting_map()[project]
                self.assertEqual(facts["receipt_count"], 0)
                self.assertTrue(facts["is_accountable"])
                self.assertIn("缺回款记录", self.operations.get_project_guidance(project)["gaps"])
                row = self.ledger.list_projects(project)[0]
                self.assertEqual((row["receipt_count"], row["receipt_minor"]), (0, 0))

    def test_void_income_is_not_confirmation_and_does_not_drop_cost(self):
        project = self.project("收入作废", mode="cash")
        income = self.income(project)
        self.cost(project)
        with self.connection.db_transaction() as conn:
            conn.execute("UPDATE settlements SET status='void' WHERE id=?", (income,))
        result = self.accounting.get_profit_dashboard(project)
        self.assertFalse(result["projects"][0]["is_accountable"])
        self.assertEqual(result["accounting_summary"]["gross_profit_minor"], -8003)
        self.assertEqual(result["accounting_summary"]["total_cost_minor"], 8003)

    def _snapshot_case(self, view):
        project = self.project("并发只读快照", mode="cash")
        income = self.income(project)
        with closing(self.connection.get_connection()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("UPDATE settlements SET status='void' WHERE id=?", (income,))
            conn.commit()
        target, name = (self.profit, "get_portfolio_summary") if view == "profit" else (self.ledger, "project_finance_rows")
        original = getattr(target, name)

        def confirm_after_amounts(*args, **kwargs):
            result = original(*args, **kwargs)
            # A separate writer commits after amounts were read but before flags.
            with closing(sqlite3.connect(self.database)) as writer:
                writer.execute("PRAGMA foreign_keys=ON")
                writer.execute("UPDATE settlements SET status='active' WHERE id=?", (income,))
                writer.commit()
            return result

        with patch.object(target, name, side_effect=confirm_after_amounts):
            rows = self.accounting.get_profit_dashboard()["projects"] if view == "profit" else self.ledger.list_projects()
        row = next(row for row in rows if row["id"] == project)
        self.assertEqual(row["settlement_minor"], 0)
        self.assertFalse(row["is_accountable"])
        refreshed = self.accounting.get_profit_dashboard(project)["projects"][0]
        self.assertEqual(refreshed["settlement_minor"], 10001)
        self.assertTrue(refreshed["is_accountable"])

    def test_profit_amounts_and_guardrails_share_one_snapshot(self):
        self._snapshot_case("profit")

    def test_ledger_amounts_and_guardrails_share_one_snapshot(self):
        self._snapshot_case("ledger")

    def test_customer_filter_and_amounts_share_one_snapshot(self):
        project = self.project("客户筛选并发快照", mode="cash")
        self.income(project)
        other_customer = self.master.create_business_partner({
            "legal_name": "合成并发客户", "roles": ["customer"],
        })
        with closing(self.connection.get_connection()) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
        original = self.profit.get_portfolio_summary

        def change_customer_after_amounts():
            result = original()
            with closing(sqlite3.connect(self.database)) as writer:
                writer.execute("PRAGMA foreign_keys=ON")
                writer.execute("UPDATE projects SET customer_partner_id=? WHERE id=?", (other_customer, project))
                writer.commit()
            return result

        with patch.object(self.profit, "get_portfolio_summary", side_effect=change_customer_after_amounts):
            result = self.accounting.get_profit_dashboard(customer_id=self.customer)
        self.assertEqual([row["id"] for row in result["projects"]], [project])
        self.assertEqual(result["accounting_summary"]["settlement_minor"], 10001)
        self.assertEqual(self.accounting.get_profit_dashboard(customer_id=self.customer)["projects"], [])

    def test_batch_query_count_does_not_grow_per_project(self):
        self.project("第一个", mode="cash")

        def query_counts():
            counts = []
            for load in (self.accounting.get_accounting_map, self.ledger.list_projects,
                         self.accounting.get_profit_dashboard):
                statements = []
                with self.connection.db_read() as conn, self.connection.use_read_snapshot(conn):
                    conn.set_trace_callback(statements.append)
                    load()
                    conn.set_trace_callback(None)
                counts.append(sum(sql.lstrip().upper().startswith(("SELECT", "WITH")) for sql in statements))
            return counts

        before = query_counts()
        for index in range(12):
            self.project(f"批量合成项目{index}")
        self.assertEqual(query_counts(), before)
        self.assertEqual(before[0], 1)

    def test_queries_are_read_only_and_preserve_every_financial_fact(self):
        project = self.project("只读核对")
        self.income(project, contract=self.contract(project))
        self.cost(project)
        with self.connection.db_read() as conn:
            before = self.fingerprint(conn)
        with patch.object(self.connection, "get_connection", side_effect=AssertionError("write connection opened")):
            self.accounting.get_accounting_map()
            self.accounting.get_profit_dashboard()
            self.accounting.get_profit_dashboard(project)
            self.accounting.get_profit_dashboard(customer_id=self.customer)
            self.ledger.list_projects()
            self.ledger.get_details(project)
        with self.connection.db_read() as conn:
            self.assertEqual(self.fingerprint(conn), before)


if __name__ == "__main__":
    unittest.main()
