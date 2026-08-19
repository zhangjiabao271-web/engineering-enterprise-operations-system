import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


class LaborOvertimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="labor_overtime_")
        cls.test_db = Path(cls.temp_dir.name) / "supplier_data.db"
        source_db = Path(__file__).resolve().parent.parent / "supplier_data.db"
        if source_db.exists():
            shutil.copy2(source_db, cls.test_db)
        else:
            # 开源环境无生产库时，从空库初始化基础表并跑全量迁移构建测试库
            import db.connection as _conn_module
            import db.migration_runner as _runner_module
            _saved_conn_path = _conn_module.DB_PATH
            _saved_runner_path = _runner_module.DB_PATH
            _conn_module.DB_PATH = cls.test_db
            _runner_module.DB_PATH = cls.test_db
            try:
                import database as _database
                _database.init_db()  # 建基础表 + run_migrations()
            finally:
                _conn_module.DB_PATH = _saved_conn_path
                _runner_module.DB_PATH = _saved_runner_path

        import db.connection as connection
        from db.migration_runner import run_migrations

        cls.original_db_path = connection.DB_PATH
        run_migrations(cls.test_db)
        connection.DB_PATH = cls.test_db

        from services import labor_service, project_profit_service, project_service

        cls.labor_service = labor_service
        cls.project_profit_service = project_profit_service
        cls.project_service = project_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def test_overtime_marker_dashboard_and_cost_invariant(self):
        empty_summary = self.labor_service.get_work_dashboard("2098-12")["summary"]
        self.assertEqual(empty_summary["overtime_days"], 0)
        self.assertEqual(empty_summary["overtime_record_count"], 0)

        suffix = uuid4().hex[:8]
        project_name = f"加班测试项目-{suffix}"
        project_id = self.project_service.create_project(
            {
                "name": project_name,
                "project_code": f"OT-{suffix}",
                "status": "进行中",
            }
        )
        worker_id = self.labor_service.add_worker(
            {
                "name": f"加班测试工人-{suffix}",
                "trade": "安装",
                "daily_rate": "300.00",
                "status": "在职",
            }
        )
        overtime_id = self.labor_service.add_work_log(
            {
                "worker_id": worker_id,
                "work_date": "2099-12-31",
                "construction_site": project_name,
                "work_type": "夜间安装",
                "work_days": 0.5,
                "daily_rate": 300,
                "is_overtime": True,
            }
        )
        self.labor_service.add_work_logs_batch(
            [
                {
                    "worker_id": worker_id,
                    "work_date": "2099-12-31",
                    "construction_site": project_name,
                    "work_type": "白天安装",
                    "work_days": 0.5,
                    "daily_rate": 300,
                    "is_overtime": False,
                }
            ]
        )

        dashboard = self.labor_service.get_work_dashboard("2099-12")
        summary = dashboard["summary"]
        self.assertEqual(summary["total_days"], 1)
        self.assertEqual(summary["overtime_days"], 0.5)
        self.assertEqual(summary["overtime_record_count"], 1)
        self.assertEqual(summary["total_amount"], 300)
        self.assertEqual(dashboard["by_worker"][0]["overtime_days"], 0.5)
        self.assertEqual(dashboard["by_site"][0]["overtime_days"], 0.5)
        self.assertEqual(
            self.labor_service.get_work_log_by_id(overtime_id)["is_overtime"],
            1,
        )

        cost_before = self.project_profit_service.get_project_summary(project_id)[
            "labor_cost_minor"
        ]
        work_log_ids = [
            row["id"]
            for row in self.labor_service.get_work_logs("2099-12")
            if row["worker_id"] == worker_id
        ]
        changed = self.labor_service.set_work_logs_overtime(work_log_ids, True)
        self.assertEqual(changed, 2)
        updated_summary = self.labor_service.get_work_dashboard("2099-12")[
            "summary"
        ]
        self.assertEqual(updated_summary["overtime_days"], 1)
        self.assertEqual(updated_summary["overtime_record_count"], 2)
        cost_after = self.project_profit_service.get_project_summary(project_id)[
            "labor_cost_minor"
        ]
        self.assertEqual(cost_after, cost_before)

    def test_work_log_site_options_are_scoped_to_selected_project(self):
        suffix = uuid4().hex[:8]
        first_project_id = self.project_service.create_project(
            {
                "name": f"工地候选甲-{suffix}",
                "project_code": f"SA-{suffix}",
                "status": "进行中",
            }
        )
        second_project_id = self.project_service.create_project(
            {
                "name": f"工地候选乙-{suffix}",
                "project_code": f"SB-{suffix}",
                "status": "进行中",
            }
        )
        first_site = f"甲项目施工地-{suffix}"
        second_site = f"乙项目施工地-{suffix}"
        self.project_service.create_project_site(
            first_project_id, {"site_name": first_site}
        )
        self.project_service.create_project_site(
            second_project_id, {"site_name": second_site}
        )

        options = self.labor_service.list_work_log_site_options(first_project_id)
        option_names = {row["name"] for row in options}
        self.assertIn(first_site, option_names)
        self.assertNotIn(second_site, option_names)

        worker_id = self.labor_service.add_worker(
            {
                "name": f"候选隔离测试工人-{suffix}",
                "trade": "安装",
                "daily_rate": "300",
                "status": "在职",
            }
        )
        first_site_id = next(
            row["id"] for row in options if row["name"] == first_site
        )
        valid_log_id = self.labor_service.add_work_log(
            {
                "worker_id": worker_id,
                "work_date": "2099-11-28",
                "construction_site": first_site,
                "work_type": "标准地点映射",
                "work_days": 0.5,
                "daily_rate": 300,
                "project_id": first_project_id,
                "project_site_id": first_site_id,
            }
        )
        valid_log = self.labor_service.get_work_log_by_id(valid_log_id)
        self.assertEqual(valid_log["project_id"], first_project_id)
        self.assertEqual(valid_log["project_site_id"], first_site_id)

        with self.assertRaisesRegex(ValueError, "不属于当前项目"):
            self.labor_service.add_work_log(
                {
                    "worker_id": worker_id,
                    "work_date": "2099-11-29",
                    "construction_site": first_site,
                    "work_type": "错误映射",
                    "work_days": 1,
                    "daily_rate": 300,
                    "project_id": second_project_id,
                    "project_site_id": first_site_id,
                }
            )

        historical_site = f"历史自由填写地点-{suffix}"
        self.labor_service.add_work_log(
            {
                "worker_id": worker_id,
                "work_date": "2099-11-30",
                "construction_site": historical_site,
                "work_type": "历史记录",
                "work_days": 1,
                "daily_rate": 300,
                "project_id": first_project_id,
            }
        )
        refreshed_names = {
            row["name"]
            for row in self.labor_service.list_work_log_site_options(first_project_id)
        }
        self.assertNotIn(historical_site, refreshed_names)

    def test_closed_projects_are_not_new_work_log_options(self):
        suffix = uuid4().hex[:8]
        project_id = self.project_service.create_project(
            {
                "name": f"关闭项目候选测试-{suffix}",
                "project_code": f"CL-{suffix}",
                "status": "已关闭",
            }
        )

        default_ids = {
            row["id"] for row in self.labor_service.list_work_log_project_options()
        }
        edit_ids = {
            row["id"]
            for row in self.labor_service.list_work_log_project_options(project_id)
        }
        self.assertNotIn(project_id, default_ids)
        self.assertIn(project_id, edit_ids)

    def test_same_day_can_be_split_across_sites_but_total_cannot_exceed_one(self):
        suffix = uuid4().hex[:8]
        first_project_id = self.project_service.create_project(
            {
                "name": f"日工天拆分甲-{suffix}",
                "project_code": f"DAY-A-{suffix}",
                "status": "进行中",
            }
        )
        second_project_id = self.project_service.create_project(
            {
                "name": f"日工天拆分乙-{suffix}",
                "project_code": f"DAY-B-{suffix}",
                "status": "进行中",
            }
        )
        worker_id = self.labor_service.add_worker(
            {
                "name": f"日工天拆分工人-{suffix}",
                "daily_rate": 300,
                "status": "在职",
            }
        )
        first_log_id = self.labor_service.add_work_log(
            {
                "worker_id": worker_id,
                "work_date": "2099-08-01",
                "construction_site": f"上午地点-{suffix}",
                "work_type": "上午施工",
                "work_days": 0.5,
                "daily_rate": 300,
                "project_id": first_project_id,
            }
        )
        second_log_id = self.labor_service.add_work_log(
            {
                "worker_id": worker_id,
                "work_date": "2099-08-01",
                "construction_site": f"下午地点-{suffix}",
                "work_type": "下午施工",
                "work_days": 0.5,
                "daily_rate": 300,
                "project_id": second_project_id,
            }
        )
        self.assertTrue(first_log_id)
        self.assertTrue(second_log_id)

        with self.assertRaisesRegex(ValueError, "合计不能超过 1"):
            self.labor_service.add_work_log(
                {
                    "worker_id": worker_id,
                    "work_date": "2099-08-01",
                    "construction_site": f"第三地点-{suffix}",
                    "work_type": "追加施工",
                    "work_days": 0.1,
                    "daily_rate": 300,
                    "project_id": first_project_id,
                }
            )

        first = self.labor_service.get_work_log_by_id(first_log_id)
        with self.assertRaisesRegex(ValueError, "合计不能超过 1"):
            self.labor_service.update_work_log(
                first_log_id,
                {
                    "worker_id": worker_id,
                    "work_date": "2099-08-01",
                    "construction_site": first["construction_site"],
                    "work_type": first["work_type"],
                    "work_days": 0.6,
                    "daily_rate": 300,
                    "project_id": first_project_id,
                },
            )

    def test_batch_daily_limit_is_atomic(self):
        suffix = uuid4().hex[:8]
        project_id = self.project_service.create_project(
            {
                "name": f"批量日上限-{suffix}",
                "project_code": f"DAY-C-{suffix}",
                "status": "进行中",
            }
        )
        worker_id = self.labor_service.add_worker(
            {
                "name": f"批量日上限工人-{suffix}",
                "daily_rate": 300,
                "status": "在职",
            }
        )
        valid_count = self.labor_service.add_work_logs_batch(
            [
                {
                    "worker_id": worker_id,
                    "work_date": "2099-08-01",
                    "construction_site": f"相同地点-{suffix}",
                    "work_type": "相同工作内容",
                    "work_days": 0.5,
                    "daily_rate": 300,
                    "project_id": project_id,
                },
                {
                    "worker_id": worker_id,
                    "work_date": "2099-08-01",
                    "construction_site": f"相同地点-{suffix}",
                    "work_type": "相同工作内容",
                    "work_days": 0.5,
                    "daily_rate": 300,
                    "project_id": project_id,
                },
            ]
        )
        self.assertEqual(valid_count, 2)

        with self.assertRaisesRegex(ValueError, "合计不能超过 1"):
            self.labor_service.add_work_logs_batch(
                [
                    {
                        "worker_id": worker_id,
                        "work_date": "2099-08-02",
                        "construction_site": f"批量上午-{suffix}",
                        "work_type": "上午",
                        "work_days": 0.6,
                        "daily_rate": 300,
                        "project_id": project_id,
                    },
                    {
                        "worker_id": worker_id,
                        "work_date": "2099-08-02",
                        "construction_site": f"批量下午-{suffix}",
                        "work_type": "下午",
                        "work_days": 0.5,
                        "daily_rate": 300,
                        "project_id": project_id,
                    },
                ]
            )
        rows = [
            row for row in self.labor_service.get_work_logs("2099-08")
            if row["worker_id"] == worker_id and row["work_date"] == "2099-08-02"
        ]
        self.assertEqual(rows, [])

    def test_labor_cost_summary_filters_period_and_project(self):
        suffix = uuid4().hex[:8]
        first_project_id = self.project_service.create_project(
            {
                "name": f"人工汇总甲-{suffix}",
                "project_code": f"LC-A-{suffix}",
                "status": "进行中",
            }
        )
        second_project_id = self.project_service.create_project(
            {
                "name": f"人工汇总乙-{suffix}",
                "project_code": f"LC-B-{suffix}",
                "status": "进行中",
            }
        )
        worker_id = self.labor_service.add_worker(
            {
                "name": f"人工汇总工人-{suffix}",
                "trade": "安装",
                "daily_rate": "300",
                "status": "在职",
            }
        )
        for project_id, work_date, days in (
            (first_project_id, "2097-03-01", 0.5),
            (first_project_id, "2097-03-02", 1),
            (first_project_id, "2098-03-01", 1),
            (second_project_id, "2097-03-03", 1),
        ):
            self.labor_service.add_work_log(
                {
                    "worker_id": worker_id,
                    "work_date": work_date,
                    "construction_site": f"人工汇总地点-{project_id}",
                    "work_type": "汇总测试",
                    "work_days": days,
                    "daily_rate": 300,
                    "project_id": project_id,
                }
            )

        summary = self.labor_service.get_labor_cost_summary(
            start_date="2097-01-01",
            end_date="2097-12-31",
            project_id=first_project_id,
        )
        self.assertEqual(summary["record_count"], 2)
        self.assertEqual(summary["worker_count"], 1)
        self.assertEqual(summary["work_days"], 1.5)
        self.assertEqual(summary["amount_minor"], 45000)


if __name__ == "__main__":
    unittest.main()
