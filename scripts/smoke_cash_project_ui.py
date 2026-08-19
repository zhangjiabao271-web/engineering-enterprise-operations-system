import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def find_source_combo(dialog, ttk):
    expected = {"正式合同工程", "零星现金工程"}
    for widget in descendants(dialog):
        if isinstance(widget, ttk.Combobox):
            values = set(widget.cget("values"))
            if expected <= values:
                return widget
    raise AssertionError(f"{dialog.title()} 缺少业务来源选择")


def label_by_text(dialog, ttk, text):
    return next(
        widget for widget in descendants(dialog)
        if isinstance(widget, ttk.Label) and widget.cget("text") == text
    )


def combo_containing(dialog, ttk, value):
    return next(
        widget for widget in descendants(dialog)
        if isinstance(widget, ttk.Combobox)
        and value in widget.cget("values")
    )


def field_by_label(dialog, ttk, text):
    label = label_by_text(dialog, ttk, text)
    target_row = int(label.grid_info()["row"])
    return next(
        widget for widget in label.master.winfo_children()
        if widget is not label
        and widget.grid_info()
        and int(widget.grid_info()["row"]) == target_row
        and int(widget.grid_info()["column"]) == 1
    )


def main():
    parser = argparse.ArgumentParser(description="Smoke-test cash project UI paths")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="cash_project_ui_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database as db
        import ttkbootstrap as ttk
        from pages import ContractManagementPage, ReceivablePage
        from services import contract_service, finance_service, project_service
        from tkinter import messagebox
        from ui.theme import configure_design_system

        unexpected = []

        def capture(kind):
            def handler(title, message, **_kwargs):
                unexpected.append((kind, str(title), str(message)))
                return True if kind == "question" else "ok"

            return handler

        messagebox.showwarning = capture("warning")
        messagebox.showerror = capture("error")
        messagebox.showinfo = capture("info")
        messagebox.askyesno = capture("question")

        db.init_db()
        project_id = project_service.create_project(
            {
                "name": "零星现金界面验收项目",
                "project_code": "CASH-UI-SMOKE",
                "customer_name": "现金界面验收客户",
                "customer_entity_type": "individual_business",
                "business_mode": "cash",
                "invoice_policy": "not_required",
                "status": "进行中",
            }
        )
        settlement_id = contract_service.create_settlement(
            {
                "project_id": project_id,
                "settlement_date": "2026-08-14",
                "amount": "1200.00",
                "basis": "界面验收",
            }
        )
        no_settlement_project_id = project_service.create_project(
            {
                "name": "零星现金无确认界面验收项目",
                "project_code": "CASH-UI-NO-SETTLEMENT",
                "customer_name": "历史现金界面验收客户",
                "customer_entity_type": "individual_business",
                "business_mode": "cash",
                "invoice_policy": "not_required",
                "status": "已完工",
            }
        )
        formal_project_id = project_service.create_project(
            {
                "name": "正式发票余额界面验收项目",
                "project_code": "FORMAL-INVOICE-UI",
                "business_mode": "contract",
                "invoice_policy": "required",
                "status": "进行中",
            }
        )
        formal_contract_id = contract_service.create_contract(
            {
                "contract_no": "FORMAL-UI-CONTRACT",
                "name": "正式发票余额界面验收合同",
                "contract_type": "project",
                "sign_date": "2026-08-14",
                "amount": "2000.00",
                "status": "active",
            }
        )
        contract_service.create_allocation(
            {
                "contract_id": formal_contract_id,
                "project_id": formal_project_id,
                "amount": "2000.00",
            }
        )
        contract_service.create_settlement(
            {
                "contract_id": formal_contract_id,
                "project_id": formal_project_id,
                "settlement_date": "2026-08-14",
                "amount": "1500.00",
            }
        )
        formal_invoice_id = finance_service.create_invoice(
            {
                "invoice_no": "FORMAL-UI-INVOICE",
                "contract_id": formal_contract_id,
                "project_id": formal_project_id,
                "invoice_date": "2026-08-14",
                "amount": "1000.00",
            }
        )
        finance_service.create_receipt(
            {
                "receipt_no": "FORMAL-UI-RECEIPT",
                "contract_id": formal_contract_id,
                "project_id": formal_project_id,
                "invoice_id": formal_invoice_id,
                "receipt_date": "2026-08-14",
                "amount": "400.00",
            }
        )

        root = ttk.Window(themename="flatly")
        root.withdraw()
        configure_design_system(root)
        try:
            contract_host = ttk.Frame(root)
            contract_host.pack(fill="both", expand=True)
            contract_page = ContractManagementPage(contract_host)
            contract_page.open_settlement_dialog(settlement_id)
            contract_dialog = root.winfo_children()[-1]
            source_combo = find_source_combo(contract_dialog, ttk)
            source_combo.set("零星现金工程")
            source_combo.event_generate("<<ComboboxSelected>>")
            root.update_idletasks()
            assert label_by_text(
                contract_dialog, ttk, "零星工程项目 *"
            ).grid_info(), "完工金额确认未显示零星工程项目选择"
            assert not label_by_text(
                contract_dialog, ttk, "合同与项目 *"
            ).grid_info(), "现金收入确认仍显示合同项目选择"
            contract_dialog.destroy()
            contract_host.destroy()

            finance_host = ttk.Frame(root)
            finance_host.pack(fill="both", expand=True)
            finance_page = ReceivablePage(finance_host)
            invoice_queue_combo = combo_containing(
                finance_host, ttk, "已结清"
            )
            assert "未结清" in invoice_queue_combo.cget("values")
            invoice_values = finance_page.invoice_tree.tree.item(
                str(formal_invoice_id), "values"
            )
            invoice_row = dict(zip(
                finance_page.invoice_tree.tree.cget("columns"),
                invoice_values,
            ))
            assert (
                invoice_row["received"],
                invoice_row["balance"],
                invoice_row["status"],
            ) == ("¥400.00", "¥600.00", "部分回款"), (
                "发票列表未显示已关联回款、余额和回款状态"
            )
            invoice_queue_combo.set("未结清")
            invoice_queue_combo.event_generate("<<ComboboxSelected>>")
            root.update_idletasks()
            assert finance_page.invoice_tree.tree.exists(str(formal_invoice_id))
            invoice_queue_combo.set("已结清")
            invoice_queue_combo.event_generate("<<ComboboxSelected>>")
            root.update_idletasks()
            assert not finance_page.invoice_tree.tree.exists(
                str(formal_invoice_id)
            ), "部分回款发票错误进入已结清队列"
            invoice_queue_combo.set("全部发票")
            invoice_queue_combo.event_generate("<<ComboboxSelected>>")
            root.update_idletasks()
            finance_page.open_receipt_dialog()
            receipt_dialog = root.winfo_children()[-1]
            formal_allocation_label = (
                "FORMAL-UI-CONTRACT → 正式发票余额界面验收项目"
            )
            allocation_combo = combo_containing(
                receipt_dialog, ttk, formal_allocation_label
            )
            allocation_combo.set(formal_allocation_label)
            allocation_combo.event_generate("<<ComboboxSelected>>")
            root.update_idletasks()
            combo_containing(
                receipt_dialog, ttk,
                "FORMAL-UI-INVOICE · 可回款 ¥600.00",
            )
            formal_receipt_amount = field_by_label(
                receipt_dialog, ttk, "回款金额（元）*"
            )
            formal_receipt_amount.insert(0, "500.00")
            adjust_button = next(
                widget for widget in descendants(receipt_dialog)
                if isinstance(widget, ttk.Button)
                and widget.cget("text") == "查看 / 调整"
            )
            adjust_button.invoke()
            root.update_idletasks()
            allocation_dialog = root.winfo_children()[-1]
            assert allocation_dialog.title() == "调整收入确认分配"
            assert any(
                isinstance(widget, ttk.Button)
                and widget.cget("text") == "使用该分配"
                for widget in descendants(allocation_dialog)
            ), "回款分配弹窗缺少确认操作"
            allocation_dialog.destroy()
            formal_receipt_amount.delete(0, "end")
            source_combo = find_source_combo(receipt_dialog, ttk)
            source_combo.set("零星现金工程")
            source_combo.event_generate("<<ComboboxSelected>>")
            root.update_idletasks()
            assert label_by_text(
                receipt_dialog, ttk, "完工金额确认 *"
            ).grid_info(), "现金回款未显示完工金额确认选择"
            assert not label_by_text(
                receipt_dialog, ttk, "合同与项目 *"
            ).grid_info(), "现金回款仍显示合同项目选择"
            assert not label_by_text(
                receipt_dialog, ttk, "关联发票"
            ).grid_info(), "现金回款仍显示发票选择"
            existing_project_label = "CASH-UI-SMOKE · 零星现金界面验收项目"
            no_settlement_project_label = (
                "CASH-UI-NO-SETTLEMENT · 零星现金无确认界面验收项目"
            )
            cash_project_combo = combo_containing(
                receipt_dialog, ttk, no_settlement_project_label
            )
            cash_project_combo.set(existing_project_label)
            cash_project_combo.event_generate("<<ComboboxSelected>>")
            root.update_idletasks()
            settlement_combo = combo_containing(
                receipt_dialog, ttk, "新增完工金额确认（本次同步建立）"
            )
            existing_settlement_label = next(
                label for label in settlement_combo.cget("values")
                if label != "新增完工金额确认（本次同步建立）"
            )
            settlement_combo.set(existing_settlement_label)
            settlement_combo.event_generate("<<ComboboxSelected>>")
            root.update_idletasks()
            assert not label_by_text(
                receipt_dialog, ttk, "完工确认日期 *"
            ).grid_info(), "选择已有完工确认后仍显示新增确认日期"
            assert not label_by_text(
                receipt_dialog, ttk, "完工金额（元）*"
            ).grid_info(), "选择已有完工确认后仍显示新增确认金额"

            cash_project_combo.set(no_settlement_project_label)
            cash_project_combo.event_generate("<<ComboboxSelected>>")
            root.update_idletasks()
            assert settlement_combo.get() == (
                "新增完工金额确认（本次同步建立）"
            ), "无完工确认的现金项目未自动进入同步建立模式"
            assert label_by_text(
                receipt_dialog, ttk, "完工确认日期 *"
            ).grid_info(), "无完工确认的现金项目未显示完工确认日期"
            assert label_by_text(
                receipt_dialog, ttk, "完工金额（元）*"
            ).grid_info(), "无完工确认的现金项目未显示完工金额"
            method_combo = next(
                widget for widget in descendants(receipt_dialog)
                if isinstance(widget, ttk.Combobox)
                and "银行转账" in widget.cget("values")
                and "现金" in widget.cget("values")
            )
            assert method_combo.get() == "现金", "现金工程未默认现金收款方式"
            settlement_amount = field_by_label(
                receipt_dialog, ttk, "完工金额（元）*"
            )
            receipt_amount = field_by_label(
                receipt_dialog, ttk, "回款金额（元）*"
            )
            settlement_amount.insert(0, "800.00")
            receipt_amount.insert(0, "500.00")
            save_button = next(
                widget for widget in descendants(receipt_dialog)
                if isinstance(widget, ttk.Button)
                and widget.cget("text") == "保存回款"
            )
            save_button.invoke()
            root.update_idletasks()
            settlements = contract_service.list_settlements(
                project_id=no_settlement_project_id
            )
            receipts = finance_service.list_receipts(no_settlement_project_id)
            assert len(settlements) == 1, "界面保存未同步建立完工金额确认"
            assert settlements[0]["amount_minor"] == 80_000
            assert len(receipts) == 1, "界面保存未建立现金回款"
            assert receipts[0]["allocated_amount_minor"] == 50_000
            assert receipts[0]["settlement_id"] == settlements[0]["id"]

            receipt_id = receipts[0]["id"]
            finance_page.receipt_tree.tree.selection_set(str(receipt_id))
            finance_page.edit_receipt()
            edit_dialog = root.winfo_children()[-1]
            assert edit_dialog.title() == "修改回款"
            edit_source_combo = find_source_combo(edit_dialog, ttk)
            assert str(edit_source_combo.cget("state")) == "disabled"
            edit_amount = field_by_label(
                edit_dialog, ttk, "回款金额（元）*"
            )
            edit_amount.delete(0, "end")
            edit_amount.insert(0, "450.00")
            save_edit_button = next(
                widget for widget in descendants(edit_dialog)
                if isinstance(widget, ttk.Button)
                and widget.cget("text") == "保存修改"
            )
            save_edit_button.invoke()
            root.update_idletasks()
            updated_receipt = finance_service.get_receipt(receipt_id)
            assert updated_receipt["allocated_amount_minor"] == 45_000
            assert updated_receipt["settlement_id"] == settlements[0]["id"]
            finance_host.destroy()
        finally:
            root.destroy()

        assert not unexpected, f"现金工程界面出现异常弹窗: {unexpected}"

    print(
        "Cash project UI smoke test passed: income confirmation, receipt, "
        "invoice queues and receipt editing"
    )


if __name__ == "__main__":
    main()
