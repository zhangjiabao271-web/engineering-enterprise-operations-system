from datetime import datetime
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import master_data_service
from ui.charts import DonutBreakdown
from ui.components import BottomToolbar, DataTable, FilterBar, KpiCard, PageHeader
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders
from ui.theme import COLORS


STATUS_LABELS = {"active": "启用", "pending": "待确认", "inactive": "已停用"}
ENTITY_TYPE_LABELS = {
    "enterprise": "企业", "individual_business": "个体工商户", "individual": "个人"
}


class CustomerPage:
    """客户经营概览与主体档案维护。"""

    def __init__(self, parent):
        self.parent = parent
        self.build_ui()
        safe_init_loaders("客户档案", [self.load_data])

    def build_ui(self):
        PageHeader(
            self.parent,
            "客户档案",
            "按客户查看年度业务、回款和当前未收余额；低频资料在档案中维护",
            actions=[
                ttk.Button(
                    self.parent, text="新增客户", bootstyle="primary",
                    command=self.open_partner_dialog,
                )
            ],
        )

        self.search_var = ttk.StringVar()
        self.status_var = ttk.StringVar(value="启用与待确认")
        self.year_var = ttk.StringVar(value=str(datetime.now().year))
        search_entry = ttk.Entry(self.parent, textvariable=self.search_var, width=28)
        search_entry.bind("<Return>", lambda _event: self.load_data())
        self.year_combo = ttk.Combobox(
            self.parent, textvariable=self.year_var,
            values=(str(datetime.now().year),), width=8, state="readonly",
        )
        self.year_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_data())
        status_combo = ttk.Combobox(
            self.parent, textvariable=self.status_var,
            values=("启用与待确认", "仅启用", "待确认", "已停用", "全部状态"),
            width=14, state="readonly",
        )
        status_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_data())
        FilterBar(
            self.parent,
            ("年度", self.year_combo),
            ("搜索名称 / 编码 / 信用代码", search_entry),
            ("状态", status_combo),
            actions=[
                ttk.Button(
                    self.parent, text="重置", bootstyle="secondary-outline",
                    command=self.reset_filters,
                )
            ],
        )

        self.view_tabs = ttk.Notebook(self.parent)
        self.view_tabs.pack(fill=BOTH, expand=True)
        dashboard_tab = ttk.Frame(self.view_tabs, padding=(0, 10, 0, 0))
        self.records_tab = ttk.Frame(
            self.view_tabs, padding=(0, 10, 0, 0)
        )
        self.view_tabs.add(dashboard_tab, text="经营看板")
        self.view_tabs.add(self.records_tab, text="客户明细")

        self._build_dashboard(dashboard_tab)

        ttk.Label(
            self.records_tab, text="客户经营明细", style="CardTitle.TLabel"
        ).pack(anchor=W, pady=(0, 8))
        BottomToolbar(
            self.records_tab,
            ttk.Button(
                self.records_tab, text="查看业务", bootstyle="primary-outline",
                command=self.open_business_detail,
            ),
            ttk.Button(
                self.records_tab, text="新增客户", bootstyle="primary-outline",
                command=self.open_partner_dialog,
            ),
            ttk.Button(
                self.records_tab, text="修改档案", bootstyle="primary-outline",
                command=self.edit_selected,
            ),
            ttk.Button(
                self.records_tab, text="停用客户", bootstyle="danger-outline",
                command=self.deactivate_selected,
            ),
        )
        self.table = DataTable(
            self.records_tab,
            specs=(
                ("name", "客户名称", 210, W),
                ("business", "本年业务额", 115, E),
                ("receipt", "本年实际回款", 115, E),
                ("receivable", "当前未回款", 115, E),
                ("projects", "在做项目", 75, CENTER),
                ("latest", "最近业务", 95, CENTER),
                ("contact", "主要联系人", 100, W),
                ("phone", "联系电话", 120, W),
                ("status", "异常状态", 75, CENTER),
            ),
            empty_text="没有符合条件的客户",
            stretch=("name",),
        )
        self.table.tree.configure(selectmode="extended")
        self.table.tree.bind(
            "<Double-1>", lambda _event: self.open_business_detail()
        )

    def _build_dashboard(self, parent):
        self.total_income_var = ttk.StringVar(value="¥0.00")
        self.income_customer_count_var = ttk.StringVar(value="0 家")
        self.total_receivable_var = ttk.StringVar(value="¥0.00")
        self.receivable_customer_count_var = ttk.StringVar(value="0 家")
        self.income_hint_var = ttk.StringVar(value="所选年度 · 当前状态范围")
        self.receivable_hint_var = ttk.StringVar(value="截至当前 · 全部年度")
        self.income_chart_hint_var = ttk.StringVar(value="按所选年度有效收入确认统计")
        self.receivable_chart_hint_var = ttk.StringVar(
            value="截至当前全部年度，不受年度筛选影响"
        )

        metrics = ttk.Frame(parent)
        metrics.pack(fill=X, pady=(0, 12))
        metric_specs = (
            ("本年确认收入", self.total_income_var, self.income_hint_var),
            ("本年有收入客户", self.income_customer_count_var, self.income_hint_var),
            ("当前未回款", self.total_receivable_var, self.receivable_hint_var),
            ("有未回款客户", self.receivable_customer_count_var, self.receivable_hint_var),
        )
        for index, (label, value_var, hint_var) in enumerate(metric_specs):
            KpiCard(metrics, label, value_var, hint_var).grid(
                row=0,
                column=index,
                sticky=EW,
                padx=(0 if index == 0 else 5, 0 if index == 3 else 5),
            )
            metrics.columnconfigure(index, weight=1)

        charts = ttk.Panedwindow(parent, orient=HORIZONTAL)
        charts.pack(fill=BOTH, expand=True, pady=(0, 12))
        income_card = self._chart_card(
            charts,
            "客户收入贡献占比",
            self.income_chart_hint_var,
        )
        receivable_card = self._chart_card(
            charts,
            "客户未回款占比",
            self.receivable_chart_hint_var,
        )
        charts.add(income_card, weight=1)
        charts.add(receivable_card, weight=1)
        chart_colors = (
            COLORS["primary"],
            COLORS["accent"],
            COLORS["cost_freight"],
            COLORS["cost_other"],
            COLORS["warning"],
            COLORS["text_muted"],
        )
        self.income_chart = DonutBreakdown(
            income_card,
            colors=chart_colors,
            center_label="确认收入",
            empty_text="暂无收入",
            limit=5,
            other_label="其他客户",
            compact_total=True,
        )
        self.income_chart.pack(fill=BOTH, expand=True)
        self.receivable_chart = DonutBreakdown(
            receivable_card,
            colors=chart_colors,
            center_label="未回款",
            empty_text="暂无未回款",
            limit=5,
            other_label="其他客户",
            compact_total=True,
        )
        self.receivable_chart.pack(fill=BOTH, expand=True)

    @staticmethod
    def _chart_card(parent, title, subtitle_var):
        card = ttk.Frame(parent, style="Card.TFrame", padding=(14, 10))
        ttk.Label(card, text=title, style="CardTitle.TLabel").pack(anchor=W)
        ttk.Label(
            card, textvariable=subtitle_var, style="CardText.TLabel"
        ).pack(anchor=W, pady=(3, 8))
        return card

    def reset_filters(self):
        self.search_var.set("")
        self.status_var.set("启用与待确认")
        self.year_var.set(str(datetime.now().year))
        self.load_data()

    def _status_code(self):
        return {
            "仅启用": "active",
            "待确认": "pending",
            "已停用": "inactive",
        }.get(self.status_var.get(), "")

    def load_data(self):
        years = master_data_service.list_customer_business_years()
        year_values = tuple(str(year) for year in years)
        self.year_combo.configure(values=year_values)
        if self.year_var.get() not in year_values:
            self.year_var.set(str(datetime.now().year))
        rows = master_data_service.list_customers(
            active_only=False, year=self.year_var.get()
        )
        status_code = self._status_code()
        if status_code:
            rows = [row for row in rows if row["status"] == status_code]
        elif self.status_var.get() == "启用与待确认":
            rows = [row for row in rows if row["status"] in ("active", "pending")]

        summary = master_data_service.summarize_customer_business(rows)
        self._refresh_dashboard(summary)
        keyword = self.search_var.get().strip().casefold()
        if keyword:
            rows = [row for row in rows if self._matches_keyword(row, keyword)]

        self.table.refresh(
            rows,
            lambda row: (
                str(row["id"]),
                (
                    row["name"],
                    self._money(row["yearly_business_minor"]),
                    self._money(row["yearly_receipt_minor"]),
                    self._money(row["current_receivable_minor"]),
                    row["active_project_count"],
                    row["latest_business_date"] or "—",
                    row["contact"] or "—",
                    row["contact_phone"] or "—",
                    "—" if row["status"] == "active" else
                    STATUS_LABELS.get(row["status"], row["status"]),
                ),
            ),
        )

    def _refresh_dashboard(self, summary):
        year = self.year_var.get()
        status_scope = self.status_var.get()
        self.total_income_var.set(
            self._money_with_symbol(summary["total_income_minor"])
        )
        self.income_customer_count_var.set(
            f"{summary['income_customer_count']} 家"
        )
        self.total_receivable_var.set(
            self._money_with_symbol(summary["total_receivable_minor"])
        )
        self.receivable_customer_count_var.set(
            f"{summary['receivable_customer_count']} 家"
        )
        self.income_hint_var.set(f"{year} 年 · {status_scope}")
        self.receivable_hint_var.set(f"截至当前 · {status_scope} · 全部年度")
        self.income_chart_hint_var.set(
            f"{year} 年有效收入确认 · {status_scope} · 前 5 名 + 其他"
        )
        self.receivable_chart_hint_var.set(
            f"截至当前全部年度 · {status_scope} · 前 5 名 + 其他"
        )
        self.income_chart.set_data(
            (row["label"], row["amount_minor"])
            for row in summary["income_rows"]
        )
        self.receivable_chart.set_data(
            (row["label"], row["amount_minor"])
            for row in summary["receivable_rows"]
        )

    @staticmethod
    def _matches_keyword(row, keyword):
        return any(
            keyword in str(row.get(key) or "").casefold()
            for key in ("name", "short_name", "partner_code", "unified_credit_code")
        )

    @staticmethod
    def _money(value):
        return f"{int(value or 0) / 100:,.2f}"

    @classmethod
    def _money_with_symbol(cls, value):
        return f"¥{cls._money(value)}"

    def _single_selected_id(self):
        selected = self.table.tree.selection()
        if len(selected) != 1:
            messagebox.showwarning("提示", "请只选择一个客户档案")
            return None
        return int(selected[0])

    def edit_selected(self):
        partner_id = self._single_selected_id()
        if partner_id:
            self.open_partner_dialog(partner_id)

    def open_business_detail(self):
        partner_id = self._single_selected_id()
        if not partner_id:
            return
        detail = master_data_service.get_customer_business_detail(
            partner_id, self.year_var.get()
        )
        customer = detail["customer"]
        summary = detail["summary"]
        year = detail["year"]

        dialog = ttk.Toplevel(self.parent)
        dialog.title(f"{customer['name']} · 客户业务")
        body, footer = build_form_dialog(
            dialog, self.parent, 1060, 650,
            min_width=820, min_height=520,
        )

        ttk.Label(
            body, text=customer["name"], style="PageTitle.TLabel"
        ).pack(anchor=W)
        contact = " · ".join(
            value for value in (customer["contact"], customer["contact_phone"])
            if value
        )
        ttk.Label(
            body,
            text=f"{year} 年经营概览" + (f" · {contact}" if contact else ""),
            style="PageSub.TLabel",
        ).pack(anchor=W, pady=(4, 14))

        metrics = ttk.Frame(body)
        metrics.pack(fill=X, pady=(0, 16))
        metric_specs = (
            ("本年业务额", summary["yearly_business_minor"], "按收入确认日期"),
            ("本年已开票", summary["yearly_invoice_minor"], "按开票日期"),
            ("本年实际回款", summary["yearly_receipt_minor"], "按到账日期"),
            ("当前未回款", summary["current_receivable_minor"], "截至当前全部年度"),
        )
        for index, (label, value, hint) in enumerate(metric_specs):
            KpiCard(
                metrics,
                label,
                ttk.StringVar(value=self._money(value)),
                ttk.StringVar(value=hint),
            ).grid(
                row=0, column=index, sticky=EW,
                padx=(0 if index == 0 else 5, 0 if index == 3 else 5),
            )
            metrics.columnconfigure(index, weight=1)

        ttk.Label(
            body, text="项目业务明细", style="CardTitle.TLabel"
        ).pack(anchor=W, pady=(0, 8))
        project_table = DataTable(
            body,
            specs=(
                ("project", "项目", 220, W),
                ("status", "项目状态", 80, CENTER),
                ("business", "本年业务额", 110, E),
                ("invoice", "本年已开票", 110, E),
                ("receipt", "本年实际回款", 110, E),
                ("receivable", "当前未回款", 110, E),
                ("latest", "最近业务", 95, CENTER),
            ),
            empty_text=f"{year} 年暂无业务，且尚未关联项目",
            stretch=("project",),
            height=10,
            pack_fill=X,
            pack_expand=False,
        )
        project_table.refresh(
            detail["projects"],
            lambda row: (
                str(row["project_id"]),
                (
                    row["project_name"], row["project_status"],
                    self._money(row["yearly_business_minor"]),
                    self._money(row["yearly_invoice_minor"]),
                    self._money(row["yearly_receipt_minor"]),
                    self._money(row["current_receivable_minor"]),
                    row["latest_business_date"] or "—",
                ),
            ),
        )
        ttk.Button(
            footer, text="关闭", bootstyle="primary-outline",
            command=dialog.destroy,
        ).pack(side=RIGHT)

    def deactivate_selected(self):
        selected = self.table.tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择需要停用的客户")
            return
        if not messagebox.askyesno(
            "确认停用",
            f"确定停用选中的 {len(selected)} 个客户？\n历史项目、合同、回款仍会保留。",
        ):
            return
        master_data_service.deactivate_business_partners(
            [int(partner_id) for partner_id in selected]
        )
        self.load_data()

    @staticmethod
    def _entry(parent, variables, key, label, row, column, *, width=26):
        ttk.Label(parent, text=label).grid(
            row=row, column=column * 2, sticky=E, padx=(0, 10), pady=6
        )
        widget = ttk.Entry(parent, textvariable=variables[key], width=width)
        widget.grid(row=row, column=column * 2 + 1, sticky=EW, pady=6, padx=(0, 18))
        return widget

    def open_partner_dialog(self, partner_id=None):
        data = master_data_service.get_business_partner(partner_id) if partner_id else {}
        data = data or {}
        dialog = ttk.Toplevel(self.parent)
        dialog.title("修改客户档案" if partner_id else "新增客户档案")
        body, footer = build_form_dialog(
            dialog, self.parent, 860, 700, min_width=700, min_height=560
        )

        variables = {
            key: ttk.StringVar(value=str(data.get(key) or ""))
            for key in (
                "legal_name", "short_name", "unified_credit_code",
                "registered_address", "business_address", "invoice_phone",
                "bank_name", "bank_account", "contact_name", "contact_department",
                "contact_title", "contact_phone", "contact_email", "contact_wechat",
                "customer_category", "settlement_terms", "credit_limit", "notes",
                "entity_type",
            )
        }
        variables["short_name"].set(data.get("short_name") or "")
        variables["credit_limit"].set(str(data.get("credit_limit", 0) or 0))
        variables["entity_type"].set(
            ENTITY_TYPE_LABELS.get(data.get("entity_type", "enterprise"), "企业")
        )
        status_var = ttk.StringVar(
            value=STATUS_LABELS.get(data.get("status", "active"), "启用")
        )

        heading = ttk.Frame(body)
        heading.grid(row=0, column=0, sticky=EW, pady=(0, 12))
        ttk.Label(heading, text="主体与银行资料", style="CardTitle.TLabel").pack(side=LEFT)
        if not partner_id:
            ttk.Label(
                heading,
                text="同名供应商将自动合并为同一主体并增加客户角色",
                style="CardText.TLabel",
            ).pack(side=RIGHT)

        common = ttk.Frame(body, style="Card.TFrame", padding=(16, 10))
        common.grid(row=1, column=0, sticky=EW, pady=(0, 12))
        for column in range(4):
            common.columnconfigure(column, weight=1 if column % 2 else 0)
        name_entry = self._entry(common, variables, "legal_name", "客户名称 *", 0, 0)
        self._entry(common, variables, "short_name", "简称", 0, 1)
        self._entry(common, variables, "unified_credit_code", "统一社会信用代码", 1, 0)
        ttk.Label(common, text="档案状态").grid(row=1, column=2, sticky=E, padx=(0, 10), pady=6)
        ttk.Combobox(
            common, textvariable=status_var,
            values=("启用", "待确认", "已停用"), state="readonly", width=24,
        ).grid(row=1, column=3, sticky=EW, pady=6, padx=(0, 18))
        self._entry(common, variables, "registered_address", "注册地址", 2, 0)
        self._entry(common, variables, "business_address", "经营地址", 2, 1)
        self._entry(common, variables, "invoice_phone", "开票电话", 3, 0)
        self._entry(common, variables, "bank_name", "开户行", 3, 1)
        self._entry(common, variables, "bank_account", "银行账号", 4, 0)
        self._entry(common, variables, "notes", "备注", 4, 1)
        ttk.Label(common, text="主体类型").grid(
            row=5, column=0, sticky=E, padx=(0, 10), pady=6
        )
        ttk.Combobox(
            common, textvariable=variables["entity_type"],
            values=tuple(ENTITY_TYPE_LABELS.values()), state="readonly", width=24,
        ).grid(row=5, column=1, sticky=EW, pady=6, padx=(0, 18))

        customer = ttk.Frame(body, style="Card.TFrame", padding=(16, 10))
        customer.grid(row=2, column=0, sticky=EW, pady=(0, 12))
        ttk.Label(customer, text="客户资料", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky=W, pady=(0, 5)
        )
        customer.columnconfigure(1, weight=1)
        self._entry(customer, variables, "customer_category", "客户分类", 1, 0, width=24)
        self._entry(customer, variables, "settlement_terms", "结算条款", 2, 0, width=24)
        self._entry(customer, variables, "credit_limit", "信用额度（元）", 3, 0, width=24)

        contact = ttk.Frame(body, style="Card.TFrame", padding=(16, 10))
        contact.grid(row=3, column=0, sticky=EW)
        ttk.Label(contact, text="主要联系人", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky=W, pady=(0, 5)
        )
        for column in range(4):
            contact.columnconfigure(column, weight=1 if column % 2 else 0)
        self._entry(contact, variables, "contact_name", "姓名", 1, 0)
        self._entry(contact, variables, "contact_phone", "电话", 1, 1)
        self._entry(contact, variables, "contact_department", "部门", 2, 0)
        self._entry(contact, variables, "contact_title", "职务", 2, 1)
        self._entry(contact, variables, "contact_email", "邮箱", 3, 0)
        self._entry(contact, variables, "contact_wechat", "微信", 3, 1)

        body.columnconfigure(0, weight=1)

        def save():
            payload = {key: variable.get().strip() for key, variable in variables.items()}
            payload["entity_type"] = next(
                code for code, label in ENTITY_TYPE_LABELS.items()
                if label == variables["entity_type"].get()
            )
            payload["roles"] = data.get("roles") or {"customer"}
            payload["status"] = next(
                (code for code, label in STATUS_LABELS.items() if label == status_var.get()),
                "active",
            )
            try:
                if partner_id:
                    master_data_service.update_business_partner(partner_id, payload)
                else:
                    master_data_service.create_business_partner(payload)
            except Exception as error:
                messagebox.showwarning("无法保存", str(error), parent=dialog)
                return
            dialog.destroy()
            self.load_data()

        add_form_actions(
            footer, cancel_command=dialog.destroy,
            primary_text="保存客户档案", primary_command=save,
        )
        name_entry.focus_set()
