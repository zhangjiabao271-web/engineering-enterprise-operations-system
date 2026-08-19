import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


class DataGovernanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="data_governance_")
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

        from services import data_governance_service, labor_service, procurement_service
        from services import project_service

        cls.governance = data_governance_service
        cls.labor = labor_service
        cls.procurement = procurement_service
        cls.projects = project_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def _project(self, suffix):
        return self.projects.create_project(
            {
                "name": f"治理测试项目-{suffix}",
                "project_code": f"GOV-{suffix}",
                "status": "进行中",
            }
        )

    def test_summary_and_explicit_labor_assignment(self):
        suffix = uuid4().hex[:8]
        project_id = self._project(suffix)
        site_id = self.projects.create_project_site(
            project_id, {"site_name": f"治理测试地点-{suffix}"}
        )
        worker_id = self.labor.add_worker(
            {
                "name": f"治理测试工人-{suffix}",
                "daily_rate": 360,
                "status": "在职",
            }
        )
        log_id = self.labor.add_work_log(
            {
                "worker_id": worker_id,
                "work_date": "2099-10-01",
                "construction_site": f"历史自由文本-{suffix}",
                "work_type": "治理测试",
                "work_days": 0.5,
                "daily_rate": 360,
                "allow_unassigned": True,
            }
        )
        before = self.governance.get_governance_summary()
        self.assertGreaterEqual(before["labor_record_count"], 1)
        changed = self.governance.assign_labor_records(
            [log_id], project_id, site_id
        )
        self.assertEqual(changed, 1)
        row = self.labor.get_work_log_by_id(log_id)
        self.assertEqual(row["project_id"], project_id)
        self.assertEqual(row["project_site_id"], site_id)
        self.assertEqual(row["construction_site"], f"治理测试地点-{suffix}")
        with self.assertRaisesRegex(ValueError, "刷新"):
            self.governance.assign_labor_records([log_id], project_id)

    def test_purchase_assignment_is_atomic(self):
        suffix = uuid4().hex[:8]
        project_id = self._project(suffix)
        # 自建一张待归集零星采购，避免依赖存量数据状态
        order_id = self.procurement.add_purchase_order(
            {
                "purchase_type": "零星采购",
                "purchase_date": "2099-10-01",
                "merchant_name_snapshot": f"治理测试商户-{suffix}",
                "allocation_method": "unassigned",
            },
            {
                "material_name_snapshot": f"治理测试材料-{suffix}",
                "quantity": 1,
                "material_unit_price_cents": 100,
            },
        )
        changed = self.governance.assign_purchase_orders([order_id], project_id)
        self.assertEqual(changed, 1)
        purchase = self.procurement.get_purchase_order(order_id)
        self.assertEqual(purchase["project_id"], project_id)
        with self.assertRaisesRegex(ValueError, "刷新"):
            self.governance.assign_purchase_orders([order_id], project_id)

    def test_confirm_customer_and_default_site_completeness(self):
        suffix = uuid4().hex[:8]
        project_id = self._project(suffix)
        result = self.governance.confirm_project_customer(
            project_id, f"治理测试客户-{suffix}", update_contracts=True
        )
        self.assertTrue(result["partner_id"])
        project = self.projects.get_project(project_id)
        self.assertEqual(project["customer_partner_id"], result["partner_id"])
        row = next(
            item for item in self.governance.list_project_completeness()
            if item["id"] == project_id
        )
        self.assertEqual(row["customer_name"], f"治理测试客户-{suffix}")
        self.assertEqual(row["site_count"], 0)
        summary = self.governance.get_governance_summary()
        self.assertGreaterEqual(summary["pending_partner_count"], 1)
        gaps = self.governance.list_fulfillment_gaps()
        self.assertTrue(
            any(
                item["issue_type"] == "待确认客商"
                and item["subject"] == f"治理测试客户-{suffix}"
                for item in gaps
            )
        )


if __name__ == "__main__":
    unittest.main()
