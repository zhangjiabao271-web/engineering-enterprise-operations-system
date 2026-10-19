"""Profit conclusions must follow one accountable rule in every view."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class AccountabilityGuardrailTests(unittest.TestCase):
    def setUp(self):
        from db import connection, migration_runner
        from db.schema import init_db
        from services import (
            contract_service,
            master_data_service,
            operations_service,
            project_service,
        )

        temporary = tempfile.TemporaryDirectory(prefix="accountability_")
        self.addCleanup(temporary.cleanup)
        database = Path(temporary.name) / "test.db"
        for module in (connection, migration_runner):
            override = patch.object(module, "DB_PATH", database)
            override.start()
            self.addCleanup(override.stop)
        init_db()
        self.contracts = contract_service
        self.operations = operations_service
        self.projects = project_service
        self.customer = master_data_service.create_business_partner(
            {"legal_name": "合成客户", "roles": ["customer"]}
        )
        self.project = self.projects.create_project(
            {
                "name": "守护工程",
                "customer_partner_id": self.customer,
                "status": "进行中",
                "business_mode": "contract",
            }
        )

    def _settle(self):
        contract = self.contracts.create_contract(
            {
                "name": "守护合同",
                "amount": "100",
                "sign_date": "2026-01-01",
                "customer_partner_id": self.customer,
                "project_id": self.project,
                "entity_id": 1,
                "status": "active",
            }
        )
        self.contracts.create_settlement(
            {
                "project_id": self.project,
                "contract_id": contract,
                "amount": "100",
                "settlement_date": "2026-10-01",
                "basis": "合成结算确认",
            }
        )
        return contract

    def _cost(self, amount="50"):
        from services import cost_service

        cost_service.create_cost(
            {
                "project_id": self.project,
                "cost_date": "2026-09-01",
                "category": sorted(cost_service.SUPPORTED_COST_CATEGORIES)[0],
                "amount": amount,
                "counterparty_name": "合成供应商",
            }
        )

    def test_unsettled_project_is_not_accountable_anywhere(self):
        guidance = self.operations.get_project_guidance(self.project)
        self.assertFalse(guidance["is_accountable"])
        self.assertFalse(self.operations.get_accountability_map()[self.project])
        overview = self.operations.get_executive_overview()
        row = next(
            item for item in overview["projects"]
            if item["project_id"] == self.project
        )
        self.assertFalse(row["is_accountable"])

    def test_settled_contract_project_is_accountable_everywhere(self):
        self._settle()
        guidance = self.operations.get_project_guidance(self.project)
        self.assertTrue(guidance["is_accountable"])
        self.assertTrue(self.operations.get_accountability_map()[self.project])

    def test_web_profit_board_carries_the_guardrail_field(self):
        from services import web_workbench_service

        self._cost()
        board = web_workbench_service.dashboards("profit")
        rows = {row["project"]["id"]: row for row in board["projects"]}
        self.assertIn("is_accountable", rows[self.project])
        self.assertFalse(rows[self.project]["is_accountable"])
        self.assertLess(rows[self.project]["gross_profit_minor"], 0)

        self._settle()
        board = web_workbench_service.dashboards("profit")
        rows = {row["project"]["id"]: row for row in board["projects"]}
        self.assertTrue(rows[self.project]["is_accountable"])


if __name__ == "__main__":
    unittest.main()
