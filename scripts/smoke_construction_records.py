import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Smoke-test project-linked construction records")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="construction_records_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database as db
        from db.connection import get_connection

        db.init_db()
        projects = db.get_projects(active_only=True)
        project = next(
            (row for row in projects if row["name"] == "澄湖"),
            projects[0],
        )
        record_id = db.add_construction_record({
            "project_id": project["id"],
            "start_date": "2026-08-30",
            "end_date": "2026-09-02",
            "work_area": "测试车间东侧",
            "work_details": (
                "H型钢 H300×150，长度 6m，8根\n"
                "彩钢板 0.6mm，长度 4.2m，36张\n"
                "檩条 C160，长度 5.8m，12根"
            ),
            "work_amount_cents": 123456,
            "team_name": "测试班组",
        })

        record = db.get_construction_record(record_id)
        assert record["project_id"] == project["id"]
        assert record["project_name"] == project["name"]
        assert record["start_date"] == "2026-08-30"
        assert record["end_date"] == "2026-09-02"
        assert record["work_amount_cents"] == 123456
        assert record["work_area"] == "测试车间东侧"
        assert "彩钢板 0.6mm" in record["work_details"]

        august = db.get_construction_records("2026-08", project["id"])
        september = db.get_construction_records("2026-09", project["id"])
        assert any(row["id"] == record_id for row in august)
        assert any(row["id"] == record_id for row in september)
        assert "测试车间东侧" in db.get_construction_work_areas(project["id"])

        db.update_construction_record(record_id, {
            "project_id": project["id"],
            "start_date": "2026-08-31",
            "end_date": "2026-09-03",
            "work_area": "测试车间西侧",
            "work_details": (
                "钢梁 GL-01，长度 7.5m，4根\n"
                "高强螺栓 M20，64套"
            ),
            "work_amount_cents": 223456,
            "team_name": "测试班组",
        })
        db.update_construction_inspection(record_id, {
            "inspection_status": "已验收",
            "inspector": "测试验收人",
            "inspection_date": "2026-09-04",
            "inspection_notes": "验收通过",
        })

        dashboard = db.get_construction_dashboard("2026-09", project["id"])
        assert dashboard["summary"]["total_amount_cents"] >= 223456
        assert dashboard["summary"]["accepted_amount_cents"] >= 223456
        assert any(
            row["project_name"] == project["name"]
            and row["label"] == "测试车间西侧"
            for row in dashboard["by_area"]
        )
        updated = db.get_construction_record(record_id)
        assert updated["work_area"] == "测试车间西侧"
        assert "高强螺栓 M20" in updated["work_details"]
        assert updated["inspection_status"] == "已验收"

        conn = get_connection()
        try:
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(construction_records)")
            }
            assert {
                "start_date", "end_date", "work_amount_cents", "work_details"
            } <= columns
            assert not conn.execute("""
                SELECT 1 FROM construction_records
                WHERE TRIM(COALESCE(work_details, ''))=''
                LIMIT 1
            """).fetchone()
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()

    print("Construction record smoke test passed")


if __name__ == "__main__":
    main()
