import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(
        description="Smoke-test project and collection archive tabs"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="archive_tabs_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database
        import ttkbootstrap as ttk
        from pages.finance_page import ReceivablePage
        from pages.project_page import ProjectManagementPage
        from services import contract_service, finance_service, project_service

        database.init_db()
        suffix = uuid4().hex[:8]

        completed_pending_id = project_service.create_project(
            {
                "project_code": f"ARCHIVE-PENDING-{suffix}",
                "name": "归档页签测试-完工待回款",
                "status": "已完工",
                "business_mode": "cash",
                "invoice_policy": "not_required",
            }
        )
        contract_service.create_settlement(
            {
                "project_id": completed_pending_id,
                "settlement_date": "2026-08-19",
                "amount": "1000.00",
            }
        )

        active_settled_id = project_service.create_project(
            {
                "project_code": f"ACTIVE-SETTLED-{suffix}",
                "name": "归档页签测试-在建已结清",
                "status": "进行中",
                "business_mode": "cash",
                "invoice_policy": "not_required",
            }
        )
        settlement_id = contract_service.create_settlement(
            {
                "project_id": active_settled_id,
                "settlement_date": "2026-08-19",
                "amount": "800.00",
            }
        )
        finance_service.create_receipt(
            {
                "project_id": active_settled_id,
                "settlement_id": settlement_id,
                "receipt_date": "2026-08-19",
                "amount": "800.00",
            }
        )

        root = ttk.Window(themename="flatly")
        root.withdraw()
        try:
            project_host = ttk.Frame(root)
            project_host.pack(fill="both", expand=True)
            project_page = ProjectManagementPage(project_host)
            root.update_idletasks()
            root.update()

            assert project_page.project_notebook.index(
                project_page.project_notebook.select()
            ) == 0
            active_tree = project_page.project_trees["active"].tree
            history_tree = project_page.project_trees["history"].tree
            assert all(
                active_tree.set(item, "status") in {"筹备中", "进行中"}
                for item in active_tree.get_children()
            )
            assert all(
                history_tree.set(item, "status") in {"已完工", "已关闭"}
                for item in history_tree.get_children()
            )
            assert str(completed_pending_id) in history_tree.get_children()
            assert str(active_settled_id) in active_tree.get_children()

            project_host.destroy()
            finance_host = ttk.Frame(root)
            finance_host.pack(fill="both", expand=True)
            finance_page = ReceivablePage(finance_host)
            root.update_idletasks()
            root.update()

            assert finance_page.collection_notebook.index(
                finance_page.collection_notebook.select()
            ) == 0
            pending_tree = finance_page.collection_trees["pending"].tree
            settled_tree = finance_page.collection_trees["settled"].tree
            assert str(completed_pending_id) in pending_tree.get_children()
            assert str(active_settled_id) in settled_tree.get_children()
            assert all(
                pending_tree.set(item, "status") != "已结清"
                for item in pending_tree.get_children()
            )
            assert all(
                settled_tree.set(item, "status") == "已结清"
                for item in settled_tree.get_children()
            )
        finally:
            root.destroy()

    print("Project and collection archive tabs smoke test passed")


if __name__ == "__main__":
    main()
