import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Verify that supplier product scopes accept custom text"
    )
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "--skip-ui",
        action="store_true",
        help="Run data checks only when the current Python runtime has no usable Tk",
    )
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="supplier_custom_scope_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database
        from services import master_data_service

        database.init_db()
        supplier_id = master_data_service.create_supplier(
            {
                "name": "油漆供应商测试",
                "category": "油漆涂料、防腐材料",
                "default_tax_rate_percent": "10",
                "contact": "测试联系人",
                "price_level": "中",
                "delivery": "一般",
                "quality": "良",
                "export": "否",
                "notes": "自定义产品范围冒烟测试",
            }
        )

        created = master_data_service.get_supplier(supplier_id)
        assert created["category"] == "油漆涂料、防腐材料"
        matches = master_data_service.list_suppliers(keyword="油漆供应商测试")
        assert any(row["id"] == supplier_id for row in matches)

        updated_data = dict(created)
        updated_data["category"] = "工业油漆、环氧地坪漆"
        master_data_service.update_supplier(supplier_id, updated_data)
        updated = master_data_service.get_supplier(supplier_id)
        assert updated["category"] == "工业油漆、环氧地坪漆"

        if not args.skip_ui:
            import ttkbootstrap as ttk

            from pages.supplier_page import SupplierPage
            from ui.theme import configure_design_system

            root = ttk.Window(themename="flatly")
            root.geometry("1200x800")
            configure_design_system(root)
            host = ttk.Frame(root, padding=24)
            host.pack(fill="both", expand=True)
            try:
                page = SupplierPage(host)
                root.update_idletasks()
                root.update()
                assert page.table.tree.get_children(), "客商档案列表为空"
                page.role_var.set("供应商")
                page.load_data()
                assert any(
                    "油漆供应商测试" in page.table.tree.set(item, "name")
                    for item in page.table.tree.get_children()
                )
            finally:
                root.destroy()

    print("Supplier custom product scope smoke test passed")


if __name__ == "__main__":
    main()
