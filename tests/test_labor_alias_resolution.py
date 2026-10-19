import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db import connection, migration_runner
from db.connection import db_read
from db.schema import init_db
from services import labor_service, project_service


class LaborAliasResolutionTests(unittest.TestCase):
    canonical = "合成同名项目"
    alias = "合成简称"

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="labor_alias_resolution_")
        self.addCleanup(folder.cleanup)
        for module in (connection, migration_runner):
            override = patch.object(module, "DB_PATH", Path(folder.name) / "synthetic.db")
            override.start()
            self.addCleanup(override.stop)
        aliases = patch.dict(
            labor_service.PROJECT_SITE_ALIASES,
            {self.alias: self.canonical, self.canonical: self.canonical},
            clear=True,
        )
        aliases.start()
        self.addCleanup(aliases.stop)
        init_db()
        self.worker_id = labor_service.add_worker(
            {"name": "合成工人", "daily_rate": "300", "status": "在职"}
        )

    def _project(self, code, name=None):
        return project_service.create_project(
            {"project_code": code, "name": name or self.canonical}
        )

    def _site(self, project_id, name=None):
        return project_service.create_project_site(
            project_id, {"site_name": name or self.canonical}
        )

    def _data(self, site=None, **extra):
        return {
            "worker_id": self.worker_id,
            "work_date": "2099-01-01",
            "construction_site": site or self.alias,
            "work_days": 1,
            **extra,
        }

    def _logs(self):
        with db_read() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM work_logs ORDER BY id")]

    def test_duplicate_project_names_reject_alias_and_canonical_without_writes(self):
        for code in ("ALIAS-A", "ALIAS-B"):
            self._site(self._project(code))
        for site in (self.alias, self.canonical):
            with self.subTest(site=site):
                self.assertIsNone(labor_service.suggest_project_for_site(site))
                with self.assertRaisesRegex(ValueError, "请选择所属项目"):
                    labor_service.add_work_log(self._data(site))
                self.assertEqual(self._logs(), [])

    def test_explicit_project_and_site_override_ambiguous_name(self):
        self._site(self._project("ALIAS-A"))
        project_id = self._project("ALIAS-B")
        site_id = self._site(project_id)
        log_id = labor_service.add_work_log(
            self._data(project_id=project_id, project_site_id=site_id)
        )
        row = labor_service.get_work_log_by_id(log_id)
        self.assertEqual((row["project_id"], row["project_site_id"]), (project_id, site_id))
        self.assertEqual(row["construction_site"], self.canonical)
        self.assertEqual(row["amount_minor"], 30000)

    def test_explicit_unassigned_does_not_pick_any_duplicate_project(self):
        self._project("ALIAS-A")
        self._project("ALIAS-B")
        log_id = labor_service.add_work_log(self._data(allow_unassigned=True))
        row = labor_service.get_work_log_by_id(log_id)
        self.assertIsNone(row["project_id"])
        self.assertIsNone(row["project_site_id"])

    def test_unique_project_and_site_resolve_for_alias_and_canonical(self):
        project_id = self._project("ALIAS-A")
        site_id = self._site(project_id)
        for day, site in enumerate((self.alias, self.canonical), start=1):
            with self.subTest(site=site):
                suggestion = labor_service.suggest_project_for_site(f" {site} ")
                self.assertEqual(suggestion["id"], project_id)
                self.assertEqual(suggestion["project_site_id"], site_id)
                log_id = labor_service.add_work_log(
                    self._data(site, work_date=f"2099-01-{day:02d}")
                )
                row = labor_service.get_work_log_by_id(log_id)
                self.assertEqual((row["project_id"], row["project_site_id"]), (project_id, site_id))
                self.assertEqual(row["construction_site"], self.canonical)

    def test_multiple_matching_sites_do_not_make_one_project_ambiguous(self):
        project_id = self._project("ALIAS-A")
        self._site(project_id)
        self._site(project_id)
        suggestion = labor_service.suggest_project_for_site(self.alias)
        self.assertEqual(suggestion["id"], project_id)
        self.assertIsNone(suggestion["project_site_id"])
        log_id = labor_service.add_work_log(self._data())
        row = labor_service.get_work_log_by_id(log_id)
        self.assertEqual(row["project_id"], project_id)
        self.assertIsNone(row["project_site_id"])

    def test_unique_project_without_site_still_resolves(self):
        project_id = self._project("ALIAS-A")
        suggestion = labor_service.suggest_project_for_site(self.alias)
        self.assertEqual(suggestion["id"], project_id)
        self.assertIsNone(suggestion["project_site_id"])

    def test_alias_obeys_same_cross_project_site_ambiguity_rule(self):
        self._site(self._project("ALIAS-A"))
        self._site(self._project("ALIAS-B", "合成另一项目"))
        for site in (self.alias, self.canonical):
            with self.subTest(site=site):
                self.assertIsNone(labor_service.suggest_project_for_site(site))
                with self.assertRaisesRegex(ValueError, "请选择所属项目"):
                    labor_service.add_work_log(self._data(site))
        self.assertEqual(self._logs(), [])

    def test_missing_alias_target_and_ordinary_ambiguity_have_no_suggestion(self):
        self.assertIsNone(labor_service.suggest_project_for_site(self.alias))
        for code in ("PLAIN-A", "PLAIN-B"):
            self._project(code, "合成普通项目")
        self.assertIsNone(labor_service.suggest_project_for_site("合成普通项目"))
        with self.assertRaisesRegex(ValueError, "请选择所属项目"):
            labor_service.add_work_log(self._data("合成普通项目"))
        self.assertEqual(self._logs(), [])

    def test_batch_and_update_reject_ambiguity_without_partial_changes(self):
        project_id = self._project("ALIAS-A")
        self._site(project_id)
        original_data = self._data(project_id=project_id)
        log_id = labor_service.add_work_log(original_data)
        self._site(self._project("ALIAS-B"))
        before = self._logs()
        entries = [
            self._data(project_id=project_id, work_date="2099-01-02"),
            self._data(work_date="2099-01-03"),
        ]
        for operation in (labor_service.preview_work_logs_batch, labor_service.add_work_logs_batch):
            with self.subTest(operation=operation.__name__):
                with self.assertRaisesRegex(ValueError, "请选择所属项目"):
                    operation(entries)
                self.assertEqual(self._logs(), before)
        with self.assertRaisesRegex(ValueError, "请选择所属项目"):
            labor_service.update_work_log(log_id, self._data(work_days=0.5))
        self.assertEqual(self._logs(), before)


if __name__ == "__main__":
    unittest.main()
