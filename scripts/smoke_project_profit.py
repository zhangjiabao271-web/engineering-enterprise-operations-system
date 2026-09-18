import argparse
import os
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
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        from db.backup import backup_database
        backup_database(args.database, test_database)

        from db.schema import init_db
        from services import (
            contract_service,
            cost_service,
            finance_service,
            project_profit_service,
            project_service,
        )

        init_db()
        # 使用专用测试项目：真实在营项目可能已有零星收入确认或回款，
        # 按迁移 550 后的规则会阻止新增合同分配。
        project_id = project_service.create_project(
            {
                "name": "利润公式测试项目",
                "customer_name": "利润公式测试客户",
                "status": "active",
            }
        )
        project = {"id": project_id}
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
