import os
import sys
import tempfile
from pathlib import Path


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def capture(window, output_path):
    from PIL import ImageGrab

    window.update_idletasks()
    window.update()
    ImageGrab.grab(window=window.winfo_id()).save(output_path)


def main():
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))
    os.environ["TCL_LIBRARY"] = str(
        project_root / ".venv" / "tcl" / "tcl8.6"
    )
    os.environ["TK_LIBRARY"] = str(
        project_root / ".venv" / "tcl" / "tk8.6"
    )
    screenshot_dir = Path(
        os.environ.get("COLLECTION_UI_SCREENSHOT_DIR", project_root / "qa")
    )
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="collection-ui-") as temp_dir:
        test_db = Path(temp_dir) / "ui-smoke.db"
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_db)
        from db.backup import backup_database
        backup_database(project_root / "supplier_data.db", test_db)

        import ttkbootstrap as ttk

        import database
        from pages.finance_page import ReceivablePage
        from ui.components import DatePicker
        from ui.theme import configure_design_system

        root = ttk.Window(themename="flatly")
        root.title("回款作战台界面验收")
        root.geometry("1200x800+0+0")
        root.minsize(1200, 800)
        database.init_db()
        configure_design_system(root)
        content = ttk.Frame(root, padding=24)
        content.pack(fill="both", expand=True)
        page = ReceivablePage(content)
        page.notebook.select(3)
        root.update()

        project_rows = page.collection_project_tree.tree.get_children()
        if not project_rows:
            raise RuntimeError("项目视图没有待回款项目")
        if not page.collection_project_tree.winfo_ismapped():
            raise RuntimeError("项目视图表格不可见")
        capture(root, screenshot_dir / "collection-project-view.png")

        page.collection_view_var.set("客户视图")
        page._refresh_collection_workbench()
        root.update()
        customer_rows = page.collection_customer_tree.tree.get_children()
        if not customer_rows or not page.collection_customer_tree.winfo_ismapped():
            raise RuntimeError("客户视图没有正确显示")
        capture(root, screenshot_dir / "collection-customer-view.png")

        page.collection_view_var.set("项目视图")
        page._refresh_collection_workbench()
        root.update()
        project_rows = page.collection_project_tree.tree.get_children()
        page.collection_project_tree.tree.selection_set(project_rows[0])
        page.open_collection_case_dialog()
        root.update()
        case_dialog = root.winfo_children()[-1]
        case_children = list(descendants(case_dialog))
        date_pickers = [
            child for child in case_children if isinstance(child, DatePicker)
        ]
        button_labels = [
            child.cget("text")
            for child in case_children
            if isinstance(child, ttk.Button)
        ]
        if len(date_pickers) != 4:
            raise RuntimeError(f"跟进弹窗日历字段数量错误：{len(date_pickers)}")
        if not {"取消", "保存跟进"} <= set(button_labels):
            raise RuntimeError(f"跟进弹窗底部操作不可用：{button_labels}")
        capture(case_dialog, screenshot_dir / "collection-followup-dialog.png")
        case_dialog.destroy()

        page.collection_project_tree.tree.selection_set(project_rows[0])
        page.open_collection_history()
        root.update()
        history_dialog = root.winfo_children()[-1]
        history_buttons = [
            child.cget("text")
            for child in descendants(history_dialog)
            if isinstance(child, ttk.Button)
        ]
        if "关闭" not in history_buttons:
            raise RuntimeError("跟进历史弹窗没有关闭操作")
        capture(history_dialog, screenshot_dir / "collection-history-dialog.png")
        history_dialog.destroy()

        root.destroy()
        print(
            "Collection workbench UI smoke passed:",
            {
                "project_rows": len(project_rows),
                "customer_rows": len(customer_rows),
                "date_pickers": len(date_pickers),
                "screenshots": 4,
            },
        )


if __name__ == "__main__":
    main()
