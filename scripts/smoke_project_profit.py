import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test V4 project profit and cash formulas"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="project_profit_v4_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database
        from services import (
            contract_service,
            cost_service,
            finance_service,
            project_profit_service,
            project_service,
        )

        database.init_db()
        project = project_service.list_projects(active_only=True)[0]
        baseline = project_profit_service.get_project_summary(project["id"])

        contract_id = contract_service.create_contract(
            {
                "contract_no": "TEST-PROFIT-CONTRACT",
                "name": "利润公式测试合同",
                "contract_type": "project",
                "sign_date": "2026-07-30",
                "amount": "10000.00",
                "status": "active",
            }
        )
        contract_service.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project["id"],
                "amount": "10000.00",
            }
        )
        contract_service.create_settlement(
            {
                "settlement_no": "TEST-PROFIT-SETTLEMENT",
                "contract_id": contract_id,
                "project_id": project["id"],
                "settlement_date": "2026-07-30",
                "amount": "6000.00",
            }
        )
        invoice_id = finance_service.create_invoice(
            {
                "invoice_no": "TEST-PROFIT-INVOICE",
                "contract_id": contract_id,
                "project_id": project["id"],
                "invoice_date": "2026-07-30",
                "amount": "5000.00",
                "tax_rate": "10",
            }
        )
        finance_service.create_receipt(
            {
                "receipt_no": "TEST-PROFIT-RECEIPT",
                "contract_id": contract_id,
                "project_id": project["id"],
                "invoice_id": invoice_id,
                "receipt_date": "2026-07-30",
                "amount": "4000.00",
            }
        )
        cost_service.create_cost(
            {
                "cost_no": "TEST-PROFIT-COST",
                "project_id": project["id"],
                "cost_date": "2026-07-30",
                "category": "分包费",
                "amount": "1000.00",
            }
        )
        summary = project_profit_service.get_project_summary(project["id"])
        assert summary["contract_minor"] - baseline["contract_minor"] == 1_000_000
        assert summary["settlement_minor"] - baseline["settlement_minor"] == 600_000
        assert summary["invoice_minor"] - baseline["invoice_minor"] == 500_000
        assert summary["receipt_minor"] - baseline["receipt_minor"] == 400_000
        assert summary["other_cost_minor"] - baseline["other_cost_minor"] == 100_000
        assert summary["gross_profit_minor"] - baseline["gross_profit_minor"] == 500_000
        assert summary["receivable_minor"] - baseline["receivable_minor"] == 200_000
        assert summary["cash_balance_minor"] - baseline["cash_balance_minor"] == 400_000

    print("Project profit and cash formula smoke test passed")


if __name__ == "__main__":
    main()
