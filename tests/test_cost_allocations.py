import shutil
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


class CostAllocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="cost_allocations_")
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

        from services import cost_service, project_profit_service, project_service

        cls.cost_service = cost_service
        cls.project_profit_service = project_profit_service
        cls.project_service = project_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def setUp(self):
        suffix = uuid4().hex[:8]
        self.projects = [
            self.project_service.create_project(
                {
                    "name": f"{name}-{suffix}",
                    "project_code": f"ALLOC-{index}-{suffix}",
                    "status": "进行中",
                }
            )
            for index, name in enumerate(("澄湖药业", "蓝湾", "屹峰药业"), 1)
        ]

    def _summary_cost(self, project_id):
        return self.project_profit_service.get_project_summary(project_id)[
            "other_cost_minor"
        ]

    def test_equal_manual_unassigned_and_reallocation_audit(self):
        baseline = {project_id: self._summary_cost(project_id) for project_id in self.projects}

        cost_id = self.cost_service.create_cost(
            {
                "cost_no": f"FUEL-{uuid4().hex[:8]}",
                "cost_date": "2026-08-05",
                "category": "用车",
                "amount": "1000.00",
                "counterparty_name": "测试加油站",
                "vehicle_no": "浙JTEST1",
                "allocation_method": "equal",
                "project_ids": self.projects,
            }
        )
        allocation = self.cost_service.get_cost_allocations(cost_id)
        self.assertEqual(allocation["method"], "equal")
        self.assertEqual(
            [line["amount_minor"] for line in allocation["lines"]],
            [33_334, 33_333, 33_333],
        )
        for project_id, expected in zip(self.projects, (33_334, 33_333, 33_333)):
            self.assertEqual(self._summary_cost(project_id) - baseline[project_id], expected)

        company_rows = [
            row
            for row in self.cost_service.list_cost_ledger()
            if row["source_type"] == "manual" and row["id"] == cost_id
        ]
        self.assertEqual(len(company_rows), 1)
        self.assertEqual(company_rows[0]["amount_minor"], 100_000)
        self.assertEqual(company_rows[0]["vehicle_no"], "浙JTEST1")

        self.cost_service.allocate_cost(
            cost_id,
            "manual",
            allocations=[
                {"project_id": self.projects[0], "amount": "500.00"},
                {"project_id": self.projects[1], "amount": "300.00"},
                {"project_id": self.projects[2], "amount": "200.00"},
            ],
        )
        self.assertEqual(self._active_amounts(cost_id), [50_000, 30_000, 20_000])
        self.assertEqual(self._void_line_count(cost_id), 3)

        with self.assertRaises(ValueError):
            self.cost_service.allocate_cost(
                cost_id,
                "manual",
                allocations=[
                    {"project_id": self.projects[0], "amount": "500.00"},
                    {"project_id": self.projects[1], "amount": "400.00"},
                ],
            )
        self.assertEqual(self._active_amounts(cost_id), [50_000, 30_000, 20_000])

        self.cost_service.allocate_cost(cost_id, "unassigned")
        cost = self.cost_service.get_cost_entry(cost_id)
        self.assertEqual(cost["allocation_status"], "unassigned")
        self.assertEqual(cost["allocated_amount_minor"], 0)
        for project_id in self.projects:
            self.assertEqual(self._summary_cost(project_id), baseline[project_id])
        company_rows = [
            row for row in self.cost_service.list_cost_ledger()
            if row["source_type"] == "manual" and row["id"] == cost_id
        ]
        self.assertEqual(len(company_rows), 1)
        self.assertEqual(company_rows[0]["amount_minor"], 100_000)

    def test_direct_allocation_and_void(self):
        project_id = self.projects[0]
        baseline = self._summary_cost(project_id)
        cost_id = self.cost_service.create_cost(
            {
                "cost_no": f"FUEL-DIRECT-{uuid4().hex[:8]}",
                "cost_date": "2026-08-05",
                "category": "用车",
                "amount": "88.80",
                "allocation_method": "direct",
                "project_ids": [project_id],
            }
        )
        self.assertEqual(self._summary_cost(project_id) - baseline, 8_880)
        self.cost_service.void_costs([cost_id])
        self.assertEqual(self._summary_cost(project_id), baseline)
        self.assertEqual(self._active_amounts(cost_id), [])

    def test_daily_operating_cost_categories(self):
        expected = ["用车", "饮食", "房租", "水电煤", "机械费"]
        self.assertEqual(self.cost_service.COST_CATEGORIES, expected)
        for category in expected:
            cost_id = self.cost_service.create_cost(
                {
                    "cost_date": "2026-08-16",
                    "category": category,
                    "amount": "1.00",
                    "allocation_method": "unassigned",
                }
            )
            self.assertEqual(
                self.cost_service.get_cost_entry(cost_id)["category"],
                category,
            )

    def _active_amounts(self, cost_id):
        from db.connection import get_connection

        conn = get_connection()
        try:
            return [
                row[0]
                for row in conn.execute(
                    """SELECT amount_minor FROM cost_allocation_lines
                       WHERE cost_entry_id=? AND status='active' ORDER BY id""",
                    (cost_id,),
                ).fetchall()
            ]
        finally:
            conn.close()

    def _void_line_count(self, cost_id):
        from db.connection import get_connection

        conn = get_connection()
        try:
            return conn.execute(
                """SELECT COUNT(*) FROM cost_allocation_lines
                   WHERE cost_entry_id=? AND status='void'""",
                (cost_id,),
            ).fetchone()[0]
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
