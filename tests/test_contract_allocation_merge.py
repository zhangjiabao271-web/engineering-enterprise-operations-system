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
                import database as _database
                _database.init_db()
            finally:
                _conn_module.DB_PATH, _runner_module.DB_PATH = _saved_paths

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


class ContractAllocationAdjustTests(unittest.TestCase):
    """项目分配金额可调整：调减释放回待分配池，但不得突破结算下限与合同总额。"""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="contract_alloc_adjust_")
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
                import database as _database
                _database.init_db()
            finally:
                _conn_module.DB_PATH, _runner_module.DB_PATH = _saved_paths

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
                "name": f"分配调整测试-{suffix}",
                "project_code": f"ADJ-{suffix}",
                "status": "进行中",
            }
        )
        self.contract_id = self.contract_service.create_contract(
            {
                "contract_no": f"HT-ADJ-{suffix}",
                "name": "分配调整测试合同",
                "contract_type": "annual",
                "sign_date": "2026-08-20",
                "amount": "1000.00",
            }
        )

    def _allocate(self, amount):
        return self.contract_service.create_allocation(
            {
                "contract_id": self.contract_id,
                "project_id": self.project_id,
                "amount": amount,
            }
        )

    def _allocation(self, allocation_id):
        return next(
            row
            for row in self.contract_service.list_allocations(
                contract_id=self.contract_id, project_id=self.project_id
            )
            if row["id"] == allocation_id
        )

    def _contract_remaining(self):
        row = next(
            row
            for row in self.contract_service.list_contracts()
            if row["id"] == self.contract_id
        )
        return row["remaining_minor"]

    def test_reduce_releases_amount_back_to_contract(self):
        allocation_id = self._allocate("600.00")
        self.assertEqual(self._contract_remaining(), 40000)
        self.contract_service.update_allocation_amount(
            allocation_id, "400.00", notes="释放多余额度"
        )
        row = self._allocation(allocation_id)
        self.assertEqual(row["allocated_amount_minor"], 40000)
        self.assertIn("释放多余额度", row["notes"])
        self.assertEqual(self._contract_remaining(), 60000)

    def test_increase_consumes_remaining_pool(self):
        allocation_id = self._allocate("600.00")
        self.contract_service.update_allocation_amount(allocation_id, "1000.00")
        self.assertEqual(self._allocation(allocation_id)["allocated_amount_minor"], 100000)
        self.assertEqual(self._contract_remaining(), 0)

    def test_increase_beyond_contract_total_rejected(self):
        allocation_id = self._allocate("800.00")
        with self.assertRaises(ValueError):
            self.contract_service.update_allocation_amount(allocation_id, "1200.00")
        self.assertEqual(self._allocation(allocation_id)["allocated_amount_minor"], 80000)

    def test_reduce_below_settled_amount_rejected(self):
        allocation_id = self._allocate("600.00")
        self.contract_service.create_settlement(
            {
                "contract_id": self.contract_id,
                "project_id": self.project_id,
                "amount": "300.00",
                "settlement_date": "2026-08-20",
            }
        )
        with self.assertRaises(ValueError):
            self.contract_service.update_allocation_amount(allocation_id, "200.00")
        self.contract_service.update_allocation_amount(allocation_id, "300.00")
        self.assertEqual(self._allocation(allocation_id)["allocated_amount_minor"], 30000)

    def test_zero_and_missing_allocation_rejected(self):
        allocation_id = self._allocate("600.00")
        with self.assertRaises(ValueError):
            self.contract_service.update_allocation_amount(allocation_id, "0")
        with self.assertRaises(ValueError):
            self.contract_service.update_allocation_amount(999999999, "100.00")
        self.contract_service.void_allocations([allocation_id])
        with self.assertRaises(ValueError):
            self.contract_service.update_allocation_amount(allocation_id, "100.00")


if __name__ == "__main__":
    unittest.main()
