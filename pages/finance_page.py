from datetime import datetime
from tkinter import messagebox, filedialog

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import (
    collection_service,
    contract_service,
    finance_service,
    project_service,
)
from ui.components import (
    BottomToolbar,
    DataTable,
    DatePicker,
    FilterBar,
    KpiCard,
    PageHeader,
)
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders
from ui.attachments import open_attachment_manager


class ReceivablePage:
    """Sales invoices, receipts and project-level allocation."""

    def __init__(self, parent):
        self.parent = parent
        self.show_void_var = ttk.BooleanVar(value=False)
        self.invoice_queue_var = ttk.StringVar(value="全部发票")
        self.missing_attachments_var = ttk.BooleanVar(value=False)
        self.build_ui()
        safe_init_loaders("开票与回款", [self.refresh])

    @staticmethod
    def money(value):
        amount = int(value or 0) / 100
        return f"{'-' if amount < 0 else ''}¥{abs(amount):,.2f}"

    @staticmethod
    def selected_id(tree):
        tree = getattr(tree, "tree", tree)  # 兼容 DataTable 组件与裸 Treeview
        selected = tree.selection()
        return int(selected[0]) if len(selected) == 1 else None

    def build_ui(self):
        PageHeader(
            self.parent,
            "开票与回款",
            "总览看项目进度，发票和实际到账分别在明细中追溯",
            actions=[
                ttk.Button(
                    self.parent, text="登记销项发票", bootstyle="primary",
                    command=self.open_invoice_dialog,
                ),
                ttk.Button(
                    self.parent, text="登记回款", bootstyle="success",
                    command=self.open_receipt_dialog,
                ),
            ],
        )

        self.project_var = ttk.StringVar(value="全部项目")
        self.project_map = {}
        self.project_combo = ttk.Combobox(
            self.parent,
            textvariable=self.project_var,
            state="readonly",
            width=34,
        )
        self.project_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh()
        )
        FilterBar(
            self.parent,
            ("项目", self.project_combo),
            ttk.Label(
                self.parent,
                text="口径：有效结算、有效发票和实际回款",
                style="CardText.TLabel",
            ),
            actions=[
                ttk.Button(
                    self.parent, text="刷新", bootstyle="secondary-outline",
                    command=self.refresh,
                ),
            ],
        )

        self.notebook = ttk.Notebook(self.parent)
        self.notebook.pack(fill=BOTH, expand=True)
        overview_tab = ttk.Frame(self.notebook, padding=(0, 10, 0, 0))
        invoice_tab = ttk.Frame(self.notebook, padding=(0, 10, 0, 0))
        receipt_tab = ttk.Frame(self.notebook, padding=(0, 10, 0, 0))
        collection_tab = ttk.Frame(self.notebook, padding=(0, 10, 0, 0))
        self.notebook.add(overview_tab, text="资金总览")
        self.notebook.add(invoice_tab, text="销项发票")
        self.notebook.add(receipt_tab, text="回款记录")
        self.notebook.add(collection_tab, text="回款跟进")
        from pages.historical_receivable_page import HistoricalReceivablePage
        historical_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(historical_tab, text="历史旧账回收")
        self.historical_page = HistoricalReceivablePage(historical_tab)

        self._build_overview(overview_tab)
        self._build_collection_workbench(collection_tab)

        invoice_filters = ttk.Frame(invoice_tab)
        invoice_filters.pack(fill=X, pady=(0, 8))
        invoice_bar = BottomToolbar(
            invoice_tab,
            ttk.Button(
                invoice_tab, text="修改发票", bootstyle="primary-outline",
                command=self.edit_invoice,
            ),
            ttk.Button(
                invoice_tab, text="作废发票", bootstyle="danger-outline",
                command=self.void_invoice,
            ),
            ttk.Button(
                invoice_tab, text="发票附件", bootstyle="secondary-outline",
                command=self.open_invoice_attachments,
            ),
        )
        ttk.Checkbutton(
            invoice_tab,
            text="显示已作废",
            variable=self.show_void_var,
            bootstyle="round-toggle",
            command=self.refresh,
        ).pack(in_=invoice_filters, side=RIGHT)
        invoice_queue_combo = ttk.Combobox(
            invoice_tab,
            textvariable=self.invoice_queue_var,
            values=("全部发票", "未结清", "已结清"),
            state="readonly",
            width=10,
        )
        invoice_queue_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh()
        )
        invoice_queue_combo.pack(in_=invoice_filters, side=RIGHT, padx=(8, 12))
        ttk.Label(invoice_tab, text="发票队列").pack(
            in_=invoice_filters, side=RIGHT
        )
        ttk.Checkbutton(
            invoice_tab, text="仅看附件待补", variable=self.missing_attachments_var,
            command=self.refresh,
        ).pack(in_=invoice_filters, side=LEFT, padx=8)
        self.invoice_tree = self._table(
            invoice_tab,
            (
                ("no", "发票号码", 150, W),
                ("date", "开票日期", 95, CENTER),
                ("attachment", "附件", 120, CENTER),
                ("project", "项目", 150, W),
                ("amount", "价税合计", 120, E),
                ("received", "已核销回款", 120, E),
                ("balance", "发票余额", 120, E),
                ("status", "回款状态", 85, CENTER),
                ("buyer", "购买方", 150, W),
                ("tax", "税率", 70, E),
                ("contract", "合同", 135, W),
                ("settlement", "收入确认", 150, W),
            ),
            empty_text="暂无销项发票，点击右上角「登记销项发票」",
            stretch=("no", "project", "contract", "settlement", "buyer"),
        )
        self.invoice_tree.tree.bind("<Double-1>", lambda _event: self.edit_invoice())

        BottomToolbar(
            receipt_tab,
            ttk.Button(
                receipt_tab, text="登记到账账户", bootstyle="primary-outline",
                command=self.record_fund_receipt,
            ),
            ttk.Button(
                receipt_tab, text="修改回款", bootstyle="primary-outline",
                command=self.edit_receipt,
            ),
            ttk.Button(
                receipt_tab, text="作废回款", bootstyle="danger-outline",
                command=self.void_receipt,
            ),
            ttk.Button(
                receipt_tab, text="回款附件", bootstyle="secondary-outline",
                command=self.open_receipt_attachments,
            ),
        )
        self.receipt_tree = self._table(
            receipt_tab,
            (
                ("date", "回款日期", 100, CENTER),
                ("project", "项目", 180, W),
                ("amount", "回款金额", 130, E),
                ("status", "收入归属", 120, CENTER),
                ("method", "收款方式", 100, CENTER),
                ("invoice", "核销发票", 145, W),
                ("matched", "已抵发票", 115, E),
                ("unmatched", "待匹配发票", 115, E),
                ("settlement", "收入确认", 165, W),
                ("payer", "付款方", 175, W),
                ("contract", "合同 / 业务类型", 150, W),
                ("no", "回款单号", 165, W),
            ),
            empty_text="暂无回款记录，点击右上角「登记回款」",
            stretch=(
                "no", "project", "contract", "invoice", "settlement", "payer"
            ),
        )
        self.receipt_tree.tree.bind(
            "<Double-1>", lambda _event: self.edit_receipt()
        )

    def _build_overview(self, parent):
        self.kpi_vars = {
            key: ttk.StringVar(value="¥0.00")
            for key in ("settlement", "invoice", "receipt")
        }
        self.kpi_hint_vars = {
            key: ttk.StringVar() for key in self.kpi_vars
        }
        kpi_grid = ttk.Frame(parent)
        kpi_grid.pack(fill=X, pady=(0, 12))
        for index, (key, label) in enumerate(
            (
                ("settlement", "确认收入"),
                ("invoice", "开票进度"),
                ("receipt", "回款进度"),
            )
        ):
            KpiCard(
                kpi_grid, label, self.kpi_vars[key], self.kpi_hint_vars[key]
            ).grid(
                row=0,
                column=index,
                sticky=NSEW,
                padx=(0 if index == 0 else 6, 0 if index == 2 else 6),
            )
            kpi_grid.columnconfigure(index, weight=1)

        self.collection_notebook = ttk.Notebook(parent)
        self.collection_notebook.pack(fill=BOTH, expand=True)
        self.collection_trees = {}
        self.collection_status_vars = {}
        for queue, label in (("pending", "待回款"), ("settled", "已结清")):
            tab = ttk.Frame(
                self.collection_notebook, padding=(0, 8, 0, 0)
            )
            self.collection_notebook.add(tab, text=label)
            self._build_collection_panel(tab, queue)
        self.collection_notebook.bind(
            "<<NotebookTabChanged>>", lambda _event: self.refresh()
        )

    def _build_collection_workbench(self, parent):
        self.collection_view_var = ttk.StringVar(value="项目视图")
        self.collection_action_filter_var = ttk.StringVar(value="全部应收")
        self.collection_customer_filter = None
        self.collection_project_rows = []
        self.collection_customer_rows = []
        self.collection_hint_var = ttk.StringVar()

        view_combo = ttk.Combobox(
            parent,
            textvariable=self.collection_view_var,
            values=("项目视图", "客户视图"),
            state="readonly",
            width=10,
        )
        view_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._refresh_collection_workbench(),
        )
        action_combo = ttk.Combobox(
            parent,
            textvariable=self.collection_action_filter_var,
            values=("全部应收", "今日需跟进", "承诺逾期", "90天以上"),
            state="readonly",
            width=12,
        )
        action_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._refresh_collection_workbench(),
        )
        FilterBar(
            parent,
            ("查看", view_combo),
            ("筛选", action_combo),
            ttk.Label(
                parent,
                textvariable=self.collection_hint_var,
                style="CardText.TLabel",
            ),
        )
        actions = ttk.Frame(parent)
        actions.pack(fill=X, pady=(0, 8))
        for text, style, command in (
            ("更新跟进", "primary-outline", self.open_collection_case_dialog),
            ("查看跟进历史", "secondary-outline", self.open_collection_history),
            ("显示全部客户", "secondary-outline", self.clear_collection_customer_filter),
        ):
            ttk.Button(
                actions,
                text=text,
                bootstyle=style,
                command=command,
            ).pack(side=RIGHT, padx=(6, 0))

        self.collection_table_host = ttk.Frame(parent)
        self.collection_table_host.pack(fill=BOTH, expand=True)
        self.collection_project_tree = self._table(
            self.collection_table_host,
            (
                ("project", "项目 / 客户", 320, W),
                ("receivable", "未回款", 120, E),
                ("schedule", "承诺 / 跟进", 180, CENTER),
                ("owner", "责任人", 90, CENTER),
                ("status", "状态 / 账龄", 130, CENTER),
                ("action", "下一步动作", 240, W),
            ),
            empty_text="当前没有待回款项目",
            stretch=("project", "action"),
            padding=10,
        )
        self.collection_project_tree.tree.bind(
            "<Double-1>", lambda _event: self.open_collection_case_dialog()
        )
        self.collection_customer_tree = self._table(
            self.collection_table_host,
            (
                ("customer", "客户", 280, W),
                ("receivable", "未回款", 130, E),
                ("aging", "最老账龄", 90, CENTER),
                ("followup", "最近应跟进", 110, CENTER),
                ("owner", "责任人", 100, CENTER),
                ("status", "当前状态", 120, CENTER),
                ("action", "下一步动作", 250, W),
            ),
            empty_text="当前没有待回款客户",
            stretch=("customer", "action"),
            padding=10,
        )
        self.collection_customer_tree.tree.bind(
            "<Double-1>", lambda _event: self.drill_collection_customer()
        )
        self.collection_customer_tree.pack_forget()

    def _collection_filtered_rows(self):
        rows = list(self.collection_project_rows)
        if self.collection_customer_filter is not None:
            rows = [
                row for row in rows
                if row["customer_key"] == self.collection_customer_filter
            ]
        filter_label = self.collection_action_filter_var.get()
        if filter_label == "今日需跟进":
            rows = [row for row in rows if row["needs_action_today"]]
        elif filter_label == "承诺逾期":
            rows = [
                row for row in rows
                if row["attention_status"] == "承诺逾期"
            ]
        elif filter_label == "90天以上":
            rows = [
                row for row in rows
                if (row["aging_days"] or 0) > 90
            ]
        return rows

    def _refresh_collection_workbench(self, project_id=None):
        if not hasattr(self, "collection_project_tree"):
            return
        if project_id is None:
            project_id = self.selected_project_id()
        if project_id is not None:
            self.collection_customer_filter = None
        self.collection_project_rows = collection_service.list_project_cases(
            project_id
        )
        rows = self._collection_filtered_rows()
        summary = collection_service.summarize_project_cases(rows)
        self.collection_hint_var.set(
            f"{summary['project_count']} 个项目 · "
            f"未回 {self.money(summary['receivable_minor'])} · "
            f"今日需处理 {summary['action_count']} 个"
        )
        self.notebook.tab(3, text=f"回款跟进 · {len(self.collection_project_rows)}")

        if self.collection_view_var.get() == "客户视图":
            self.collection_project_tree.pack_forget()
            self.collection_customer_tree.pack(fill=BOTH, expand=True)
            self.collection_customer_rows = collection_service.list_customer_cases(
                rows
            )
            self.collection_customer_tree.refresh(
                self.collection_customer_rows,
                lambda row: (row["customer_key"], (
                    f"{row['customer_name']} · {row['project_count']}个项目",
                    self.money(row["receivable_minor"]),
                    row["aging_bucket"],
                    row["next_followup_date"] or "未安排",
                    row["owner_name"] or "未指定",
                    (
                        f"{row['attention_status']} · {row['action_count']}项"
                        if row["action_count"]
                        else row["attention_status"]
                    ),
                    row["next_action"] or "未填写",
                )),
            )
            return

        self.collection_customer_tree.pack_forget()
        self.collection_project_tree.pack(fill=BOTH, expand=True)
        self.collection_project_tree.refresh(
            rows,
            lambda row: (str(row["project_id"]), (
                f"{row['project_name']} · {row['customer_name']}",
                self.money(row["receivable_minor"]),
                (
                    f"{row['promised_date'] or '未承诺'} / "
                    f"{row['next_followup_date'] or '未安排'}"
                ),
                row["owner_name"] or "未指定",
                f"{row['attention_status']} · {row['aging_bucket']}",
                row["next_action"] or "未填写",
            )),
        )

    def clear_collection_customer_filter(self):
        self.collection_customer_filter = None
        self._refresh_collection_workbench()

    def drill_collection_customer(self):
        selected = self.collection_customer_tree.tree.selection()
        if len(selected) != 1:
            return
        key = selected[0]
        customer = next(
            (
                row for row in self.collection_customer_rows
                if row["customer_key"] == key
            ),
            None,
        )
        if not customer:
            return
        self.collection_customer_filter = customer["customer_key"]
        self.collection_view_var.set("项目视图")
        self._refresh_collection_workbench()

    def _selected_collection_project_id(self):
        if self.collection_view_var.get() != "项目视图":
            return None
        return self.selected_id(self.collection_project_tree)

    def open_collection_case_dialog(self):
        project_id = self._selected_collection_project_id()
        if not project_id:
            messagebox.showwarning(
                "提示", "请在项目视图中选择一个待回款项目"
            )
            return
        case = collection_service.get_project_case(project_id)
        if not case or case["receivable_minor"] <= 0:
            messagebox.showwarning("提示", "该项目当前没有待回款余额")
            return

        dialog = ttk.Toplevel(self.parent)
        dialog.title("更新回款跟进")
        body, footer = build_form_dialog(
            dialog, self.parent, 840, 700, min_width=700, min_height=560
        )
        status_labels = {
            label: code
            for code, label in collection_service.CASE_STATUSES.items()
            if code != "closed"
        }
        variables = {
            "due_date": ttk.StringVar(
                value=case["due_date"] or case["oldest_unpaid_date"] or ""
            ),
            "promised_date": ttk.StringVar(value=case["promised_date"] or ""),
            "next_followup_date": ttk.StringVar(
                value=case["next_followup_date"] or ""
            ),
            "owner_name": ttk.StringVar(value=case["owner_name"] or ""),
            "status": ttk.StringVar(
                value=collection_service.CASE_STATUSES.get(
                    case["case_status"], "待跟进"
                )
            ),
            "followup_date": ttk.StringVar(
                value=datetime.now().strftime("%Y-%m-%d")
            ),
        }

        summary = ttk.Frame(body, style="Card.TFrame", padding=14)
        summary.pack(fill=X)
        ttk.Label(
            summary, text=case["project_name"], style="CardTitle.TLabel"
        ).pack(anchor=W)
        ttk.Label(
            summary,
            text=(
                f"{case['customer_name']} · 未回 {self.money(case['receivable_minor'])}"
                f" · {case['aging_bucket']} · {case['attention_status']}"
            ),
            style="CardText.TLabel",
        ).pack(anchor=W, pady=(4, 0))

        plan = ttk.Frame(body, style="Card.TFrame", padding=14)
        plan.pack(fill=X, pady=(12, 0))
        ttk.Label(plan, text="跟进计划", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky=W, pady=(0, 8)
        )
        fields = (
            ("应收到期日", "due_date", "date"),
            ("客户承诺付款日", "promised_date", "date"),
            ("下次跟进日", "next_followup_date", "date"),
            ("责任人", "owner_name", "text"),
            ("跟进状态", "status", "status"),
            ("本次跟进日期", "followup_date", "date"),
        )
        for index, (label, key, kind) in enumerate(fields):
            field = ttk.Frame(plan, style="Card.TFrame")
            field.grid(
                row=index // 2 + 1,
                column=index % 2,
                sticky=EW,
                padx=(0, 10) if index % 2 == 0 else (10, 0),
                pady=6,
            )
            ttk.Label(field, text=label, style="CardText.TLabel").pack(anchor=W)
            if kind == "date":
                widget = DatePicker(
                    field,
                    textvariable=variables[key],
                    allow_empty=key != "followup_date",
                    popup_title=f"选择{label}",
                )
                widget.pack(fill=X, pady=(4, 0))
            elif kind == "status":
                ttk.Combobox(
                    field,
                    textvariable=variables[key],
                    values=tuple(status_labels),
                    state="readonly",
                ).pack(fill=X, pady=(4, 0), ipady=4)
            else:
                ttk.Entry(field, textvariable=variables[key]).pack(
                    fill=X, pady=(4, 0), ipady=4
                )
        plan.columnconfigure(0, weight=1)
        plan.columnconfigure(1, weight=1)

        details = ttk.Frame(body, style="Card.TFrame", padding=14)
        details.pack(fill=BOTH, expand=True, pady=(12, 0))
        text_fields = {}
        for label, key, height, initial in (
            ("本次跟进记录（未联系可留空）", "followup_content", 3, ""),
            ("下一步动作", "next_action", 2, case["next_action"] or ""),
            ("逾期原因", "overdue_reason", 2, case["overdue_reason"] or ""),
            ("补充说明", "notes", 2, case["notes"] or ""),
        ):
            ttk.Label(details, text=label, style="CardText.TLabel").pack(
                anchor=W, pady=(8 if text_fields else 0, 4)
            )
            widget = ttk.Text(details, height=height, wrap="word")
            widget.pack(fill=X)
            widget.insert("1.0", initial)
            text_fields[key] = widget

        form_error = ttk.StringVar()
        ttk.Label(
            body,
            textvariable=form_error,
            style="FormError.TLabel",
            wraplength=700,
        ).pack(anchor=W, pady=(8, 0))

        def save():
            form_error.set("")
            try:
                collection_service.save_project_case(
                    project_id,
                    {
                        "due_date": variables["due_date"].get(),
                        "promised_date": variables["promised_date"].get(),
                        "next_followup_date": variables["next_followup_date"].get(),
                        "owner_name": variables["owner_name"].get(),
                        "status": status_labels[variables["status"].get()],
                        "followup_date": variables["followup_date"].get(),
                        **{
                            key: widget.get("1.0", END).strip()
                            for key, widget in text_fields.items()
                        },
                    },
                )
            except Exception as error:
                form_error.set(str(error))
                return
            dialog.destroy()
            self.refresh()

        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text="保存跟进",
            primary_command=save,
        )

    def open_collection_history(self):
        project_id = self._selected_collection_project_id()
        if not project_id:
            messagebox.showwarning(
                "提示", "请在项目视图中选择一个待回款项目"
            )
            return
        case = collection_service.get_project_case(project_id)
        logs = collection_service.list_followup_logs(project_id)
        dialog = ttk.Toplevel(self.parent)
        dialog.title("回款跟进历史")
        body, footer = build_form_dialog(
            dialog, self.parent, 920, 560, min_width=760, min_height=480
        )
        ttk.Label(
            body,
            text=(
                f"{case['project_name']} · {case['customer_name']} · "
                f"未回 {self.money(case['receivable_minor'])}"
            ),
            style="CardTitle.TLabel",
        ).pack(anchor=W, pady=(0, 8))
        history = self._table(
            body,
            (
                ("date", "跟进日期", 100, CENTER),
                ("content", "跟进内容", 300, W),
                ("promised", "承诺付款日", 105, CENTER),
                ("next", "下次跟进", 100, CENTER),
                ("action", "下一步动作", 240, W),
            ),
            empty_text="尚无跟进历史",
            stretch=("content", "action"),
            padding=0,
        )
        history.refresh(
            logs,
            lambda row: (str(row["id"]), (
                row["followup_date"],
                row["content"],
                row["promised_date"] or "—",
                row["next_followup_date"] or "—",
                row["next_action"] or "—",
            )),
        )
        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text="关闭",
            primary_command=dialog.destroy,
        )

    def _build_collection_panel(self, parent, queue):
        project_card = ttk.Frame(
            parent, style="Card.TFrame", padding=(10, 8)
        )
        project_card.pack(fill=BOTH, expand=True)
        project_header = ttk.Frame(project_card, style="Card.TFrame")
        project_header.pack(fill=X, pady=(0, 6))
        ttk.Label(
            project_header,
            text=("待收项目资金进度" if queue == "pending" else "已结清项目"),
            style="CardTitle.TLabel",
        ).pack(side=LEFT)
        status_var = ttk.StringVar()
        self.collection_status_vars[queue] = status_var
        ttk.Button(project_header, text='收款完毕', bootstyle='primary-outline',
                   command=lambda: self.open_cash_closure(queue)).pack(side=LEFT, padx=(12, 4))
        ttk.Button(project_header, text='结清记录 / 撤销', bootstyle='secondary-outline',
                   command=lambda: self.open_cash_closure(queue, history=True)).pack(side=LEFT, padx=4)
        ttk.Label(
            project_header,
            textvariable=status_var,
            style="CardText.TLabel",
        ).pack(side=RIGHT)
        project_tree = self._table(
            project_card,
            (
                ("project", "项目", 180, W),
                ("mode", "业务类型", 90, CENTER),
                ("settlement", "确认收入", 120, E),
                ("invoice", "开票进度", 180, E),
                ("receipt", "回款进度", 180, E),
                ("receivable", "未回款", 120, E),
                ("status", "回款状态", 90, CENTER),
            ),
            empty_text=(
                "当前没有待回款或部分回款项目"
                if queue == "pending" else "当前没有已结清项目"
            ),
            stretch=("project",),
            padding=0,
            expand=True,
        )
        project_tree.tree.bind(
            "<Double-1>",
            lambda event, source=project_tree: self._filter_selected_project(
                event, source
            ),
        )
        self.collection_trees[queue] = project_tree

    def open_cash_closure(self, queue, history=False):
        from services import cash_closure_service
        selected = self.collection_trees[queue].tree.selection()
        if not selected:
            messagebox.showinfo('提示', '请先在下方选择一个零星工程项目', parent=self.parent)
            return
        project_id = int(selected[0])
        try:
            if history:
                records = cash_closure_service.history(project_id)
                if not records:
                    messagebox.showinfo('结清记录', '该项目尚无抹零结清记录', parent=self.parent)
                    return
                active = next((r for r in records if r['status'] == 'active'), None)
                text = '\n'.join(
                    f"{r['created_at'][:10]} · {r['reason']} · 调减 {self.money(r['reduction_minor'])}"
                    f" · {'有效' if r['status'] == 'active' else '已撤销'}\n{r['notes']}"
                    for r in records)
                if not active:
                    messagebox.showinfo('结清历史', text, parent=self.parent)
                elif messagebox.askyesno('结清记录 / 撤销', text + '\n\n是否撤销当前结清调整？\n'
                                        '将恢复调整前的确认收入和应收差额，实际回款不变。', parent=self.parent):
                    cash_closure_service.revoke(active['id'])
                    self.refresh()
                return
            data = cash_closure_service.preview(project_id)
        except Exception as error:
            messagebox.showwarning('无法结清', str(error), parent=self.parent)
            return
        dialog = ttk.Toplevel(self.parent)
        dialog.title('收款完毕 · 确认最终结算')
        body, footer = build_form_dialog(dialog, self.parent, 650, 500, min_width=550, min_height=400)
        text = (f"项目：{data['project_name']}\n\n"
                f"原确认收入：{self.money(data['original_income_minor'])}\n"
                f"累计实际收到：{self.money(data['received_minor'])}\n"
                f"不再收取的差额：{self.money(data['reduction_minor'])}\n\n"
                '确认后按实收金额调减收入，应收归零；实际回款及施工状态不变。')
        ttk.Label(body, text=text, wraplength=500, justify=LEFT).pack(fill=X, pady=10)
        ttk.Label(body, text='结清原因 *').pack(anchor=W)
        reason = ttk.StringVar(value='抹零')
        ttk.Combobox(body, textvariable=reason, values=cash_closure_service.REASONS,
                     state='readonly').pack(fill=X, pady=6)
        ttk.Label(body, text='备注').pack(anchor=W)
        notes = ttk.Entry(body)
        notes.pack(fill=X, pady=6)
        confirmed = ttk.BooleanVar(value=False)
        ttk.Checkbutton(body, text='我确认剩余差额不再收取，按上述实收金额结清',
                        variable=confirmed).pack(anchor=W, pady=12)

        def save():
            if not confirmed.get():
                messagebox.showwarning('请确认', '请先核对差额并勾选确认', parent=dialog)
                return
            try:
                cash_closure_service.close_collection(project_id, reason.get(), data, notes.get())
            except Exception as error:
                messagebox.showwarning('无法结清', str(error), parent=dialog)
                return
            dialog.destroy()
            self.refresh()
            self.collection_notebook.select(1)

        add_form_actions(footer, cancel_command=dialog.destroy,
                         primary_text='确认结清', primary_command=save)

    def _current_collection_queue(self):
        index = self.collection_notebook.index(
            self.collection_notebook.select()
        )
        return ("pending", "settled")[index]

    @staticmethod
    def _table(parent, specs, *, empty_text="暂无数据", stretch=None,
               padding=10, height=None, expand=True):
        """DataTable 包装：支持 padding/height/expand，与原 _tree 助手同契约。"""
        table = DataTable(
            parent, specs=specs, empty_text=empty_text,
            stretch=stretch, padding=padding,
        )
        table.pack_configure(fill=BOTH if expand else X, expand=expand)
        if height is not None:
            table.tree.configure(height=height)
        return table

    @staticmethod
    def _percent(value):
        return "—" if value is None else f"{value:.1f}%"

    def _refresh_project_options(self):
        current = self.project_var.get()
        self.project_map = {"全部项目": None}
        self.project_map.update(
            {
                f"{row['name']} · {row['project_code']}": row["id"]
                for row in project_service.list_projects()
            }
        )
        self.project_combo.configure(values=list(self.project_map))
        if current not in self.project_map:
            self.project_var.set("全部项目")

    def selected_project_id(self):
        return self.project_map.get(self.project_var.get())

    def _filter_selected_project(self, _event=None, source=None):
        project_tree = source or self.collection_trees[
            self._current_collection_queue()
        ]
        selected = project_tree.tree.selection()
        if len(selected) != 1:
            return
        project_id = int(selected[0])
        label = next(
            (
                label
                for label, mapped_id in self.project_map.items()
                if mapped_id == project_id
            ),
            None,
        )
        if label:
            self.project_var.set(label)
            self.refresh()

    def refresh(self):
        self._refresh_project_options()
        project_id = self.selected_project_id()
        dashboard = finance_service.get_finance_dashboard(project_id)
        invoices = finance_service.list_invoices(project_id)
        displayed_invoices = finance_service.list_invoices(
            project_id, include_void=self.show_void_var.get()
        )
        invoice_queue = self.invoice_queue_var.get()
        if invoice_queue == "未结清":
            displayed_invoices = [
                row for row in displayed_invoices
                if row["status"] == "active" and row["unreceived_minor"] > 0
            ]
        elif invoice_queue == "已结清":
            displayed_invoices = [
                row for row in displayed_invoices
                if row["status"] == "active" and row["unreceived_minor"] == 0
            ]
        if self.missing_attachments_var.get():
            displayed_invoices = [row for row in displayed_invoices if row["attachment_needs_attention"]]
        receipts = finance_service.list_receipts(project_id)
        finance_projects = [
            row
            for row in dashboard["projects"]
            if any(
                row[key]
                for key in (
                    "settlement_minor",
                    "invoice_minor",
                    "receipt_minor",
                )
            )
        ]

        collection_groups = {
            "pending": [
                row for row in finance_projects
                if row["collection_status"] != "已结清"
            ],
            "settled": [
                row for row in finance_projects
                if row["collection_status"] == "已结清"
            ],
        }

        def project_mapper(row):
            return str(row["project_id"]), (
                row["project_name"],
                (
                    "零星工程" if row["business_mode"] == "cash"
                    else "合同工程"
                ),
                self.money(row["settlement_minor"]),
                (
                    "无需开票" if row["invoice_policy"] == "not_required"
                    else f"{self.money(row['invoice_minor'])} · "
                    f"{self._percent(row['invoice_rate_percent'])}"
                ),
                f"{self.money(row['receipt_minor'])} · "
                f"{self._percent(row['receipt_rate_percent'])}",
                self.money(row["receivable_minor"]),
                row["collection_status"],
            )

        for queue, rows in collection_groups.items():
            self.collection_trees[queue].refresh(rows, project_mapper)
            tab_index = 0 if queue == "pending" else 1
            tab_label = "待回款" if queue == "pending" else "已结清"
            self.collection_notebook.tab(
                tab_index, text=f"{tab_label} · {len(rows)}"
            )
            self.collection_status_vars[queue].set(
                f"{len(rows)} 个项目 · 双击项目筛选明细"
                if rows else (
                    "当前没有待收项目"
                    if queue == "pending" else "当前没有结清项目"
                )
            )

        current_projects = collection_groups[self._current_collection_queue()]
        summary = finance_service.summarize_finance_projects(current_projects)
        self.kpi_vars["settlement"].set(self.money(summary["settlement_minor"]))
        self.kpi_vars["invoice"].set(self.money(summary["invoice_minor"]))
        self.kpi_vars["receipt"].set(self.money(summary["receipt_minor"]))
        self.kpi_hint_vars["settlement"].set(
            f"当前页签 {summary['project_count']} 个有资金数据的项目"
        )
        self.kpi_hint_vars["invoice"].set(
            f"开票率 {self._percent(summary['invoice_rate_percent'])} · "
            f"待开 {self.money(summary['uninvoiced_minor'])}"
        )
        pending_hint = (
            f" · 预收 {self.money(summary['advance_minor'])}"
            f" · 历史待分配 {self.money(summary['pending_receipt_minor'] - summary['advance_minor'])}"
            if summary["pending_receipt_minor"] else ""
        )
        self.kpi_hint_vars["receipt"].set(
            f"回款率 {self._percent(summary['receipt_rate_percent'])} · "
            f"未回 {self.money(summary['receivable_minor'])}{pending_hint}"
        )

        missing_attachment_count = sum(
            row["attachment_needs_attention"] for row in invoices
        )
        self.notebook.tab(
            1,
            text=(
                f"销项发票 · {len(invoices)}"
                f" · 附件待补 {missing_attachment_count}"
            ),
        )
        self.invoice_tree.refresh(
            displayed_invoices,
            lambda row: (str(row["id"]), (
                row["invoice_no"],
                row["invoice_date"],
                row["attachment_status"],
                row["project_name"],
                self.money(row["amount_minor"]),
                self.money(row["received_minor"]),
                self.money(row["unreceived_minor"]),
                row["collection_status"],
                row["buyer_name_snapshot"],
                row['tax_rate_label'] or f"{row['tax_rate_bps'] / 100:g}%",
                row["contract_no"],
                row["settlement_no"] or "未关联",
            )),
        )
        self.notebook.tab(2, text=f"回款记录 · {len(receipts)}")
        self.receipt_tree.refresh(
            receipts,
            lambda row: (str(row["id"]), (
                row["receipt_date"],
                row["project_name"],
                self.money(row["allocated_amount_minor"]),
                row["allocation_status"],
                row["payment_method"],
                (
                    row["invoice_no"] or "无需发票"
                    if row["business_mode"] == "cash"
                    else row["invoice_no"] or "等待后续开票"
                ),
                (
                    "—" if row["business_mode"] == "cash"
                    else self.money(row["invoice_matched_minor"])
                ),
                (
                    "—" if row["business_mode"] == "cash"
                    else self.money(row["invoice_unmatched_minor"])
                ),
                row["settlement_no"] or ('预收款' if row.get('automatic_income_allocation') else '待分配'),
                row["payer_name_snapshot"],
                row["contract_no"] or "零星现金工程",
                row["receipt_no"],
            )),
        )
        self._refresh_collection_workbench(project_id)

    def _allocation_map(self):
        return {
            f"{row['contract_no']} → {row['project_name']}": row
            for row in contract_service.list_allocations(
                project_id=self.selected_project_id()
            )
        }

    def _settlement_map(self):
        return {
            (
                f"{row['project_name']} · {row['settlement_no']} · "
                f"{row['settlement_date']}"
            ): row
            for row in contract_service.list_settlements(
                project_id=self.selected_project_id()
            )
        }

    def _invoice_target_map(self):
        targets = {}
        for settlement in contract_service.list_settlements(
            project_id=self.selected_project_id()
        ):
            if settlement["invoice_policy"] == "not_required":
                continue
            key = (settlement["project_id"], settlement["contract_id"])
            target = targets.setdefault(
                key,
                {
                    "project_id": settlement["project_id"],
                    "contract_id": settlement["contract_id"],
                    "project_name": settlement["project_name"],
                    "contract_no": settlement["contract_no"],
                    "settlement_count": 0,
                    "amount_minor": 0,
                    "invoiced_minor": 0,
                },
            )
            target["settlement_count"] += 1
            target["amount_minor"] += int(settlement["amount_minor"] or 0)
            target["invoiced_minor"] += int(
                settlement["invoiced_minor"] or 0
            )
        for allocation in contract_service.list_allocations(
            project_id=self.selected_project_id()
        ):
            if (allocation['income_mode'] != 'invoice'
                    or allocation['contract_status'] == 'void'
                    or allocation['invoice_policy'] == 'not_required'):
                continue
            key = (allocation['project_id'], allocation['contract_id'])
            target = targets.setdefault(key, {
                'project_id': allocation['project_id'],
                'contract_id': allocation['contract_id'],
                'project_name': allocation['project_name'],
                'contract_no': allocation['contract_no'],
                'settlement_count': 0, 'amount_minor': 0, 'invoiced_minor': 0,
            })
            target['income_mode'] = 'invoice'
        result = {}
        for target in targets.values():
            target["uninvoiced_minor"] = max(
                target["amount_minor"] - target["invoiced_minor"], 0
            )
            contract_label = target["contract_no"] or "未关联合同"
            result[
                f"{target['project_name']} · {contract_label}"
            ] = target
        return result

    def open_invoice_dialog(self, invoice_id=None):
        target_map = self._invoice_target_map()
        if not target_map:
            messagebox.showwarning("提示", "请先登记有效的收入确认")
            return
        invoice = finance_service.get_invoice(invoice_id) if invoice_id else None
        if invoice_id and not invoice:
            messagebox.showwarning("提示", "发票记录不存在")
            return
        restoring = bool(invoice and invoice["status"] == "void")
        prompt = "请选择开票项目 / 合同"
        if invoice:
            target_label = next(
                (
                    label
                    for label, target in target_map.items()
                    if target["project_id"] == invoice["project_id"]
                    and target["contract_id"] == invoice["contract_id"]
                ),
                None,
            )
        else:
            target_label = (
                next(iter(target_map)) if len(target_map) == 1 else None
            )
        if restoring:
            dialog_title = "恢复并修改销项发票"
            primary_text = "恢复并保存"
            success_message = "发票已恢复并更新。"
        elif invoice:
            dialog_title = "修改销项发票"
            primary_text = "保存修改"
            success_message = "发票已更新。"
        else:
            dialog_title = "登记销项发票"
            primary_text = "保存发票"
            success_message = "发票已登记。"
        dialog = ttk.Toplevel(self.parent)
        dialog.title(dialog_title)
        body, footer = build_form_dialog(
            dialog, self.parent, 700, 590, min_width=590, min_height=450
        )
        initial_tax_rate = '0'
        if invoice:
            initial_tax_rate = f"{invoice['tax_rate_bps'] / 100:g}"
            label = invoice['tax_rate_label']
            if ' / ' in label or label == '多税率':
                initial_tax_rate = '多税率'
            elif label in ('免税', '不征税'):
                initial_tax_rate = label
        variables = {
            "target": ttk.StringVar(value=target_label or prompt),
            "no": ttk.StringVar(value=invoice["invoice_no"] if invoice else ""),
            "date": ttk.StringVar(
                value=invoice["invoice_date"]
                if invoice else datetime.now().strftime("%Y-%m-%d")
            ),
            "amount": ttk.StringVar(
                value=f"{invoice['amount_minor'] / 100:.2f}" if invoice else ""
            ),
            "tax": ttk.StringVar(value=initial_tax_rate),
            'net': ttk.StringVar(value=f"{invoice['net_amount_minor']/100:.2f}" if invoice and invoice['net_amount_minor'] is not None else ''),
            'tax_amount': ttk.StringVar(value=f"{invoice['tax_amount_minor']/100:.2f}" if invoice and invoice['tax_amount_minor'] is not None else ''),
            "buyer": ttk.StringVar(
                value=invoice["buyer_name_snapshot"] if invoice else ""
            ),
        }
        specs = (
            ("开票项目 / 合同 *", "target"),
            ("发票号码（不可重复）", "no"),
            ("开票日期 *", "date"),
            ("价税合计（元）*", "amount"),
            ("未税金额（元）", "net"),
            ("票面税额（元）", "tax_amount"),
            ("税率（% / 多税率 / 免税）", "tax"),
            ("购买方", "buyer"),
        )
        target_hint_var = ttk.StringVar()
        target_combo = None
        pdf_state = {'recognition': None, 'busy': False}
        pdf_confirmed = ttk.BooleanVar(value=False)
        pdf_hint = ttk.StringVar(value='本地识别，不上传；支持有文字的电子发票PDF。旧票税额未知可留空。')
        import_box = ttk.Frame(body)
        import_box.grid(row=0, column=0, columnspan=2, sticky=EW, pady=(0, 12))
        import_button = ttk.Button(import_box, text='选择 PDF 自动识别', bootstyle='primary-outline')
        import_button.pack(anchor=W)
        ttk.Label(import_box, textvariable=pdf_hint, wraplength=520, justify=LEFT).pack(fill=X, pady=6)
        confirm_widget = ttk.Checkbutton(import_box, text='已核对票号、日期、金额、税额及项目，随保存留存PDF附件', variable=pdf_confirmed)
        for row, (label, key) in enumerate(specs, 1):
            ttk.Label(body, text=label).grid(
                row=row, column=0, sticky=E, padx=(0, 12), pady=7
            )
            if key == "target":
                field = ttk.Frame(body)
                target_combo = ttk.Combobox(
                    field,
                    textvariable=variables[key],
                    values=[prompt, *target_map],
                    state="readonly",
                )
                target_combo.pack(fill=X, ipady=4)
                ttk.Label(
                    field,
                    textvariable=target_hint_var,
                    style="CardText.TLabel",
                ).pack(anchor=W, pady=(5, 0))
                field.grid(row=row, column=1, sticky=EW, pady=7)
            elif key == "date":
                DatePicker(
                    body,
                    textvariable=variables[key],
                    popup_title="选择开票日期",
                ).grid(row=row, column=1, sticky=EW, pady=7)
            else:
                ttk.Entry(
                    body, textvariable=variables[key]
                ).grid(row=row, column=1, sticky=EW, pady=7, ipady=4)

        def refresh_target_hint(_event=None):
            target = target_map.get(variables["target"].get())
            if not target:
                target_hint_var.set("选择后显示项目合同的总开票进度")
                return
            if target.get('income_mode') == 'invoice':
                target_hint_var.set(
                    '随开票自动确认收入，无需另填收入确认。\n'
                    f"累计已开 {self.money(target['invoiced_minor'])}；本次金额保存后自动计入收入。"
                )
                return
            target_hint_var.set(
                f"确认收入 {self.money(target['amount_minor'])} · "
                f"已开 {self.money(target['invoiced_minor'])} · "
                f"待开 {self.money(target['uninvoiced_minor'])} · "
                f"{target['settlement_count']} 笔收入确认按日期自动分配"
            )

        target_combo.bind("<<ComboboxSelected>>", refresh_target_hint)
        refresh_target_hint()

        def choose_pdf():
            from queue import Queue, Empty
            from threading import Thread
            from services import invoice_pdf_service
            path = filedialog.askopenfilename(parent=dialog, title='选择销项发票PDF', filetypes=[('PDF发票', '*.pdf')])
            if not path:
                return
            if not messagebox.askyesno('识别并填表', '识别成功后将替换本窗口的票号、日期、金额、税额及购买方。\n项目仍需核对，是否继续？', parent=dialog):
                return
            pdf_state['busy'] = True
            import_button.configure(state='disabled')
            pdf_hint.set('正在本地识别PDF，请稍候……')
            results = Queue()

            def work():
                try:
                    results.put((invoice_pdf_service.recognize(path), None))
                except Exception as error:
                    results.put((None, str(error)))

            def finish():
                if not dialog.winfo_exists():
                    return
                try:
                    result, error = results.get_nowait()
                except Empty:
                    dialog.after(100, finish)
                    return
                pdf_state['busy'] = False
                import_button.configure(state='normal')
                if error:
                    pdf_hint.set('本次识别失败，原表单及已选附件未改变。')
                    messagebox.showwarning('无法识别', error, parent=dialog)
                    return
                pdf_state['recognition'] = result
                pdf_confirmed.set(False)
                for key, field in [('no', 'invoice_no'), ('date', 'invoice_date'), ('amount', 'amount'),
                                   ('net', 'net_amount'), ('tax_amount', 'tax_amount'), ('tax', 'tax_rate'), ('buyer', 'buyer_name')]:
                    variables[key].set(result[field])
                contracts = {c['id']: c for c in contract_service.list_contracts()}
                matches = [label for label, target in target_map.items()
                           if result['buyer_name'] and contracts.get(target['contract_id'], {}).get('customer_name_snapshot') == result['buyer_name']]
                if not invoice:
                    variables['target'].set(matches[0] if len(matches) == 1 else prompt)
                refresh_target_hint()
                warnings = list(result['warnings'])
                if len(matches) > 1:
                    warnings.append('该客户对应多个项目/合同，请手动选择，不能仅按客户归集')
                elif not matches:
                    warnings.append('未找到唯一匹配项目，请手动选择')
                duplicates = [r for r in finance_service.list_invoices(include_void=True)
                              if r['invoice_no'] == result['invoice_no'] and (not invoice or r['id'] != invoice['id'])]
                if duplicates:
                    warnings.append('此票号已存在，请勿重复登记；可到原记录修改并添加附件')
                from pathlib import Path
                pdf_hint.set('已识别：' + Path(path).name + '\n' + ('；'.join(warnings) if warnings else '金额勾稽一致，请核对后保存。'))
                confirm_widget.pack(anchor=W, pady=5)

            Thread(target=work, daemon=True).start()
            dialog.after(100, finish)

        import_button.configure(command=choose_pdf)
        ttk.Label(body, text="备注").grid(
            row=len(specs)+1, column=0, sticky=NE, padx=(0, 12), pady=7
        )
        notes = ttk.Text(body, height=5, wrap="word")
        notes.grid(row=len(specs)+1, column=1, sticky=EW, pady=7)
        if invoice and invoice["notes"]:
            notes.insert("1.0", invoice["notes"])
        if restoring:
            ttk.Label(
                body,
                text="该发票已作废；保存后将恢复为有效并重新计入开票金额。",
                style="CardText.TLabel",
            ).grid(row=len(specs)+2, column=1, sticky=W, pady=(2, 7))
        body.columnconfigure(1, weight=1)

        def save():
            if pdf_state['busy']:
                messagebox.showwarning('请稍候', 'PDF仍在识别，请等待完成再保存', parent=dialog)
                return
            if pdf_state['recognition'] and not pdf_confirmed.get():
                messagebox.showwarning('请先核对', '请核对票面信息和项目归属，并勾选确认', parent=dialog)
                return
            if pdf_state['recognition'] and any(not variables[k].get().strip() for k in ('no','date','amount','net','tax_amount','tax','buyer')):
                messagebox.showwarning('请补充信息', '识别未确认的字段请核对原票后补全', parent=dialog)
                return
            target = target_map.get(variables["target"].get())
            if not target:
                messagebox.showwarning(
                    "无法保存", "请选择本次发票对应的项目和合同", parent=dialog
                )
                return
            if restoring and not messagebox.askyesno(
                "确认恢复",
                "保存后该发票将恢复为有效，并重新计入项目开票金额。确定继续吗？",
                parent=dialog,
            ):
                return
            try:
                data = {
                    "invoice_no": variables["no"].get(),
                    "project_id": target["project_id"],
                    "contract_id": target["contract_id"],
                    "invoice_date": variables["date"].get(),
                    "amount": variables["amount"].get(),
                    "tax_rate": variables["tax"].get(),
                    'net_amount': variables['net'].get(),
                    'tax_amount': variables['tax_amount'].get(),
                    'tax_rate_label': ((pdf_state['recognition'] or invoice or {}).get('tax_rate_label', '')
                                       if variables['tax'].get() == '多税率' else ''),
                    "buyer_name": variables["buyer"].get(),
                    "notes": notes.get("1.0", END).strip(),
                }
                if pdf_state['recognition']:
                    from services.invoice_pdf_service import save_with_pdf
                    save_with_pdf(data, pdf_state['recognition'], invoice['id'] if invoice else None)
                elif invoice:
                    finance_service.update_invoice(invoice["id"], data)
                else:
                    finance_service.create_invoice(data)
            except Exception as error:
                messagebox.showwarning("无法保存", str(error), parent=dialog)
                return
            dialog.destroy()
            self.refresh()
            self.notebook.select(1)
            messagebox.showinfo(
                "保存成功",
                success_message,
                parent=self.parent,
            )

        add_form_actions(
            footer, cancel_command=dialog.destroy,
            primary_text=primary_text,
            primary_command=save,
        )

    def edit_invoice(self):
        invoice_id = self.selected_id(self.invoice_tree)
        if not invoice_id:
            messagebox.showwarning("提示", "请先选择要修改的发票")
            return
        self.open_invoice_dialog(invoice_id)

    def open_receipt_dialog(self, receipt_id=None):
        receipt = (
            finance_service.get_receipt(receipt_id) if receipt_id else None
        )
        if receipt_id and not receipt:
            messagebox.showwarning("提示", "有效回款记录不存在")
            return
        editing = receipt is not None
        selected_project_id = (
            receipt["project_id"] if editing else self.selected_project_id()
        )
        allocation_map = {}
        for settlement in contract_service.list_settlements(
            project_id=selected_project_id
        ):
            is_current_pair = (
                editing
                and settlement["project_id"] == receipt["project_id"]
                and settlement["contract_id"] == receipt["contract_id"]
            )
            if (
                settlement["source_type"] != "contract"
                or not (settlement["unreceived_minor"] > 0 or is_current_pair)
            ):
                continue
            pricing = contract_service.PRICING_MODES[settlement["pricing_mode"]]
            label = (
                f"{settlement['contract_no']} · {pricing} → "
                f"{settlement['project_code']} · {settlement['project_name']}"
            )
            allocation_map[label] = settlement
        for row in contract_service.list_allocations(project_id=selected_project_id):
            if row['contract_status'] == 'void':
                continue
            pricing = contract_service.PRICING_MODES[row['pricing_mode']]
            label = (f"{row['contract_no']} · {pricing} → "
                     f"{row['project_code']} · {row['project_name']}")
            allocation_map.setdefault(label, row)
        cash_projects = [
            row for row in project_service.list_projects(active_only=False)
            if row["business_mode"] == "cash"
            and (
                row["status"] != "已关闭"
                or (editing and row["id"] == selected_project_id)
            )
            and (not selected_project_id or row["id"] == selected_project_id)
        ]
        cash_project_map = {
            f"{row['project_code']} · {row['name']}": row
            for row in cash_projects
        }
        current_settlement_id = receipt["settlement_id"] if editing else None
        cash_settlements_by_project = {}
        for settlement in contract_service.list_settlements(
            project_id=selected_project_id
        ):
            if (
                settlement["source_type"] == "cash_job"
                and (
                    settlement["unreceived_minor"] > 0
                    or settlement["id"] == current_settlement_id
                )
            ):
                cash_settlements_by_project.setdefault(
                    settlement["project_id"], []
                ).append(settlement)
        if not allocation_map and not cash_project_map:
            messagebox.showwarning(
                "提示", "请先登记可回款的收入确认，或建立零星现金工程项目"
            )
            return

        invoices = finance_service.list_invoices()
        current_is_cash = editing and receipt["business_mode"] == "cash"
        if current_is_cash or not allocation_map:
            default_source = "零星现金工程"
        else:
            default_source = "正式合同工程"
        current_allocation = next(
            (
                label for label, row in allocation_map.items()
                if editing
                and row["project_id"] == receipt["project_id"]
                and row["contract_id"] == receipt["contract_id"]
            ),
            "",
        )
        current_cash_project = next(
            (
                label for label, row in cash_project_map.items()
                if editing and row["id"] == receipt["project_id"]
            ),
            "",
        )

        dialog = ttk.Toplevel(self.parent)
        dialog.title("修改回款" if editing else "登记回款")
        body, footer = build_form_dialog(
            dialog, self.parent, 730, 760, min_width=610, min_height=520
        )
        today = datetime.now().strftime("%Y-%m-%d")
        new_settlement_label = "新增完工金额确认（本次同步建立）"
        variables = {
            "source": ttk.StringVar(value=default_source),
            "allocation": ttk.StringVar(
                value=current_allocation or next(iter(allocation_map), "")
            ),
            "cash_project": ttk.StringVar(
                value=current_cash_project or next(iter(cash_project_map), "")
            ),
            "cash_settlement": ttk.StringVar(),
            "invoice": ttk.StringVar(value="按客户时间顺序自动核销"),
            "no": ttk.StringVar(value=receipt["receipt_no"] if editing else ""),
            "date": ttk.StringVar(
                value=receipt["receipt_date"] if editing else today
            ),
            "settlement_date": ttk.StringVar(value=today),
            "settlement_amount": ttk.StringVar(),
            "amount": ttk.StringVar(
                value=f"{receipt['allocated_amount_minor'] / 100:.2f}"
                if editing else ""
            ),
            "payer": ttk.StringVar(
                value=receipt["payer_name_snapshot"] if editing else ""
            ),
            "method": ttk.StringVar(
                value=receipt["payment_method"] if editing else "银行转账"
            ),
        }
        automatic_invoice_label = "按客户时间顺序自动核销"
        invoice_map = {automatic_invoice_label: None}
        invoice_available_map = {automatic_invoice_label: None}
        cash_settlement_map = {}
        invoice_help_var = ttk.StringVar()
        allocation_summary_var = ttk.StringVar(
            value="系统按确认日期自动分配"
        )
        manual_allocation_state = {"items": None}

        specs = (
            ("回款来源 *", "source"),
            ("合同与项目 *", "allocation"),
            ("零星工程项目 *", "cash_project"),
            ("完工金额确认 *", "cash_settlement"),
            ("指定发票（可选）", "invoice"),
            ("收入确认分配", "settlement_distribution"),
            ("回款单号", "no"),
            ("回款日期 *", "date"),
            ("完工确认日期 *", "settlement_date"),
            ("完工金额（元）*", "settlement_amount"),
            ("回款金额（元）*", "amount"),
            ("付款方", "payer"),
            ("收款方式", "method"),
        )
        widgets = {}
        field_labels = {}
        combo_values = {
            "source": ["正式合同工程", "零星现金工程"],
            "allocation": list(allocation_map),
            "cash_project": list(cash_project_map),
            "cash_settlement": [],
            "invoice": list(invoice_map),
            "method": ["银行转账", "现金", "票据", "其他"],
        }
        distribution_button = None
        for row, (label, key) in enumerate(specs):
            label_widget = ttk.Label(body, text=label)
            label_widget.grid(
                row=row, column=0, sticky=E, padx=(0, 12), pady=7
            )
            if key == "settlement_distribution":
                widget = ttk.Frame(body)
                ttk.Label(
                    widget,
                    textvariable=allocation_summary_var,
                    style="Muted.TLabel",
                ).pack(side=LEFT, fill=X, expand=True)
                distribution_button = ttk.Button(
                    widget,
                    text="查看 / 调整",
                    bootstyle="secondary-outline",
                    command=lambda: open_distribution_dialog(),
                )
                distribution_button.pack(side=RIGHT, padx=(10, 0))
            elif key in combo_values:
                widget = ttk.Combobox(
                    body, textvariable=variables[key],
                    values=combo_values[key], state="readonly"
                )
            elif key in ("date", "settlement_date"):
                widget = DatePicker(
                    body,
                    textvariable=variables[key],
                    popup_title=(
                        "选择回款日期" if key == "date" else "选择完工确认日期"
                    ),
                )
            else:
                widget = ttk.Entry(body, textvariable=variables[key])
            grid_options = {"row": row, "column": 1, "sticky": EW, "pady": 7}
            if not isinstance(widget, DatePicker):
                grid_options["ipady"] = 4
            widget.grid(**grid_options)
            widgets[key] = widget
            field_labels[key] = label_widget

        settlement_help_var = ttk.StringVar()
        settlement_help = ttk.Label(
            body,
            textvariable=settlement_help_var,
            style="Muted.TLabel",
            wraplength=480,
            justify=LEFT,
        )
        invoice_help = ttk.Label(
            body,
            textvariable=invoice_help_var,
            style="Muted.TLabel",
            wraplength=480,
            justify=LEFT,
        )
        for help_widget in (settlement_help, invoice_help):
            help_widget.grid(
                row=len(specs), column=1, sticky=W, pady=(0, 5)
            )

        def selected_formal_allocation():
            return allocation_map.get(variables["allocation"].get())

        def sync_distribution_summary():
            invoice_id = invoice_map.get(variables["invoice"].get())
            items = manual_allocation_state["items"]
            if invoice_id:
                allocation_summary_var.set("随所选发票自动关联收入确认")
                distribution_button.configure(state="disabled")
            elif items:
                total_minor = sum(item["amount_minor"] for item in items)
                allocation_summary_var.set(
                    f"手动分配 {len(items)} 笔 · {self.money(total_minor)}"
                )
                distribution_button.configure(state="normal")
            else:
                allocation_summary_var.set("自动抵扣收入，超出部分记为预收款")
                distribution_button.configure(state="normal")

        def open_distribution_dialog():
            allocation = selected_formal_allocation()
            if not allocation:
                messagebox.showwarning(
                    "提示", "请先选择合同与项目", parent=dialog
                )
                return
            if invoice_map.get(variables["invoice"].get()):
                messagebox.showinfo(
                    "收入确认分配",
                    "已选择发票，系统会按照发票对应的收入确认自动关联。",
                    parent=dialog,
                )
                return
            base_payload = {
                "project_id": allocation["project_id"],
                "contract_id": allocation["contract_id"],
                "amount": variables["amount"].get(),
            }
            try:
                automatic = finance_service.preview_receipt_allocations(
                    base_payload,
                    exclude_receipt_id=receipt_id if editing else None,
                )
            except Exception as error:
                messagebox.showwarning(
                    "无法分配", str(error), parent=dialog
                )
                return

            available_settlements = contract_service.list_settlements(
                project_id=allocation["project_id"],
                contract_id=allocation["contract_id"],
            )
            current_by_settlement = {
                row["settlement_id"]: row["allocated_amount_minor"]
                for row in (receipt.get("allocations", []) if editing else [])
                if row["settlement_id"]
            }
            available_settlements = [
                row for row in available_settlements
                if row["source_type"] == "contract"
                and (
                    row["unreceived_minor"] > 0
                    or current_by_settlement.get(row["id"], 0) > 0
                )
            ]
            if not available_settlements:
                messagebox.showwarning(
                    "无法分配", "当前合同项目没有可回款的收入确认", parent=dialog
                )
                return

            allocation_dialog = ttk.Toplevel(dialog)
            allocation_dialog.title("调整收入确认分配")
            allocation_body, allocation_footer = build_form_dialog(
                allocation_dialog,
                dialog,
                760,
                560,
                min_width=680,
                min_height=420,
            )
            ttk.Label(
                allocation_body,
                text="本次回款如何计入收入确认",
                style="CardTitle.TLabel",
            ).grid(row=0, column=0, columnspan=5, sticky=W, pady=(0, 3))
            ttk.Label(
                allocation_body,
                text="默认按确认日期从早到晚分配；只有需要调整时才修改右侧金额。",
                style="Muted.TLabel",
            ).grid(row=1, column=0, columnspan=5, sticky=W, pady=(0, 12))
            for column, (text, sticky) in enumerate(
                (
                    ("确认编号", W),
                    ("日期", ""),
                    ("确认金额", E),
                    ("可回款", E),
                    ("本次分配（元）", E),
                )
            ):
                ttk.Label(
                    allocation_body, text=text, style="SpineLabel.TLabel"
                ).grid(
                    row=2,
                    column=column,
                    sticky=sticky,
                    padx=(0, 10) if column < 4 else 0,
                    pady=(0, 6),
                )

            planned_minor = {
                row["settlement_id"]: row["amount_minor"] for row in automatic
            }
            if manual_allocation_state["items"]:
                planned_minor = {
                    row["settlement_id"]: row["amount_minor"]
                    for row in manual_allocation_state["items"]
                }
            amount_vars = {}
            for index, settlement in enumerate(available_settlements, start=3):
                available_minor = settlement["unreceived_minor"] + (
                    current_by_settlement.get(settlement["id"], 0)
                )
                amount_minor = planned_minor.get(settlement["id"], 0)
                amount_var = ttk.StringVar(
                    value=f"{amount_minor / 100:.2f}" if amount_minor else ""
                )
                amount_vars[settlement["id"]] = amount_var
                values = (
                    settlement["settlement_no"],
                    settlement["settlement_date"],
                    self.money(settlement["amount_minor"]),
                    self.money(available_minor),
                )
                for column, value in enumerate(values):
                    ttk.Label(allocation_body, text=value).grid(
                        row=index,
                        column=column,
                        sticky=W if column == 0 else E,
                        padx=(0, 10),
                        pady=5,
                    )
                ttk.Entry(
                    allocation_body,
                    textvariable=amount_var,
                    width=16,
                    justify=RIGHT,
                ).grid(row=index, column=4, sticky=EW, pady=5, ipady=3)
            allocation_body.columnconfigure(0, weight=1)
            allocation_body.columnconfigure(4, weight=1)

            def use_manual_distribution():
                requested = [
                    {
                        "settlement_id": settlement_id,
                        "amount": amount_var.get(),
                    }
                    for settlement_id, amount_var in amount_vars.items()
                    if amount_var.get().strip()
                ]
                try:
                    validated = finance_service.preview_receipt_allocations(
                        {**base_payload, "settlement_allocations": requested},
                        exclude_receipt_id=receipt_id if editing else None,
                    )
                except Exception as error:
                    messagebox.showwarning(
                        "无法使用该分配", str(error), parent=allocation_dialog
                    )
                    return
                manual_allocation_state["items"] = [
                    {
                        "settlement_id": row["settlement_id"],
                        "amount_minor": row["amount_minor"],
                    }
                    for row in validated
                ]
                allocation_dialog.destroy()
                sync_distribution_summary()

            def use_automatic_distribution():
                manual_allocation_state["items"] = None
                allocation_dialog.destroy()
                sync_distribution_summary()

            add_form_actions(
                allocation_footer,
                cancel_command=allocation_dialog.destroy,
                secondary_text="恢复自动分配",
                secondary_command=use_automatic_distribution,
                primary_text="使用该分配",
                primary_command=use_manual_distribution,
            )

        def sync_invoice_help(_event=None):
            available = invoice_available_map.get(variables["invoice"].get())
            invoice_help_var.set(
                "留空时按同一客户的发票日期自动核销；"
                "尚未开票的金额会保留为待匹配。"
                if available is None
                else f"手动指定后，本次最多可核销 {self.money(available)}。"
            )
            if available is not None:
                manual_allocation_state["items"] = None
            sync_distribution_summary()

        def refresh_invoices(_event=None, selected_invoice_id=None):
            if _event is not None:
                manual_allocation_state["items"] = None
            invoice_map.clear()
            invoice_available_map.clear()
            invoice_map[automatic_invoice_label] = None
            invoice_available_map[automatic_invoice_label] = None
            selected_label = automatic_invoice_label
            allocation = allocation_map.get(variables["allocation"].get())
            current_by_invoice = {
                match["invoice_id"]: match["allocated_amount_minor"]
                for match in (
                    receipt.get("invoice_matches", []) if editing else []
                )
            }
            if allocation:
                for row in invoices:
                    current_receipt_amount = current_by_invoice.get(
                        row["id"], 0
                    )
                    available_minor = (
                        row["unreceived_minor"] + current_receipt_amount
                    )
                    if (
                        row["project_id"] == allocation["project_id"]
                        and row["contract_id"] == allocation["contract_id"]
                        and (available_minor > 0 or row["id"] == selected_invoice_id)
                    ):
                        label = (
                            f"{row['invoice_no']} · "
                            f"可回款 {self.money(available_minor)}"
                        )
                        invoice_map[label] = row["id"]
                        invoice_available_map[label] = available_minor
                        if row["id"] == selected_invoice_id:
                            selected_label = label
            widgets["invoice"].configure(values=list(invoice_map))
            variables["invoice"].set(selected_label)
            sync_invoice_help()

        def sync_cash_help():
            project = cash_project_map.get(variables["cash_project"].get())
            if not project:
                settlement_help_var.set("请选择零星工程项目。")
                return
            settlement_help_var.set(
                '只登记实际收款，无需填写完工金额。\n'
                '已有收入自动抵扣，剩余记为预收款；以后确认收入时自动抵扣。'
            )
            return

        def sync_settlement_fields(_event=None):
            for key in ("cash_settlement", "settlement_date", "settlement_amount"):
                for widget in (field_labels[key], widgets[key]):
                    widget.grid_remove()
            if variables["source"].get() == "零星现金工程":
                settlement_help.grid()
            else:
                settlement_help.grid_remove()
            sync_cash_help()

        def refresh_cash_settlements(_event=None, selected_settlement_id=None):
            project = cash_project_map.get(variables["cash_project"].get())
            cash_settlement_map.clear()
            selected_label = ""
            if project:
                for settlement in cash_settlements_by_project.get(project["id"], []):
                    available_minor = settlement["unreceived_minor"] + (
                        receipt["allocated_amount_minor"]
                        if editing and settlement["id"] == current_settlement_id
                        else 0
                    )
                    label = (
                        f"{settlement['settlement_no']} · "
                        f"{settlement['settlement_date']} · "
                        f"可回款 {self.money(available_minor)}"
                    )
                    cash_settlement_map[label] = settlement
                    if settlement["id"] == selected_settlement_id:
                        selected_label = label
            can_create = False
            if project and not editing:
                agreed_minor = project.get("cash_agreed_amount_minor")
                can_create = (
                    agreed_minor is None
                    or int(project.get("cash_confirmed_minor") or 0)
                    < int(agreed_minor)
                )
            if can_create:
                cash_settlement_map[new_settlement_label] = None
            widgets["cash_settlement"].configure(
                values=list(cash_settlement_map)
            )
            variables["cash_settlement"].set(
                selected_label or next(iter(cash_settlement_map), "")
            )
            sync_settlement_fields()

        def sync_source(_event=None):
            is_cash = variables["source"].get() == "零星现金工程"
            visible_keys = (
                ("cash_project", "cash_settlement")
                if is_cash
                else ("allocation", "invoice", "settlement_distribution")
            )
            hidden_keys = (
                ("allocation", "invoice", "settlement_distribution")
                if is_cash
                else (
                    "cash_project",
                    "cash_settlement",
                    "settlement_date",
                    "settlement_amount",
                )
            )
            for key in visible_keys:
                field_labels[key].grid()
                widgets[key].grid()
            for key in hidden_keys:
                field_labels[key].grid_remove()
                widgets[key].grid_remove()
            if is_cash:
                invoice_help.grid_remove()
                settlement_help.grid()
                sync_settlement_fields()
            else:
                settlement_help.grid_remove()
                invoice_help.grid()
                sync_distribution_summary()
            if not editing:
                variables["method"].set("现金" if is_cash else "银行转账")

        widgets["allocation"].bind("<<ComboboxSelected>>", refresh_invoices)
        widgets["invoice"].bind("<<ComboboxSelected>>", sync_invoice_help)
        widgets["cash_project"].bind(
            "<<ComboboxSelected>>", refresh_cash_settlements
        )
        widgets["cash_settlement"].bind(
            "<<ComboboxSelected>>", sync_settlement_fields
        )
        widgets["source"].bind("<<ComboboxSelected>>", sync_source)
        refresh_invoices(
            selected_invoice_id=receipt["invoice_id"] if editing else None
        )
        refresh_cash_settlements(
            selected_settlement_id=current_settlement_id
        )
        sync_source()
        last_suggested_payer = ['']

        def sync_payer(*_args):
            if editing:
                return
            if variables['source'].get() == '零星现金工程':
                selected = cash_project_map.get(variables['cash_project'].get()) or {}
                suggestion = finance_service.default_receipt_payer(selected.get('id'))
            else:
                selected = allocation_map.get(variables['allocation'].get()) or {}
                suggestion = finance_service.default_receipt_payer(selected.get('project_id'), selected.get('contract_id'))
            current = variables['payer'].get().strip()
            if not current or current == last_suggested_payer[0]:
                variables['payer'].set(suggestion)
            last_suggested_payer[0] = suggestion

        for key in ('source', 'allocation', 'cash_project'):
            variables[key].trace_add('write', sync_payer)
        sync_payer()
        if editing:
            for key in ("source", "allocation", "cash_project", "cash_settlement"):
                widgets[key].configure(state="disabled")

        ttk.Label(body, text="备注").grid(
            row=len(specs) + 1, column=0, sticky=NE, padx=(0, 12), pady=7
        )
        notes = ttk.Text(body, height=5, wrap="word")
        notes.grid(row=len(specs) + 1, column=1, sticky=EW, pady=7)
        if editing and receipt["notes"]:
            notes.insert("1.0", receipt["notes"])
        body.columnconfigure(1, weight=1)

        def save():
            is_cash = variables["source"].get() == "零星现金工程"
            allocation = allocation_map.get(variables["allocation"].get())
            cash_project = cash_project_map.get(variables["cash_project"].get())
            settlement = cash_settlement_map.get(
                variables["cash_settlement"].get()
            )
            selected = cash_project if is_cash else allocation
            if not selected:
                messagebox.showwarning("提示", "请选择有效的回款来源", parent=dialog)
                return
            project_id = (
                cash_project["id"] if is_cash else allocation["project_id"]
            )
            payload = {
                "receipt_no": variables["no"].get(),
                "project_id": project_id,
                "contract_id": None if is_cash else allocation["contract_id"],
                "invoice_id": (
                    None if is_cash else invoice_map.get(variables["invoice"].get())
                ),
                "settlement_id": None,
                "allow_advance": is_cash,
                "receipt_date": variables["date"].get(),
                "amount": variables["amount"].get(),
                "payer_name": variables["payer"].get(),
                "payment_method": variables["method"].get(),
                "notes": notes.get("1.0", END).strip(),
            }
            if (
                not is_cash
                and payload["invoice_id"] is None
                and manual_allocation_state["items"] is not None
            ):
                payload["settlement_allocations"] = manual_allocation_state[
                    "items"
                ]
            try:
                if editing:
                    finance_service.update_receipt(receipt_id, payload)
                else:
                    finance_service.create_receipt(payload)
            except Exception as error:
                messagebox.showwarning("无法保存", str(error), parent=dialog)
                return
            dialog.destroy()
            self.refresh()
            self.notebook.select(2)

        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text="保存修改" if editing else "保存回款",
            primary_command=save,
            primary_style="primary" if editing else "success",
        )

    def edit_receipt(self):
        receipt_id = self.selected_id(self.receipt_tree)
        if not receipt_id:
            messagebox.showwarning("提示", "请先选择要修改的回款")
            return
        self.open_receipt_dialog(receipt_id)

    def void_invoice(self):
        invoice_id = self.selected_id(self.invoice_tree)
        if not invoice_id:
            messagebox.showwarning("提示", "请先选择发票")
            return
        invoice = finance_service.get_invoice(invoice_id)
        if invoice and invoice["status"] == "void":
            messagebox.showwarning(
                "提示", "该发票已经作废；如需恢复，请点击“修改发票”。"
            )
            return
        if not messagebox.askyesno("确认作废", "确定作废该发票吗？"):
            return
        try:
            finance_service.void_invoices([invoice_id])
        except ValueError as error:
            messagebox.showwarning("无法作废", str(error))
            return
        self.refresh()

    def record_fund_receipt(self):
        from pages.funds_page import open_source_payment
        receipt_id = self.selected_id(self.receipt_tree)
        if not receipt_id:
            messagebox.showwarning("提示", "请先选择回款")
            return
        open_source_payment(self.parent, "receipt", receipt_id, on_saved=self.refresh)

    def void_receipt(self):
        receipt_id = self.selected_id(self.receipt_tree)
        if not receipt_id:
            messagebox.showwarning("提示", "请先选择回款")
            return
        if not messagebox.askyesno("确认作废", "确定作废该回款吗？"):
            return
        try:
            finance_service.void_receipts([receipt_id])
        except Exception as error:
            messagebox.showwarning("无法作废", str(error))
            return
        self.refresh()

    def open_invoice_attachments(self):
        invoice_id = self.selected_id(self.invoice_tree)
        if not invoice_id:
            messagebox.showwarning("提示", "请先选择发票")
            return
        open_attachment_manager(
            self.parent,
            "invoice",
            invoice_id,
            "销项发票",
            on_change=self.refresh,
        )

    def open_receipt_attachments(self):
        receipt_id = self.selected_id(self.receipt_tree)
        if not receipt_id:
            messagebox.showwarning("提示", "请先选择回款")
            return
        open_attachment_manager(
            self.parent, "receipt", receipt_id, "回款"
        )
