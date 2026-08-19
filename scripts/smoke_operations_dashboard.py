import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test the V4 operating dashboard service"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="operations_dashboard_v4_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database
        from services import (
            contract_service,
            operations_service,
        )

        database.init_db()
        assert operations_service._percent(0, 0) is None
        before = operations_service.get_executive_overview("2026-07")
        north_star = before["north_star"]
        assert north_star["active_project_count"] >= 1
        assert north_star["accountable_project_count"] <= north_star[
            "active_project_count"
        ]
        assert 0 <= (north_star["percent"] or 0) <= 100
        assert len(before["projects"]) >= north_star["active_project_count"]

        project = next(row for row in before["projects"] if row["is_active"])
        baseline_count = north_star["accountable_project_count"]
        baseline_profit = before["summary"]["confirmed_gross_profit_minor"]
        was_accountable = project["is_accountable"]
        baseline_project_profit = project["gross_profit_minor"]

        contract_id = contract_service.create_contract(
            {
                "contract_no": "TEST-DASHBOARD-CONTRACT",
                "name": "经营驾驶舱测试合同",
                "contract_type": "project",
                "sign_date": "2026-07-30",
                "amount": "12345.67",
                "status": "active",
            }
        )
        allocation_id = contract_service.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project["project_id"],
                "amount": "12345.67",
            }
        )
        settlement_id = contract_service.create_settlement(
            {
                "settlement_no": "TEST-DASHBOARD-SETTLEMENT",
                "contract_id": contract_id,
                "project_id": project["project_id"],
                "settlement_date": "2026-07-30",
                "amount": "12345.67",
                "basis": "临时数据库经营驾驶舱测试",
            }
        )
        after = operations_service.get_executive_overview("2026-07")
        after_project = next(
            row
            for row in after["projects"]
            if row["project_id"] == project["project_id"]
        )

        assert after_project["is_accountable"]
        assert after_project["stage_code"] in (
            "accountable",
            "invoice",
            "receipt",
        )
        assert after_project["settlement_minor"] - project["settlement_minor"] == 1_234_567
        expected_count = baseline_count if was_accountable else baseline_count + 1
        assert after["north_star"]["accountable_project_count"] == expected_count
        expected_profit_delta = (
            1_234_567
            if was_accountable
            else baseline_project_profit + 1_234_567
        )
        assert (
            after["summary"]["confirmed_gross_profit_minor"] - baseline_profit
            == expected_profit_delta
        )

        contract_service.void_settlements([settlement_id])
        contract_service.void_allocations([allocation_id])
        contract_service.void_contracts([contract_id])
        restored = operations_service.get_executive_overview("2026-07")
        assert (
            restored["north_star"]["accountable_project_count"]
            == baseline_count
        )
        assert (
            restored["summary"]["confirmed_gross_profit_minor"]
            == baseline_profit
        )

        for key in (
            "settlement_coverage_percent",
            "purchase_attribution_percent",
            "labor_attribution_percent",
        ):
            value = restored["drivers"][key]
            assert value is None or 0 <= value <= 100

    print("Operations dashboard smoke test passed")


if __name__ == "__main__":
    main()
