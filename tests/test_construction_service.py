import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


class ConstructionServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="construction_service_")
        cls.test_db = Path(cls.temp_dir.name) / "supplier_data.db"
        source_db = Path(__file__).resolve().parent.parent / "supplier_data.db"
        from db.backup import backup_database

        _oss_source_db = source_db
        if _oss_source_db.exists():
            backup_database(_oss_source_db, cls.test_db)
        else:
            # 开源环境无生产库：从空库初始化基础表并跑全量迁移构建测试库
            import db.connection as _conn_module
            import db.migration_runner as _runner_module
            _saved_paths = (_conn_module.DB_PATH, _runner_module.DB_PATH)
            _conn_module.DB_PATH = cls.test_db
            _runner_module.DB_PATH = cls.test_db
            try:
                from db.schema import init_db as _init_db
                _init_db()
            finally:
                _conn_module.DB_PATH, _runner_module.DB_PATH = _saved_paths
        import db.connection as connection
        from db.migration_runner import run_migrations

        cls.original_db_path = connection.DB_PATH
        run_migrations(cls.test_db)
        connection.DB_PATH = cls.test_db

        from services import construction_service, project_service

        cls.construction_service = construction_service
        cls.project_service = project_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def _create_project(self, suffix, name=None):
        return self.project_service.create_project(
            {
                "name": name or f"施工服务测试项目-{suffix}",
                "project_code": f"CONST-{suffix}",
                "customer_name": f"施工服务测试客户-{suffix}",
                "status": "进行中",
            }
        )

    def _add_record(self, suffix, project_id, **overrides):
        data = {
            "project_id": project_id,
            "start_date": "2026-08-01",
            "end_date": "2026-08-05",
            "work_area": f"测试工区-{suffix}",
            "work_details": f"支架安装-{suffix}\n工程量：10 批",
            "quantity": 10,
            "unit": "批",
            "team_name": f"测试班组-{suffix}",
            "description": f"测试备注-{suffix}",
            "work_amount_cents": 10000,
        }
        data.update(overrides)
        return self.construction_service.add_construction_record(data)

    def _fetch_attachments(self, record_id):
        from db.connection import get_connection

        conn = get_connection()
        try:
            rows = conn.execute(
                "SELECT * FROM business_attachments WHERE construction_record_id=?",
                (record_id,),
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def test_add_and_get_record(self):
        suffix = uuid4().hex[:8]
        project_id = self._create_project(suffix)

        record_id = self._add_record(suffix, project_id)
        self.assertIsInstance(record_id, int)

        record = self.construction_service.get_construction_record(record_id)
        self.assertIsNotNone(record)
        self.assertEqual(record["project_id"], project_id)
        self.assertEqual(record["work_area"], f"测试工区-{suffix}")
        self.assertEqual(record["work_item"], f"支架安装-{suffix}")
        self.assertEqual(record["quantity"], 10)
        self.assertEqual(record["unit"], "批")
        self.assertEqual(record["team_name"], f"测试班组-{suffix}")
        self.assertEqual(record["work_amount_cents"], 10000)
        self.assertEqual(record["record_date"], "2026-08-05")
        self.assertEqual(record["inspection_status"], "待验收")
        self.assertEqual(record["record_status"], "有效")
        self.assertEqual(record["photo_count"], 0)
        self.assertIn(suffix, record["project_name"])

    def test_add_record_builds_details_from_legacy_fields(self):
        suffix = uuid4().hex[:8]
        project_id = self._create_project(suffix)

        record_id = self._add_record(
            suffix,
            project_id,
            work_details="",
            work_item=f"单项工程-{suffix}",
            quantity=5,
            unit="吨",
            description=f"补充说明-{suffix}",
        )
        record = self.construction_service.get_construction_record(record_id)
        self.assertEqual(record["work_item"], f"单项工程-{suffix}")
        self.assertIn(f"单项工程-{suffix}", record["work_details"])
        self.assertIn("工程量：5 吨", record["work_details"])
        self.assertIn(f"补充说明-{suffix}", record["work_details"])

        empty_id = self._add_record(
            suffix,
            project_id,
            work_details="",
            work_item="",
            quantity=0,
            unit="",
            description="",
        )
        empty_record = self.construction_service.get_construction_record(empty_id)
        self.assertEqual(empty_record["work_item"], "综合安装明细")

    def test_add_record_missing_dates_raises(self):
        suffix = uuid4().hex[:8]
        project_id = self._create_project(suffix)

        with self.assertRaises(KeyError):
            self.construction_service.add_construction_record(
                {"project_id": project_id, "start_date": "2026-08-01"}
            )
        with self.assertRaises(KeyError):
            self.construction_service.add_construction_record(
                {"project_id": project_id, "end_date": "2026-08-05"}
            )

    def test_add_record_negative_quantity_rejected(self):
        suffix = uuid4().hex[:8]
        project_id = self._create_project(suffix)

        with self.assertRaises(sqlite3.IntegrityError):
            self._add_record(suffix, project_id, quantity=-1)

    def test_add_record_unknown_project_raises(self):
        suffix = uuid4().hex[:8]

        with self.assertRaisesRegex(ValueError, "所选项目不存在"):
            self._add_record(suffix, 999999999)

    def test_add_record_reuses_prefixed_site_on_name_conflict(self):
        suffix = uuid4().hex[:8]
        shared_name = f"施工服务同名项目-{suffix}"
        first_project_id = self._create_project(suffix, name=shared_name)
        second_suffix = uuid4().hex[:8]
        second_project_id = self.project_service.create_project(
            {
                "name": shared_name,
                "project_code": f"CONST-{second_suffix}",
                "customer_name": f"施工服务测试客户-{second_suffix}",
                "status": "进行中",
            }
        )

        first_id = self._add_record(suffix, first_project_id)
        second_id = self._add_record(second_suffix, second_project_id)

        first = self.construction_service.get_construction_record(first_id)
        second = self.construction_service.get_construction_record(second_id)
        self.assertEqual(first["site_name"], shared_name)
        self.assertEqual(second["site_name"], f"CONST-{second_suffix} · {shared_name}")

    def test_update_record(self):
        suffix = uuid4().hex[:8]
        project_id = self._create_project(suffix)
        record_id = self._add_record(suffix, project_id)

        self.construction_service.update_construction_record(
            record_id,
            {
                "project_id": project_id,
                "start_date": "2026-08-02",
                "end_date": "2026-08-10",
                "work_area": f"更新工区-{suffix}",
                "work_details": f"更新明细-{suffix}",
                "quantity": 20,
                "unit": "台",
                "team_name": f"更新班组-{suffix}",
                "description": f"更新备注-{suffix}",
                "work_amount_cents": 25000,
            },
        )

        record = self.construction_service.get_construction_record(record_id)
        self.assertEqual(record["work_area"], f"更新工区-{suffix}")
        self.assertEqual(record["work_item"], f"更新明细-{suffix}")
        self.assertEqual(record["quantity"], 20)
        self.assertEqual(record["unit"], "台")
        self.assertEqual(record["team_name"], f"更新班组-{suffix}")
        self.assertEqual(record["work_amount_cents"], 25000)
        self.assertEqual(record["start_date"], "2026-08-02")
        self.assertEqual(record["end_date"], "2026-08-10")

        self.construction_service.update_construction_record(
            record_id,
            {
                "start_date": "2026-08-02",
                "end_date": "2026-08-10",
                "work_details": "",
                "work_item": f"legacy更新-{suffix}",
                "quantity": 3,
                "unit": "项",
                "description": "",
            },
        )
        record = self.construction_service.get_construction_record(record_id)
        self.assertEqual(record["work_item"], f"legacy更新-{suffix}")
        self.assertIn("工程量：3 项", record["work_details"])

    def test_update_record_switches_project_site(self):
        suffix = uuid4().hex[:8]
        first_project_id = self._create_project(suffix)
        second_project_id = self._create_project(uuid4().hex[:8])
        record_id = self._add_record(suffix, first_project_id)

        record = self.construction_service.get_construction_record(record_id)
        self.assertEqual(record["project_id"], first_project_id)

        self.construction_service.update_construction_record(
            record_id,
            {
                "project_id": second_project_id,
                "start_date": "2026-08-01",
                "end_date": "2026-08-05",
                "work_details": f"换项目-{suffix}",
            },
        )

        record = self.construction_service.get_construction_record(record_id)
        self.assertEqual(record["project_id"], second_project_id)

    def test_update_missing_record_raises(self):
        suffix = uuid4().hex[:8]

        with self.assertRaisesRegex(ValueError, "施工记录不存在"):
            self.construction_service.update_construction_record(
                999999999,
                {
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-05",
                    "work_details": f"不存在-{suffix}",
                },
            )

    def test_get_records_filters(self):
        suffix = uuid4().hex[:8]
        first_project_id = self._create_project(suffix)
        second_project_id = self._create_project(uuid4().hex[:8])

        august_id = self._add_record(suffix, first_project_id)
        september_id = self._add_record(
            suffix,
            first_project_id,
            start_date="2026-09-01",
            end_date="2026-09-03",
        )
        other_project_id = self._add_record(suffix, second_project_id)
        spanning_id = self._add_record(
            suffix,
            first_project_id,
            start_date="2026-07-28",
            end_date="2026-08-02",
        )

        august_rows = self.construction_service.get_construction_records(
            month="2026-08", keyword=suffix
        )
        august_ids = {row["id"] for row in august_rows}
        self.assertIn(august_id, august_ids)
        self.assertIn(other_project_id, august_ids)
        self.assertIn(spanning_id, august_ids)
        self.assertNotIn(september_id, august_ids)

        july_rows = self.construction_service.get_construction_records(
            month="2026-07", keyword=suffix
        )
        self.assertIn(spanning_id, {row["id"] for row in july_rows})

        project_rows = self.construction_service.get_construction_records(
            month="2026-08", project_id=first_project_id
        )
        project_ids = {row["id"] for row in project_rows}
        self.assertIn(august_id, project_ids)
        self.assertNotIn(other_project_id, project_ids)

        self.construction_service.update_construction_inspection(
            other_project_id, {"inspection_status": "已验收"}
        )
        accepted_rows = self.construction_service.get_construction_records(
            month="2026-08", inspection_status="已验收", keyword=suffix
        )
        self.assertEqual({row["id"] for row in accepted_rows}, {other_project_id})

        all_rows = self.construction_service.get_construction_records(keyword=suffix)
        self.assertGreaterEqual(len(all_rows), 4)

    def test_inspection_flow(self):
        suffix = uuid4().hex[:8]
        project_id = self._create_project(suffix)

        accepted_id = self._add_record(suffix, project_id)
        self.assertEqual(
            self.construction_service.get_construction_record(accepted_id)[
                "inspection_status"
            ],
            "待验收",
        )
        self.construction_service.update_construction_inspection(
            accepted_id,
            {
                "inspection_status": "已验收",
                "inspector": f"验收员-{suffix}",
                "inspection_date": "2026-08-06",
                "inspection_notes": "合格",
            },
        )
        record = self.construction_service.get_construction_record(accepted_id)
        self.assertEqual(record["inspection_status"], "已验收")
        self.assertEqual(record["inspector"], f"验收员-{suffix}")
        self.assertEqual(record["inspection_date"], "2026-08-06")
        self.assertEqual(record["inspection_notes"], "合格")

        rectified_id = self._add_record(suffix, project_id)
        self.construction_service.update_construction_inspection(
            rectified_id,
            {"inspection_status": "需整改", "inspection_notes": "焊缝需补焊"},
        )
        record = self.construction_service.get_construction_record(rectified_id)
        self.assertEqual(record["inspection_status"], "需整改")
        self.assertIsNone(record["inspection_date"])

        self.construction_service.update_construction_inspection(
            rectified_id,
            {
                "inspection_status": "已验收",
                "inspector": f"验收员-{suffix}",
                "inspection_date": "2026-08-08",
            },
        )
        record = self.construction_service.get_construction_record(rectified_id)
        self.assertEqual(record["inspection_status"], "已验收")
        self.assertEqual(record["inspection_date"], "2026-08-08")

    def test_inspection_invalid_status_rejected(self):
        suffix = uuid4().hex[:8]
        project_id = self._create_project(suffix)
        record_id = self._add_record(suffix, project_id)

        with self.assertRaisesRegex(ValueError, "验收结论无效"):
            self.construction_service.update_construction_inspection(
                record_id, {"inspection_status": "作废"}
            )

        record = self.construction_service.get_construction_record(record_id)
        self.assertEqual(record["inspection_status"], "待验收")

    def test_inspection_state_machine(self):
        """已验收只能回炉整改，不能直接退回待验收；作废记录不能再验收/修改。"""
        suffix = uuid4().hex[:8]
        project_id = self._create_project(suffix)
        record_id = self._add_record(suffix, project_id)

        self.construction_service.update_construction_inspection(
            record_id, {"inspection_status": "已验收", "inspector": "甲"}
        )
        with self.assertRaisesRegex(ValueError, "不能直接改为"):
            self.construction_service.update_construction_inspection(
                record_id, {"inspection_status": "待验收"}
            )
        # 已验收 → 需整改（回炉）允许
        self.construction_service.update_construction_inspection(
            record_id, {"inspection_status": "需整改", "inspection_notes": "复检不合格"}
        )
        self.assertEqual(
            self.construction_service.get_construction_record(record_id)[
                "inspection_status"
            ],
            "需整改",
        )
        # 不存在的记录
        with self.assertRaisesRegex(ValueError, "施工记录不存在"):
            self.construction_service.update_construction_inspection(
                10**9, {"inspection_status": "已验收"}
            )
        # 作废后不能再验收、也不能再修改
        self.construction_service.void_construction_records([record_id])
        with self.assertRaisesRegex(ValueError, "已作废"):
            self.construction_service.update_construction_inspection(
                record_id, {"inspection_status": "已验收"}
            )
        with self.assertRaisesRegex(ValueError, "已作废"):
            self.construction_service.update_construction_record(
                record_id,
                {
                    "start_date": "2026-08-01",
                    "end_date": "2026-08-02",
                    "work_details": "作废后修改",
                },
            )

    def test_void_records_hides_from_list_and_dashboard(self):
        suffix = uuid4().hex[:8]
        project_id = self._create_project(suffix)
        keep_id = self._add_record(suffix, project_id)
        void_id = self._add_record(suffix, project_id)

        self.construction_service.void_construction_records([void_id])
        self.construction_service.void_construction_records([])

        rows = self.construction_service.get_construction_records(keyword=suffix)
        row_ids = {row["id"] for row in rows}
        self.assertIn(keep_id, row_ids)
        self.assertNotIn(void_id, row_ids)

        voided = self.construction_service.get_construction_record(void_id)
        self.assertIsNotNone(voided)
        self.assertEqual(voided["record_status"], "作废")

        dashboard = self.construction_service.get_construction_dashboard(
            "2026-08", project_id=project_id
        )
        self.assertEqual(dashboard["summary"]["record_count"], 1)

    def test_photo_lifecycle(self):
        suffix = uuid4().hex[:8]
        project_id = self._create_project(suffix)
        record_id = self._add_record(suffix, project_id)

        photo_file = Path(self.temp_dir.name) / f"photo_{suffix}.jpg"
        photo_file.write_bytes(b"\xff\xd8\xff\xe0")
        photo_id = self.construction_service.add_construction_photo(
            record_id,
            str(photo_file),
            original_name=f"现场照片-{suffix}.jpg",
            photo_type="验收照片",
            notes=f"照片备注-{suffix}",
        )
        self.assertIsInstance(photo_id, int)

        photos = self.construction_service.get_construction_photos(record_id)
        self.assertEqual(len(photos), 1)
        self.assertEqual(photos[0]["id"], photo_id)
        self.assertEqual(photos[0]["record_id"], record_id)
        self.assertEqual(photos[0]["photo_type"], "验收照片")
        self.assertEqual(photos[0]["file_path"], str(photo_file))
        self.assertEqual(photos[0]["original_name"], f"现场照片-{suffix}.jpg")

        record = self.construction_service.get_construction_record(record_id)
        self.assertEqual(record["photo_count"], 1)

        attachments = self._fetch_attachments(record_id)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["status"], "active")
        self.assertEqual(attachments[0]["category"], "验收照片")
        self.assertEqual(attachments[0]["file_path"], str(photo_file))

        deleted = self.construction_service.delete_construction_photo(photo_id)
        self.assertIsNotNone(deleted)
        self.assertEqual(deleted["file_path"], str(photo_file))
        self.assertEqual(self.construction_service.get_construction_photos(record_id), [])

        attachments = self._fetch_attachments(record_id)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0]["status"], "void")

        self.assertIsNone(self.construction_service.delete_construction_photo(photo_id))

    def test_add_photo_unknown_record_rejected(self):
        suffix = uuid4().hex[:8]
        photo_file = Path(self.temp_dir.name) / f"orphan_{suffix}.jpg"
        photo_file.write_bytes(b"\xff\xd8\xff\xe0")

        with self.assertRaises((sqlite3.IntegrityError, ValueError)):
            self.construction_service.add_construction_photo(
                999999999, str(photo_file), f"孤儿照片-{suffix}.jpg"
            )

    def test_dashboard_summary(self):
        suffix = uuid4().hex[:8]
        project_id = self._create_project(suffix)

        pending_id = self._add_record(
            suffix, project_id, work_area=f"甲区-{suffix}", work_amount_cents=10000
        )
        accepted_id = self._add_record(
            suffix, project_id, work_area=f"甲区-{suffix}", work_amount_cents=20000
        )
        rectified_id = self._add_record(
            suffix, project_id, work_area=f"乙区-{suffix}", work_amount_cents=30000
        )
        self._add_record(
            suffix,
            project_id,
            start_date="2026-09-01",
            end_date="2026-09-02",
            work_amount_cents=99999,
        )

        self.construction_service.update_construction_inspection(
            accepted_id, {"inspection_status": "已验收"}
        )
        self.construction_service.update_construction_inspection(
            rectified_id, {"inspection_status": "需整改"}
        )

        photo_file = Path(self.temp_dir.name) / f"dashboard_{suffix}.jpg"
        photo_file.write_bytes(b"\xff\xd8\xff\xe0")
        self.construction_service.add_construction_photo(
            pending_id, str(photo_file), f"仪表盘-{suffix}.jpg"
        )

        dashboard = self.construction_service.get_construction_dashboard(
            "2026-08", project_id=project_id
        )
        summary = dashboard["summary"]
        self.assertEqual(summary["record_count"], 3)
        self.assertEqual(summary["pending_count"], 1)
        self.assertEqual(summary["accepted_count"], 1)
        self.assertEqual(summary["rectification_count"], 1)
        self.assertEqual(summary["total_amount_cents"], 60000)
        self.assertEqual(summary["accepted_amount_cents"], 20000)
        self.assertEqual(summary["photo_count"], 1)

        project_name = f"施工服务测试项目-{suffix}"
        by_site = {row["label"]: row for row in dashboard["by_site"]}
        self.assertIn(project_name, by_site)
        self.assertEqual(by_site[project_name]["record_count"], 3)
        self.assertEqual(by_site[project_name]["pending_count"], 1)
        self.assertEqual(by_site[project_name]["amount_cents"], 60000)

        by_area = {
            row["label"]: row
            for row in dashboard["by_area"]
            if row["project_name"] == project_name
        }
        self.assertEqual(by_area[f"甲区-{suffix}"]["record_count"], 2)
        self.assertEqual(by_area[f"甲区-{suffix}"]["amount_cents"], 30000)
        self.assertEqual(by_area[f"乙区-{suffix}"]["amount_cents"], 30000)

        september = self.construction_service.get_construction_dashboard(
            "2026-09", project_id=project_id
        )
        self.assertEqual(september["summary"]["record_count"], 1)
        self.assertEqual(september["summary"]["total_amount_cents"], 99999)

        december = self.construction_service.get_construction_dashboard(
            "2026-12", project_id=project_id
        )
        self.assertEqual(december["summary"]["record_count"], 0)

    def test_get_sites_and_work_areas(self):
        suffix = uuid4().hex[:8]
        project_id = self._create_project(suffix)
        record_id = self._add_record(suffix, project_id, work_area=f"独立工区-{suffix}")

        sites = self.construction_service.get_construction_sites(active_only=True)
        site_names = {site["site_name"] for site in sites}
        self.assertIn(f"施工服务测试项目-{suffix}", site_names)

        projects = self.construction_service.get_projects(active_only=True)
        self.assertIn(f"施工服务测试项目-{suffix}", {p["name"] for p in projects})

        areas = self.construction_service.get_construction_work_areas(project_id)
        self.assertEqual(areas, [f"独立工区-{suffix}"])

        all_areas = self.construction_service.get_construction_work_areas()
        self.assertIn(f"独立工区-{suffix}", all_areas)

        self.construction_service.void_construction_records([record_id])
        areas = self.construction_service.get_construction_work_areas(project_id)
        self.assertNotIn(f"独立工区-{suffix}", areas)


if __name__ == "__main__":
    unittest.main()
