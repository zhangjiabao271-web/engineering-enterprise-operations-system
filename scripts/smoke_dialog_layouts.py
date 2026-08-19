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


def verify_dialog(
    root,
    dialog,
    expected_primary=None,
    scrollable=True,
    required_labels=(),
    required_buttons=(),
    forbidden_buttons=(),
    required_choices=(),
    require_vertical_scrollbar=False,
    required_date_pickers=0,
):
    import ttkbootstrap as ttk
    from ttkbootstrap.widgets.scrolled import ScrolledFrame
    from ui.components import DatePicker

    root.update_idletasks()
    assert dialog.resizable() == (1, 1), f"{dialog.title()} is not resizable"
    children = list(descendants(dialog))
    visible_labels = {
        child.cget("text")
        for child in children
        if isinstance(child, ttk.Label)
    }
    for label in required_labels:
        assert label in visible_labels, f"{dialog.title()} has no {label} field"
    button_labels = [
        child.cget("text")
        for child in children
        if isinstance(child, ttk.Button)
    ]
    for button in required_buttons:
        assert button in button_labels, f"{dialog.title()} has no {button} action"
    for button in forbidden_buttons:
        assert button not in button_labels, (
            f"{dialog.title()} unexpectedly contains {button}"
        )
    choice_labels = {
        child.cget("text")
        for child in children
        if isinstance(child, (ttk.Checkbutton, ttk.Radiobutton))
    }
    for choice in required_choices:
        assert choice in choice_labels, (
            f"{dialog.title()} has no {choice} choice"
        )
    if len(required_choices) > 1:
        choice_widgets = {
            child.cget("text"): child
            for child in children
            if isinstance(child, (ttk.Checkbutton, ttk.Radiobutton))
        }
        first, second = (choice_widgets[label] for label in required_choices[:2])
        first.invoke()
        second.invoke()
        assert second.instate(["selected"]), (
            f"{dialog.title()} did not select {required_choices[1]}"
        )
        assert first.instate(["!selected"]), (
            f"{dialog.title()} work-day choices are not mutually exclusive"
        )
    if require_vertical_scrollbar:
        assert any(
            isinstance(child, ttk.Scrollbar)
            and str(child.cget("orient")) == "vertical"
            for child in children
        ), f"{dialog.title()} has no vertical scrollbar"
    date_picker_count = sum(
        isinstance(child, DatePicker) for child in children
    )
    assert date_picker_count >= required_date_pickers, (
        f"{dialog.title()} has {date_picker_count} calendar fields; "
        f"expected at least {required_date_pickers}"
    )
    if scrollable:
        assert any(isinstance(child, ScrolledFrame) for child in children), (
            f"{dialog.title()} has no scrollable form body"
        )
        assert "取消" in button_labels, (
            f"{dialog.title()} has no fixed cancel action"
        )
        if expected_primary:
            assert expected_primary in button_labels, (
                f"{dialog.title()} has no {expected_primary} action"
            )
    dialog.destroy()
    root.update_idletasks()


def page_host(root, page_class):
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import BOTH

    host = ttk.Frame(root, padding=24)
    host.pack(fill=BOTH, expand=True)
    return host, page_class(host)


