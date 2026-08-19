import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


class ContractAllocationMergeTests(unittest.TestCase):
    """同一合同+项目追加分配应合并到已有记录，而不是触发 UNIQUE 约束。"""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="contract_alloc_merge_")
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

        from services import contract_service, project_service

        cls.contract_service = contract_service
        cls.project_service = project_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def setUp(self):
        suffix = uuid4().hex[:8]
        self.project_id = self.project_service.create_project(
            {
                "name": f"分配合并测试-{suffix}",
                "project_code": f"MERGE-{suffix}",
                "status": "进行中",
            }
        )
        self.contract_id = self.contract_service.create_contract(
            {
                "contract_no": f"HT-MERGE-{suffix}",
                "name": "分配合并测试合同",
                "contract_type": "annual",
                "sign_date": "2026-08-07",
                "amount": "1000.00",
            }
        )

    def _active_allocations(self):
        return self.contract_service.list_allocations(
            contract_id=self.contract_id, project_id=self.project_id
        )

    def test_same_project_top_up_merges_into_existing_row(self):
        first_id = self.contract_service.create_allocation(
            {
                "contract_id": self.contract_id,
                "project_id": self.project_id,
                "amount": "600.00",
                "notes": "首次分配",
            }
        )
        second_id = self.contract_service.create_allocation(
            {
                "contract_id": self.contract_id,
                "project_id": self.project_id,
                "amount": "400.00",
                "notes": "补足尾款",
            }
        )
        self.assertEqual(first_id, second_id)
        rows = self._active_allocations()
        self.assertEqual(len(rows), 1, "同一合同+项目应只有一条生效分配")
        self.assertEqual(rows[0]["allocated_amount_minor"], 100000)
        self.assertIn("首次分配", rows[0]["notes"])
        self.assertIn("补足尾款", rows[0]["notes"])

    def test_over_amount_top_up_still_rejected(self):
        self.contract_service.create_allocation(
            {
                "contract_id": self.contract_id,
                "project_id": self.project_id,
                "amount": "800.00",
            }
        )
        with self.assertRaises(ValueError):
            self.contract_service.create_allocation(
                {
                    "contract_id": self.contract_id,
                    "project_id": self.project_id,
                    "amount": "300.00",
                }
            )
        rows = self._active_allocations()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["allocated_amount_minor"], 80000)

    def test_different_project_still_creates_separate_row(self):
        suffix = uuid4().hex[:8]
        other_project = self.project_service.create_project(
            {
                "name": f"分配合并测试二-{suffix}",
                "project_code": f"MERGE2-{suffix}",
                "status": "进行中",
            }
        )
        self.contract_service.create_allocation(
            {
                "contract_id": self.contract_id,
                "project_id": self.project_id,
                "amount": "600.00",
            }
        )
        self.contract_service.create_allocation(
            {
                "contract_id": self.contract_id,
                "project_id": other_project,
                "amount": "400.00",
            }
        )
        rows = self.contract_service.list_allocations(contract_id=self.contract_id)
        by_project = {row["project_id"]: row["allocated_amount_minor"] for row in rows}
        self.assertEqual(by_project[self.project_id], 60000)
        self.assertEqual(by_project[other_project], 40000)


if __name__ == "__main__":
    unittest.main()
