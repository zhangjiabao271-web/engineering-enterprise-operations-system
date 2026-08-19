import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test V4 labor attribution and void behavior"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="labor_v4_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database
        from db.connection import get_connection
        from services import labor_service, project_profit_service, project_service

        database.init_db()
        worker = labor_service.get_workers(active_only=True)[0]
        projects = project_service.list_projects(active_only=True)
        first, second = projects[:2]
        baseline_first = project_profit_service.get_project_summary(first["id"])
        baseline_second = project_profit_service.get_project_summary(second["id"])

        log_id = labor_service.add_work_log(
            {
                "worker_id": worker["id"],
                "work_date": "2099-01-01",
                "construction_site": first["name"],
                "work_type": "V4 归属测试",
                "work_days": 0.5,
                "daily_rate": 300,
                "amount": 150,
                "notes": "临时数据库",
            }
        )
        created = labor_service.get_work_log_by_id(log_id)
        assert created["project_id"] == first["id"]
        assert created["daily_rate_minor"] == 30_000
        assert created["amount_minor"] == 15_000
        after_first = project_profit_service.get_project_summary(first["id"])
        assert (
            after_first["labor_cost_minor"] - baseline_first["labor_cost_minor"]
            == 15_000
        )

        labor_service.update_work_log(
            log_id,
            {
                "worker_id": worker["id"],
                "work_date": "2099-01-01",
                "construction_site": second["name"],
                "work_type": "V4 归属测试",
                "work_days": 1,
                "daily_rate": 300,
                "amount": 300,
                "notes": "改归另一个独立项目",
            },
        )
        updated = labor_service.get_work_log_by_id(log_id)
        assert updated["project_id"] == second["id"]
        after_second = project_profit_service.get_project_summary(second["id"])
        assert (
            after_second["labor_cost_minor"] - baseline_second["labor_cost_minor"]
            == 30_000
        )

        labor_service.delete_work_logs([log_id])
        assert labor_service.get_work_log_by_id(log_id) is None
        restored_second = project_profit_service.get_project_summary(second["id"])
        assert restored_second["labor_cost_minor"] == baseline_second["labor_cost_minor"]

        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT status, public_id FROM work_logs WHERE id=?", (log_id,)
            ).fetchone()
            assert row["status"] == "void"
            assert row["public_id"]
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()

    print("V4 labor attribution smoke test passed")


if __name__ == "__main__":
    main()
