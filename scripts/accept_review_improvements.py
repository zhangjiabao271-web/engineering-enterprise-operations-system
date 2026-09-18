"""Exercise the changed desktop controls against an online database snapshot."""

import os
import sys
import tempfile
from pathlib import Path


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def main():
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    with tempfile.TemporaryDirectory(prefix="review_acceptance_") as folder:
        test_db = Path(folder)/"supplier_data.db"
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_db)
        os.environ["SUPPLY_CHAIN_ATTACHMENTS_PATH"] = str(Path(folder)/"attachments")
        from db.backup import backup_database
        from db.migration_runner import run_migrations
        backup_database(project_root/"supplier_data.db", test_db)
        run_migrations(test_db)

        import ttkbootstrap as ttk
        from services import cost_service, project_service
        from pages.cost_page import CostLedgerPage
        from pages.finance_page import ReceivablePage
        from ui.theme import configure_design_system
        from services.backup_service import create_backup_archive
        import zipfile

        project_id = project_service.create_project({"name":"界面验收测试项目"})
        cost_id = cost_service.create_cost({"project_id":project_id, "category":"管理费", "cost_date":"2026-09-06", "amount":"10.00"})
        root = ttk.Window(themename="flatly")
        root.geometry("1366x768")
        configure_design_system(root)
        errors = []
        root.report_callback_exception = lambda *args: errors.append(str(args))
        try:
            host = ttk.Frame(root, padding=16)
            host.pack(fill="both", expand=True)
            finance = ReceivablePage(host)
            finance.notebook.select(1)
            root.update()
            assert "attachment" in finance.invoice_tree.tree["columns"]
            finance.missing_attachments_var.set(True)
            finance.refresh()
            for item in finance.invoice_tree.tree.get_children():
                assert "已上传" not in finance.invoice_tree.tree.set(item, "attachment")
            shot_dir = project_root/"qa"/"review_20260906"
            shot_dir.mkdir(parents=True, exist_ok=True)
            from PIL import ImageGrab
            root.update()
            ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(), root.winfo_rootx()+root.winfo_width(), root.winfo_rooty()+root.winfo_height())).save(shot_dir/"invoice_attachments.png")
            host.destroy()

            host = ttk.Frame(root, padding=16)
            host.pack(fill="both", expand=True)
            costs = CostLedgerPage(host)
            root.update()
            costs.detail_notebook.select(2)
            selected = f"manual:{cost_id}"
            assert costs.other_tree.tree.exists(selected)
            costs.other_tree.tree.selection_set(selected)
            costs.edit_selected_cost()
            root.update()
            dialog = next(child for child in descendants(root) if isinstance(child, ttk.Toplevel))
            amount = next(child for child in descendants(dialog) if isinstance(child, ttk.Entry) and child.get()=="10.00")
            amount.delete(0, "end")
            amount.insert(0, "12.34")
            save = next(child for child in descendants(dialog) if isinstance(child, ttk.Button) and child.cget("text")=="保存修改")
            assert save.winfo_ismapped()
            root.update()
            ImageGrab.grab(bbox=(dialog.winfo_rootx(), dialog.winfo_rooty(), dialog.winfo_rootx()+dialog.winfo_width(), dialog.winfo_rooty()+dialog.winfo_height())).save(shot_dir/"cost_edit.png")
            save.invoke()
            root.update()
            assert cost_service.get_cost_entry(cost_id)["amount_minor"]==1234
            assert len(cost_service.list_cost_revisions(cost_id))==1
            costs.other_tree.tree.selection_set(selected)
            costs.edit_selected_cost()
            root.update()
            dialog = next(child for child in descendants(root) if isinstance(child, ttk.Toplevel))
            history = next(child for child in descendants(dialog) if isinstance(child, ttk.Button) and child.cget("text")=="修改历史")
            history.invoke()
            root.update()
            assert any(isinstance(child, ttk.Text) and "12.34" in child.get("1.0", "end") for child in descendants(root))
            host.destroy()
            host = ttk.Frame(root, padding=16)
            host.pack(fill="both", expand=True)
            from pages.import_export_page import ImportExportPage
            from openpyxl import Workbook
            from unittest.mock import patch
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["ID", "采购单号", "采购类型", "采购日期", "项目", "供应商ID", "产品ID", "供应商/商户", "材料名称", "规格", "单位", "数量", "材料单价（未税）", "税率（%）", "含税单价", "未税材料额", "税额", "含税材料额", "运费", "计入项目成本", "成本类别", "支付方式", "支付状态", "票据状态", "经办人", "用途", "备注"])
            good = [None, "REVIEW-UI-IMPORT", "零星采购", "2026-09-06", "导入界面测试项目", None, None, "测试商户", "测试材料", "", "个", 1, 1.005, 0, None, None, None, None, 0, None, "材料费", "现金", "已付款", "无发票", "", "", ""]
            sheet.append(good)
            invalid = list(good)
            invalid[1], invalid[4], invalid[11] = "REVIEW-UI-INVALID", "无效数量不建项目", 0
            sheet.append(invalid)
            duplicate = list(good)
            duplicate[4] = "重复单号不建项目"
            sheet.append(duplicate)
            import_path = Path(folder)/"import.xlsx"
            workbook.save(import_path)
            workbook.close()
            imports = ImportExportPage(host)
            with patch("pages.import_export_page.filedialog.askopenfilename", return_value=str(import_path)):
                imports.import_purchases()
            root.update()
            text = "\n".join(child.get("1.0", "end") for child in descendants(root) if isinstance(child, ttk.Text))
            assert "第 3 行" in text and "第 4 行" in text, text
            names = {row["name"] for row in project_service.list_projects()}
            assert "导入界面测试项目" in names
            assert "无效数量不建项目" not in names and "重复单号不建项目" not in names
            from db.connection import get_connection
            from contextlib import closing
            with closing(get_connection()) as conn:
                assert conn.execute("SELECT total_amount_cents FROM purchase_orders WHERE order_no='REVIEW-UI-IMPORT'").fetchone()[0] == 101
            assert not errors, errors
        finally:
            root.destroy()

        archive_path = Path(folder)/"backup.zip"
        manifest = create_backup_archive(archive_path)
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.testzip() is None
            assert "manifest.json" in archive.namelist()
        print("Desktop acceptance passed: attachment filter, cost amount save, revision history, import failure feedback and atomicity, backup archive")
        print("Backup attachments:", len(manifest["files"]), "missing:", len(manifest["missing_files"]))


if __name__ == "__main__":
    main()
