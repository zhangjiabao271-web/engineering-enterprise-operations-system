import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4


class ContractPricingMigrationTests(unittest.TestCase):
    def test_existing_contracts_default_to_fixed_price(self):
        from db.migrations.contract_pricing_modes import add_contract_pricing_modes

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """CREATE TABLE contracts (
                       id INTEGER PRIMARY KEY,
                       contract_no TEXT NOT NULL
                   )"""
            )
            conn.execute(
                "INSERT INTO contracts(id, contract_no) VALUES (1, 'LEGACY-001')"
            )
            add_contract_pricing_modes(conn)
            row = conn.execute(
                """SELECT pricing_mode, control_limit_minor
                   FROM contracts WHERE id=1"""
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(row, ("fixed", None))

    def test_actual_contract_project_links_allow_zero_amount(self):
        from db.migrations.contract_project_links import (
            allow_zero_value_contract_project_links,
        )

        conn = sqlite3.connect(":memory:")
        try:
            conn.execute(
                """CREATE TABLE contract_project_allocations (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       public_id TEXT NOT NULL UNIQUE,
                       contract_id INTEGER NOT NULL,
                       project_id INTEGER NOT NULL,
                       allocated_amount_minor INTEGER NOT NULL
                           CHECK(allocated_amount_minor > 0),
                       notes TEXT,
                       status TEXT NOT NULL DEFAULT 'active'
                           CHECK(status IN ('active', 'void')),
                       source_legacy_entry_id INTEGER UNIQUE,
                       created_at TEXT NOT NULL,
                       updated_at TEXT NOT NULL
                   )"""
            )
            allow_zero_value_contract_project_links(conn)
            conn.execute(
                """INSERT INTO contract_project_allocations (
                       public_id, contract_id, project_id,
                       allocated_amount_minor, status, created_at, updated_at
                   ) VALUES ('zero-link', 1, 1, 0, 'active', 'now', 'now')"""
            )
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """INSERT INTO contract_project_allocations (
                           public_id, contract_id, project_id,
                           allocated_amount_minor, status, created_at, updated_at
                       ) VALUES ('negative-link', 1, 2, -1, 'active', 'now', 'now')"""
                )
        finally:
            conn.close()


class ContractPricingServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="contract_pricing_")
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

        from services import contract_service, finance_service, project_service

        cls.contracts = contract_service
        cls.finance = finance_service
        cls.projects = project_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def _project(self, label):
        suffix = uuid4().hex[:8]
        return self.projects.create_project(
            {
                "name": f"{label}-{suffix}",
                "project_code": f"PRICE-{suffix}",
                "status": "进行中",
            }
        )

    def _contract(self, label, **overrides):
        suffix = uuid4().hex[:8]
        data = {
            "contract_no": f"PRICE-CONTRACT-{suffix}",
            "name": f"{label}-{suffix}",
            "contract_type": "annual",
            "sign_date": "2026-08-22",
            "status": "active",
        }
        data.update(overrides)
        return self.contracts.create_contract(data)

    def test_actual_price_contract_tracks_settlement_without_changing_amount(self):
        first_project = self._project("据实项目一")
        second_project = self._project("据实项目二")
        contract_id = self._contract(
            "据实年度框架",
            pricing_mode="actual",
            control_limit="2500.00",
        )
        for project_id in (first_project, second_project):
            self.contracts.create_allocation(
                {
                    "contract_id": contract_id,
                    "project_id": project_id,
                }
            )

        first_settlement_id = self.contracts.create_settlement(
            {
                "contract_id": contract_id,
                "project_id": first_project,
                "settlement_date": "2026-08-22",
                "amount": "1200.00",
            }
        )
        self.contracts.create_settlement(
            {
                "contract_id": contract_id,
                "project_id": second_project,
                "settlement_date": "2026-08-22",
                "amount": "1800.00",
            }
        )

        contract = self.contracts.get_contract(contract_id)
        self.assertEqual(contract["pricing_mode"], "actual")
        self.assertEqual(contract["tax_inclusive_amount_minor"], 0)
        self.assertEqual(contract["control_limit_minor"], 250_000)
        self.assertEqual(contract["settled_minor"], 300_000)
        self.assertEqual(contract["project_count"], 2)
        self.assertEqual(contract["control_overrun_minor"], 50_000)
        self.assertIn(
            "超过控制上限 ¥500.00",
            self.contracts.contract_pricing_warning(contract_id),
        )

        invoice_id = self.finance.create_invoice(
            {
                "invoice_no": f"PRICE-INVOICE-{uuid4().hex[:8]}",
                "contract_id": contract_id,
                "project_id": first_project,
                "settlement_id": first_settlement_id,
                "invoice_date": "2026-08-22",
                "amount": "500.00",
            }
        )
        receipt_id = self.finance.create_receipt(
            {
                "contract_id": contract_id,
                "project_id": first_project,
                "invoice_id": invoice_id,
                "receipt_date": "2026-08-22",
                "amount": "500.00",
            }
        )
        receipt = self.finance.get_receipt(receipt_id)
        self.assertEqual(receipt["settlement_id"], first_settlement_id)
        self.assertEqual(receipt["allocated_amount_minor"], 50_000)

    def test_provisional_amount_is_warning_not_hard_limit(self):
        project_id = self._project("暂定总价项目")
        contract_id = self._contract(
            "暂定总价合同",
            pricing_mode="provisional",
            amount="100.00",
            control_limit="150.00",
        )
        self.contracts.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "amount": "100.00",
            }
        )

        self.contracts.create_settlement(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-22",
                "amount": "200.00",
            }
        )

        contract = self.contracts.get_contract(contract_id)
        self.assertEqual(contract["settled_minor"], 20_000)
        self.assertEqual(contract["reference_overrun_minor"], 10_000)
        self.assertEqual(contract["control_overrun_minor"], 5_000)
        self.assertTrue(contract["pricing_warning"])

    def test_actual_price_contract_rejects_unlinked_project(self):
        linked_project = self._project("据实已关联项目")
        unlinked_project = self._project("据实未关联项目")
        contract_id = self._contract(
            "据实关联校验",
            pricing_mode="actual",
        )
        allocation_id = self.contracts.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": linked_project,
            }
        )
        allocation = next(
            row
            for row in self.contracts.list_allocations(contract_id=contract_id)
            if row["id"] == allocation_id
        )
        self.assertEqual(allocation["allocated_amount_minor"], 0)

        self.contracts.create_settlement(
            {
                "contract_id": contract_id,
                "project_id": linked_project,
                "settlement_date": "2026-08-22",
                "amount": "100.00",
            }
        )
        with self.assertRaisesRegex(ValueError, "尚未关联到所选项目"):
            self.contracts.create_settlement(
                {
                    "contract_id": contract_id,
                    "project_id": unlinked_project,
                    "settlement_date": "2026-08-22",
                    "amount": "100.00",
                }
            )

    def test_fixed_price_contract_keeps_allocation_boundary(self):
        project_id = self._project("固定总价项目")
        contract_id = self._contract(
            "固定总价合同", pricing_mode="fixed", amount="100.00"
        )
        settlement = {
            "contract_id": contract_id,
            "project_id": project_id,
            "settlement_date": "2026-08-22",
            "amount": "100.00",
        }
        with self.assertRaisesRegex(ValueError, "固定总价合同尚未分配"):
            self.contracts.create_settlement(settlement)

        self.contracts.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "amount": "100.00",
            }
        )
        settlement["amount"] = "100.01"
        with self.assertRaisesRegex(ValueError, "不能超过.*合同分配额"):
            self.contracts.create_settlement(settlement)

    def test_actual_price_contract_can_be_updated_without_contract_amount(self):
        contract_id = self._contract(
            "据实合同修改",
            pricing_mode="actual",
            control_limit="1000.00",
        )
        current = self.contracts.get_contract(contract_id)
        self.contracts.update_contract(
            contract_id,
            {
                "contract_no": current["contract_no"],
                "name": current["name"],
                "contract_type": current["contract_type"],
                "pricing_mode": "actual",
                "sign_date": current["sign_date"],
                "amount": "",
                "control_limit": "2000.00",
                "status": current["status"],
            },
        )
        updated = self.contracts.get_contract(contract_id)
        self.assertEqual(updated["tax_inclusive_amount_minor"], 0)
        self.assertEqual(updated["control_limit_minor"], 200_000)

    def test_fixed_contract_with_history_can_change_to_actual_pricing(self):
        project_id = self._project("固定转据实")
        contract_id = self._contract(
            "固定转据实合同", pricing_mode="fixed", amount="100.00"
        )
        allocation_id = self.contracts.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "amount": "100.00",
            }
        )
        settlement_id = self.contracts.create_settlement(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-22",
                "amount": "100.00",
            }
        )
        invoice_id = self.finance.create_invoice(
            {
                "invoice_no": f"ACTUAL-CONVERT-{uuid4().hex[:8]}",
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_id": settlement_id,
                "invoice_date": "2026-08-22",
                "amount": "50.00",
            }
        )
        receipt_id = self.finance.create_receipt(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "invoice_id": invoice_id,
                "receipt_date": "2026-08-22",
                "amount": "30.00",
            }
        )
        current = self.contracts.get_contract(contract_id)
        self.contracts.update_contract(
            contract_id,
            {
                "contract_no": current["contract_no"],
                "name": current["name"],
                "contract_type": current["contract_type"],
                "pricing_mode": "actual",
                "sign_date": current["sign_date"],
                "amount": "",
                "control_limit": "200.00",
                "status": current["status"],
            },
        )

        updated = self.contracts.get_contract(contract_id)
        self.assertEqual(updated["pricing_mode"], "actual")
        self.assertEqual(updated["tax_inclusive_amount_minor"], 0)
        self.assertEqual(updated["control_limit_minor"], 20_000)
        self.assertEqual(updated["allocated_minor"], 0)
        self.assertEqual(updated["settled_minor"], 10_000)
        allocation = next(
            row for row in self.contracts.list_allocations(contract_id=contract_id)
            if row["id"] == allocation_id
        )
        self.assertEqual(allocation["allocated_amount_minor"], 0)
        self.assertEqual(
            self.finance.get_invoice(invoice_id)["amount_minor"], 5_000
        )
        self.assertEqual(
            self.finance.get_receipt(receipt_id)["allocated_amount_minor"],
            3_000,
        )

        self.contracts.update_settlement(
            settlement_id,
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-22",
                "amount": "150.00",
            },
        )
        self.assertEqual(
            self.contracts.get_settlement(settlement_id)["amount_minor"],
            15_000,
        )

    def test_pricing_mode_cannot_change_after_income_confirmation(self):
        project_id = self._project("计价方式锁定")
        contract_id = self._contract(
            "计价方式锁定合同", pricing_mode="actual"
        )
        self.contracts.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project_id,
            }
        )
        self.contracts.create_settlement(
            {
                "contract_id": contract_id,
                "project_id": project_id,
                "settlement_date": "2026-08-22",
                "amount": "100.00",
            }
        )
        current = self.contracts.get_contract(contract_id)
        with self.assertRaisesRegex(ValueError, "只能改为单价据实结算"):
            self.contracts.update_contract(
                contract_id,
                {
                    "contract_no": current["contract_no"],
                    "name": current["name"],
                    "contract_type": current["contract_type"],
                    "pricing_mode": "fixed",
                    "sign_date": current["sign_date"],
                    "amount": "100.00",
                    "status": current["status"],
                },
            )


if __name__ == "__main__":
    unittest.main()
