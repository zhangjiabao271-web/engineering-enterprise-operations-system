from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import contract_service, finance_service, project_service
from ui.components import (
    BottomToolbar,
    DataTable,
    FilterBar,
    KpiCard,
    PageHeader,
)
from ui.dialogs import safe_init_loaders
from ui.attachments import open_attachment_manager

from pages.finance_collection_ui import FinanceCollectionMixin
from pages.finance_invoice_dialog import FinanceInvoiceDialogMixin
from pages.finance_receipt_dialog import FinanceReceiptDialogMixin


class ReceivablePage(FinanceCollectionMixin, FinanceInvoiceDialogMixin, FinanceReceiptDialogMixin):
    """Sales invoices, receipts and project-level allocation."""

    def __init__(self, parent, initial_project_id=None):
        self.parent = parent
        self.initial_project_id = initial_project_id
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

        from pages.input_invoice_page import EntityPage, InputInvoicePage
        entity_tab = ttk.Frame(self.notebook, padding=10)
        input_tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(entity_tab, text="经营主体")
        self.notebook.add(input_tab, text="进项发票")
        self.entity_page = EntityPage(entity_tab)
        self.input_invoice_page = InputInvoicePage(input_tab)

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
        ttk.Label(
            invoice_tab,
            text="发票抵扣仅限同一客户、同一经营主体。主体未确认的发票或回款请先到“经营主体”页确认；实际到账不受影响。",
            wraplength=760,
        ).pack(fill=X, padx=8, pady=(4, 8))
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
        if self.initial_project_id is not None:
            current = next(
                (label for label, project_id in self.project_map.items()
                 if project_id == self.initial_project_id),
                "全部项目",
            )
            self.initial_project_id = None
            self.project_var.set(current)
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
            if (allocation['contract_status'] == 'void'
                    or allocation['invoice_policy'] == 'not_required'):
                continue
            key = (allocation['project_id'], allocation['contract_id'])
            if allocation['income_mode'] == 'receipt':
                if key in targets:
                    targets[key]['income_mode'] = 'receipt'
                continue
            if allocation['income_mode'] != 'invoice':
                continue
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


    def edit_invoice(self):
        invoice_id = self.selected_id(self.invoice_tree)
        if not invoice_id:
            messagebox.showwarning("提示", "请先选择要修改的发票")
            return
        self.open_invoice_dialog(invoice_id)


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
