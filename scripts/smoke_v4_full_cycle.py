import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def expect_value_error(action):
    try:
        action()
    except ValueError:
        return
    raise AssertionError("Expected ValueError")


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test the complete V4 project operating cycle"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="v4_full_cycle_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database
        from db.connection import get_connection
        from services import (
            contract_service,
            cost_service,
            finance_service,
            operations_service,
            project_profit_service,
            project_service,
        )

        database.init_db()
        projects = project_service.list_projects(active_only=True)
        assert len(projects) >= 2
        project = projects[0]
        other_project = projects[1]
        baseline = project_profit_service.get_project_summary(project["id"])
        other_baseline = project_profit_service.get_project_summary(
            other_project["id"]
        )

        contract_id = contract_service.create_contract(
            {
                "contract_no": "TEST-V4-ANNUAL-001",
                "name": "V4 年度合同测试",
                "contract_type": "annual",
                "sign_date": "2026-07-30",
                "start_date": "2026-01-01",
                "end_date": "2026-12-31",
                "amount": "10000.00",
                "status": "active",
                "customer_name": "测试客户",
            }
        )
        allocation_id = contract_service.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project["id"],
                "amount": "8000.00",
                "notes": "只分配到当前项目",
            }
        )
        expect_value_error(
            lambda: contract_service.create_allocation(
                {
                    "contract_id": contract_id,
                    "project_id": other_project["id"],
                    "amount": "3000.00",
                }
            )
        )
        settlement_id = contract_service.create_settlement(
            {
                "settlement_no": "TEST-V4-CH-001",
                "contract_id": contract_id,
                "project_id": project["id"],
                "settlement_date": "2026-07-30",
                "period_start": "2026-07-01",
                "period_end": "2026-07-30",
                "amount": "6000.00",
                "basis": "测试结算依据",
            }
        )
        invoice_id = finance_service.create_invoice(
            {
                "invoice_no": "TEST-V4-FP-001",
                "contract_id": contract_id,
                "project_id": project["id"],
                "invoice_date": "2026-07-30",
                "amount": "5000.00",
                "tax_rate": "10",
                "buyer_name": "测试客户",
            }
        )
        expect_value_error(
            lambda: finance_service.create_invoice(
                {
                    "invoice_no": "TEST-V4-FP-OVER",
                    "contract_id": contract_id,
                    "project_id": project["id"],
                    "invoice_date": "2026-07-30",
                    "amount": "1001.00",
                    "tax_rate": "10",
                }
            )
        )
        receipt_id = finance_service.create_receipt(
            {
                "receipt_no": "TEST-V4-HK-001",
                "contract_id": contract_id,
                "project_id": project["id"],
                "invoice_id": invoice_id,
                "receipt_date": "2026-07-30",
                "amount": "4000.00",
                "payer_name": "测试客户",
                "payment_method": "银行转账",
            }
        )
        expect_value_error(
            lambda: finance_service.create_receipt(
                {
                    "receipt_no": "TEST-V4-HK-INVOICE-OVER",
                    "contract_id": contract_id,
                    "project_id": project["id"],
                    "invoice_id": invoice_id,
                    "receipt_date": "2026-07-30",
                    "amount": "1001.00",
                }
            )
        )
        expect_value_error(
            lambda: finance_service.create_receipt(
                {
                    "receipt_no": "TEST-V4-HK-OVER",
                    "contract_id": contract_id,
                    "project_id": project["id"],
                    "receipt_date": "2026-07-30",
                    "amount": "2001.00",
                }
            )
        )
        cost_id = cost_service.create_cost(
            {
                "cost_no": "TEST-V4-CB-001",
                "project_id": project["id"],
                "cost_date": "2026-07-30",
                "category": "分包费",
                "amount": "1000.00",
                "counterparty_name": "测试分包商",
            }
        )
        summary = project_profit_service.get_project_summary(project["id"])
        assert summary["contract_minor"] - baseline["contract_minor"] == 800_000
        assert summary["settlement_minor"] - baseline["settlement_minor"] == 600_000
        assert summary["invoice_minor"] - baseline["invoice_minor"] == 500_000
        assert summary["receipt_minor"] - baseline["receipt_minor"] == 400_000
        assert summary["other_cost_minor"] - baseline["other_cost_minor"] == 100_000
        assert summary["gross_profit_minor"] - baseline["gross_profit_minor"] == 500_000
        assert summary["receivable_minor"] - baseline["receivable_minor"] == 200_000
        assert summary["cash_balance_minor"] - baseline["cash_balance_minor"] == 400_000

        untouched = project_profit_service.get_project_summary(
            other_project["id"]
        )
        for key in (
            "contract_minor",
            "settlement_minor",
            "invoice_minor",
            "receipt_minor",
            "other_cost_minor",
        ):
            assert untouched[key] == other_baseline[key]

        overview = operations_service.get_executive_overview("2026-07")
        row = next(
            item
            for item in overview["projects"]
            if item["project_id"] == project["id"]
        )
        assert row["is_accountable"]
        assert row["stage_code"] == "receipt"
        expect_value_error(
            lambda: contract_service.void_settlements([settlement_id])
        )

        conn = get_connection()
        try:
            required = {
                "contracts",
                "contract_project_allocations",
                "settlements",
                "sales_invoices",
                "receipts",
                "receipt_allocations",
                "cost_entries",
                "payment_entries",
            }
            actual = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            assert required <= actual
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
            assert conn.execute(
                "SELECT COUNT(*) FROM work_logs WHERE public_id IS NULL"
            ).fetchone()[0] == 0
        finally:
            conn.close()

        finance_service.void_receipts([receipt_id])
        finance_service.void_invoices([invoice_id])
        contract_service.void_settlements([settlement_id])
        cost_service.void_costs([cost_id])
        contract_service.void_allocations([allocation_id])
        contract_service.void_contracts([contract_id])

        restored = project_profit_service.get_project_summary(project["id"])
        for key in (
            "contract_minor",
            "settlement_minor",
            "invoice_minor",
            "receipt_minor",
            "other_cost_minor",
        ):
            assert restored[key] == baseline[key]

    print("V4 full operating cycle smoke test passed")


if __name__ == "__main__":
    main()
