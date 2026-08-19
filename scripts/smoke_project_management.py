import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Smoke-test V3 project management")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="project_management_v3_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database
        from db.connection import get_connection
        from services import project_service

        database.init_db()
        conn = get_connection()
        try:
            unmapped_sites = conn.execute(
                """SELECT COUNT(*)
                   FROM construction_sites cs
                   LEFT JOIN project_sites ps
                     ON ps.legacy_construction_site_id=cs.id
                   WHERE ps.id IS NULL"""
            ).fetchone()[0]
            assert unmapped_sites == 0
        finally:
            conn.close()
        project_id = project_service.create_project(
            {
                "project_code": "TEST-PROJECT-001",
                "name": "项目管理冒烟测试",
                "customer_name": "测试客户",
                "manager": "测试负责人",
                "address": "测试地址",
                "status": "筹备中",
                "planned_start_date": "2026-08-01",
                "planned_end_date": "2026-12-31",
                "notes": "临时数据库",
            }
        )
        project_service.update_project(
            project_id,
            {
                "project_code": "TEST-PROJECT-001",
                "name": "项目管理冒烟测试（更新）",
                "customer_name": "测试客户",
                "manager": "测试负责人",
                "address": "更新地址",
                "status": "进行中",
                "planned_start_date": "2026-08-02",
                "planned_end_date": "2026-12-30",
                "notes": "更新成功",
            },
        )
        site_id = project_service.create_project_site(
            project_id,
            {
                "site_code": "AREA-001",
                "site_name": "主厂房",
                "address": "项目东区",
            },
        )
        project_service.update_project_site(
            site_id,
            {
                "site_code": "AREA-001",
                "site_name": "主厂房东区",
                "address": "项目东区一层",
            },
        )

        project = project_service.get_project(project_id)
        assert project["name"] == "项目管理冒烟测试（更新）"
        assert project["status"] == "进行中"
        assert project["site_count"] == 1
        sites = project_service.list_project_sites(project_id)
        assert len(sites) == 1 and sites[0]["site_name"] == "主厂房东区"

        conn = get_connection()
        try:
            legacy = conn.execute(
                """SELECT * FROM construction_sites
                   WHERE id=(SELECT legacy_construction_site_id
                             FROM project_sites WHERE id=?)""",
                (site_id,),
            ).fetchone()
            assert legacy and legacy["is_active"] == 1
            assert legacy["address"] == "项目东区一层"
            assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        finally:
            conn.close()

        project_service.deactivate_project_sites([site_id])
        assert project_service.list_project_sites(project_id) == []
        project_service.close_projects([project_id])
        assert project_service.get_project(project_id)["status"] == "已关闭"

    print("Project management smoke test passed")


if __name__ == "__main__":
    main()