def main():
    parser = argparse.ArgumentParser(description="Smoke-test all modal layouts")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="dialog_layouts_") as temp_dir:
        test_database = Path(temp_dir) / "supplier_data.db"
        shutil.copy2(args.database, test_database)
        os.environ["SUPPLY_CHAIN_DB_PATH"] = str(test_database)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

        import database as db
        from pages import (
            AIAssistantPage,
            ContractManagementPage,
            ConstructionRecordPage,
            CostLedgerPage,
            CustomerPage,
            ProjectProfitPage,
            ProjectManagementPage,
            PurchasePage,
            ReceivablePage,
            SupplierPage,
            WorkdayDashboardPage,
        )
        from services import contract_service, project_service
        import ttkbootstrap as ttk
        from tkinter import messagebox
        from ui.attachments import open_attachment_manager
        from ui.theme import configure_design_system

        unexpected_dialogs = []

        def capture_dialog(kind):
            def handler(title, message, **_kwargs):
                unexpected_dialogs.append((kind, str(title), str(message)))
                return True if kind == "question" else "ok"

            return handler

        messagebox.showwarning = capture_dialog("warning")
        messagebox.showerror = capture_dialog("error")
        messagebox.showinfo = capture_dialog("info")
        messagebox.askyesno = capture_dialog("question")

        db.init_db()
        checked = []
        root = ttk.Window(themename="flatly")
        root.withdraw()
        configure_design_system(root)

        host, page = page_host(root, SupplierPage)
        page.open_partner_dialog()
        verify_dialog(
            root,
            root.winfo_children()[-1],
            "保存供应商档案",
            required_labels=(
                "供应商名称 *", "主要联系人", "供应商资料",
            ),
        )
        checked.append("新增供应商档案")
        host.destroy()

        host, page = page_host(root, CustomerPage)
        page.open_partner_dialog()
        verify_dialog(
            root,
            root.winfo_children()[-1],
            "保存客户档案",
            required_labels=(
                "客户名称 *", "主体类型", "主要联系人", "客户资料",
            ),
        )
        checked.append("新增客户档案")
        host.destroy()

        host, page = page_host(root, ProjectManagementPage)
        project = project_service.list_projects()[0]
        page.open_project_dialog(project["id"])
        verify_dialog(
            root, root.winfo_children()[-1], "保存项目",
            required_labels=("业务模式 *", "开票要求 *", "客户主体"),
            required_date_pickers=2,
        )
        checked.append("编辑项目")
        project_with_site = next(
            row for row in project_service.list_projects()
            if project_service.list_project_sites(row["id"], include_inactive=True)
        )
        page.selected_project_id = project_with_site["id"]
        site = project_service.list_project_sites(
            project_with_site["id"], include_inactive=True
        )[0]
        page.open_site_dialog(site["id"])
        verify_dialog(root, root.winfo_children()[-1], "保存地点")
        checked.append("编辑施工地点")
        host.destroy()

        host, page = page_host(root, ProjectProfitPage)
        root.update_idletasks()
        host.destroy()

        contract_id = contract_service.create_contract(
            {
                "contract_no": "TEST-DIALOG-CONTRACT",
                "name": "弹窗布局测试合同",
                "contract_type": "project",
                "sign_date": "2026-07-30",
                "amount": "20000.00",
                "status": "active",
            }
        )
        allocation_id = contract_service.create_allocation(
            {
                "contract_id": contract_id,
                "project_id": project["id"],
                "amount": "10000.00",
            }
        )
        contract_service.create_settlement(
            {
                "settlement_no": "TEST-DIALOG-SETTLEMENT",
                "contract_id": contract_id,
                "project_id": project["id"],
                "settlement_date": "2026-07-30",
                "amount": "8000.00",
            }
        )

        host, page = page_host(root, ContractManagementPage)
        page.open_contract_dialog()
        verify_dialog(
            root, root.winfo_children()[-1], "保存合同",
            required_date_pickers=3,
        )
        checked.append("新增合同")
        page.open_allocation_dialog()
        verify_dialog(root, root.winfo_children()[-1], "确认分配")
        checked.append("合同分配")
        page.open_settlement_dialog()
        verify_dialog(
            root, root.winfo_children()[-1], "确认收入",
            required_labels=("业务来源 *", "零星工程项目 *", "确认依据"),
            required_date_pickers=3,
        )
        checked.append("登记收入确认")
        attachment_dialog = open_attachment_manager(
            root, "contract", contract_id, "测试合同"
        )
        verify_dialog(
            root,
            attachment_dialog,
            scrollable=False,
            required_buttons=("添加文件", "打开文件", "作废附件", "关闭"),
            require_vertical_scrollbar=True,
        )
        checked.append("合同附件管理")
        host.destroy()

        host, page = page_host(root, ReceivablePage)
        page.open_invoice_dialog()
        verify_dialog(
            root,
            root.winfo_children()[-1],
            "保存发票",
            required_labels=("收入确认 *",),
            required_date_pickers=1,
        )
        checked.append("登记销项发票")
        invoice_id = int(page.invoice_tree.tree.get_children()[0])
        page.open_invoice_dialog(invoice_id)
        verify_dialog(
            root,
            root.winfo_children()[-1],
            "保存修改",
            required_labels=("收入确认 *",),
            required_date_pickers=1,
        )
        checked.append("修改销项发票")
        page.open_receipt_dialog()
        verify_dialog(
            root, root.winfo_children()[-1], "保存回款",
            required_labels=("回款来源 *", "完工金额确认 *"),
            required_date_pickers=1,
        )
        checked.append("登记回款")
        host.destroy()

        host, page = page_host(root, CostLedgerPage)
        page.open_cost_dialog()
        cost_dialog = root.winfo_children()[-1]
        category_combo = next(
            widget for widget in descendants(cost_dialog)
            if isinstance(widget, ttk.Combobox)
            and tuple(widget.cget("values"))
            == ("用车", "饮食", "房租", "水电煤", "机械费")
        )
        assert category_combo.get() == "用车"
        assert cost_dialog.title() == "登记成本"
        verify_dialog(
            root,
            cost_dialog,
            "保存成本",
            required_labels=(
                "费用信息",
                "项目归集",
                "补充说明",
                "归集方式 *",
                "商家 / 收款方",
                "车辆 / 车牌（用车可填）",
            ),
            forbidden_buttons=("刷新预览",),
            required_date_pickers=1,
        )
        checked.append("登记其他成本")
        host.destroy()

        host, page = page_host(root, WorkdayDashboardPage)
        worker = db.get_workers()[0]
        page.open_worker_dialog(worker["id"])
        verify_dialog(root, root.winfo_children()[-1], "保存工人")
        checked.append("修改工人")
        page.worker_tree.selection_set(page.worker_tree.get_children()[0])
        page.open_rate_adjustment_dialog()
        verify_dialog(
            root,
            root.winfo_children()[-1],
            "确认调薪",
            required_labels=(
                "调薪设置", "新日工资 *", "生效日期 *", "影响范围 *",
                "截止日期", "限定项目", "调薪原因 *", "调整预览",
                "最近调薪记录",
            ),
            required_buttons=("预览影响", "确认调薪"),
            required_date_pickers=2,
        )
        checked.append("工资调整预览")
        work_log = db.get_work_logs()[0]
        page.open_log_dialog(work_log["id"])
        verify_dialog(
            root,
            root.winfo_children()[-1],
            "保存记录",
            required_labels=("加班标记",),
            required_choices=("0.5 工天", "1 工天"),
            required_date_pickers=1,
        )
        checked.append("修改工天记录")
        page.open_batch_log_dialog()
        verify_dialog(
            root,
            root.winfo_children()[-1],
            "批量保存",
            required_labels=("加班标记",),
            required_choices=("0.5 工天", "1 工天"),
            required_date_pickers=1,
        )
        checked.append("批量新增工天")
        host.destroy()

        host, page = page_host(root, ConstructionRecordPage)
        record = db.get_construction_records()[0]
        page.open_record_dialog(record["id"])
        verify_dialog(
            root, root.winfo_children()[-1], "保存施工记录",
            required_date_pickers=2,
        )
        checked.append("修改施工记录")
        page.tree.selection_set(page.tree.get_children()[0])
        page.inspect_selected()
        verify_dialog(
            root, root.winfo_children()[-1], "保存验收结果",
            required_date_pickers=1,
        )
        checked.append("工程量验收")
        page.open_photo_manager(record["id"])
        verify_dialog(root, root.winfo_children()[-1], scrollable=False)
        checked.append("现场照片")
        host.destroy()

        host, page = page_host(root, PurchasePage)
        page.open_project_dialog()
        verify_dialog(root, root.winfo_children()[-1], "保存项目")
        checked.append("采购中心新增项目")
        page.open_purchase_dialog("正式采购")
        purchase_dialog = root.winfo_children()[-1]
        project_values = next(
            tuple(child.cget("values"))
            for child in descendants(purchase_dialog)
            if isinstance(child, ttk.Combobox)
            and "待归集（稍后分配）" in tuple(child.cget("values"))
        )
        assert any("桦岭拆盖旧工厂" in value for value in project_values)
        assert not any("澄湖环保站" in value for value in project_values)
        purchase_combos = [
            child
            for child in descendants(purchase_dialog)
            if isinstance(child, ttk.Combobox)
        ]
        category_combo = next(
            child
            for child in purchase_combos
            if tuple(child.cget("values"))
            == ("材料费", "工具和设备", "其他")
        )
        attribution_combo = next(
            child
            for child in purchase_combos
            if tuple(child.cget("values"))
            == ("单项目归集", "多项目平均分摊")
        )
        category_combo.set("工具和设备")
        category_combo.event_generate("<<ComboboxSelected>>")
        attribution_combo.set("多项目平均分摊")
        attribution_combo.event_generate("<<ComboboxSelected>>")
        root.update_idletasks()
        project_choices = [
            child
            for child in descendants(purchase_dialog)
            if isinstance(child, ttk.Checkbutton)
            and " · " in child.cget("text")
        ]
        select_all_projects = next(
            child
            for child in descendants(purchase_dialog)
            if isinstance(child, ttk.Checkbutton)
            and child.cget("text") == "全选项目"
        )
        assert len(project_choices) >= 2
        assert project_choices[0].master.winfo_manager() == "grid"
        select_all_projects.invoke()
        assert all(choice.instate(["selected"]) for choice in project_choices)
        project_choices[0].invoke()
        assert select_all_projects.instate(["!selected"])
        project_choices[0].invoke()
        assert select_all_projects.instate(["selected"])
        checked.append("工具设备多项目平均分摊选择")
        verify_dialog(
            root,
            purchase_dialog,
            "保存并继续录入",
            required_date_pickers=1,
        )
        checked.append("新增采购包含已完工项目")
        for purchase_type in ("正式采购", "零星采购"):
            order = db.get_purchase_orders(purchase_type=purchase_type)[0]
            page.open_purchase_dialog(purchase_type, order["id"])
            verify_dialog(
                root,
                root.winfo_children()[-1],
                "保存修改",
                required_labels=(
                    "材料单价（未税，元）*",
                    "税率（%）*",
                    "含税单价（元）",
                    "未税材料额（元）",
                    "税额（元）",
                    "运费（元）",
                    "计入项目成本（元）",
                ),
                required_date_pickers=1,
            )
            checked.append(f"修改{purchase_type}")
        tree = page.formal_tree
        tree.selection_set(tree.get_children()[0])
        page.open_status_dialog(tree)
        verify_dialog(root, root.winfo_children()[-1], "确认更新")
        checked.append("更新支付与票据状态")
        page.unassigned_tree.insert("", "end", iid="999", values=(999,))
        page.unassigned_tree.selection_set(page.unassigned_tree.get_children()[-1])
        page.assign_selected()
        verify_dialog(root, root.winfo_children()[-1], "确认归集")
        checked.append("归集到项目")
        host.destroy()

        host, page = page_host(root, AIAssistantPage)
        page.open_config_dialog()
        verify_dialog(root, root.winfo_children()[-1], "保存配置")
        checked.append("AI 设置")
        host.destroy()
        assert not unexpected_dialogs, (
            f"unexpected dialogs during layout smoke test: {unexpected_dialogs}"
        )
        root.destroy()

    print(f"Dialog layout smoke test passed: {len(checked)} dialogs")


if __name__ == "__main__":
    main()
