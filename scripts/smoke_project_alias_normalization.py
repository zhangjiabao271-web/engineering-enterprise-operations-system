import argparse
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def family_snapshot(conn, values):
    placeholders = ",".join("?" for _ in values)
    return tuple(conn.execute(
        f"""SELECT COUNT(*), COALESCE(SUM(amount_minor), 0)
            FROM work_logs WHERE TRIM(construction_site) IN ({placeholders})""",
        values,
    ).fetchone())


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test project and construction-site alias normalization"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="project_aliases_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        before = sqlite3.connect(test_database)
        if scalar(
            before,
            "SELECT COUNT(*) FROM schema_migrations WHERE version=210",
        ):
            before.execute("DELETE FROM schema_migrations WHERE version=210")
            for alias, canonical in (
                ("澄湖", "澄湖药业"),
                ("屹峰", "屹峰药业"),
            ):
                project_id = scalar(
                    before,
                    "SELECT id FROM projects WHERE name=?",
                    (canonical,),
                )
                before.execute(
                    """UPDATE construction_sites SET site_name=?
                       WHERE project_id=? AND site_name=?""",
                    (alias, project_id, canonical),
                )
                before.execute(
                    """UPDATE project_sites SET name=?
                       WHERE project_id=? AND name=?""",
                    (alias, project_id, canonical),
                )
                before.execute(
                    """UPDATE work_logs SET construction_site=?
                       WHERE construction_site=?""",
                    (alias, canonical),
                )
            before.commit()
        jing_family = family_snapshot(before, ("澄湖", "澄湖药业"))
        tianyu_family = family_snapshot(before, ("屹峰", "屹峰药业"))
        environmental_station = before.execute(
            "SELECT id, name, status FROM projects WHERE name='澄湖环保站'"
        ).fetchone()
        before.close()

        from db.migration_runner import run_migrations

        result = run_migrations(test_database)
        assert 210 in result["applied"]

        conn = sqlite3.connect(test_database)
        conn.row_factory = sqlite3.Row
        try:
            assert scalar(
                conn,
                "SELECT COUNT(*) FROM schema_migrations WHERE version=210",
            ) == 1
            assert scalar(
                conn,
                """SELECT COUNT(*) FROM construction_sites
                   WHERE site_name IN ('澄湖', '屹峰')""",
            ) == 0
            assert scalar(
                conn,
                """SELECT COUNT(*) FROM project_sites
                   WHERE name IN ('澄湖', '屹峰')""",
            ) == 0
            assert scalar(
                conn,
                """SELECT COUNT(*) FROM work_logs
                   WHERE TRIM(construction_site) IN ('澄湖', '屹峰')""",
            ) == 0
            assert family_snapshot(conn, ("澄湖药业",)) == jing_family
            assert family_snapshot(conn, ("屹峰药业",)) == tianyu_family
            assert scalar(
                conn,
                """SELECT COUNT(*) FROM work_logs wl
                   JOIN projects p ON p.id=wl.project_id
                   WHERE wl.construction_site='澄湖药业'
                     AND p.name<>'澄湖药业'""",
            ) == 0
            assert scalar(
                conn,
                """SELECT COUNT(*) FROM work_logs wl
                   JOIN projects p ON p.id=wl.project_id
                   WHERE wl.construction_site='屹峰药业'
                     AND p.name<>'屹峰药业'""",
            ) == 0
            current_station = conn.execute(
                "SELECT id, name, status FROM projects WHERE name='澄湖环保站'"
            ).fetchone()
            assert tuple(current_station) == tuple(environmental_station)
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()

        second = run_migrations(test_database)
        assert second["applied"] == []

        from services import labor_service

        conn = sqlite3.connect(test_database)
        worker_ids = [row[0] for row in conn.execute(
            "SELECT id FROM workers ORDER BY id LIMIT 2"
        ).fetchall()]
        conn.close()
        assert worker_ids

        log_id = labor_service.add_work_log(
            {
                "worker_id": worker_ids[0],
                "work_date": "2099-12-30",
                "construction_site": "澄湖",
                "work_type": "别名归一测试",
                "work_days": 1,
                "daily_rate": 100,
                "amount": 100,
                "notes": "临时数据库",
            }
        )
        row = labor_service.get_work_log_by_id(log_id)
        assert row["construction_site"] == "澄湖药业"
        conn = sqlite3.connect(test_database)
        project_name = conn.execute(
            "SELECT name FROM projects WHERE id=?", (row["project_id"],)
        ).fetchone()[0]
        conn.close()
        assert project_name == "澄湖药业"

        labor_service.update_work_log(
            log_id,
            {
                "worker_id": worker_ids[0],
                "work_date": "2099-12-30",
                "construction_site": "屹峰",
                "work_type": "别名归一测试",
                "work_days": 1,
                "daily_rate": 100,
                "amount": 100,
                "notes": "临时数据库",
            },
        )
        row = labor_service.get_work_log_by_id(log_id)
        assert row["construction_site"] == "屹峰药业"
        conn = sqlite3.connect(test_database)
        project_name = conn.execute(
            "SELECT name FROM projects WHERE id=?", (row["project_id"],)
        ).fetchone()[0]
        conn.close()
        assert project_name == "屹峰药业"

        if len(worker_ids) > 1:
            labor_service.add_work_logs_batch(
                [
                    {
                        "worker_id": worker_ids[1],
                        "work_date": "2099-12-31",
                        "construction_site": "澄湖",
                        "work_type": "批量别名归一测试",
                        "work_days": 1,
                        "daily_rate": 100,
                        "amount": 100,
                        "notes": "临时数据库",
                    }
                ]
            )
            conn = sqlite3.connect(test_database)
            batch_site = conn.execute(
                """SELECT construction_site FROM work_logs
                   WHERE work_date='2099-12-31'
                     AND work_type='批量别名归一测试'"""
            ).fetchone()[0]
            conn.close()
            assert batch_site == "澄湖药业"

    print("Project and construction-site alias normalization passed")


if __name__ == "__main__":
    main()
