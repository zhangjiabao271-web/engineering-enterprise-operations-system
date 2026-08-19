from datetime import datetime
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import contract_service, finance_service, project_service
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
        self.notebook.add(overview_tab, text="资金总览")
        self.notebook.add(invoice_tab, text="销项发票")
        self.notebook.add(receipt_tab, text="回款记录")

        self._build_overview(overview_tab)

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
        ).pack(in_=invoice_bar, side=RIGHT)
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
        invoice_queue_combo.pack(in_=invoice_bar, side=RIGHT, padx=(8, 12))
        ttk.Label(invoice_tab, text="发票队列").pack(
            in_=invoice_bar, side=RIGHT
        )
        self.invoice_tree = self._table(
            invoice_tab,
            (
                ("no", "发票号码", 150, W),
                ("date", "开票日期", 95, CENTER),
                ("project", "项目", 150, W),
                ("amount", "价税合计", 120, E),
                ("received", "已关联回款", 120, E),
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
                ("invoice", "关联发票", 145, W),
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
            f" · 待分配 {self.money(summary['pending_receipt_minor'])}"
            if summary["pending_receipt_minor"] else ""
        )
        self.kpi_hint_vars["receipt"].set(
            f"回款率 {self._percent(summary['receipt_rate_percent'])} · "
            f"未回 {self.money(summary['receivable_minor'])}{pending_hint}"
        )

        self.notebook.tab(1, text=f"销项发票 · {len(invoices)}")
        self.invoice_tree.refresh(
            displayed_invoices,
            lambda row: (str(row["id"]), (
                row["invoice_no"],
                row["invoice_date"],
                row["project_name"],
                self.money(row["amount_minor"]),
                self.money(row["received_minor"]),
                self.money(row["unreceived_minor"]),
                row["collection_status"],
                row["buyer_name_snapshot"],
                f"{row['tax_rate_bps'] / 100:g}%",
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
                    else row["invoice_no"] or "未关联发票"
                ),
                row["settlement_no"] or "待分配",
                row["payer_name_snapshot"],
                row["contract_no"] or "零星现金工程",
                row["receipt_no"],
            )),
        )

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

    def open_invoice_dialog(self, invoice_id=None):
        settlement_map = self._settlement_map()
        if not settlement_map:
            messagebox.showwarning("提示", "请先登记有效的收入确认")
            return
        invoice = finance_service.get_invoice(invoice_id) if invoice_id else None
        if invoice_id and not invoice:
            messagebox.showwarning("提示", "发票记录不存在")
            return
        restoring = bool(invoice and invoice["status"] == "void")
        prompt = "请选择收入确认"
        if invoice:
            settlement_label = next(
                (
                    label
                    for label, settlement in settlement_map.items()
                    if settlement["id"] == invoice.get("settlement_id")
                ),
                None,
            )
        else:
            settlement_label = (
                next(iter(settlement_map)) if len(settlement_map) == 1 else None
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
        variables = {
            "settlement": ttk.StringVar(value=settlement_label or prompt),
            "no": ttk.StringVar(value=invoice["invoice_no"] if invoice else ""),
            "date": ttk.StringVar(
                value=invoice["invoice_date"]
                if invoice else datetime.now().strftime("%Y-%m-%d")
            ),
            "amount": ttk.StringVar(
                value=f"{invoice['amount_minor'] / 100:.2f}" if invoice else ""
            ),
            "tax": ttk.StringVar(
                value=f"{invoice['tax_rate_bps'] / 100:g}" if invoice else "0"
            ),
            "buyer": ttk.StringVar(
                value=invoice["buyer_name_snapshot"] if invoice else ""
            ),
        }
        specs = (
            ("收入确认 *", "settlement"),
            ("发票号码（不可重复）", "no"),
            ("开票日期 *", "date"),
            ("价税合计（元）*", "amount"),
            ("税率（%）", "tax"),
            ("购买方", "buyer"),
        )
        settlement_hint_var = ttk.StringVar()
        settlement_combo = None
        for row, (label, key) in enumerate(specs):
            ttk.Label(body, text=label).grid(
                row=row, column=0, sticky=E, padx=(0, 12), pady=7
            )
            if key == "settlement":
                field = ttk.Frame(body)
                settlement_combo = ttk.Combobox(
                    field,
                    textvariable=variables[key],
                    values=[prompt, *settlement_map],
                    state="readonly",
                )
                settlement_combo.pack(fill=X, ipady=4)
                ttk.Label(
                    field,
                    textvariable=settlement_hint_var,
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

        def refresh_settlement_hint(_event=None):
            settlement = settlement_map.get(variables["settlement"].get())
            if not settlement:
                settlement_hint_var.set("选择后显示本笔结算的开票进度")
                return
            settlement_hint_var.set(
                f"结算 {self.money(settlement['amount_minor'])} · "
                f"已开 {self.money(settlement['invoiced_minor'])} "
                f"({self._percent(settlement['invoice_rate_percent'])}) · "
                f"待开 {self.money(settlement['uninvoiced_minor'])}"
            )

        settlement_combo.bind("<<ComboboxSelected>>", refresh_settlement_hint)
        refresh_settlement_hint()
        ttk.Label(body, text="备注").grid(
            row=6, column=0, sticky=NE, padx=(0, 12), pady=7
        )
        notes = ttk.Text(body, height=5, wrap="word")
        notes.grid(row=6, column=1, sticky=EW, pady=7)
        if invoice and invoice["notes"]:
            notes.insert("1.0", invoice["notes"])
        if restoring:
            ttk.Label(
                body,
                text="该发票已作废；保存后将恢复为有效并重新计入开票金额。",
                style="CardText.TLabel",
            ).grid(row=7, column=1, sticky=W, pady=(2, 7))
        body.columnconfigure(1, weight=1)

        def save():
            settlement = settlement_map.get(variables["settlement"].get())
            if not settlement:
                messagebox.showwarning(
                    "无法保存", "请选择本次发票对应的收入确认", parent=dialog
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
                    "project_id": settlement["project_id"],
                    "contract_id": settlement["contract_id"],
                    "settlement_id": settlement["id"],
                    "invoice_date": variables["date"].get(),
                    "amount": variables["amount"].get(),
                    "tax_rate": variables["tax"].get(),
                    "buyer_name": variables["buyer"].get(),
                    "notes": notes.get("1.0", END).strip(),
                }
                if invoice:
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
        allocation_map = {
            f"{row['contract_no']} → {row['project_name']}": row
            for row in contract_service.list_allocations(
                project_id=selected_project_id
            )
        }
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
                "提示", "请先建立合同项目分配，或建立零星现金工程项目"
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
            "invoice": ttk.StringVar(value="不关联具体发票"),
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
        invoice_map = {"不关联具体发票": None}
        invoice_available_map = {"不关联具体发票": None}
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
            ("关联发票", "invoice"),
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

        settlement_help = ttk.Label(
            body,
            text="填写工程总完工金额；保存后系统会同时建立完工确认并关联本次回款。",
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
                allocation_summary_var.set("系统按确认日期自动分配")
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
                "可不关联发票；如选择发票，回款金额不能超过所示余额。"
                if available is None
                else f"该发票本次最多可关联 {self.money(available)}。"
            )
            if available is not None:
                manual_allocation_state["items"] = None
            sync_distribution_summary()

        def refresh_invoices(_event=None, selected_invoice_id=None):
            if _event is not None:
                manual_allocation_state["items"] = None
            invoice_map.clear()
            invoice_available_map.clear()
            invoice_map["不关联具体发票"] = None
            invoice_available_map["不关联具体发票"] = None
            selected_label = "不关联具体发票"
            allocation = allocation_map.get(variables["allocation"].get())
            if allocation:
                for row in invoices:
                    current_receipt_amount = (
                        receipt["allocated_amount_minor"]
                        if editing and row["id"] == receipt["invoice_id"]
                        else 0
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

        def sync_settlement_fields(_event=None):
            is_new = (
                not editing
                and variables["cash_settlement"].get() == new_settlement_label
            )
            for key in ("settlement_date", "settlement_amount"):
                for widget in (field_labels[key], widgets[key]):
                    if is_new:
                        widget.grid()
                    else:
                        widget.grid_remove()
            if is_new:
                settlement_help.grid()
            else:
                settlement_help.grid_remove()

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
            if not editing:
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
                "settlement_id": (
                    settlement["id"] if is_cash and settlement else None
                ),
                "settlement_date": variables["settlement_date"].get(),
                "settlement_amount": variables["settlement_amount"].get(),
                "settlement_basis": "回款补录时同步建立完工金额确认",
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

    def void_receipt(self):
        receipt_id = self.selected_id(self.receipt_tree)
        if not receipt_id:
            messagebox.showwarning("提示", "请先选择回款")
            return
        if not messagebox.askyesno("确认作废", "确定作废该回款吗？"):
            return
        finance_service.void_receipts([receipt_id])
        self.refresh()

    def open_invoice_attachments(self):
        invoice_id = self.selected_id(self.invoice_tree)
        if not invoice_id:
            messagebox.showwarning("提示", "请先选择发票")
            return
        open_attachment_manager(
            self.parent, "invoice", invoice_id, "销项发票"
        )

    def open_receipt_attachments(self):
        receipt_id = self.selected_id(self.receipt_tree)
        if not receipt_id:
            messagebox.showwarning("提示", "请先选择回款")
            return
        open_attachment_manager(
            self.parent, "receipt", receipt_id, "回款"
        )
