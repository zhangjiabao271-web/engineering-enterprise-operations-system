"""数据治理中心：集中修复影响经营口径的数据缺口。"""

from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, CENTER, E, END, EW, LEFT, RIGHT, W, X

from services import data_governance_service, labor_service, master_data_service
from services import project_service
from ui.components import DataTable, FilterBar, KpiCard, PageHeader
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders
from ui.theme import COLORS, SPACING


def _money(minor):
    return f"¥{int(minor or 0) / 100:,.2f}"


class DataGovernancePage:
    """One workbench for unassigned costs and incomplete business relations."""

    def __init__(self, parent, navigate=None):
        self.parent = parent
        self.navigate = navigate
        self.labor_keyword_var = ttk.StringVar()
        self.purchase_keyword_var = ttk.StringVar()
        self.kpi_vars = {
            key: ttk.StringVar(value="—")
            for key in ("labor", "purchase", "customer", "other")
        }
        self.kpi_hints = {
            key: ttk.StringVar(value="正在核对") for key in self.kpi_vars
        }
        self._build_ui()
        safe_init_loaders("数据治理中心", [self.refresh_all])

    def _build_ui(self):
        PageHeader(
            self.parent,
            "数据治理中心",
            "只处理会影响项目利润、客户应收和经营口径的缺口",
            actions=[
                ttk.Button(
                    self.parent, text="刷新体检", bootstyle="primary-outline",
                    command=self.refresh_all,
                )
            ],
        )
        self.health_var = ttk.StringVar(value="正在核对经营口径…")
        health = ttk.Frame(self.parent, style="Card.TFrame", padding=(16, 10))
        health.pack(fill=X, pady=(0, SPACING["md"]))
        ttk.Frame(health, width=3, style="NavIndicator.TFrame").pack(
            side=LEFT, fill="y", padx=(0, 12)
        )
        ttk.Label(health, text="经营口径体检", style="CardTitle.TLabel").pack(side=LEFT)
        ttk.Label(
            health, textvariable=self.health_var, style="CardText.TLabel",
        ).pack(side=LEFT, padx=(16, 0))
        ttk.Label(
            health, text="以下操作均保留原始记录，不会自动猜测归属",
            style="KpiHintMuted.TLabel",
        ).pack(side=RIGHT)

        grid = ttk.Frame(self.parent)
        grid.pack(fill=X, pady=(0, SPACING["md"]))
        specs = (
            ("labor", "待归集人工"),
            ("purchase", "待归集采购"),
            ("customer", "缺客户项目"),
            ("other", "其他经营缺口"),
        )
        for index, (key, label) in enumerate(specs):
            card = KpiCard(
                grid, label, self.kpi_vars[key], self.kpi_hints[key],
                hint_wraplength=220,
            )
            card.grid(
                row=0, column=index, sticky=EW,
                padx=(0 if index == 0 else 5, 0 if index == len(specs) - 1 else 5),
            )
            grid.columnconfigure(index, weight=1)

        self.notebook = ttk.Notebook(self.parent, bootstyle="primary")
        self.notebook.pack(fill=BOTH, expand=True)
        self._build_labor_tab()
        self._build_purchase_tab()
        self._build_project_tab()
        self._build_fulfillment_tab()

    def _tab(self, label):
        frame = ttk.Frame(self.notebook, padding=(0, 12, 0, 0))
        self.notebook.add(frame, text=label)
        return frame

    def _build_labor_tab(self):
        tab = self._tab("人工待归集")
        keyword = ttk.Entry(tab, textvariable=self.labor_keyword_var, width=18)
        keyword.bind("<Return>", lambda _event: self.load_labor())
        FilterBar(
            tab,
            ("搜索", keyword),
            ttk.Button(tab, text="查询", bootstyle="primary", command=self.load_labor),
            ttk.Button(
                tab, text="清空", bootstyle="secondary-outline",
                command=lambda: (self.labor_keyword_var.set(""), self.load_labor()),
            ),
            actions=[
                ttk.Button(
                    tab, text="归集选中记录", bootstyle="primary-outline",
                    command=self.assign_selected_labor,
                )
            ],
        )
        self.labor_table = DataTable(
            tab,
            specs=(
                ("date", "日期", 90, CENTER),
                ("worker", "工人", 90, W),
                ("site", "原工地文本", 160, W),
                ("work", "工作内容", 180, W),
                ("days", "工天", 65, E),
                ("amount", "人工成本", 105, E),
                ("notes", "备注", 150, W),
            ),
            empty_text="没有待归集人工，项目人工成本口径完整",
            stretch=("site", "work", "notes"),
            padding=0,
        )
        self.labor_table.tree.configure(selectmode="extended")

    def _build_purchase_tab(self):
        tab = self._tab("采购待归集")
        keyword = ttk.Entry(tab, textvariable=self.purchase_keyword_var, width=18)
        keyword.bind("<Return>", lambda _event: self.load_purchases())
        FilterBar(
            tab,
            ("搜索", keyword),
            ttk.Button(tab, text="查询", bootstyle="primary", command=self.load_purchases),
            actions=[
                ttk.Button(
                    tab, text="归集选中采购", bootstyle="primary-outline",
                    command=self.assign_selected_purchases,
                )
            ],
        )
        self.purchase_table = DataTable(
            tab,
            specs=(
                ("date", "采购日期", 90, CENTER),
                ("no", "采购单号", 125, CENTER),
                ("supplier", "供应商", 155, W),
                ("material", "材料", 235, W),
                ("amount", "计入成本", 110, E),
                ("notes", "备注", 160, W),
            ),
            empty_text="没有待归集采购，材料成本已进入项目口径",
            stretch=("supplier", "material", "notes"),
            padding=0,
        )
        self.purchase_table.tree.configure(selectmode="extended")

    def _build_project_tab(self):
        tab = self._tab("项目主数据")
        FilterBar(
            tab,
            ttk.Label(
                tab, text="进行中项目的客户、地点、合同与结算完整度",
                style="Toolbar.TLabel",
            ),
            actions=[
                ttk.Button(
                    tab, text="确认客户", bootstyle="primary-outline",
                    command=self.confirm_customer,
                ),
                ttk.Button(
                    tab, text="建立默认地点", bootstyle="secondary-outline",
                    command=self.create_default_site,
                ),
            ],
        )
        self.project_table = DataTable(
            tab,
            specs=(
                ("code", "项目编码", 105, CENTER),
                ("name", "项目", 175, W),
                ("customer", "客户", 150, W),
                ("mode", "业务模式", 105, CENTER),
                ("site", "施工地点", 90, CENTER),
                ("contract", "合同分配", 90, CENTER),
                ("settlement", "收入确认", 80, CENTER),
                ("action", "建议动作", 220, W),
            ),
            empty_text="没有进行中的项目主数据缺口",
            stretch=("name", "customer", "action"),
            padding=0,
        )
        self.project_table.tree.configure(selectmode="browse")
        self.project_table.tree.tag_configure("gap", foreground=COLORS["text"])

    def _build_fulfillment_tab(self):
        tab = self._tab("履约与资金待办")
        FilterBar(
            tab,
            ttk.Label(
                tab, text="待验收和关键附件缺口不会自动补造",
                style="Toolbar.TLabel",
            ),
            actions=[
                ttk.Button(
                    tab, text="前往对应模块", bootstyle="primary-outline",
                    command=self.go_to_selected_gap,
                )
            ],
        )
        self.gap_table = DataTable(
            tab,
            specs=(
                ("type", "问题", 105, CENTER),
                ("project", "项目", 150, W),
                ("subject", "业务对象", 210, W),
                ("date", "业务日期", 90, CENTER),
                ("amount", "涉及金额", 110, E),
                ("action", "下一步", 210, W),
            ),
            empty_text="没有待处理的履约或关键附件缺口",
            stretch=("project", "subject", "action"),
            padding=0,
        )
        self.gap_table.tree.configure(selectmode="browse")

    def refresh_all(self):
        self.load_summary()
        self.load_labor()
        self.load_purchases()
        self.load_projects()
        self.load_gaps()

    def load_summary(self):
        data = data_governance_service.get_governance_summary()
        self.kpi_vars["labor"].set(_money(data["labor_amount_minor"]))
        self.kpi_hints["labor"].set(
            f"{data['labor_record_count']} 条 · {data['labor_work_days']:g} 工天"
        )
        self.kpi_vars["purchase"].set(_money(data["purchase_amount_minor"]))
        self.kpi_hints["purchase"].set(f"{data['purchase_record_count']} 笔采购")
        self.kpi_vars["customer"].set(f"{data['missing_customer_count']} 个")
        self.kpi_hints["customer"].set("进行中项目尚未关联正式客商")
        other = (
            data["missing_site_count"] + data["missing_contract_count"]
            + data["missing_settlement_count"] + data["pending_inspection_count"]
            + data["pending_partner_count"]
            + data["cash_unpaid_count"]
            + data["cash_receipt_missing_voucher_count"]
        )
        self.kpi_vars["other"].set(f"{other} 项")
        self.kpi_hints["other"].set(
            f"地点 {data['missing_site_count']} · 合同 {data['missing_contract_count']} · "
            f"结算 {data['missing_settlement_count']} · 验收 {data['pending_inspection_count']} · "
            f"客商 {data['pending_partner_count']} · 零星待收 {data['cash_unpaid_count']} · "
            f"现金凭证 {data['cash_receipt_missing_voucher_count']}"
        )
        critical = data["labor_record_count"] + data["purchase_record_count"]
        self.health_var.set(
            f"当前有 {critical} 条成本事实尚未进入项目口径"
        )

    def load_labor(self):
        rows = data_governance_service.list_unassigned_labor(
            self.labor_keyword_var.get().strip()
        )
        self.labor_table.refresh(
            rows,
            lambda row: (str(row["id"]), (
                row["work_date"], row["worker_name"], row["construction_site"],
                row["work_type"], f"{row['work_days']:g}",
                _money(row["amount_minor"]), row["notes"],
            )),
        )

    def load_purchases(self):
        rows = data_governance_service.list_unassigned_purchases(
            self.purchase_keyword_var.get().strip()
        )
        self.purchase_table.refresh(
            rows,
            lambda row: (str(row["id"]), (
                row["purchase_date"], row["order_no"], row["supplier_name"],
                row["materials"] or "", _money(row["amount_minor"]), row["notes"],
            )),
        )

    def load_projects(self):
        rows = data_governance_service.list_project_completeness()
        rows = [
            row for row in rows
            if not row["customer_partner_id"] or not row["site_count"]
            or (
                row["business_mode"] == "contract" and not row["contract_count"]
            ) or not row["settlement_count"]
        ]

        def mapper(row):
            actions = []
            if not row["customer_partner_id"]:
                actions.append("确认客户")
            if not row["site_count"]:
                actions.append("建立地点")
            if row["business_mode"] == "contract" and not row["contract_count"]:
                actions.append("分配合同")
            if not row["settlement_count"]:
                actions.append(
                    "登记完工金额" if row["business_mode"] == "cash" else "登记结算"
                )
            return str(row["id"]), (
                row["project_code"], row["name"], row["customer_name"] or "未确认",
                "零星现金" if row["business_mode"] == "cash" else "正式合同",
                f"{row['site_count']} 个" if row["site_count"] else "缺失",
                (
                    "不适用" if row["business_mode"] == "cash"
                    else f"{row['contract_count']} 份" if row["contract_count"]
                    else "缺失"
                ),
                f"{row['settlement_count']} 笔" if row["settlement_count"] else "缺失",
                "、".join(actions),
            ), ("gap",)

        self.project_table.refresh(rows, mapper)

    def load_gaps(self):
        rows = data_governance_service.list_fulfillment_gaps()
        self.gap_table.refresh(
            rows,
            lambda row: (f"{row['issue_type']}:{row['id']}", (
                row["issue_type"], row["project_name"], row["subject"],
                row["business_date"] or "", _money(row["amount_minor"]), row["action"],
            )),
        )

    def _selected_ids(self, table, message):
        ids = [int(value) for value in table.tree.selection()]
        if not ids:
            messagebox.showwarning("提示", message)
        return ids

    def _open_assignment_dialog(self, ids, kind):
        if not ids:
            return
        projects = labor_service.list_work_log_project_options()
        if not projects:
            messagebox.showwarning("提示", "请先在项目台账建立进行中的项目。")
            return
        project_by_label = {
            f"{row['name']}（{row['project_code']}）": row for row in projects
        }
        dialog = ttk.Toplevel(self.parent)
        dialog.title("确认人工归集" if kind == "labor" else "确认采购归集")
        body, footer = build_form_dialog(
            dialog, self.parent, 560, 390, min_width=500, min_height=330
        )
        ttk.Label(body, text="确认目标项目", style="PageTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky=W
        )
        ttk.Label(
            body,
            text=f"已选择 {len(ids)} 条记录。提交前请再次核对目标项目。",
            style="PageSub.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky=W, pady=(3, 16))
        ttk.Label(body, text="目标项目 *").grid(row=2, column=0, sticky=E, padx=(0, 10), pady=8)
        project_var = ttk.StringVar()
        project_combo = ttk.Combobox(
            body, textvariable=project_var, values=tuple(project_by_label),
            state="readonly", width=38,
        )
        project_combo.grid(row=2, column=1, sticky=EW, pady=8)
        site_var = ttk.StringVar(value="不指定地点（保留原工地文本）")
        site_combo = None
        site_map = {}
        if kind == "labor":
            ttk.Label(body, text="施工地点").grid(row=3, column=0, sticky=E, padx=(0, 10), pady=8)
            site_combo = ttk.Combobox(
                body, textvariable=site_var,
                values=("不指定地点（保留原工地文本）",),
                state="readonly", width=38,
            )
            site_combo.grid(row=3, column=1, sticky=EW, pady=8)

            def refresh_sites(_event=None):
                nonlocal site_map
                project = project_by_label.get(project_var.get())
                sites = labor_service.list_work_log_site_options(
                    project["id"] if project else None
                )
                site_map = {row["name"]: row for row in sites}
                site_combo.configure(
                    values=("不指定地点（保留原工地文本）", *site_map)
                )
                site_var.set("不指定地点（保留原工地文本）")

            project_combo.bind("<<ComboboxSelected>>", refresh_sites)
        ttk.Label(
            body,
            text="不会根据历史名称自动猜测；人工选择地点时会把原工地文本规范为该地点。",
            style="PageSub.TLabel", wraplength=390,
        ).grid(row=4, column=1, sticky=W, pady=(8, 0))
        body.columnconfigure(1, weight=1)

        def save():
            project = project_by_label.get(project_var.get())
            if not project:
                messagebox.showwarning("提示", "请选择目标项目。", parent=dialog)
                return
            site = site_map.get(site_var.get()) if kind == "labor" else None
            if not messagebox.askyesno(
                "再次确认",
                f"确定将 {len(ids)} 条{('人工' if kind == 'labor' else '采购')}记录"
                f"归集到“{project['name']}”吗？",
                parent=dialog,
            ):
                return
            try:
                if kind == "labor":
                    changed = data_governance_service.assign_labor_records(
                        ids, project["id"], site["id"] if site else None
                    )
                else:
                    changed = data_governance_service.assign_purchase_orders(
                        ids, project["id"]
                    )
            except Exception as error:
                messagebox.showerror("归集失败", str(error), parent=dialog)
                return
            dialog.destroy()
            self.refresh_all()
            messagebox.showinfo("归集完成", f"已确认 {changed} 条记录的项目归属。")

        add_form_actions(
            footer, cancel_command=dialog.destroy,
            primary_text="确认归集", primary_command=save,
        )
        project_combo.focus_set()

    def assign_selected_labor(self):
        self._open_assignment_dialog(
            self._selected_ids(self.labor_table, "请选择需要归集的人工记录。"),
            "labor",
        )

    def assign_selected_purchases(self):
        self._open_assignment_dialog(
            self._selected_ids(self.purchase_table, "请选择需要归集的采购记录。"),
            "purchase",
        )

    def _selected_project(self):
        value = self.project_table.selected_id()
        if value is None:
            messagebox.showwarning("提示", "请先选择一个项目。")
        return value

    def confirm_customer(self):
        project_id = self._selected_project()
        if project_id is None:
            return
        project = project_service.get_project(project_id)
        customers = master_data_service.list_customers(active_only=True)
        names = sorted({row["name"] for row in customers})
        dialog = ttk.Toplevel(self.parent)
        dialog.title("确认项目客户")
        body, footer = build_form_dialog(
            dialog, self.parent, 570, 390, min_width=500, min_height=330
        )
        ttk.Label(body, text="确认项目客户", style="PageTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky=W
        )
        ttk.Label(
            body, text=f"项目：{project['name']}", style="PageSub.TLabel"
        ).grid(row=1, column=0, columnspan=2, sticky=W, pady=(3, 16))
        ttk.Label(body, text="客户名称 *").grid(row=2, column=0, sticky=E, padx=(0, 10), pady=8)
        name_var = ttk.StringVar(value=project["customer_name"] or "")
        customer_combo = ttk.Combobox(
            body, textvariable=name_var, values=names, width=38
        )
        customer_combo.grid(row=2, column=1, sticky=EW, pady=8)
        update_contracts = ttk.BooleanVar(value=True)
        ttk.Checkbutton(
            body, text="同时补齐该项目已分配合同中尚未确认的客户",
            variable=update_contracts, bootstyle="primary-round-toggle",
        ).grid(row=3, column=1, sticky=W, pady=8)
        ttk.Label(
            body, text="可以选择已有客户，也可以直接输入新客户名称。新名称会建立正式客户客商。",
            style="PageSub.TLabel", wraplength=390,
        ).grid(row=4, column=1, sticky=W, pady=(5, 0))
        body.columnconfigure(1, weight=1)

        def save():
            try:
                result = data_governance_service.confirm_project_customer(
                    project_id, name_var.get(), update_contracts=update_contracts.get()
                )
            except Exception as error:
                messagebox.showerror("保存失败", str(error), parent=dialog)
                return
            dialog.destroy()
            self.refresh_all()
            messagebox.showinfo(
                "客户已确认",
                f"项目客户关系已建立；同步更新合同 {result['contract_count']} 份。",
            )

        add_form_actions(
            footer, cancel_command=dialog.destroy,
            primary_text="确认客户", primary_command=save,
        )
        customer_combo.focus_set()

    def create_default_site(self):
        project_id = self._selected_project()
        if project_id is None:
            return
        project = project_service.get_project(project_id)
        existing = project_service.list_project_sites(project_id)
        if existing:
            messagebox.showinfo("无需处理", "该项目已经有启用的施工地点。")
            return
        if not messagebox.askyesno(
            "确认建立地点",
            f"为“{project['name']}”建立同名默认施工地点吗？\n"
            "后续可在项目台账中修改名称和地址。",
        ):
            return
        try:
            project_service.create_project_site(
                project_id,
                {"site_name": project["name"], "address": project["address"] or ""},
            )
        except Exception as error:
            messagebox.showerror("建立失败", str(error))
            return
        self.refresh_all()
        messagebox.showinfo("地点已建立", "采购、工天和施工记录现在可选择该地点。")

    def go_to_selected_gap(self):
        selected = self.gap_table.tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择一个待办。")
            return
        issue_type = selected[0].split(":", 1)[0]
        target = {
            "待验收施工": "construction",
            "合同缺附件": "contract",
            "待确认客商": "supplier",
            "零星工程待收款": "finance",
            "现金回款缺凭证": "finance",
        }.get(issue_type, "home")
        if self.navigate:
            self.navigate(target)
        else:
            messagebox.showinfo(
                "下一步",
                "请前往“施工与验收”处理验收，或前往“合同与结算”补充附件。",
            )
