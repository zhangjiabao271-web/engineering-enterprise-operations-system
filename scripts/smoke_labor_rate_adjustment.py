import argparse
import os
import shutil
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path


def log_payload(worker_id, project, work_date, work_type, days, rate=300):
    return {
        "worker_id": worker_id,
        "work_date": work_date.isoformat(),
        "construction_site": project["name"],
        "project_id": project["id"],
        "work_type": work_type,
        "work_days": days,
        "daily_rate": rate,
        "notes": "调薪自动测试",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test effective-dated labor rate adjustments"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="labor_rate_adjustment_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database
        from db.connection import get_connection
        from services import labor_service, project_profit_service, project_service

        database.init_db()
        worker_id = labor_service.add_worker(
            {
                "name": "调薪自动测试工人",
                "trade": "测试工种",
                "daily_rate": 300,
                "status": "在职",
                "notes": "临时数据库",
            }
        )
        worker = labor_service.get_worker_by_id(worker_id)
        labor_service.update_worker(
            worker_id,
            {
                "name": worker["name"],
                "trade": "更新后的测试工种",
                "phone": "",
                "daily_rate": 999,
                "status": "在职",
                "notes": "档案修改不得绕过调薪流程",
            },
        )
        worker = labor_service.get_worker_by_id(worker_id)
        assert worker["daily_rate"] == 300
        first, second = project_service.list_projects(active_only=True)[:2]
        today = date.today()
        effective = today

        before_id = labor_service.add_work_log(
            log_payload(
                worker["id"], first, effective - timedelta(days=1),
                "调薪范围外", 1,
            )
        )
        first_id = labor_service.add_work_log(
            log_payload(worker["id"], first, effective, "调薪项目甲", 0.25)
        )
        second_id = labor_service.add_work_log(
            log_payload(
                worker["id"], second, effective,
                "调薪项目乙", 0.25,
            )
        )
        locked_id = labor_service.add_work_log(
            log_payload(worker["id"], second, today, "已结算锁定", 0.25)
        )
        assert labor_service.set_work_logs_rate_locked(
            [locked_id], True, "工资已经结算"
        ) == 1

        baseline_first = project_profit_service.get_project_summary(first["id"])
        baseline_second = project_profit_service.get_project_summary(second["id"])
        request = {
            "worker_id": worker["id"],
            "new_daily_rate": 350,
            "effective_from": effective.isoformat(),
            "scope_mode": "through_today",
            "reason": "项目调薪自动测试",
        }
        preview = labor_service.preview_rate_adjustment(request)
        assert preview["affected_count"] == 2
        assert preview["skipped_locked_count"] == 1
        assert preview["unchanged_count"] == 0
        assert preview["total_days"] == 0.5
        assert preview["old_amount_minor"] == 15_000
        assert preview["new_amount_minor"] == 17_500
        assert preview["delta_minor"] == 2_500
        impacts = {row["project_id"]: row for row in preview["project_impacts"]}
        assert impacts[first["id"]]["delta_minor"] == 1_250
        assert impacts[second["id"]]["delta_minor"] == 1_250

        applied = labor_service.apply_rate_adjustment(request)
        assert applied["adjustment_id"]
        assert applied["affected_count"] == 2
        assert labor_service.get_worker_by_id(worker["id"])["daily_rate"] == 350
        assert labor_service.get_work_log_by_id(before_id)["amount_minor"] == 30_000
        assert labor_service.get_work_log_by_id(first_id)["daily_rate_minor"] == 35_000
        assert labor_service.get_work_log_by_id(first_id)["amount_minor"] == 8_750
        assert labor_service.get_work_log_by_id(second_id)["amount_minor"] == 8_750
        assert labor_service.get_work_log_by_id(locked_id)["daily_rate_minor"] == 30_000
        assert labor_service.get_work_log_by_id(locked_id)["amount_minor"] == 7_500

        after_first = project_profit_service.get_project_summary(first["id"])
        after_second = project_profit_service.get_project_summary(second["id"])
        assert after_first["labor_cost_minor"] - baseline_first["labor_cost_minor"] == 1_250
        assert after_second["labor_cost_minor"] - baseline_second["labor_cost_minor"] == 1_250
        assert after_first["gross_profit_minor"] - baseline_first["gross_profit_minor"] == -1_250
        assert after_second["gross_profit_minor"] - baseline_second["gross_profit_minor"] == -1_250
        assert after_first["cash_balance_minor"] == baseline_first["cash_balance_minor"]
        assert after_second["cash_balance_minor"] == baseline_second["cash_balance_minor"]

        locked_data = labor_service.get_work_log_by_id(locked_id)
        try:
            labor_service.update_work_log(locked_id, dict(locked_data))
        except ValueError as error:
            assert "锁定" in str(error)
        else:
            raise AssertionError("Locked work log should not be editable")
        try:
            labor_service.delete_work_logs([locked_id])
        except ValueError as error:
            assert "锁定" in str(error)
        else:
            raise AssertionError("Locked work log should not be deleted")

        future_only = labor_service.apply_rate_adjustment(
            {
                "worker_id": worker["id"],
                "new_daily_rate": 400,
                "effective_from": today.isoformat(),
                "scope_mode": "future_only",
                "reason": "只调整后续默认工资",
            }
        )
        assert future_only["affected_count"] == 0
        assert labor_service.get_work_log_by_id(first_id)["amount_minor"] == 8_750
        new_id = labor_service.add_work_log(
            {
                "worker_id": worker["id"],
                "work_date": today.isoformat(),
                "construction_site": first["name"],
                "project_id": first["id"],
                "work_type": "调薪后新增工天",
                "work_days": 0.25,
                "notes": "未显式传工资，应读取生效版本",
            }
        )
        new_log = labor_service.get_work_log_by_id(new_id)
        assert new_log["daily_rate_minor"] == 40_000
        assert new_log["amount_minor"] == 10_000
        assert labor_service.get_effective_worker_rate(
            worker["id"], today.isoformat()
        ) == 400

        history = labor_service.list_rate_adjustments(worker["id"])
        assert len(history) >= 2
        assert history[0]["new_rate_minor"] == 40_000
        assert history[1]["delta_minor"] == 2_500

        custom_preview = labor_service.preview_rate_adjustment(
            {
                "worker_id": worker["id"],
                "new_daily_rate": 450,
                "effective_from": today.isoformat(),
                "scope_mode": "custom",
                "range_end": today.isoformat(),
                "project_id": first["id"],
                "reason": "仅调整指定项目",
            }
        )
        assert custom_preview["affected_count"] == 2
        assert {row["project_id"] for row in custom_preview["project_impacts"]} == {
            first["id"]
        }
        custom_applied = labor_service.apply_rate_adjustment(
            {
                "worker_id": worker["id"],
                "new_daily_rate": 450,
                "effective_from": today.isoformat(),
                "scope_mode": "custom",
                "range_end": today.isoformat(),
                "project_id": first["id"],
                "reason": "仅调整指定项目",
            }
        )
        assert custom_applied["affected_count"] == 2
        assert labor_service.get_work_log_by_id(second_id)["amount_minor"] == 8_750
        assert labor_service.get_work_log_by_id(new_id)["daily_rate_minor"] == 45_000
        assert labor_service.get_effective_worker_rate(
            worker["id"], today.isoformat()
        ) == 450

        assert labor_service.set_work_logs_rate_locked(
            [locked_id], False, "结算记录复核后解锁"
        ) == 1
        assert not labor_service.get_work_log_by_id(locked_id)["rate_locked"]

        conn = get_connection()
        try:
            assert conn.execute(
                "SELECT COUNT(*) FROM labor_rate_adjustment_items WHERE adjustment_id=?",
                (applied["adjustment_id"],),
            ).fetchone()[0] == 2
            assert conn.execute(
                "SELECT COUNT(*) FROM labor_rate_lock_events WHERE work_log_id=?",
                (locked_id,),
            ).fetchone()[0] == 2
            assert conn.execute(
                "SELECT COUNT(*) FROM worker_rate_versions WHERE worker_id=?",
                (worker["id"],),
            ).fetchone()[0] >= 3
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()

    print("Labor rate adjustment, locking and audit smoke test passed")


if __name__ == "__main__":
    main()
