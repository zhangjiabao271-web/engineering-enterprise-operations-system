"""Retired budgets preserve actual funds and historical migration support."""
import unittest
from pathlib import Path


class CashBudgetRetirementTests(unittest.TestCase):
    def test_budget_runtime_is_removed_but_funds_service_is_available(self):
        from services import funds_service, web_workbench_service
        root = Path(__file__).resolve().parents[1]
        self.assertFalse((root / 'services/cash_budget_service.py').exists())
        self.assertNotIn('cash_budget', web_workbench_service.SPECS)
        self.assertTrue(callable(funds_service.record_transaction))
        self.assertTrue(callable(funds_service.list_transactions))

    def test_historical_schema_migration_is_preserved(self):
        from db.migrations import MIGRATIONS
        self.assertIn(520, [number for number, _name, _migrate in MIGRATIONS])


if __name__ == '__main__':
    unittest.main()
