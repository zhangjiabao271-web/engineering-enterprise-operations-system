import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db import connection
from db.backup import backup_database

if not connection.DB_PATH.exists():
    # 开源环境无生产库：构建空库（基础表 + 全量迁移）作为测试基准
    import db.migration_runner as _runner_module

    _base_dir = tempfile.TemporaryDirectory(prefix="oss_base_")
    connection.DB_PATH = Path(_base_dir.name) / "supplier_data.db"
    _runner_module.DB_PATH = connection.DB_PATH
    import database as _database

    _database.init_db()
from db.migration_runner import run_migrations
from services import cash_budget_service as budget, cost_service, project_service
from services.expense_categories import suggestion


class CashBudgetTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='cash_budget_test_')
        self.addCleanup(self.folder.cleanup)
        self.path = Path(self.folder.name) / 'test.db'
        backup_database(connection.DB_PATH, self.path)
        run_migrations(self.path)
        with closing(sqlite3.connect(self.path)) as conn:
            for table in ('cash_budget_snapshots', 'cash_budget_rules', 'cash_budget_events', 'cash_budget_months', 'cash_budget_audit'):
                conn.execute(f'DELETE FROM {table}')
            conn.commit()
        swap = patch.object(connection, 'DB_PATH', self.path)
        swap.start()
        self.addCleanup(swap.stop)
        source_patch = patch.object(budget, 'sources', return_value=[])
        source_patch.start()
        self.addCleanup(source_patch.stop)

    def snapshot(self):
        return budget.save_snapshot({'balance_date': '2026-08-31', 'amount': '10000', 'reserve': '50'})

    def rule(self, **changes):
        return budget.save_rule({'title': '测试预算', 'category': '场地与日常运营 / 办公通信',
            'amount': '3000', 'cadence': 'daily', 'next_date': '2026-09-01', **changes})

    def test_no_account_or_snapshot_is_not_fake_zero(self):
        self.assertIsNone(budget.forecast()['snapshot'])

    def test_month_budget_conserves_cents_and_reserve(self):
        self.snapshot()
        self.rule()
        result = budget.forecast()
        self.assertEqual(sum(r['out_minor'] for r in result['daily'][:30]), 300000)
        self.assertEqual(result['reserve30_minor'], 305000)
        self.assertEqual(result['available_minor'], 695000)

    def test_unknown_due_payable_reserved_without_fake_date(self):
        self.snapshot()
        budget.save_event({'title': '未安排付款', 'direction': 'out', 'amount': '2000'})
        result = budget.forecast()
        self.assertEqual(result['reserve30_minor'], 205000)
        self.assertEqual(sum(r['out_minor'] for r in result['daily']), 0)

    def test_receipts_only_in_optimistic_scenario(self):
        self.snapshot()
        budget.save_event({'title': '预计回款', 'direction': 'in', 'amount': '500', 'due_date': '2026-09-01'})
        result = budget.forecast()['daily'][0]
        self.assertEqual(result['balance_minor'], 1000000)
        self.assertEqual(result['expected_minor'], 1050000)

    def test_month_end_date_and_end_of_loan(self):
        self.snapshot()
        self.rule(cadence='monthly', next_date='2026-08-31', end_date='2026-09-30')
        result = budget.forecast()
        self.assertEqual(next(r for r in result['daily'] if r['date'] == '2026-09-30')['out_minor'], 300000)
        self.assertEqual(sum(r['out_minor'] for r in result['daily'][30:]), 0)

    def test_same_category_cannot_double_budget(self):
        self.rule()
        with self.assertRaises(ValueError):
            self.rule()

    def test_actual_cost_reduces_current_month_budget(self):
        budget.save_snapshot({'balance_date': '2026-09-10', 'amount': '10000', 'reserve': '0'})
        self.rule()
        cost_service.create_cost({'category': '场地与日常运营 / 办公通信', 'cost_date': '2026-09-09',
                                  'amount': '1000', 'allocation_method': 'unassigned'})
        result = budget.forecast()
        self.assertEqual(sum(r['out_minor'] for r in result['daily'] if r['date'] <= '2026-09-30'), 200000)

    def test_two_months_need_explicit_coverage(self):
        self.assertTrue(all(r['amount_minor'] is None for r in budget.historical_suggestions('2026-09-11')))
        budget.confirm_month('2026-07')
        self.assertTrue(all(r['amount_minor'] is None for r in budget.historical_suggestions('2026-09-11')))
        budget.confirm_month('2026-08')
        self.assertTrue(all(r['amount_minor'] is not None for r in budget.historical_suggestions('2026-09-11')))

    def test_principal_and_personal_cannot_be_project_costs(self):
        project = project_service.create_project({'name': '预算非成本测试', 'business_mode': 'cash'})
        for category in ('还款及大额支出 / 还款本金', '生活与家庭 / 家庭日常'):
            with self.assertRaisesRegex(ValueError, '不能直接归集'):
                cost_service.create_cost({'category': category, 'project_id': project, 'cost_date': '2026-09-10', 'amount': '100'})

    def test_suggestions_do_not_guess_household_or_loan_split(self):
        self.assertFalse(suggestion({'category': '用车', 'counterparty_name_snapshot': '购房欠款归还'})[1])
        self.assertFalse(suggestion({'category': '用车', 'counterparty_name_snapshot': '车贷'})[1])
        self.assertTrue(suggestion({'category': '管理费', 'counterparty_name_snapshot': '代理记账公司'})[1])

    def test_close_plan_does_not_create_cash_transaction(self):
        with closing(sqlite3.connect(self.path)) as conn:
            before = conn.execute('SELECT COUNT(*) FROM fund_transactions').fetchone()[0]
        identity = budget.save_event({'title': '未来付款', 'direction': 'out', 'amount': '50'})
        budget.close_event(identity, 'done')
        self.assertFalse(budget.list_events())
        with closing(sqlite3.connect(self.path)) as conn:
            self.assertEqual(before, conn.execute('SELECT COUNT(*) FROM fund_transactions').fetchone()[0])
            self.assertFalse(conn.execute('PRAGMA foreign_key_check').fetchall())

    def test_invalid_money_and_end_dates(self):
        with self.assertRaises(ValueError):
            self.rule(amount='NaN')
        with self.assertRaises(ValueError):
            self.rule(end_date='2026-01-01')


if __name__ == '__main__':
    unittest.main()
