from datetime import datetime
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import (
    contract_service,
    master_data_service,
    project_service,
)
from ui.components import BottomToolbar, DataTable, DatePicker, PageHeader
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders
from ui.attachments import open_attachment_manager


class ContractManagementPage:
    """Contracts, project allocations and settlement confirmations."""

    def __init__(self, parent):
        self.parent = parent
        self.build_ui()
        safe_init_loaders("合同与结算", [self.refresh])

    @staticmethod
    def money(value):
        amount = int(value or 0) / 100
        return f"{'-' if amount < 0 else ''}¥{abs(amount):,.2f}"

    @staticmethod
    def percent(value):
        return f"{float(value or 0):.1f}%"

    @staticmethod
    def selected_id(tree):
        tree = getattr(tree, "tree", tree)  # 兼容 DataTable 组件与裸 Treeview
        selected = tree.selection()
        return int(selected[0]) if len(selected) == 1 else None

    def build_ui(self):
        PageHeader(
            self.parent,
            "合同与结算",
            "年度合同可以分配到多个项目，但每个项目仍独立核算",
            actions=[
                ttk.Button(
                    self.parent, text="新增合同", bootstyle="primary",
                    command=self.open_contract_dialog,
                ),
                ttk.Button(
                    self.parent, text="分配到项目", bootstyle="primary-outline",
                    command=self.open_allocation_dialog,
                ),
                ttk.Button(
                    self.parent, text="登记收入确认", bootstyle="success-outline",
                    command=self.open_settlement_dialog,
                ),
            ],
        )

        self.notebook = ttk.Notebook(self.parent)
        self.notebook.pack(fill=BOTH, expand=True)
        contract_tab = ttk.Frame(self.notebook, padding=(0, 10, 0, 0))
        allocation_tab = ttk.Frame(self.notebook, padding=(0, 10, 0, 0))
        settlement_tab = ttk.Frame(self.notebook, padding=(0, 10, 0, 0))
        self.notebook.add(contract_tab, text="合同台账")
        self.notebook.add(allocation_tab, text="项目分配")
        self.notebook.add(settlement_tab, text="收入确认")

        self.contract_tree = self._table(
            contract_tab,
            (
                ("contract_no", "合同编号", 145, W),
                ("name", "合同名称", 220, W),
                ("customer", "客户", 145, W),
                ("type", "类型", 105, CENTER),
                ("amount", "合同金额", 115, E),
                ("allocated", "已分配", 115, E),
                ("remaining", "待分配", 115, E),
                ("status", "状态", 80, CENTER),
            ),
            empty_text="暂无合同，点击右上角「新增合同」",
            stretch=("contract_no", "name", "customer"),
        )
        BottomToolbar(
            contract_tab,
            ttk.Button(
                contract_tab, text="编辑合同", bootstyle="secondary-outline",
                command=self.edit_contract,
            ),
            ttk.Button(
                contract_tab, text="作废合同", bootstyle="danger-outline",
                command=self.void_contract,
            ),
            ttk.Button(
                contract_tab, text="合同附件", bootstyle="secondary-outline",
                command=self.open_contract_attachments,
            ),
        )

        self.allocation_tree = self._table(
            allocation_tab,
            (
                ("contract", "合同", 230, W),
                ("project", "独立核算项目", 210, W),
                ("amount", "分配金额", 130, E),
                ("notes", "说明", 280, W),
            ),
            empty_text="暂无项目分配，点击右上角「分配到项目」",
            stretch=("contract", "project", "notes"),
        )
        BottomToolbar(
            allocation_tab,
            ttk.Button(
                allocation_tab, text="新增分配", bootstyle="primary-outline",
                command=self.open_allocation_dialog,
            ),
            ttk.Button(
                allocation_tab, text="作废分配", bootstyle="danger-outline",
                command=self.void_allocation,
            ),
        )

        self.settlement_tree = self._table(
            settlement_tab,
            (
                ("type", "确认类型", 90, CENTER),
                ("no", "确认编号", 120, W),
                ("date", "确认日期", 88, CENTER),
                ("project", "项目", 140, W),
                ("contract", "合同", 110, W),
                ("amount", "确认金额", 105, E),
                ("invoice", "开票进度", 140, E),
                ("receipt", "回款进度", 140, E),
                ("unreceived", "未回款", 110, E),
                ("status", "回款状态", 82, CENTER),
            ),
            empty_text="暂无收入确认，点击右上角「登记收入确认」",
            stretch=("no", "project", "contract"),
        )
        BottomToolbar(
            settlement_tab,
            ttk.Button(
                settlement_tab, text="新增收入确认", bootstyle="success-outline",
                command=self.open_settlement_dialog,
            ),
            ttk.Button(
                settlement_tab, text="修改收入确认", bootstyle="primary-outline",
                command=self.edit_settlement,
            ),
            ttk.Button(
                settlement_tab, text="作废收入确认", bootstyle="danger-outline",
                command=self.void_settlement,
            ),
            ttk.Button(
                settlement_tab, text="确认附件", bootstyle="secondary-outline",
                command=self.open_settlement_attachments,
            ),
        )
        self.settlement_tree.tree.bind(
            "<Double-1>", lambda _event: self.edit_settlement()
        )

    @staticmethod
    def _table(parent, specs, *, empty_text="暂无数据", stretch=None):
        return DataTable(parent, specs=specs, empty_text=empty_text, stretch=stretch)

    def refresh(self):
        self.contract_tree.refresh(
            contract_service.list_contracts(),
            lambda row: (str(row["id"]), (
                row["contract_no"],
                row["name"],
                row["customer_name"],
                contract_service.CONTRACT_TYPES[row["contract_type"]],
                self.money(row["tax_inclusive_amount_minor"]),
                self.money(row["allocated_minor"]),
                self.money(row["remaining_minor"]),
                contract_service.CONTRACT_STATUSES[row["status"]],
            )),
        )

        self.allocation_tree.refresh(
            contract_service.list_allocations(),
            lambda row: (str(row["id"]), (
                f"{row['contract_no']} · {row['contract_name']}",
                f"{row['project_name']} · {row['project_code']}",
                self.money(row["allocated_amount_minor"]),
                row["notes"] or "",
            )),
        )

        def settlement_mapper(row):
            return str(row["id"]), (
                "完工金额确认" if row["source_type"] == "cash_job" else "合同结算",
                row["settlement_no"],
                row["settlement_date"],
                row["project_name"],
                row["contract_no"] or "无需合同",
                self.money(row["amount_minor"]),
                (
                    "无需开票"
                    if row["invoice_policy"] == "not_required"
                    else
                    f"{self.money(row['invoiced_minor'])} · "
                    f"{self.percent(row['invoice_rate_percent'])}"
                ),
                f"{self.money(row['received_minor'])} · "
                f"{self.percent(row['receipt_rate_percent'])}",
                self.money(row["unreceived_minor"]),
                row["collection_status"],
            )

        self.settlement_tree.refresh(
            contract_service.list_settlements(), settlement_mapper
        )

    def open_contract_dialog(self, contract_id=None):
        data = contract_service.get_contract(contract_id) if contract_id else {}
        customers = master_data_service.list_customers(active_only=True)
        if data.get("customer_partner_id") and not any(
            row["id"] == data["customer_partner_id"] for row in customers
        ):
            historical = next(
                (
                    row for row in master_data_service.list_customers()
                    if row["id"] == data["customer_partner_id"]
                ),
                None,
            )
            if historical:
                customers.append(historical)
        customer_map = {
            f"{row['name']} · {row['partner_code']}": row["id"]
            for row in customers
        }
        reverse_type = {
            value: key for key, value in contract_service.CONTRACT_TYPES.items()
        }
        reverse_status = {
            value: key
            for key, value in contract_service.CONTRACT_STATUSES.items()
            if key != "void"
        }
        parent_map = {"无上级合同": None}
        parent_map.update(
            {
                f"{row['contract_no']} · {row['name']}": row["id"]
                for row in contract_service.list_contracts()
                if row["id"] != contract_id
            }
        )
        selected_parent = next(
            (
                label for label, value in parent_map.items()
                if value == data.get("parent_contract_id")
            ),
            "无上级合同",
        )
        selected_customer = next(
            (
                label for label, value in customer_map.items()
                if value == data.get("customer_partner_id")
            ),
            data.get("customer_name", ""),
        )
        dialog = ttk.Toplevel(self.parent)
        dialog.title("编辑合同" if contract_id else "新增合同")
        body, footer = build_form_dialog(
            dialog, self.parent, 720, 650, min_width=600, min_height=480
        )
        variables = {
            "contract_no": ttk.StringVar(value=data.get("contract_no", "")),
            "name": ttk.StringVar(value=data.get("name", "")),
            "customer": ttk.StringVar(value=selected_customer),
            "type": ttk.StringVar(
                value=contract_service.CONTRACT_TYPES.get(
                    data.get("contract_type", "annual")
                )
            ),
            "parent": ttk.StringVar(value=selected_parent),
            "sign_date": ttk.StringVar(
                value=data.get("sign_date") or datetime.now().strftime("%Y-%m-%d")
            ),
            "start_date": ttk.StringVar(value=data.get("start_date") or ""),
            "end_date": ttk.StringVar(value=data.get("end_date") or ""),
            "amount": ttk.StringVar(
                value=(
                    f"{data.get('tax_inclusive_amount_minor', 0) / 100:.2f}"
                    if contract_id else ""
                )
            ),
            "status": ttk.StringVar(
                value=contract_service.CONTRACT_STATUSES.get(
                    data.get("status", "active")
                )
            ),
        }
        ttk.Label(
            body, text="合同金额是收入边界，不直接等于已结算收入。",
            style="PageSub.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky=W, pady=(0, 12))
        specs = (
            ("合同编号", "contract_no"),
            ("合同名称 *", "name"),
            ("客户", "customer"),
            ("合同类型 *", "type"),
            ("上级合同 / 原合同", "parent"),
            ("签订日期 *", "sign_date"),
            ("开始日期", "start_date"),
            ("结束日期", "end_date"),
            ("含税合同金额（元）*", "amount"),
            ("状态 *", "status"),
        )
        for row, (label, key) in enumerate(specs, 1):
            ttk.Label(body, text=label).grid(
                row=row, column=0, sticky=E, padx=(0, 12), pady=7
            )
            if key == "customer":
                widget = ttk.Combobox(
                    body, textvariable=variables[key],
                    values=list(customer_map), state="normal"
                )
            elif key == "type":
                widget = ttk.Combobox(
                    body, textvariable=variables[key],
                    values=list(reverse_type), state="readonly"
                )
            elif key == "parent":
                widget = ttk.Combobox(
                    body, textvariable=variables[key],
                    values=list(parent_map), state="readonly"
                )
            elif key == "status":
                widget = ttk.Combobox(
                    body, textvariable=variables[key],
                    values=list(reverse_status), state="readonly"
                )
            elif key in ("sign_date", "start_date", "end_date"):
                widget = DatePicker(
                    body,
                    textvariable=variables[key],
                    allow_empty=key != "sign_date",
                    popup_title=f"选择{label.rstrip(' *')}",
                )
            else:
                widget = ttk.Entry(body, textvariable=variables[key])
            grid_options = {"row": row, "column": 1, "sticky": EW, "pady": 7}
            if not isinstance(widget, DatePicker):
                grid_options["ipady"] = 4
            widget.grid(**grid_options)
        ttk.Label(body, text="备注").grid(
            row=11, column=0, sticky=NE, padx=(0, 12), pady=7
        )
        notes = ttk.Text(body, height=5, wrap="word")
        notes.grid(row=11, column=1, sticky=EW, pady=7)
        notes.insert("1.0", data.get("notes") or "")
        body.columnconfigure(1, weight=1)

        def save():
            customer_label = variables["customer"].get().strip()
            payload = {
                "contract_no": variables["contract_no"].get().strip(),
                "name": variables["name"].get().strip(),
                "customer_partner_id": customer_map.get(customer_label),
                "customer_name": customer_label.split(" · ")[0],
                "contract_type": reverse_type[variables["type"].get()],
                "parent_contract_id": parent_map[variables["parent"].get()],
                "sign_date": variables["sign_date"].get().strip(),
                "start_date": variables["start_date"].get().strip(),
                "end_date": variables["end_date"].get().strip(),
                "amount": variables["amount"].get().strip(),
                "status": reverse_status[variables["status"].get()],
                "notes": notes.get("1.0", END).strip(),
            }
            try:
                if contract_id:
                    contract_service.update_contract(contract_id, payload)
                else:
                    contract_service.create_contract(payload)
            except Exception as error:
                messagebox.showwarning("无法保存", str(error), parent=dialog)
                return
            dialog.destroy()
            self.refresh()

        add_form_actions(
            footer, cancel_command=dialog.destroy,
            primary_text="保存合同", primary_command=save,
        )

    def edit_contract(self):
        contract_id = self.selected_id(self.contract_tree)
        if not contract_id:
            messagebox.showwarning("提示", "请先选择一个合同")
            return
        self.open_contract_dialog(contract_id)

    def open_allocation_dialog(self):
        contracts = [
            row for row in contract_service.list_contracts()
            if row["status"] in ("draft", "active") and row["remaining_minor"] > 0
        ]
        projects = project_service.list_projects()
        if not contracts or not projects:
            messagebox.showwarning("提示", "请先建立可分配合同和项目")
            return
        contract_map = {
            f"{row['contract_no']} · 可分配 {self.money(row['remaining_minor'])}": row["id"]
            for row in contracts
        }
        project_map = {
            f"{row['name']} · {row['project_code']}": row["id"]
            for row in projects
        }
        dialog = ttk.Toplevel(self.parent)
        dialog.title("合同分配到项目")
        body, footer = build_form_dialog(
            dialog, self.parent, 680, 470, min_width=560, min_height=400
        )
        variables = {
            "contract": ttk.StringVar(value=next(iter(contract_map))),
            "project": ttk.StringVar(value=next(iter(project_map))),
            "amount": ttk.StringVar(),
        }
        for row, (label, key, values) in enumerate(
            (
                ("合同 *", "contract", list(contract_map)),
                ("独立核算项目 *", "project", list(project_map)),
                ("分配金额（元）*", "amount", None),
            )
        ):
            ttk.Label(body, text=label).grid(
                row=row, column=0, sticky=E, padx=(0, 12), pady=8
            )
            widget = (
                ttk.Combobox(
                    body, textvariable=variables[key],
                    values=values, state="readonly"
                )
                if values else ttk.Entry(body, textvariable=variables[key])
            )
            widget.grid(row=row, column=1, sticky=EW, pady=8, ipady=5)
        ttk.Label(body, text="分配说明").grid(
            row=3, column=0, sticky=NE, padx=(0, 12), pady=8
        )
        notes = ttk.Text(body, height=5, wrap="word")
        notes.grid(row=3, column=1, sticky=EW, pady=8)
        body.columnconfigure(1, weight=1)

        def save():
            try:
                contract_service.create_allocation(
                    {
                        "contract_id": contract_map[variables["contract"].get()],
                        "project_id": project_map[variables["project"].get()],
                        "amount": variables["amount"].get(),
                        "notes": notes.get("1.0", END).strip(),
                    }
                )
            except Exception as error:
                messagebox.showwarning("无法分配", str(error), parent=dialog)
                return
            dialog.destroy()
            self.refresh()

        add_form_actions(
            footer, cancel_command=dialog.destroy,
            primary_text="确认分配", primary_command=save,
        )

    def open_settlement_dialog(self, settlement_id=None):
        allocations = contract_service.list_allocations()
        allocation_map = {
            f"{row['contract_no']} → {row['project_name']}": row
            for row in allocations
        }
        editing = settlement_id is not None
        current = (
            contract_service.get_settlement(settlement_id) if editing else {}
        ) or {}
        cash_projects = [
            row for row in project_service.list_projects(active_only=not editing)
            if row["business_mode"] == "cash"
        ]
        cash_project_map = {
            f"{row['project_code']} · {row['name']}": row for row in cash_projects
        }
        if not allocation_map and not cash_project_map:
            messagebox.showwarning(
                "提示", "请先建立合同项目分配，或建立零星现金工程项目"
            )
            return
        current_is_cash = current.get("source_type") == "cash_job"
        source_labels = ("正式合同工程", "零星现金工程")
        dialog = ttk.Toplevel(self.parent)
        dialog.title("修改收入确认" if editing else "登记收入确认")
        body, footer = build_form_dialog(
            dialog, self.parent, 710, 620, min_width=590, min_height=450
        )
        current_allocation = ""
        for label, row in allocation_map.items():
            if (
                row["contract_id"] == current.get("contract_id")
                and row["project_id"] == current.get("project_id")
            ):
                current_allocation = label
                break
        current_cash_project = next(
            (
                label for label, row in cash_project_map.items()
                if row["id"] == current.get("project_id")
            ),
            "",
        )
        variables = {
            "source": ttk.StringVar(
                value="零星现金工程" if current_is_cash else "正式合同工程"
            ),
            "allocation": ttk.StringVar(
                value=current_allocation or next(iter(allocation_map), "")
            ),
            "cash_project": ttk.StringVar(
                value=current_cash_project or next(iter(cash_project_map), "")
            ),
            "no": ttk.StringVar(value=current.get("settlement_no", "")),
            "date": ttk.StringVar(
                value=current.get("settlement_date")
                or datetime.now().strftime("%Y-%m-%d")
            ),
            "start": ttk.StringVar(value=current.get("period_start") or ""),
            "end": ttk.StringVar(value=current.get("period_end") or ""),
            "amount": ttk.StringVar(
                value=f"{int(current.get('amount_minor') or 0) / 100:.2f}"
                if editing
                else ""
            ),
        }
        specs = (
            ("业务来源 *", "source"),
            ("合同与项目 *", "allocation"),
            ("零星工程项目 *", "cash_project"),
            ("确认编号", "no"),
            ("确认日期 *", "date"),
            ("施工开始", "start"),
            ("施工结束", "end"),
            ("确认金额（元）*", "amount"),
        )
        field_rows = {}
        for row, (label, key) in enumerate(specs):
            label_widget = ttk.Label(body, text=label)
            label_widget.grid(
                row=row, column=0, sticky=E, padx=(0, 12), pady=7
            )
            if key in ("source", "allocation", "cash_project"):
                values = (
                    source_labels if key == "source"
                    else list(allocation_map) if key == "allocation"
                    else list(cash_project_map)
                )
                widget = ttk.Combobox(
                    body, textvariable=variables[key],
                    values=values, state="readonly"
                )
            elif key in ("date", "start", "end"):
                widget = DatePicker(
                    body,
                    textvariable=variables[key],
                    allow_empty=key in ("start", "end"),
                    popup_title=f"选择{label.rstrip(' *')}",
                )
            else:
                widget = ttk.Entry(body, textvariable=variables[key])
            grid_options = {"row": row, "column": 1, "sticky": EW, "pady": 7}
            if not isinstance(widget, DatePicker):
                grid_options["ipady"] = 4
            widget.grid(**grid_options)
            field_rows[key] = (label_widget, widget)
        if editing:
            field_rows["source"][1].configure(state="disabled")
            target_key = "cash_project" if current_is_cash else "allocation"
            field_rows[target_key][1].configure(state="disabled")

        def sync_source(_event=None):
            is_cash = variables["source"].get() == "零星现金工程"
            visible_key = "cash_project" if is_cash else "allocation"
            hidden_key = "allocation" if is_cash else "cash_project"
            for widget in field_rows[visible_key]:
                widget.grid()
            for widget in field_rows[hidden_key]:
                widget.grid_remove()

        field_rows["source"][1].bind("<<ComboboxSelected>>", sync_source)
        sync_source()
        ttk.Label(body, text="确认依据").grid(
            row=8, column=0, sticky=NE, padx=(0, 12), pady=7
        )
        basis = ttk.Text(body, height=5, wrap="word")
        basis.grid(row=8, column=1, sticky=EW, pady=7)
        if editing and current.get("basis"):
            basis.insert("1.0", current["basis"])
        body.columnconfigure(1, weight=1)

        def save():
            is_cash = variables["source"].get() == "零星现金工程"
            if is_cash:
                selected = cash_project_map.get(variables["cash_project"].get())
                contract_id = None
            else:
                selected = allocation_map.get(variables["allocation"].get())
                contract_id = selected["contract_id"] if selected else None
            if not selected:
                messagebox.showwarning("提示", "请选择有效的项目来源", parent=dialog)
                return
            payload = {
                "settlement_no": variables["no"].get(),
                "contract_id": contract_id,
                "project_id": selected.get("project_id", selected.get("id")),
                "settlement_date": variables["date"].get(),
                "period_start": variables["start"].get(),
                "period_end": variables["end"].get(),
                "amount": variables["amount"].get(),
                "basis": basis.get("1.0", END).strip(),
            }
            try:
                if editing:
                    contract_service.update_settlement(settlement_id, payload)
                else:
                    contract_service.create_settlement(payload)
            except Exception as error:
                messagebox.showwarning("无法保存", str(error), parent=dialog)
                return
            dialog.destroy()
            self.refresh()

        add_form_actions(
            footer, cancel_command=dialog.destroy,
            primary_text="保存修改" if editing else "确认收入",
            primary_command=save,
        )

    def void_contract(self):
        contract_id = self.selected_id(self.contract_tree)
        if not contract_id:
            messagebox.showwarning("提示", "请先选择合同")
            return
        if not messagebox.askyesno("确认作废", "确定作废该合同吗？"):
            return
        try:
            contract_service.void_contracts([contract_id])
        except ValueError as error:
            messagebox.showwarning("无法作废", str(error))
            return
        self.refresh()

    def void_allocation(self):
        allocation_id = self.selected_id(self.allocation_tree)
        if not allocation_id:
            messagebox.showwarning("提示", "请先选择项目分配")
            return
        if not messagebox.askyesno("确认作废", "确定作废该项目分配吗？"):
            return
        try:
            contract_service.void_allocations([allocation_id])
        except ValueError as error:
            messagebox.showwarning("无法作废", str(error))
            return
        self.refresh()

    def edit_settlement(self):
        settlement_id = self.selected_id(self.settlement_tree)
        if not settlement_id:
            messagebox.showwarning("提示", "请先选择结算记录")
            return
        self.open_settlement_dialog(settlement_id)

    def void_settlement(self):
        settlement_id = self.selected_id(self.settlement_tree)
        if not settlement_id:
            messagebox.showwarning("提示", "请先选择结算记录")
            return
        if not messagebox.askyesno("确认作废", "确定作废该结算记录吗？"):
            return
        contract_service.void_settlements([settlement_id])
        self.refresh()

    def open_contract_attachments(self):
        contract_id = self.selected_id(self.contract_tree)
        if not contract_id:
            messagebox.showwarning("提示", "请先选择合同")
            return
        open_attachment_manager(
            self.parent, "contract", contract_id, "合同"
        )

    def open_settlement_attachments(self):
        settlement_id = self.selected_id(self.settlement_tree)
        if not settlement_id:
            messagebox.showwarning("提示", "请先选择结算记录")
            return
        open_attachment_manager(
            self.parent, "settlement", settlement_id, "结算"
        )
