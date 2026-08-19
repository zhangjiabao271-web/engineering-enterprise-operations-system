import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Load every new V4 desktop page on a database copy"
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="v4_pages_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import ttkbootstrap as ttk

        import database
        from pages import (
            ContractManagementPage,
            CostLedgerPage,
            OperationsDashboardPage,
            ProjectWorkspacePage,
            ReceivablePage,
        )
        from ui.theme import configure_design_system

        database.init_db()
        root = ttk.Window(themename="flatly")
        root.geometry("1200x800")
        configure_design_system(root)
        loaded = []
        try:
            for page_type in (
                OperationsDashboardPage,
                ProjectWorkspacePage,
                ContractManagementPage,
                ReceivablePage,
                CostLedgerPage,
            ):
                host = ttk.Frame(root, padding=24)
                host.pack(fill="both", expand=True)
                if page_type in (
                    OperationsDashboardPage,
                    ProjectWorkspacePage,
                ):
                    page = page_type(host, lambda _key: None)
                else:
                    page = page_type(host)
                root.update_idletasks()
                root.update()
                assert host.winfo_reqwidth() > 100
                assert host.winfo_reqheight() > 100
                loaded.append(page_type.__name__)
                host.destroy()
        finally:
            root.destroy()

    print(f"V4 page load smoke test passed: {', '.join(loaded)}")


if __name__ == "__main__":
    main()
