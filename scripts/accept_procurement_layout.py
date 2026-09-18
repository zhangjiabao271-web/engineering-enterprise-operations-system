"""Check procurement layouts using an isolated database and synthetic visible rows."""
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def main():
    project_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(project_root))
    with tempfile.TemporaryDirectory(prefix="purchase_layout_") as folder:
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(Path(folder) / "test.db")
        os.environ["SUPPLY_CHAIN_ATTACHMENTS_PATH"] = str(Path(folder) / "attachments")
        from db.backup import backup_database
        from db.migration_runner import run_migrations
        backup_database(project_root / "supplier_data.db", os.environ["SUPPLY_CHAIN_DB_PATH"])
        run_migrations()
        from services import attachment_service, procurement_service as purchase, master_data_service, project_service
        supplier = master_data_service.create_supplier({"name": "布局验收示例材料厂"})
        project = project_service.create_project({"name": "布局验收示例钢平台"})
        order = purchase.add_purchase_order(
            {"purchase_type": "正式采购", "supplier_id": supplier, "project_id": project,
             "merchant_name_snapshot": "布局验收示例材料厂", "purchase_date": "2026-09-09",
             "freight_amount_cents": 50000, "payment_status": "未付款"},
            {"material_name_snapshot": "布局验收混合规格钢材", "settlement_mode": "weight",
             "net_weight": "5", "weight_unit": "吨", "weight_unit_price": "3800",
             "price_basis": "inclusive", "tax_rate_bps": 1300},
        )
        assert attachment_service.purchase_attachment_statuses([order])[order] == "未上传"
        source = project_root / "WORKFLOW.md"
        attachment = attachment_service.add_attachment("purchase", order, source)
        assert attachment_service.purchase_attachment_statuses([order])[order] == "已上传 1 个"
        attachment_service.void_attachments([attachment])
        assert attachment_service.purchase_attachment_statuses([order])[order] == "未上传"

        import ttkbootstrap as ttk
        from PIL import ImageGrab
        from pages.purchase_management_page import PurchaseManagementPage
        from ui.theme import configure_design_system
        root = ttk.Window(themename="flatly")
        root.tk.call("tk", "scaling", 96 / 72)
        configure_design_system(root)
        root.geometry("1280x800+20+20")
        errors = []
        root.report_callback_exception = lambda *args: errors.append(str(args))
        output = project_root / "qa" / "procurement_layout_20260909"
        output.mkdir(parents=True, exist_ok=True)
        try:
            sidebar = ttk.Frame(root, width=180)
            sidebar.pack(side="left", fill="y")
            sidebar.pack_propagate(False)
            ttk.Label(sidebar, text="工程经营系统").pack(pady=24)
            content = ttk.Frame(root, padding=16)
            content.pack(fill="both", expand=True)
            page = PurchaseManagementPage(content)
            page.month_var.set("2026-09")
            page.search_var.set("布局验收")
            page.refresh_lists()
            page.all_tree.selection_set(str(order))
            root.update()
            assert page.notebook.select() == str(page.all_tab)
            assert page.notebook.tabs()[0] == str(page.dashboard_tab)
            assert page.all_tree.set(str(order), "payment") == "未付款"
            assert "19,500.00" in page.order_details[page.all_tree]["text"].get("1.0", "end")
            with patch.object(page, "open_purchase_dialog") as edit:
                page.edit_selected_purchase(page.all_tree, None)
                edit.assert_called_once_with("正式采购", order)

            def capture(widget, name):
                widget.attributes("-topmost", True)
                widget.lift()
                widget.focus_force()
                root.update()
                time.sleep(0.2)
                root.update()
                ImageGrab.grab(bbox=(widget.winfo_rootx(), widget.winfo_rooty(),
                                    widget.winfo_rootx() + widget.winfo_width(),
                                    widget.winfo_rooty() + widget.winfo_height())).save(output / name)

            capture(root, "purchase-list.png")
            long_name = "布局验收示例厂区钢结构平台及附属设施维修工程（年度框架施工项目）"
            with patch.object(project_service, "list_projects", return_value=[
                {"id": project, "project_code": "P-LAYOUT-2026-001", "name": long_name},
            ]):
                page.refresh_filters()
                label = f"{long_name} · P-LAYOUT-2026-001"
                page.project_filter_var.set(label)
                page.refresh_filters()
                assert page.selected_project_id() == project
                root.update()
                original_width = page.project_combo.winfo_width()
                page.project_combo.tk.call("ttk::combobox::Post", str(page.project_combo))
                root.update()
                popdown = page.project_combo.tk.call("ttk::combobox::PopdownWindow", str(page.project_combo))
                popup_width = int(root.tk.call("winfo", "width", popdown))
                assert popup_width > original_width, (popup_width, original_width)
                assert page.project_combo.winfo_width() == original_width
                time.sleep(0.2)
                root.update()
                assert int(root.tk.call("winfo", "ismapped", popdown))
                ImageGrab.grab(bbox=(root.winfo_rootx(), root.winfo_rooty(),
                                    root.winfo_rootx() + root.winfo_width(),
                                    root.winfo_rooty() + root.winfo_height())).save(output / "project-dropdown.png")
                root.tk.call("ttk::combobox::Unpost", str(page.project_combo))
                page.project_tooltip.show_tip()
                root.update()
                assert page.project_tooltip.text == label
                tip_labels = [w for w in descendants(page.project_tooltip.toplevel) if isinstance(w, ttk.Label)]
                assert any(w.cget("text") == label for w in tip_labels)
                page.project_tooltip.hide_tip()
            page.project_filter_var.set("全部项目")
            page.refresh_filters()
            page.open_purchase_dialog("正式采购", order)
            root.update()
            dialog = next(w for w in descendants(root) if isinstance(w, ttk.Toplevel))
            dialog.geometry("1040x730+30+30")
            root.update()
            capture(dialog, "purchase-weight-wide.png")

            def check_form():
                for widget in descendants(dialog):
                    if isinstance(widget, ttk.Label) and widget.winfo_ismapped() and widget.grid_info():
                        assert widget.winfo_width() >= widget.winfo_reqwidth(), (widget.cget("text"), widget.winfo_width(), widget.winfo_reqwidth())
                save = next(w for w in descendants(dialog) if isinstance(w, ttk.Button) and w.cget("text") == "保存修改")
                assert save.winfo_ismapped()
                assert save.winfo_rooty() + save.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height()

            check_form()
            mode = next(w for w in descendants(dialog) if isinstance(w, ttk.Combobox) and "按结算总额" in w["values"])
            for value in ("按数量", "按过磅重量", "按结算总额"):
                mode.set(value)
                root.update()
                check_form()
            dialog.geometry("600x650+30+30")
            root.update()
            check_form()
            visible_labels = [w for w in descendants(dialog) if isinstance(w, ttk.Label) and w.grid_info() and w.winfo_ismapped()]
            assert all(w.grid_info()["column"] == 0 for w in visible_labels)
            capture(dialog, "purchase-total-narrow.png")
            dialog.destroy()
            root.geometry("1000x700+20+20")
            root.update()
            time.sleep(0.2)
            root.update()
            pane = page.order_details[page.all_tree]["pane"]
            assert not errors, errors
            assert str(pane.cget("orient")) == "vertical", (root.geometry(), pane.winfo_width(), pane.cget("orient"))
            assert page.order_details[page.all_tree]["text"].winfo_height() >= 80
            for button in descendants(page.order_details[page.all_tree]["panel"]):
                if isinstance(button, ttk.Button):
                    assert button.winfo_ismapped(), button.cget("text")
                    assert button.winfo_rooty() + button.winfo_height() <= root.winfo_rooty() + root.winfo_height(), button.cget("text")
            capture(root, "purchase-list-narrow.png")
            assert not errors, errors
            print("PASS: list/detail, actual payment status, edit routing, attachment status, three pricing layouts, narrow single-column form, fixed save buttons")
        finally:
            root.destroy()


if __name__ == "__main__":
    main()
