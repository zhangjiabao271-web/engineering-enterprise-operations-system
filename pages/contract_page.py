from datetime import datetime
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import (
    contract_service,
    master_data_service,
    project_service,
)
from ui.components import BottomToolbar, DataTable, DatePicker, FilterBar, PageHeader
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders
from ui.attachments import open_attachment_manager


class ContractManagementPage:
    """Contracts, project allocations and settlement confirmations."""

    def __init__(self, parent, initial_project_id=None):
        self.parent = parent
        self.initial_project_id = initial_project_id
        self.project_var = ttk.StringVar(value="全部项目")
        self.project_map = {}
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
            "固定总价控制边界，年度框架可按实际工程量持续确认收入",
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

        self.project_combo = ttk.Combobox(
            self.parent, textvariable=self.project_var,
            state="readonly", width=32,
        )
        self.project_combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh())
        FilterBar(
            self.parent,
            ("项目", self.project_combo),
            ttk.Label(
                self.parent,
                text="合同金额是整份合同口径；本项目金额看项目分配和收入确认",
                style="Toolbar.TLabel",
            ),
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
                ("contract_no", "合同编号", 125, W),
                ("name", "合同名称", 175, W),
                ("customer", "客户", 125, W),
                ("type", "类型", 95, CENTER),
                ("pricing", "计价方式", 105, CENTER),
                ("amount", "约定金额", 105, E),
                ("limit", "控制上限", 105, E),
                ("settled", "已确认收入", 125, E),
                ("projects", "项目数", 65, CENTER),
                ("status", "状态", 72, CENTER),
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
                ("amount", "分配/关联", 130, E),
                ("notes", "说明", 280, W),
            ),
            empty_text="暂无项目关联，点击右上角「分配到项目」",
            stretch=("contract", "project", "notes"),
        )
        BottomToolbar(
            allocation_tab,
            ttk.Button(
                allocation_tab, text="新增关联/分配", bootstyle="primary-outline",
                command=self.open_allocation_dialog,
            ),
            ttk.Button(
                allocation_tab, text="调整金额", bootstyle="primary-outline",
                command=self.open_adjust_allocation_dialog,
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
                settlement_tab, text="结算附件", bootstyle="secondary-outline",
                command=self.open_settlement_attachments,
            ),
        )
        self.settlement_tree.tree.bind(
            "<Double-1>", lambda _event: self.edit_settlement()
        )

    @staticmethod
    def _table(parent, specs, *, empty_text="暂无数据", stretch=None):
        return DataTable(parent, specs=specs, empty_text=empty_text, stretch=stretch)

    def _refresh_project_options(self):
        current = self.project_var.get()
        self.project_map = {"全部项目": None}
        self.project_map.update({
            f"{row['name']} · {row['project_code']}": row["id"]
            for row in project_service.list_projects()
        })
        self.project_combo.configure(values=list(self.project_map))
        if self.initial_project_id is not None:
            current = next(
                (label for label, project_id in self.project_map.items()
                 if project_id == self.initial_project_id),
                "全部项目",
            )
            self.initial_project_id = None
        self.project_var.set(current if current in self.project_map else "全部项目")

    def selected_project_id(self):
        return self.project_map.get(self.project_var.get())

    def refresh(self):
        self._refresh_project_options()
        project_id = self.selected_project_id()
        allocations = contract_service.list_allocations(project_id=project_id)
        settlements = contract_service.list_settlements(project_id=project_id)
        contracts = contract_service.list_contracts(project_id=project_id)

        def contract_mapper(row):
            agreed_amount = (
                "据实结算"
                if row["pricing_mode"] == "actual"
                else self.money(row["tax_inclusive_amount_minor"])
            )
            control_limit = (
                self.money(row["control_limit_minor"])
                if row["control_limit_minor"] is not None
                else "—"
            )
            settled = self.money(row["settled_minor"])
            if row["pricing_warning"]:
                settled += " · 超额"
            return str(row["id"]), (
                row["contract_no"],
                row["name"],
                row["customer_name"],
                contract_service.CONTRACT_TYPES[row["contract_type"]],
                contract_service.PRICING_MODES[row["pricing_mode"]],
                agreed_amount,
                control_limit,
                settled,
                row["project_count"],
                contract_service.CONTRACT_STATUSES[row["status"]],
            )

        self.contract_tree.refresh(
            contracts,
            contract_mapper,
        )

        self.allocation_tree.refresh(
            allocations,
            lambda row: (str(row["id"]), (
                f"{row['contract_no']} · {row['contract_name']}",
                f"{row['project_name']} · {row['project_code']}",
                (
                    "据实关联"
                    if row["pricing_mode"] == "actual"
                    else self.money(row["allocated_amount_minor"])
                ),
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
            settlements, settlement_mapper
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
        reverse_pricing = {
            value: key for key, value in contract_service.PRICING_MODES.items()
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
        initial_contract_type = data.get("contract_type", "annual")
        initial_pricing_mode = data.get("pricing_mode") or (
            "actual" if initial_contract_type == "annual" else "fixed"
        )
        from services.invoice_income_service import INCOME_MODES
        reverse_income = {label: key for key, label in INCOME_MODES.items()}
        dialog = ttk.Toplevel(self.parent)
        dialog.title("编辑合同" if contract_id else "新增合同")
        body, footer = build_form_dialog(
            dialog, self.parent, 720, 650, min_width=600, min_height=480
        )
        variables = {
            "income_mode": ttk.StringVar(value=INCOME_MODES[data.get('income_mode', 'manual')]),
            "contract_no": ttk.StringVar(value=data.get("contract_no", "")),
            "name": ttk.StringVar(value=data.get("name", "")),
            "customer": ttk.StringVar(value=selected_customer),
            "type": ttk.StringVar(
                value=contract_service.CONTRACT_TYPES.get(
                    initial_contract_type
                )
            ),
            "pricing": ttk.StringVar(
                value=contract_service.PRICING_MODES[initial_pricing_mode]
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
                    if contract_id and initial_pricing_mode != "actual" else ""
                )
            ),
            "control_limit": ttk.StringVar(
                value=(
                    f"{data['control_limit_minor'] / 100:.2f}"
                    if data.get("control_limit_minor") is not None
                    else ""
                )
            ),
            "status": ttk.StringVar(
                value=contract_service.CONTRACT_STATUSES.get(
                    data.get("status", "active")
                )
            ),
        }
        pricing_help = ttk.Label(body, style="PageSub.TLabel")
        pricing_help.grid(
            row=0, column=0, columnspan=2, sticky=W, pady=(0, 12)
        )
        specs = (
            ("合同编号", "contract_no"),
            ("合同名称 *", "name"),
            ("客户", "customer"),
            ("合同类型 *", "type"),
            ("计价方式 *", "pricing"),
            ("收入确认方式 *", "income_mode"),
            ("上级合同 / 原合同", "parent"),
            ("签订日期 *", "sign_date"),
            ("开始日期", "start_date"),
            ("结束日期", "end_date"),
            ("含税合同金额（元）*", "amount"),
            ("控制上限（元，可选）", "control_limit"),
            ("状态 *", "status"),
        )
        field_rows = {}
        for row, (label, key) in enumerate(specs, 1):
            label_widget = ttk.Label(body, text=label)
            label_widget.grid(
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
            elif key == "income_mode":
                widget = ttk.Combobox(
                    body, textvariable=variables[key],
                    values=list(reverse_income), state="readonly"
                )
            elif key == "pricing":
                widget = ttk.Combobox(
                    body, textvariable=variables[key],
                    values=list(reverse_pricing), state="readonly"
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
            field_rows[key] = (label_widget, widget)
        ttk.Label(body, text="备注").grid(
            row=len(specs) + 1, column=0, sticky=NE, padx=(0, 12), pady=7
        )
        notes = ttk.Text(body, height=5, wrap="word")
        notes.grid(row=len(specs) + 1, column=1, sticky=EW, pady=7)
        notes.insert("1.0", data.get("notes") or "")
        body.columnconfigure(1, weight=1)

        def sync_pricing(_event=None):
            pricing_mode = reverse_pricing[variables["pricing"].get()]
            amount_label, amount_widget = field_rows["amount"]
            control_label, control_widget = field_rows["control_limit"]
            if pricing_mode == "actual":
                amount_label.grid_remove()
                amount_widget.grid_remove()
            else:
                amount_label.configure(
                    text=(
                        "含税合同金额（元）*"
                        if pricing_mode == "fixed"
                        else "暂定金额（元）*"
                    )
                )
                amount_label.grid()
                amount_widget.grid()
            if pricing_mode == "fixed":
                control_label.grid_remove()
                control_widget.grid_remove()
                pricing_help.configure(
                    text="固定总价作为合同边界，项目分配和收入确认不得超出。"
                )
            else:
                control_label.grid()
                control_widget.grid()
                pricing_help.configure(
                    text=(
                        "暂定金额用于经营参考，超额会提示但不会阻止登记。"
                        if pricing_mode == "provisional"
                        else "按实际工程量 × 协议单价结算，不预设合同总额。"
                    )
                )

        field_rows["pricing"][1].bind("<<ComboboxSelected>>", sync_pricing)
        sync_pricing()

        def save():
            customer_label = variables["customer"].get().strip()
            payload = {
                "contract_no": variables["contract_no"].get().strip(),
                "name": variables["name"].get().strip(),
                "customer_partner_id": customer_map.get(customer_label),
                "customer_name": customer_label.split(" · ")[0],
                "contract_type": reverse_type[variables["type"].get()],
                "pricing_mode": reverse_pricing[variables["pricing"].get()],
                "income_mode": reverse_income[variables["income_mode"].get()],
                "parent_contract_id": parent_map[variables["parent"].get()],
                "sign_date": variables["sign_date"].get().strip(),
                "start_date": variables["start_date"].get().strip(),
                "end_date": variables["end_date"].get().strip(),
                "amount": variables["amount"].get().strip(),
                "control_limit": variables["control_limit"].get().strip(),
                "status": reverse_status[variables["status"].get()],
                "notes": notes.get("1.0", END).strip(),
            }
            if (payload['income_mode'] == 'invoice'
                    and data.get('income_mode', 'manual') != 'invoice'
                    and not messagebox.askyesno(
                        '确认随开票收入',
                        '仅用于先确认实际结算、再开票的年度框架合同。\n'
                        '启用后登记多少价税合计，就自动确认多少收入；无需重复登记。\n'
                        '已有收入将先核对，不一致时不会切换。确定继续吗？', parent=dialog)):
                return
            if (payload['income_mode'] == 'receipt'
                    and data.get('income_mode', 'manual') != 'receipt'
                    and not messagebox.askyesno(
                        '确认随回款结算',
                        '仅适用于回款同时证明已实际结算的年度框架合同。\n'
                        '超出已有确认的回款会同步补记结算，后续开票只关联已有收入。\n'
                        '修改或作废回款不会自动撤销已确认结算，收入更正仍到合同与结算处理。\n'
                        '确定启用吗？', parent=dialog)):
                return
            switching_to_actual = (
                contract_id
                and initial_pricing_mode != "actual"
                and payload["pricing_mode"] == "actual"
                and (data.get("allocated_minor") or data.get("settled_minor"))
            )
            if switching_to_actual and not messagebox.askyesno(
                "确认调整计价方式",
                "改为单价据实结算后，系统将同步：\n"
                "1. 不再保留预设合同总额；\n"
                "2. 原项目分配改为据实关联；\n"
                "3. 已有收入确认、发票和回款保持不变。\n\n"
                "确定继续吗？",
                parent=dialog,
            ):
                return
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
            if row["status"] in ("draft", "active")
            and (
                row["pricing_mode"] != "fixed"
                or row["remaining_minor"] > 0
            )
        ]
        projects = project_service.list_projects(active_only=False)
        if not contracts or not projects:
            messagebox.showwarning(
                "提示", "请先建立可关联的合同和正式项目"
            )
            return

        def contract_label(row):
            pricing_label = contract_service.PRICING_MODES[row["pricing_mode"]]
            if row["pricing_mode"] == "fixed":
                detail = f"可分配 {self.money(row['remaining_minor'])}"
            elif row["pricing_mode"] == "provisional":
                detail = f"暂定 {self.money(row['tax_inclusive_amount_minor'])}"
            else:
                detail = "仅建立项目关联"
            return f"{row['contract_no']} · {pricing_label} · {detail}"

        contract_map = {contract_label(row): row for row in contracts}
        project_map = {
            f"{row['name']} · {row['project_code']} · "
            f"{'无需开票' if row['invoice_policy'] == 'not_required' else '可开票'}": row["id"]
            for row in projects
        }
        dialog = ttk.Toplevel(self.parent)
        dialog.title("合同关联/分配到项目")
        body, footer = build_form_dialog(
            dialog, self.parent, 680, 470, min_width=560, min_height=400
        )
        variables = {
            "contract": ttk.StringVar(value=next(iter(contract_map))),
            "project": ttk.StringVar(value=next(iter(project_map))),
            "amount": ttk.StringVar(),
        }
        ttk.Label(body, text="合同 *").grid(
            row=0, column=0, sticky=E, padx=(0, 12), pady=8
        )
        contract_combo = ttk.Combobox(
            body, textvariable=variables["contract"],
            values=list(contract_map), state="readonly"
        )
        contract_combo.grid(row=0, column=1, sticky=EW, pady=8, ipady=5)
        ttk.Label(body, text="独立核算项目 *").grid(
            row=1, column=0, sticky=E, padx=(0, 12), pady=8
        )
        ttk.Combobox(
            body, textvariable=variables["project"],
            values=list(project_map), state="readonly"
        ).grid(row=1, column=1, sticky=EW, pady=8, ipady=5)
        amount_label = ttk.Label(body, text="分配金额（元）*")
        amount_label.grid(row=2, column=0, sticky=E, padx=(0, 12), pady=8)
        amount_entry = ttk.Entry(body, textvariable=variables["amount"])
        amount_entry.grid(row=2, column=1, sticky=EW, pady=8, ipady=5)
        ttk.Label(body, text="分配说明").grid(
            row=3, column=0, sticky=NE, padx=(0, 12), pady=8
        )
        notes = ttk.Text(body, height=5, wrap="word")
        notes.grid(row=3, column=1, sticky=EW, pady=8)
        ttk.Label(
            body, text="无需开票也可关联合同；关联后按合同管理，保留原开票要求。",
            wraplength=480,
        ).grid(row=4, column=0, columnspan=2, sticky=W, pady=8)
        body.columnconfigure(1, weight=1)

        def sync_pricing_mode(_event=None):
            contract = contract_map[variables["contract"].get()]
            if contract["pricing_mode"] == "actual":
                variables["amount"].set("0.00")
                amount_label.grid_remove()
                amount_entry.grid_remove()
            else:
                if variables["amount"].get() == "0.00":
                    variables["amount"].set("")
                amount_label.grid()
                amount_entry.grid()

        contract_combo.bind("<<ComboboxSelected>>", sync_pricing_mode)
        sync_pricing_mode()

        def save():
            try:
                contract_service.create_allocation(
                    {
                        "contract_id": contract_map[
                            variables["contract"].get()
                        ]["id"],
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
            primary_text="确认关联/分配", primary_command=save,
        )

    def open_adjust_allocation_dialog(self):
        allocation_id = self.selected_id(self.allocation_tree)
        if not allocation_id:
            messagebox.showwarning("提示", "请先选择项目分配")
            return
        allocation = next(
            (
                row
                for row in contract_service.list_allocations()
                if row["id"] == allocation_id
            ),
            None,
        )
        if not allocation:
            messagebox.showwarning("提示", "所选项目分配已不存在")
            self.refresh()
            return
        if allocation["pricing_mode"] == "actual":
            messagebox.showinfo(
                "提示", "单价据实结算合同只建立项目关联，没有分配金额"
            )
            return
        settled_minor = sum(
            row["amount_minor"]
            for row in contract_service.list_settlements(
                project_id=allocation["project_id"],
                contract_id=allocation["contract_id"],
            )
            if row["status"] == "active"
        )
        dialog = ttk.Toplevel(self.parent)
        dialog.title("调整分配金额")
        body, footer = build_form_dialog(
            dialog, self.parent, 560, 430, min_width=480, min_height=360
        )
        info_rows = (
            ("合同", f"{allocation['contract_no']} · {allocation['contract_name']}"),
            ("项目", f"{allocation['project_name']} · {allocation['project_code']}"),
            ("当前分配额", self.money(allocation["allocated_amount_minor"])),
            ("已结算（调减下限）", self.money(settled_minor)),
        )
        for row, (label, text) in enumerate(info_rows):
            ttk.Label(body, text=label).grid(
                row=row, column=0, sticky=E, padx=(0, 12), pady=6
            )
            ttk.Label(body, text=text).grid(row=row, column=1, sticky=W, pady=6)
        amount_var = ttk.StringVar(
            value=f"{allocation['allocated_amount_minor'] / 100:.2f}"
        )
        ttk.Label(body, text="新分配金额（元）*").grid(
            row=4, column=0, sticky=E, padx=(0, 12), pady=8
        )
        ttk.Entry(body, textvariable=amount_var).grid(
            row=4, column=1, sticky=EW, pady=8, ipady=5
        )
        ttk.Label(body, text="调整原因").grid(
            row=5, column=0, sticky=NE, padx=(0, 12), pady=8
        )
        reason = ttk.Text(body, height=3, wrap="word")
        reason.grid(row=5, column=1, sticky=EW, pady=8)
        body.columnconfigure(1, weight=1)

        def save():
            try:
                contract_service.update_allocation_amount(
                    allocation_id,
                    amount_var.get(),
                    notes=reason.get("1.0", END).strip(),
                )
            except Exception as error:
                messagebox.showwarning("无法调整", str(error), parent=dialog)
                return
            dialog.destroy()
            self.refresh()

        add_form_actions(
            footer, cancel_command=dialog.destroy,
            primary_text="确认调整", primary_command=save,
        )

    def open_settlement_dialog(self, settlement_id=None):
        editing = settlement_id is not None
        current = (
            contract_service.get_settlement(settlement_id) if editing else {}
        ) or {}
        available_projects = project_service.list_projects(active_only=not editing)
        contract_project_map = {
            (
                f"{row['contract_no']} · "
                f"{contract_service.PRICING_MODES[row['pricing_mode']]} → "
                f"{row['project_code']} · {row['project_name']}"
            ): row
            for row in contract_service.list_allocations()
        }
        cash_projects = [
            row for row in available_projects if row["business_mode"] == "cash"
        ]
        def cash_project_label(row):
            agreed_minor = row.get("cash_agreed_amount_minor")
            if agreed_minor is None:
                boundary = "未设总额"
            else:
                remaining_minor = max(
                    int(agreed_minor) - int(row.get("cash_confirmed_minor") or 0),
                    0,
                )
                boundary = f"还可确认 {self.money(remaining_minor)}"
            return f"{row['project_code']} · {row['name']} · {boundary}"

        cash_project_map = {
            cash_project_label(row): row for row in cash_projects
        }
        if not contract_project_map and not cash_project_map:
            messagebox.showwarning(
                "提示", "请先建立可结算合同与正式项目，或建立零星现金工程项目"
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
        for label, row in contract_project_map.items():
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
                value=current_allocation or next(iter(contract_project_map), "")
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
                    else list(contract_project_map) if key == "allocation"
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

        cash_capacity_var = ttk.StringVar()
        cash_capacity_help = ttk.Label(
            body,
            textvariable=cash_capacity_var,
            style="Muted.TLabel",
            wraplength=500,
            justify=LEFT,
        )
        cash_capacity_help.grid(row=8, column=1, sticky=W, pady=(0, 7))

        def sync_cash_capacity(_event=None):
            project = cash_project_map.get(variables["cash_project"].get())
            amount_widget = field_rows["amount"][1]
            if not project:
                cash_capacity_var.set("请选择零星工程项目。")
                amount_widget.configure(state="normal")
                return
            agreed_minor = project.get("cash_agreed_amount_minor")
            if agreed_minor is None:
                cash_capacity_var.set(
                    "该项目未设置约定总额，可分次确认；如有明确总价，建议先在项目台账补充。"
                )
                amount_widget.configure(state="normal")
                return
            confirmed_minor = int(project.get("cash_confirmed_minor") or 0)
            if editing and project["id"] == current.get("project_id"):
                confirmed_minor -= int(current.get("amount_minor") or 0)
            available_minor = max(int(agreed_minor) - confirmed_minor, 0)
            cash_capacity_var.set(
                f"约定总额 {self.money(agreed_minor)} · "
                f"除本笔外已确认 {self.money(confirmed_minor)} · "
                f"本笔最多 {self.money(available_minor)}"
            )
            amount_widget.configure(
                state="normal" if available_minor > 0 else "disabled"
            )

        def sync_source(_event=None):
            is_cash = variables["source"].get() == "零星现金工程"
            visible_key = "cash_project" if is_cash else "allocation"
            hidden_key = "allocation" if is_cash else "cash_project"
            for widget in field_rows[visible_key]:
                widget.grid()
            for widget in field_rows[hidden_key]:
                widget.grid_remove()
            if is_cash:
                cash_capacity_help.grid()
                sync_cash_capacity()
            else:
                cash_capacity_help.grid_remove()
                field_rows["amount"][1].configure(state="normal")

        field_rows["source"][1].bind("<<ComboboxSelected>>", sync_source)
        field_rows["cash_project"][1].bind(
            "<<ComboboxSelected>>", sync_cash_capacity
        )
        sync_source()
        ttk.Label(body, text="确认依据").grid(
            row=9, column=0, sticky=NE, padx=(0, 12), pady=7
        )
        basis = ttk.Text(body, height=5, wrap="word")
        basis.grid(row=9, column=1, sticky=EW, pady=7)
        if editing and current.get("basis"):
            basis.insert("1.0", current["basis"])
        body.columnconfigure(1, weight=1)

        def save():
            is_cash = variables["source"].get() == "零星现金工程"
            if is_cash:
                selected = cash_project_map.get(variables["cash_project"].get())
                contract_id = None
            else:
                selected = contract_project_map.get(variables["allocation"].get())
                contract_id = selected["contract_id"] if selected else None
            if not selected:
                messagebox.showwarning("提示", "请选择有效的项目来源", parent=dialog)
                return
            project_id = selected["id"] if is_cash else selected["project_id"]
            payload = {
                "settlement_no": variables["no"].get(),
                "contract_id": contract_id,
                "project_id": project_id,
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
            if contract_id:
                warning = contract_service.contract_pricing_warning(contract_id)
                if warning:
                    messagebox.showwarning("额度提示", warning, parent=self.parent)

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
