"""Inclusive period attendance, month drill-down, and overtime accounting."""

import tempfile
import unittest
import os
import ctypes
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4


class WorkdayMonthlyServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix="workday_monthly_")
        cls.test_db = Path(cls.temp_dir.name) / "supplier_data.db"
        source_db = Path(os.environ["SUPPLY_CHAIN_DB_PATH"])
        from db.backup import backup_database
        from db.migration_runner import run_migrations
        import db.connection as connection

        backup_database(source_db, cls.test_db)
        cls.original_db_path = connection.DB_PATH
        run_migrations(cls.test_db)
        connection.DB_PATH = cls.test_db
        from services import labor_service

        cls.labor = labor_service

    @classmethod
    def tearDownClass(cls):
        import db.connection as connection

        connection.DB_PATH = cls.original_db_path
        cls.temp_dir.cleanup()

    def _add_log(self, worker_id, work_date, days, *, overtime=False):
        return self.labor.add_work_log({
            "worker_id": worker_id,
            "work_date": work_date,
            "construction_site": "月度汇总测试工地",
            "work_type": "安装",
            "work_days": days,
            "daily_rate": 300,
            "is_overtime": overtime,
            "allow_unassigned": True,
        })

    def test_cross_year_same_name_retired_worker_and_void_log(self):
        name = f"月度同名工人-{uuid4().hex[:8]}"
        first = self.labor.add_worker({"name": name, "status": "离职"})
        second = self.labor.add_worker({"name": name, "status": "在职"})
        self.labor.add_worker({"name": f"无记录工人-{uuid4().hex[:8]}"})
        dec_log = self._add_log(first, "2099-12-31", 0.5, overtime=True)
        jan_log = self._add_log(first, "2100-01-01", 1)
        self._add_log(second, "2100-01-01", 0.5)
        void_log = self._add_log(first, "2100-01-02", 1)
        self.labor.delete_work_logs([void_log])
        self._add_log(first, "2100-02-01", 1)

        summary = self.labor.get_worker_monthly_workdays(
            "2099-12-31", "2100-01-01"
        )
        self.assertEqual(summary["months"], ["2099-12", "2100-01"])
        self.assertEqual(summary["worker_count"], 2)
        self.assertEqual(summary["record_count"], 3)
        self.assertEqual(summary["work_days"], 2)
        self.assertEqual(summary["overtime_days"], 0.5)
        self.assertEqual(summary["month_totals"], {"2099-12": 0.5, "2100-01": 1.5})
        by_id = {row["worker_id"]: row for row in summary["workers"]}
        self.assertEqual(by_id[first]["worker_status"], "离职")
        self.assertEqual(by_id[first]["months"], {"2099-12": 0.5, "2100-01": 1})
        self.assertEqual(by_id[second]["months"], {"2100-01": 0.5})

        dec_details = self.labor.get_worker_month_work_logs(
            first, "2099-12", "2099-12-31", "2100-01-01"
        )
        self.assertEqual([row["id"] for row in dec_details["details"]], [dec_log])
        self.assertEqual(dec_details["work_days"], 0.5)
        self.assertEqual(dec_details["overtime_days"], 0.5)
        jan_details = self.labor.get_worker_month_work_logs(
            first, "2100-01", "2099-12-31", "2100-01-01"
        )
        self.assertEqual([row["id"] for row in jan_details["details"]], [jan_log])
        self.assertEqual(jan_details["overtime_days"], 0)

    def test_invalid_period_and_outside_month(self):
        with self.assertRaisesRegex(ValueError, "开始日期不能晚于结束日期"):
            self.labor.get_worker_monthly_workdays("2100-02-01", "2100-01-01")
        with self.assertRaisesRegex(ValueError, "月份必须是 YYYY-MM"):
            self.labor.get_worker_month_work_logs(1, "2100-13", "2100-01-01", "2100-01-31")
        result = self.labor.get_worker_month_work_logs(
            1, "2099-12", "2100-01-01", "2100-01-31"
        )
        self.assertEqual(result["record_count"], 0)

    def test_active_workers_first_then_period_workdays_descending(self):
        suffix = uuid4().hex[:8]
        workers = {
            "active_low": self.labor.add_worker({"name": f"A在职低-{suffix}", "status": "在职"}),
            "retired_high": self.labor.add_worker({"name": f"A离职高-{suffix}", "status": "离职"}),
            "active_high": self.labor.add_worker({"name": f"Z在职高-{suffix}", "status": "在职"}),
            "retired_low": self.labor.add_worker({"name": f"Z离职低-{suffix}", "status": "离职"}),
            "tie_first": self.labor.add_worker({"name": f"同名在职-{suffix}", "status": "在职"}),
            "tie_second": self.labor.add_worker({"name": f"同名在职-{suffix}", "status": "在职"}),
        }
        for key, days in (("active_low", 0.5), ("retired_high", 1),
                          ("active_high", 1), ("retired_low", 1),
                          ("tie_first", 1), ("tie_second", 1)):
            self._add_log(workers[key], "2101-03-01", days)
        self._add_log(workers["active_high"], "2101-03-02", 0.5, overtime=True)
        self._add_log(workers["retired_high"], "2101-03-02", 1)
        self._add_log(workers["active_low"], "2101-03-03", 1)
        void_log = self._add_log(workers["active_low"], "2101-03-02", 1)
        self.labor.delete_work_logs([void_log])

        result = self.labor.get_worker_monthly_workdays("2101-03-01", "2101-03-02")

        self.assertEqual([row["worker_id"] for row in result["workers"]],
                         [workers[key] for key in ("active_high", "tie_first", "tie_second",
                                                   "active_low", "retired_high", "retired_low")])
        self.assertEqual([row["work_days"] for row in result["workers"]], [1.5, 1, 1, 0.5, 2, 1])
        self.assertEqual(result["record_count"], 8)
        self.assertEqual(result["work_days"], 7)
        self.assertEqual(result["month_totals"], {"2101-03": 7})
        self.assertEqual(result["overtime_days"], 0.5)




if __name__ == "__main__":
    unittest.main()
