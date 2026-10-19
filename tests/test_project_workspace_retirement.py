"""Project-scoped navigation after retiring the duplicate workspace entry."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from db import connection
from db.backup import backup_database
from db.migration_runner import run_migrations
from services import contract_service, finance_service, operations_service, project_service






class _IsolatedProjectDatabase(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="project_navigation_")
        self.addCleanup(folder.cleanup)
        target = Path(folder.name) / "supplier_data.db"
        backup_database(connection.DB_PATH, target)
        run_migrations(target)
        db_patch = patch.object(connection, "DB_PATH", target)
        db_patch.start()
        self.addCleanup(db_patch.stop)


class ProjectWorkspaceRetirementTests(_IsolatedProjectDatabase):
    def test_project_guidance_matches_dashboard(self):
        overview = operations_service.get_executive_overview()
        for project in overview["projects"]:
            guidance = operations_service.get_project_guidance(project["project_id"])
            for key in ("stage_code", "stage_label", "gaps", "gap_text"):
                self.assertEqual(guidance[key], project[key])

    def test_contract_project_filter_includes_only_related_contracts(self):
        project_ids = [
            project_service.create_project({"name": f"跳转筛选-{uuid4().hex[:8]}"})
            for _ in range(2)
        ]
        contract_ids = []
        for project_id in project_ids:
            contract_id = contract_service.create_contract({
                "name": f"跳转合同-{uuid4().hex[:8]}",
                "contract_type": "project",
                "sign_date": "2026-09-28",
                "amount": "1000.00",
                "status": "active",
            })
            contract_service.create_allocation({
                "contract_id": contract_id,
                "project_id": project_id,
                "amount": "1000.00",
            })
            contract_ids.append(contract_id)
        for project_id, expected_id in zip(project_ids, contract_ids):
            visible_ids = {
                row["id"] for row in contract_service.list_contracts(project_id=project_id)
            }
            self.assertEqual(visible_ids.intersection(contract_ids), {expected_id})

    def test_advance_receipt_is_not_described_as_invalid_over_collection(self):
        project_id = project_service.create_project({
            "name": f"预收提示-{uuid4().hex[:8]}",
            "business_mode": "cash", "invoice_policy": "not_required",
        })
        finance_service.create_receipt({
            "project_id": project_id, "amount": "40000.00",
            "receipt_date": "2026-09-28",
        })
        guidance = operations_service.get_project_guidance(project_id)
        self.assertIn("部分回款待确认收入", guidance["gaps"])
        self.assertNotIn("回款超过结算", guidance["gap_text"])






if __name__ == "__main__":
    unittest.main()
