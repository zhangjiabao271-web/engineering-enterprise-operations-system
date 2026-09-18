from datetime import datetime
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.widgets import ToolTip
from ttkbootstrap.constants import *

from services import attachment_service, master_data_service, procurement_service, project_service
from ui.components import DataTable, DatePicker, FilterBar, KpiCard, PageHeader
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders
from ui.purchase_entry import reset_continuous_purchase_line
from ui.theme import COLORS, SPACING
from ui.scaling import scale_px, working_area
from ui.typeahead import filter_supplier_offer_labels
from ui.attachments import open_attachment_manager

class PurchaseManagementPage:
    """统一采购中心：看板、正式采购、零星采购和待归集。"""

    def __init__(self, parent):
        self.parent = parent
        self.month_var = ttk.StringVar(value=datetime.now().strftime("%Y-%m"))
        self.project_filter_var = ttk.StringVar(value="全部项目")
        self.search_var = ttk.StringVar()
        self.project_filter_map = {"全部项目": None}
        self.kpi_vars = {key: ttk.StringVar(value="¥0") for key in (
            "total", "formal", "petty", "unassigned", "no_invoice", "reimbursement"
        )}
        self.kpi_vars["merchants"] = ttk.StringVar(value="0")
        self.delta_var = ttk.StringVar(value="较上月 --")
        self.order_details = {}
        self.order_rows = {}
        self.build_ui()
        safe_init_loaders("采购中心", [self.refresh_filters, self.refresh_all])

    def build_ui(self):
        # 1. 页面头部（组件化）
        PageHeader(
            self.parent,
            "采购管理",
            "采购记录、成本归属与附件",
            actions=[
                ttk.Button(
                    self.parent, text="新增正式采购", bootstyle=PRIMARY,
                    command=lambda: self.open_purchase_dialog("正式采购"),
                ),
                ttk.Button(
                    self.parent, text="快速记零星采购", bootstyle=SUCCESS,
                    command=lambda: self.open_purchase_dialog("零星采购"),
                ),
                ttk.Button(
                    self.parent, text="新增项目", bootstyle=OUTLINE,
                    command=self.open_project_dialog,
                ),
            ],
        )

        # 统计集中到独立页，默认工作区留给采购记录。
        self.notebook = ttk.Notebook(self.parent, bootstyle=PRIMARY)
        self.dashboard_tab = ttk.Frame(self.notebook, padding=12)
        kpi_specs = [
            ("total", "本月采购总额", self.delta_var),
            ("formal", "正式采购", None),
            ("petty", "零星采购", None),
            ("merchants", "供应商 / 商户", None),
        ]
        kpi_grid = ttk.Frame(self.dashboard_tab)
        kpi_grid.pack(fill=X, pady=(0, SPACING["md"]))
        for index, (key, label, hint_var) in enumerate(kpi_specs):
            KpiCard(kpi_grid, label, self.kpi_vars[key], hint_var).grid(
                row=0, column=index, sticky=EW,
                padx=(0 if index == 0 else 6, 0 if index == 3 else 6),
            )
            kpi_grid.columnconfigure(index, weight=1)

        # 3. 风险预警行（3个，纯文字组件卡，不再用彩色条）
        risk_specs = [
            ("unassigned", "待归集项目"),
            ("no_invoice", "无票 / 票据未确认"),
            ("reimbursement", "员工垫付待处理"),
        ]
        risk_grid = ttk.Frame(self.dashboard_tab)
        risk_grid.pack(fill=X, pady=(0, SPACING["md"]))
        for index, (key, label) in enumerate(risk_specs):
            KpiCard(risk_grid, label, self.kpi_vars[key]).grid(
                row=0, column=index, sticky=EW,
                padx=(0 if index == 0 else 6, 0 if index == 2 else 6),
            )
            risk_grid.columnconfigure(index, weight=1)

        # 4. 全局工具栏（组件化）
        self.month_combo = ttk.Combobox(self.parent, textvariable=self.month_var,
                                        width=10, state="readonly")
        self.month_combo.bind("<<ComboboxSelected>>", lambda event: self.refresh_all())
        self.project_combo = ttk.Combobox(self.parent, textvariable=self.project_filter_var,
                                          width=20, state="readonly")
        self.project_combo.configure(style="PurchaseProject.TCombobox", postcommand=self._size_project_dropdown)
        self.project_tooltip = ToolTip(
            self.project_combo, text=self.project_filter_var.get(),
            delay=450, wraplength=scale_px(self.project_combo, 520),
        )
        self.project_filter_var.trace_add("write", self._update_project_tooltip)
        self.project_combo.bind("<Destroy>", lambda event: self.project_tooltip.leave(), add="+")
        self.project_combo.bind("<<ComboboxSelected>>", lambda event: self.refresh_all())
        search_entry = ttk.Entry(self.parent, textvariable=self.search_var, width=18)
        search_entry.bind("<Return>", lambda event: self.refresh_lists())
        FilterBar(
            self.parent,
            ("月份", self.month_combo),
            ("项目", self.project_combo),
            ("搜索", search_entry),
            actions=[
                ttk.Button(self.parent, text="查询", bootstyle=INFO, command=self.refresh_lists),
                ttk.Button(self.parent, text="清空", bootstyle=SECONDARY, command=self.clear_search),
            ],
        )

        # 5. 标签页区域
        self.notebook.pack(fill=BOTH, expand=True)

        self.all_tab = ttk.Frame(self.notebook, padding=10)
        self.formal_tab = ttk.Frame(self.notebook, padding=10)
        self.petty_tab = ttk.Frame(self.notebook, padding=10)
        self.unassigned_tab = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(self.all_tab, text="  全部采购  ")
        self.notebook.add(self.formal_tab, text="  正式采购  ")
        self.notebook.add(self.petty_tab, text="  零星采购  ")
        self.notebook.add(self.unassigned_tab, text="  待归集  ")
        self.notebook.insert(0, self.dashboard_tab, text="  采购统计  ")

        # 看板：排行榜
        self.build_dashboard()

        self.all_frame = self._build_order_table(self.all_tab, "当前条件下暂无采购记录")
        self.all_tree = self.all_frame.tree
        self._build_order_tools(self.all_tab, None, self.all_tree)

        # 正式采购：操作按钮 + 表格（工具条置于表格上方）
        self.formal_frame = self._build_order_table(self.formal_tab, "本月暂无正式采购记录")
        self.formal_tree = self.formal_frame.tree
        self._build_order_tools(self.formal_tab, "正式采购", self.formal_tree)

        # 零星采购
        self.petty_frame = self._build_order_table(self.petty_tab, "本月暂无零星采购记录")
        self.petty_tree = self.petty_frame.tree
        self._build_order_tools(self.petty_tab, "零星采购", self.petty_tree)

        # 待归集
        self.unassigned_frame = self._build_order_table(self.unassigned_tab, "暂无待归集采购，成本已全部归属项目")
        self.unassigned_tree = self.unassigned_frame.tree
        tools = self._build_unassigned_tools(self.unassigned_tab, self.unassigned_tree)
        tools.pack(fill=X, pady=(0, 8), before=self.order_details[self.unassigned_tree]["pane"])

    def _build_order_tools(self, parent, purchase_type, tree):
        tools = ttk.Frame(self.order_details[tree]["panel"])
        tools.pack(side=BOTTOM, fill=X, pady=(8, 0), before=self.order_details[tree]["body"])
        actions = (
            ("修改采购", lambda: self.edit_selected_purchase(tree, purchase_type)),
            ("登记实际付款", lambda: self.record_fund_payment(tree)),
            ("磅单 / 附件", lambda: self.open_purchase_attachments(tree)),
            ("更新支付 / 票据", lambda: self.open_status_dialog(tree)),
            ("作废选中", lambda: self.void_selected(tree)),
        )
        for index, (label, command) in enumerate(actions):
            ttk.Button(tools, text=label, bootstyle="secondary-outline", command=command).grid(
                row=index // 2, column=index % 2, sticky=EW, padx=3, pady=4,
            )
        tools.columnconfigure((0, 1), weight=1)
        return tools

    def _build_unassigned_tools(self, parent, tree):
        tools = ttk.Frame(parent)
        ttk.Label(tools, text="这些采购尚未归属项目，会影响项目成本准确性。", style="CardText.TLabel").pack(side=LEFT)
        ttk.Button(tools, text="归集到项目", bootstyle=SUCCESS,
                   command=self.assign_selected).pack(side=RIGHT, padx=4)
        return tools

    def record_fund_payment(self, tree):
        from pages.funds_page import open_source_payment
        ids = self.selected_order_ids(tree)
        if len(ids) != 1:
            messagebox.showwarning("提示", "请先选择一笔采购记录")
            return
        open_source_payment(self.parent, "purchase", ids[0], on_saved=self.refresh_all)

    def open_purchase_attachments(self, tree):
        ids = self.selected_order_ids(tree)
        if len(ids) != 1:
            messagebox.showwarning("提示", "请先选择一笔采购记录")
            return
        open_attachment_manager(
            self.parent, "purchase", ids[0], "采购磅单与规格清单",
            on_change=self.refresh_lists,
        )

    def build_dashboard(self):
        rankings = ttk.Panedwindow(self.dashboard_tab, orient=HORIZONTAL)
        rankings.pack(fill=BOTH, expand=True)
        project_card = ttk.Frame(rankings, style="Card.TFrame", padding=14)
        merchant_card = ttk.Frame(rankings, style="Card.TFrame", padding=14)
        rankings.add(project_card, weight=1)
        rankings.add(merchant_card, weight=1)
        ttk.Label(project_card, text="项目采购投入", style="CardTitle.TLabel").pack(anchor=W, pady=(0, 8))
        self.project_rank = DataTable(
            project_card,
            specs=(
                ("label", "项目", 180, CENTER),
                ("orders", "笔数", 60, CENTER),
                ("amount", "金额", 105, CENTER),
            ),
            empty_text="本月暂无项目采购",
            stretch=("label",),
        )
        ttk.Label(merchant_card, text="供应商 / 商户采购排行", style="CardTitle.TLabel").pack(anchor=W, pady=(0, 8))
        self.merchant_rank = DataTable(
            merchant_card,
            specs=(
                ("label", "供应商 / 商户", 180, CENTER),
                ("orders", "笔数", 60, CENTER),
                ("amount", "金额", 105, CENTER),
            ),
            empty_text="本月暂无供应商采购记录",
            stretch=("label",),
        )
        for table in (self.project_rank, self.merchant_rank):
            table.tree.configure(height=8)

    def _build_order_table(self, parent, empty_text):
        """主表只显示常用列，完整快照放在可滚动详情区。"""
        pane = tk.PanedWindow(
            parent, orient=HORIZONTAL, background=COLORS["border"],
            borderwidth=0, sashwidth=6, sashrelief="flat",
        )
        pane.pack(fill=BOTH, expand=True)
        main = ttk.Frame(pane)
        panel = ttk.Frame(pane, padding=12, width=320)
        pane.add(main, stretch="always")
        pane.add(panel, stretch="never")
        table = DataTable(
            main,
            specs=(
                ("no", "单号", 120, CENTER),
                ("date", "日期", 88, CENTER),
                ("project", "项目 / 归属", 140, W),
                ("merchant", "供应商 / 商户", 140, CENTER),
                ("material", "材料 / 供应商", 170, W),
                ("mode", "计价方式", 100, CENTER),
                ("quantity", "结算数量 / 重量", 130, E),
                ("price", "结算单价 / 总额", 150, E),
                ("amount", "项目成本", 110, E),
                ("payment", "支付状态", 80, CENTER),
                ("invoice", "票据", 80, CENTER),
                ("status", "状态", 90, CENTER),
                ("attachment", "附件", 100, W),
            ),
            empty_text=empty_text,
        )
        tree = table.tree
        ttk.Style().configure("Purchase.Treeview", rowheight=scale_px(tree, 48))
        tree.configure(style="Purchase.Treeview", selectmode="extended", displaycolumns=("date", "project", "material", "amount", "payment", "attachment"))
        horizontal = ttk.Scrollbar(table, orient=HORIZONTAL, command=tree.xview)
        horizontal.pack(side=BOTTOM, fill=X, before=tree)
        tree.configure(xscrollcommand=horizontal.set)
        tree._horizontal_scrollbar_added = True
        ttk.Label(panel, text="采购详情", style="CardTitle.TLabel").pack(anchor=W, pady=(0, 8))
        detail_body = ttk.Frame(panel)
        detail_body.pack(fill=BOTH, expand=True)
        detail = tk.Text(
            detail_body, width=30, height=10, wrap="word", state="disabled",
            font=("Microsoft YaHei UI", 10), background=COLORS["surface"],
            foreground=COLORS["text"], relief="flat", padx=4, pady=4,
            spacing1=3, spacing3=5, highlightthickness=0,
        )
        scroll = ttk.Scrollbar(detail_body, command=detail.yview)
        scroll.pack(side=RIGHT, fill=Y)
        detail.pack(fill=BOTH, expand=True)
        detail.configure(yscrollcommand=scroll.set)
        self.order_details[tree] = {"text": detail, "pane": pane, "panel": panel, "body": detail_body}
        pane.bind("<Configure>", lambda event: self._layout_order_pane(tree, event))
        tree.bind("<<TreeviewSelect>>", lambda event: self._show_order_detail(tree))
        tree.bind("<Double-1>", lambda event: self.edit_selected_purchase(tree, None))
        self._show_order_detail(tree)
        # 状态颜色统一走设计令牌，不再使用散落色值
        tree.tag_configure("status_green", foreground=COLORS["accent"])
        tree.tag_configure("status_orange", foreground=COLORS["warning"])
        tree.tag_configure("status_red", foreground=COLORS["danger"])
        return table

    def _layout_order_pane(self, tree, event):
        state = self.order_details[tree]
        pane = state["pane"]
        orientation = HORIZONTAL if event.width >= scale_px(pane, 980) else VERTICAL
        if state.get("orientation") == orientation:
            return
        state["orientation"] = orientation
        pane.configure(orient=orientation)
        def position():
            if pane.winfo_exists():
                if orientation == HORIZONTAL:
                    split = pane.winfo_width() - scale_px(pane, 320)
                else:
                    # Keep full detail lines above the fixed payment/action buttons.
                    split = max(scale_px(pane, 150), pane.winfo_height() - scale_px(pane, 300))
                if orientation == HORIZONTAL:
                    pane.sash_place(0, max(120, split), 0)
                else:
                    pane.sash_place(0, 0, max(120, split))
        pane.after_idle(position)

    def _show_order_detail(self, tree):
        selected = tree.selection()
        row = self.order_rows.get(tree, {}).get(selected[0]) if len(selected) == 1 else None
        if row:
            quantity = tree.set(selected[0], "quantity")
            text = (
                f"{row['material_name_snapshot']}\n"
                f"{row['merchant_name_snapshot']}\n\n"
                f"单号：{row['order_no']}\n日期：{row['purchase_date']}\n"
                f"项目：{row['project_name'] or '待归集'}\n"
                f"规格：{row.get('specification_snapshot') or '未填写'}\n\n"
                f"{procurement_service.SETTLEMENT_MODES[row['settlement_mode']]} · {quantity}\n"
                f"单价 / 结算额：{self.settlement_price_text(row)}\n"
                f"未税材料额：{self.money(row['material_amount_cents'])}\n"
                f"税额：{self.money(row['tax_amount_cents'])}\n"
                f"另列运费：{self.money(row['freight_amount_cents'])}\n"
                f"项目成本：{self.money(row['project_cost_cents'])}\n\n"
                f"原支付标记：{row.get('payment_status') or '未确认'} · {row.get('payment_method') or '未记录'}\n"
                f"{self._fund_payment_detail(row['id'])}\n"
                f"票据：{row.get('invoice_status') or '未确认'}\n"
                f"磅单号：{row.get('weigh_ticket_no') or '未填写'}\n"
                f"附件：{tree.set(selected[0], 'attachment')}\n"
                f"备注：{row.get('notes') or '未填写'}"
            )
            if row.get("item_count", 1) > 1:
                text += "\n\n本单材料明细：\n" + "\n".join(
                    f"{index}. {item['material_name_snapshot']} · {item.get('specification_snapshot') or ''}\n"
                    f"   {item['quantity']} {item.get('unit_snapshot') or ''} · {self.settlement_price_text(item)} · 含税 {self.money(item['line_amount_cents'])}元"
                    for index, item in enumerate(row["items"], 1)
                )
        elif selected:
            text = f"已选中 {len(selected)} 笔采购。\n可批量更新支付 / 票据或作废。\n\n查看明细、修改或添加附件请只选择一笔。"
        else:
            text = "选择一笔采购查看计价依据和完整信息。\n\n双击记录可修改。\n按住 Ctrl 可选择多笔。"
        detail = self.order_details[tree]["text"]
        detail.configure(state="normal")
        detail.delete("1.0", END)
        detail.insert("1.0", text)
        detail.configure(state="disabled")

    def _update_project_tooltip(self, *_args):
        self.project_tooltip.hide_tip()
        self.project_tooltip.text = self.project_filter_var.get()

    def _size_project_dropdown(self):
        """Widen only this popdown, using its actual font and the usable screen bounds."""
        combo = self.project_combo
        popdown = combo.tk.call("ttk::combobox::PopdownWindow", str(combo))
        font = tkfont.Font(root=combo, font=combo.tk.call(f"{popdown}.f.l", "cget", "-font"))
        content_width = max((font.measure(value) for value in combo["values"]), default=0)
        left, _top, right, _bottom = working_area(combo)
        margin = scale_px(combo, 8)
        width = min(
            max(combo.winfo_width(), content_width + scale_px(combo, 40)),
            right - left - margin * 2,
        )
        x = min(max(combo.winfo_rootx(), left + margin), right - margin - width)
        ttk.Style().configure(
            "PurchaseProject.TCombobox",
            postoffset=(x - combo.winfo_rootx(), 0, width - combo.winfo_width(), 0),
        )

    def refresh_filters(self):
        selected_project_id = self.selected_project_id()
        today = datetime.now()
        selectable = []
        for offset in range(-3, 37):
            total_month = today.year * 12 + today.month - 1 - offset
            year, month_index = divmod(total_month, 12)
            selectable.append(f"{year:04d}-{month_index + 1:02d}")
        months = sorted(set(procurement_service.list_purchase_months() + selectable), reverse=True)
        self.month_combo["values"] = months
        projects = project_service.list_projects()
        self.project_filter_map = {"全部项目": None}
        for project in projects:
            self.project_filter_map[f"{project['name']} · {project['project_code']}"] = project["id"]
        self.project_combo["values"] = list(self.project_filter_map)
        self.project_filter_var.set(next(
            (label for label, project_id in self.project_filter_map.items() if project_id == selected_project_id),
            "全部项目",
        ))

    def selected_project_id(self):
        return self.project_filter_map.get(self.project_filter_var.get())

    def refresh_all(self):
        self.refresh_dashboard()
        self.refresh_lists()

    def refresh_dashboard(self):
        data = procurement_service.get_purchase_dashboard(self.month_var.get(), self.selected_project_id())
        summary = data["summary"]
        self.kpi_vars["total"].set(self.money(summary["total_cents"]))
        self.kpi_vars["formal"].set(self.money(summary["formal_cents"]))
        self.kpi_vars["petty"].set(self.money(summary["petty_cents"]))
        self.kpi_vars["merchants"].set(str(summary["merchant_count"]))
        self.kpi_vars["unassigned"].set(self.money(summary["unassigned_cents"]))
        self.kpi_vars["no_invoice"].set(self.money(summary["no_invoice_cents"]))
        self.kpi_vars["reimbursement"].set(self.money(summary["reimbursement_cents"]))
        previous = summary["previous_cents"]
        if previous:
            change = (summary["total_cents"] - previous) / previous * 100
            self.delta_var.set(f"较上月 {'+' if change >= 0 else ''}{change:.1f}%")
        else:
            self.delta_var.set("上月无数据")
        self.fill_rank(self.project_rank, data["by_project"])
        self.fill_rank(self.merchant_rank, data["by_merchant"])

    def fill_rank(self, table, rows):
        table.refresh(
            rows,
            lambda row: (None, (row["label"], row["order_count"], self.money(row["amount_cents"]))),
        )

    def refresh_lists(self):
        from services import funds_service
        self.fund_payment_rows = {row['id']:row for row in funds_service.list_sources('purchase', include_closed=True)}
        month = self.month_var.get()
        project_id = self.selected_project_id()
        keyword = self.search_var.get().strip()
        self.fill_order_tree(self.all_frame, procurement_service.list_purchase_orders(month, project_id=project_id, keyword=keyword))
        self.fill_order_tree(self.formal_frame, procurement_service.list_purchase_orders(month, "正式采购", project_id, keyword))
        self.fill_order_tree(self.petty_frame, procurement_service.list_purchase_orders(month, "零星采购", project_id, keyword))
        self.fill_order_tree(self.unassigned_frame, procurement_service.list_purchase_orders(month, project_id=None, keyword=keyword, unassigned_only=True))

    def fill_order_tree(self, table, rows):
        previous_selection = table.tree.selection()
        self.order_rows[table.tree] = {str(row["id"]): row for row in rows}
        attachment_statuses = attachment_service.purchase_attachment_statuses(row["id"] for row in rows)
        def mapper(row):
            invoice_status = row.get("invoice_status", "")
            payment_status = row.get("payment_status", "")
            funding = getattr(self, "fund_payment_rows", {}).get(row["id"])
            if funding and funding["verified"]:
                if funding["remaining_minor"] < 0:
                    payment_status = "待核对"
                elif funding["remaining_minor"] == 0:
                    payment_status = "已付款"
                elif funding["paid_minor"] + funding["opening_settled_minor"]:
                    payment_status = "部分付款"
                else:
                    payment_status = "未付款"

            if invoice_status == "无发票":
                status_text = "无票"
                status_tag = "status_red"
            elif payment_status == "已付款":
                status_text = "已付款"
                status_tag = "status_green"
            else:
                status_text = "未确认"
                status_tag = "status_orange"

            values = (
                row["order_no"],
                row["purchase_date"],
                row["project_name"] or "待归集",
                row["merchant_name_snapshot"],
                f"{row['material_name_snapshot']}\n{row['merchant_name_snapshot']}",
                "多材料采购" if row.get("item_count", 1) > 1 else procurement_service.SETTLEMENT_MODES[row["settlement_mode"]],
                self.order_quantity_text(row),
                self.settlement_price_text(row),
                self.money(row["project_cost_cents"]),
                payment_status,
                row["invoice_status"],
                status_text,
                attachment_statuses[row["id"]],
            )
            # 行 id 直接作为 iid，取代旧版隐藏的 ID 列
            return str(row["id"]), values, (status_tag,)

        table.refresh(rows, mapper)
        surviving = [iid for iid in previous_selection if table.tree.exists(iid)]
        if surviving:
            table.tree.selection_set(surviving)
        self._show_order_detail(table.tree)

    def _fund_payment_detail(self, order_id):
        row = getattr(self, "fund_payment_rows", {}).get(order_id)
        if not row or not row["verified"]:
            return "资金账：历史付款待核实，原支付标记不自动扣账户"
        return (f"启用前已付：{self.money(row['opening_settled_minor'])}\n"
                f"资金账已付：{self.money(row['paid_minor'])}\n"
                f"剩余待付：{self.money(row['remaining_minor'])}")

    @staticmethod
    def order_quantity_text(row):
        if row.get("item_count", 1) > 1:
            return f"{row['item_count']}条材料"
        if row["settlement_mode"] == "weight":
            return f"{row['net_weight']} {row['weight_unit']}"
        return f"{row['quantity']:g} {row['unit_snapshot']}"

    @staticmethod
    def settlement_price_text(row):
        if row.get("item_count", 1) > 1:
            return f"共{row['item_count']}条材料，详见明细"
        basis = "含税" if row["price_basis"] == "inclusive" else "未税"
        if row["settlement_mode"] == "weight":
            return f"{row['weight_unit_price']}元/{row['weight_unit']}（{basis}）"
        if row["settlement_mode"] == "total":
            return f"{row['settlement_total_cents'] / 100:.2f}元/批（{basis}）"
        key = "tax_inclusive_unit_price_cents" if row["price_basis"] == "inclusive" else "material_unit_price_cents"
        return f"{row[key] / 100:.2f}元/{row['unit_snapshot']}（{basis}）"

    def clear_search(self):
        self.search_var.set("")
        self.project_filter_var.set("全部项目")
        self.refresh_all()

    @staticmethod
    def money(cents):
        return f"¥{int(cents or 0) / 100:,.2f}"

    @staticmethod
    def number(value):
        value = float(value or 0)
        return f"{value:.0f}" if value.is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")

    @staticmethod
    def _purchase_projects(include_closed=False):
        projects = project_service.list_projects(active_only=False)
        if include_closed:
            return projects
        return [project for project in projects if project["status"] != "已关闭"]

    def open_project_dialog(self, on_saved=None):
        dialog = ttk.Toplevel(self.parent)
        dialog.title("新增项目")
        body, footer = build_form_dialog(
            dialog, self.parent, 520, 440,
            min_width=480, min_height=360,
        )
        values = {key: ttk.StringVar() for key in ("name", "customer", "address", "manager", "notes")}
        status_var = ttk.StringVar(value="进行中")
        customers = master_data_service.list_customers(active_only=True)
        customer_names = [row["name"] for row in customers]
        customer_map = {row["name"]: row["id"] for row in customers}
        for row, (label, key) in enumerate([
            ("项目名称 *", "name"), ("客户名称", "customer"), ("项目地址", "address"),
            ("项目负责人", "manager"), ("备注", "notes")
        ]):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky=E, padx=(0, 12), pady=8)
            widget = (
                ttk.Combobox(
                    body, textvariable=values[key], values=customer_names,
                    state="normal", width=36,
                )
                if key == "customer"
                else ttk.Entry(body, textvariable=values[key], width=36)
            )
            widget.grid(row=row, column=1, sticky=EW, pady=8, ipady=5)
        ttk.Label(body, text="状态").grid(row=5, column=0, sticky=E, padx=(0, 12), pady=8)
        ttk.Combobox(body, textvariable=status_var, values=["筹备中", "进行中", "已完工", "已关闭"],
                     state="readonly").grid(row=5, column=1, sticky=EW, pady=8)
        body.columnconfigure(1, weight=1)

        def save():
            if not values["name"].get().strip():
                messagebox.showwarning("提示", "请输入项目名称", parent=dialog)
                return
            project_id = project_service.create_project({
                "name": values["name"].get().strip(), "customer_name": values["customer"].get().strip(),
                "customer_partner_id": customer_map.get(values["customer"].get().strip()),
                "address": values["address"].get().strip(), "manager": values["manager"].get().strip(),
                "status": status_var.get(), "notes": values["notes"].get().strip(),
            })
            dialog.destroy()
            self.refresh_filters()
            if on_saved:
                on_saved(project_id)

        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text="保存项目",
            primary_command=save,
        )

    def open_purchase_dialog(self, purchase_type, order_id=None):
        is_petty = purchase_type == "零星采购"
        edit_data = procurement_service.get_purchase_order(order_id) if order_id else {}
        if order_id and not edit_data:
            messagebox.showwarning("提示", "采购单不存在或已经作废")
            return
        saved_items = edit_data.get("items", [])
        if saved_items:
            edit_data = dict(edit_data, **{key: value for key, value in saved_items[0].items() if key != "items"})
        pending_items = [dict(item) for item in saved_items[1:]]
        current_item_id = saved_items[0]["item_id"] if saved_items else None
        edit_allocations = (
            procurement_service.get_purchase_allocations(order_id)
            if order_id
            else []
        )
        # 已完工项目仍可能补录材料、运费或迟到票据；仅关闭项目停止新增业务。
        projects = self._purchase_projects(include_closed=bool(order_id))
        project_map = {"待归集（稍后分配）": None}
        project_map.update({f"{p['project_code']} · {p['name']}": p["id"] for p in projects})
        suppliers = master_data_service.list_suppliers()
        supplier_map = {f"{s['name']}": s["id"] for s in suppliers}
        products_by_label = {}

        dialog = ttk.Toplevel(self.parent)
        if order_id:
            dialog.title("修改零星采购" if is_petty else "修改正式采购")
        else:
            dialog.title("快速记零星采购" if is_petty else "新增正式采购")
        body, footer = build_form_dialog(
            dialog, self.parent, 1040, 730,
            min_width=560, min_height=480,
        )

        project_value = next(iter(project_map))
        for label, project_id in project_map.items():
            if project_id == edit_data.get("project_id"):
                project_value = label
                break
        supplier_value = next(iter(supplier_map), "")
        for label, supplier_id in supplier_map.items():
            if supplier_id == edit_data.get("supplier_id"):
                supplier_value = label
                break

        vars_ = {
            "settlement_mode": ttk.StringVar(value=procurement_service.SETTLEMENT_MODES[edit_data.get("settlement_mode", "quantity")]),
            "net_weight": ttk.StringVar(value=edit_data.get("net_weight") or ""),
            "weight_unit": ttk.StringVar(value=edit_data.get("weight_unit") or "吨"),
            "weight_unit_price": ttk.StringVar(value=edit_data.get("weight_unit_price") or ""),
            "settlement_total": ttk.StringVar(value=f"{edit_data['settlement_total_cents'] / 100:.2f}" if edit_data.get("settlement_total_cents") is not None else ""),
            "weigh_ticket_no": ttk.StringVar(value=edit_data.get("weigh_ticket_no") or ""),
            "price_basis": ttk.StringVar(value="含税价" if edit_data.get("price_basis", "inclusive") == "inclusive" else "未税价"),
            "project": ttk.StringVar(value=project_value),
            "date": ttk.StringVar(value=edit_data.get("purchase_date", datetime.now().strftime("%Y-%m-%d"))),
            "supplier": ttk.StringVar(value=supplier_value),
            "merchant": ttk.StringVar(value=edit_data.get("merchant_name_snapshot", "")),
            "material": ttk.StringVar(value=edit_data.get("material_name_snapshot", "")),
            "spec": ttk.StringVar(value=edit_data.get("specification_snapshot", "")),
            "unit": ttk.StringVar(value=edit_data.get("unit_snapshot", "件")),
            "qty": ttk.StringVar(value=str(edit_data.get("quantity", "1"))),
            "material_unit_price": ttk.StringVar(value=(
                str(edit_data.get("tax_inclusive_unit_price_cents" if edit_data.get("price_basis") == "inclusive" else "material_unit_price_cents", 0) / 100)
                if order_id else ""
            )),
            "tax_rate": ttk.StringVar(value=(
                f"{edit_data.get('tax_rate_bps', 0) / 100:g}" if order_id else "0"
            )),
            "tax_inclusive_unit_price": ttk.StringVar(value=(
                f"{edit_data.get('tax_inclusive_unit_price_cents', 0) / 100:.2f}"
                if order_id else "0.00"
            )),
            "material_amount": ttk.StringVar(value=(
                f"{edit_data.get('material_amount_cents', 0) / 100:.2f}"
                if order_id else "0.00"
            )),
            "material_gross": ttk.StringVar(value="0.00"),
            "current_material_gross": ttk.StringVar(value="--"),
            "tax_amount": ttk.StringVar(value=(
                f"{edit_data.get('tax_amount_cents', 0) / 100:.2f}"
                if order_id else "0.00"
            )),
            "freight": ttk.StringVar(value=(
                f"{edit_data.get('freight_amount_cents', 0) / 100:.2f}"
                if order_id else "0.00"
            )),
            "project_cost": ttk.StringVar(value=(
                f"{edit_data.get('project_cost_cents', 0) / 100:.2f}"
                if order_id else "0.00"
            )),
            "category": ttk.StringVar(value=edit_data.get("cost_category", "材料费")),
            "purpose": ttk.StringVar(value=edit_data.get("purpose", "")),
            "payment_method": ttk.StringVar(value=edit_data.get("payment_method", "微信" if is_petty else "对公转账")),
            "payment_status": ttk.StringVar(value=edit_data.get("payment_status", "已付款" if is_petty else "未确认")),
            "invoice": ttk.StringVar(value=edit_data.get("invoice_status", "无发票" if is_petty else "未确认")),
            "purchaser": ttk.StringVar(value=edit_data.get("purchaser", "")),
            "notes": ttk.StringVar(value=edit_data.get("notes", "")),
            "product": ttk.StringVar(),
            "attribution": ttk.StringVar(
                value=(
                    "多项目平均分摊"
                    if edit_data.get("allocation_method") == "equal"
                    else "单项目归集"
                )
            ),
        }
        product_hint_var = ttk.StringVar()
        continuous_feedback_var = ttk.StringVar()
        allocation_preview_var = ttk.StringVar()
        selected_project_vars = {
            project["id"]: ttk.BooleanVar(value=False) for project in projects
        }
        for allocation in edit_allocations:
            selected = selected_project_vars.get(allocation["project_id"])
            if selected is not None:
                selected.set(True)
        continuous_saved_count = 0

        if not order_id and not is_petty:
            ttk.Label(
                footer,
                textvariable=continuous_feedback_var,
                bootstyle=SUCCESS,
                wraplength=450,
            ).pack(side=TOP, anchor=W)

        row = 0
        form_fields = []
        def add_field(label, widget):
            nonlocal row
            label_widget = ttk.Label(body, text=label, wraplength=scale_px(body, 155), justify=LEFT)
            label_widget.grid(row=row, column=0, sticky=E, padx=(0, 12), pady=6)
            grid_options = {"row": row, "column": 1, "sticky": EW, "pady": 6}
            if not isinstance(widget, DatePicker):
                grid_options["ipady"] = 4
            widget.grid(**grid_options)
            row += 1
            form_fields.append((label_widget, widget))
            return label_widget, widget

        category_values = list(procurement_service.PURCHASE_COST_CATEGORIES)
        if vars_["category"].get() not in category_values:
            category_values.append(vars_["category"].get())
        category_combo = ttk.Combobox(
            body,
            textvariable=vars_["category"],
            values=category_values,
            state="readonly",
        )
        add_field("成本类别", category_combo)
        attribution_combo = ttk.Combobox(
            body,
            textvariable=vars_["attribution"],
            values=("单项目归集", "多项目平均分摊"),
            state="readonly",
        )
        attribution_label, attribution_combo = add_field(
            "项目归集方式", attribution_combo
        )
        project_combo = ttk.Combobox(
            body,
            textvariable=vars_["project"],
            values=list(project_map),
            state="normal",
        )
        project_label, project_combo = add_field("所属项目（输入关键词）", project_combo)

        def filter_projects(event=None):
            if event and event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
                return
            query = vars_["project"].get().strip().casefold()
            if vars_["project"].get() in project_map:
                labels = list(project_map)
            else:
                labels = [label for label in project_map if query in label.casefold()]
            project_combo.configure(values=labels)
            if event and query and labels and vars_["project"].get() not in project_map:
                project_combo.after_idle(lambda: project_combo.event_generate("<Down>"))

        project_combo.bind("<KeyRelease>", filter_projects)
        project_combo.configure(postcommand=filter_projects)

        allocation_frame = ttk.Frame(body, style="Card.TFrame", padding=12)
        allocation_header = ttk.Frame(allocation_frame, style="Card.TFrame")
        allocation_header.pack(fill=X, pady=(0, 6))
        ttk.Label(
            allocation_header,
            text="选择共同承担成本的项目",
            style="CardTitle.TLabel",
        ).pack(side=LEFT)
        select_all_projects_button = ttk.Button(
            allocation_header,
            text="全选项目",
            bootstyle="secondary-outline",
            command=lambda: toggle_all_allocation_projects(),
        )
        select_all_projects_button.pack(side=RIGHT)
        project_labels = {
            project["id"]: f"{project['project_code']} · {project['name']}"
            for project in projects
        }
        ttk.Separator(allocation_frame).pack(fill=X, pady=(0, 4))
        for project in projects:
            ttk.Checkbutton(
                allocation_frame,
                text=project_labels[project["id"]],
                variable=selected_project_vars[project["id"]],
                command=lambda: allocation_project_selection_changed(),
            ).pack(anchor=W, pady=2)
        ttk.Separator(allocation_frame).pack(fill=X, pady=(8, 6))
        ttk.Label(
            allocation_frame,
            textvariable=allocation_preview_var,
            style="CardText.TLabel",
            wraplength=500,
            justify=LEFT,
        ).pack(anchor=W)
        allocation_frame.grid(
            row=row, column=0, columnspan=2, sticky=EW, pady=(2, 8)
        )
        row += 1
        add_field(
            "采购日期 *",
            DatePicker(
                body,
                textvariable=vars_["date"],
                popup_title="选择采购日期",
            ),
        )
        if is_petty:
            add_field("商户名称 *", ttk.Entry(body, textvariable=vars_["merchant"]))
            add_field("材料名称 *", ttk.Entry(body, textvariable=vars_["material"]))
        else:
            supplier_combo = ttk.Combobox(body, textvariable=vars_["supplier"], values=list(supplier_map), state="readonly")
            add_field("供应商 *", supplier_combo)
            product_combo = ttk.Combobox(body, textvariable=vars_["product"], state="normal")
            add_field("材料 *", product_combo)
            product_hint_var.set("输入材料名称或规格即可筛选；请从匹配结果中选择。")
            ttk.Label(
                body,
                textvariable=product_hint_var,
                style="PageSub.TLabel",
            ).grid(row=row, column=1, sticky=W, pady=(0, 4))
            row += 1
        add_field("规格", ttk.Entry(body, textvariable=vars_["spec"]))
        mode_combo = ttk.Combobox(body, textvariable=vars_["settlement_mode"], values=tuple(procurement_service.SETTLEMENT_MODES.values()), state="readonly")
        add_field("计价方式", mode_combo)
        ttk.Label(body, text="过磅填本次净重和重量单价；整批可直接填材料名称，规格清单及磅单在保存后通过“磅单 / 附件”上传。",
                  wraplength=480, style="CardText.TLabel").grid(row=row, column=1, sticky=W, pady=4)
        row += 1
        unit_row = ttk.Frame(body)
        ttk.Entry(unit_row, textvariable=vars_["qty"], width=12).pack(side=LEFT)
        ttk.Label(unit_row, text="  单位  ").pack(side=LEFT)
        ttk.Entry(unit_row, textvariable=vars_["unit"], width=10).pack(side=LEFT)
        qty_fields = add_field("数量 / 单位 *", unit_row)
        weight_fields = []
        weight_fields.extend(add_field("实际净重 *", ttk.Entry(body, textvariable=vars_["net_weight"])))
        weight_fields.extend(add_field("过磅单位", ttk.Combobox(body, textvariable=vars_["weight_unit"], values=("吨", "公斤"), state="readonly")))
        weight_price_label, weight_price_entry = add_field("重量单价（元/吨）*", ttk.Entry(body, textvariable=vars_["weight_unit_price"]))
        weight_fields.extend((weight_price_label, weight_price_entry))
        total_fields = add_field("整批结算金额（不含运费）*", ttk.Entry(body, textvariable=vars_["settlement_total"]))
        add_field("磅单号 / 结算单号", ttk.Entry(body, textvariable=vars_["weigh_ticket_no"]))
        basis_combo = ttk.Combobox(body, textvariable=vars_["price_basis"], values=("含税价", "未税价"), state="readonly")
        add_field("报价口径", basis_combo)
        price_label, price_entry = add_field("材料单价（含税，元）*", ttk.Entry(body, textvariable=vars_["material_unit_price"]))
        add_field("税率（%）*", ttk.Entry(body, textvariable=vars_["tax_rate"]))
        gross_label, gross_entry = add_field(
            "含税单价（元）",
            ttk.Entry(body, textvariable=vars_["tax_inclusive_unit_price"], state="readonly"),
        )
        add_field("运费（元）", ttk.Entry(body, textvariable=vars_["freight"]))
        add_field("用途 / 施工位置", ttk.Entry(body, textvariable=vars_["purpose"]))
        add_field("支付方式", ttk.Combobox(body, textvariable=vars_["payment_method"],
                                          values=["现金", "微信", "支付宝", "对公转账", "员工垫付", "未记录"], state="readonly"))
        add_field("支付状态", ttk.Combobox(body, textvariable=vars_["payment_status"],
                                          values=["已付款", "未付款", "未确认"], state="readonly"))
        add_field("票据状态", ttk.Combobox(body, textvariable=vars_["invoice"],
                                          values=["有发票", "收据", "无发票", "未确认"], state="readonly"))
        add_field("经办人", ttk.Entry(body, textvariable=vars_["purchaser"]))
        add_field("备注", ttk.Entry(body, textvariable=vars_["notes"]))
        body.columnconfigure(1, weight=1)

        current_summary = ttk.Frame(footer)
        current_summary.pack(side=TOP, fill=X, pady=(0, 6))
        ttk.Label(current_summary, text="当前材料金额（含税，不含运费）：", style="CardTitle.TLabel").pack(side=LEFT)
        ttk.Label(current_summary, textvariable=vars_["current_material_gross"], style="CardTitle.TLabel").pack(side=LEFT)
        summary = ttk.Frame(footer)
        summary.pack(side=TOP, fill=X, pady=(0, 10))
        for index, (label, key) in enumerate((
            ("整单材料合计 · 含税（元）", "material_gross"),
            ("另列运费（元）", "freight"),
            ("项目成本 · 含运费（元）", "project_cost"),
        )):
            column = ttk.Frame(summary)
            column.grid(row=0, column=index, sticky=EW, padx=(0, 12))
            ttk.Label(column, text=label).pack(anchor=W)
            ttk.Entry(column, textvariable=vars_[key], state="readonly", width=16).pack(fill=X)
            summary.columnconfigure(index, weight=1)

        paired = {widget for pair in form_fields for widget in pair}
        full_width = [widget for widget in body.winfo_children() if widget not in paired and widget.grid_info()]
        ordered = sorted(
            [(int(label.grid_info()["row"]), (label, widget)) for label, widget in form_fields]
            + [(int(widget.grid_info()["row"]), (widget,)) for widget in full_width],
            key=lambda item: item[0],
        )
        layout_width = None

        def layout_form(event=None):
            nonlocal layout_width
            width = body.winfo_width()
            if event is not None and width == layout_width:
                return
            layout_width = width
            pairs_per_row = 2 if width >= scale_px(body, 850) else 1
            body.columnconfigure(3, weight=1 if pairs_per_row == 2 else 0)
            current_row, slot = 0, 0
            for _, widgets in ordered:
                if not widgets[0].grid_info():
                    continue
                if len(widgets) == 1:
                    current_row += bool(slot)
                    widgets[0].grid_configure(row=current_row, column=0, columnspan=pairs_per_row * 2, sticky=EW)
                    if isinstance(widgets[0], ttk.Label):
                        widgets[0].configure(wraplength=max(300, width - 60))
                    current_row += 1
                    slot = 0
                    continue
                label, widget = widgets
                label.grid_configure(row=current_row, column=slot * 2, sticky=W, padx=(0 if slot == 0 else 20, 12))
                widget.grid_configure(row=current_row, column=slot * 2 + 1, sticky=W if isinstance(widget, DatePicker) else EW)
                slot += 1
                if slot == pairs_per_row:
                    current_row += 1
                    slot = 0

        body.bind("<Configure>", layout_form, add="+")
        dialog.after_idle(layout_form)

        def pricing_input():
            mode = next(key for key, label in procurement_service.SETTLEMENT_MODES.items() if label == vars_["settlement_mode"].get())
            item = {
                "settlement_mode": mode, "quantity": vars_["qty"].get(),
                "price_basis": "inclusive" if vars_["price_basis"].get() == "含税价" else "exclusive",
                "tax_rate_bps": procurement_service.decimal_minor(vars_["tax_rate"].get()),
                "net_weight": vars_["net_weight"].get(), "weight_unit": vars_["weight_unit"].get(),
                "weight_unit_price": vars_["weight_unit_price"].get(),
                "weigh_ticket_no": vars_["weigh_ticket_no"].get(),
            }
            if mode == "quantity":
                price = procurement_service.decimal_minor(vars_["material_unit_price"].get())
                item.update(material_unit_price_cents=price, tax_inclusive_unit_price_cents=price)
            elif mode == "total":
                item["settlement_total_cents"] = procurement_service.decimal_minor(vars_["settlement_total"].get())
            return item

        def calculate(*args):
            inclusive = vars_["price_basis"].get() == "含税价"
            mode = vars_["settlement_mode"].get()
            if not is_petty and mode != "按数量":
                product_hint_var.set("可选择已有材料或直接填写整批名称；本次重量单价或结算金额请单独填写。")
            for widgets, visible in ((qty_fields, mode == "按数量"), ((price_label, price_entry, gross_label, gross_entry), mode == "按数量"), (weight_fields, mode == "按过磅重量"), (total_fields, mode == "按结算总额")):
                for widget in widgets:
                    if visible:
                        widget.grid()
                    else:
                        widget.grid_remove()
            weight_price_label.configure(text=f"重量单价（{'含税' if inclusive else '未税'}，元/{vars_['weight_unit'].get()}）*")
            price_label.configure(text="材料单价（含税，元）*" if inclusive else "材料单价（未税，元）*")
            gross_label.configure(text="含税成交单价（元）" if inclusive else "含税单价（约，元）")
            try:
                amounts = procurement_service.calculate_purchase_item_amounts(
                    {"freight_amount_cents": procurement_service.decimal_minor(vars_["freight"].get() or 0)}, pricing_input())
                queued_amount = sum(procurement_service.calculate_purchase_item_amounts({}, item)["line_amount_cents"] for item in pending_items)
                vars_["current_material_gross"].set(f"¥{amounts['line_amount_cents'] / 100:,.2f}")
                vars_["tax_inclusive_unit_price"].set(
                    f"{amounts['tax_inclusive_unit_price_cents'] / 100:.2f}"
                )
                vars_["material_amount"].set(
                    f"{amounts['material_amount_cents'] / 100:.2f}"
                )
                vars_["material_gross"].set(
                    f"{(amounts['line_amount_cents'] + queued_amount) / 100:.2f}"
                )
                vars_["tax_amount"].set(
                    f"{amounts['tax_amount_cents'] / 100:.2f}"
                )
                vars_["project_cost"].set(
                    f"{(amounts['project_cost_cents'] + queued_amount) / 100:.2f}"
                )
            except (ValueError, TypeError):
                for key in (
                    "current_material_gross",
                    "tax_inclusive_unit_price",
                    "material_amount",
                    "material_gross",
                    "tax_amount",
                    "project_cost",
                ):
                    vars_[key].set("--")
            refresh_allocation_preview()
            layout_form()

        def selected_allocation_project_ids():
            return [
                project_id
                for project_id, selected in selected_project_vars.items()
                if selected.get()
            ]

        def all_allocation_projects_selected():
            return bool(selected_project_vars) and all(
                selected.get() for selected in selected_project_vars.values()
            )

        def sync_select_all_projects():
            select_all_projects_button.configure(
                text=(
                    "取消全选"
                    if all_allocation_projects_selected()
                    else "全选项目"
                )
            )

        def toggle_all_allocation_projects():
            select_all = not all_allocation_projects_selected()
            for selected in selected_project_vars.values():
                selected.set(select_all)
            sync_select_all_projects()
            refresh_allocation_preview()

        def allocation_project_selection_changed():
            sync_select_all_projects()
            refresh_allocation_preview()

        def refresh_allocation_preview():
            if vars_["attribution"].get() != "多项目平均分摊":
                allocation_preview_var.set("")
                return
            project_ids = selected_allocation_project_ids()
            if len(project_ids) < 2:
                allocation_preview_var.set("请至少勾选两个项目，金额会自动平均分摊。")
                return
            try:
                total_cents = round(float(vars_["project_cost"].get()) * 100)
                plan = procurement_service.build_equal_allocation_plan(
                    total_cents, project_ids
                )
            except (TypeError, ValueError):
                allocation_preview_var.set("填写数量和价格后，这里会显示分摊结果。")
                return
            allocation_preview_var.set(
                "分摊预览：\n"
                + "\n".join(
                    f"{project_labels[line['project_id']]}  "
                    f"{self.money(line['amount_minor'])}"
                    for line in plan
                )
            )

        def refresh_attribution_ui(*_args):
            is_tool = (
                vars_["category"].get()
                == procurement_service.TOOL_EQUIPMENT_CATEGORY
            )
            if not is_tool:
                vars_["attribution"].set("单项目归集")
                attribution_label.grid_remove()
                attribution_combo.grid_remove()
            else:
                attribution_label.grid()
                attribution_combo.grid()

            if is_tool and vars_["attribution"].get() == "多项目平均分摊":
                project_label.grid_remove()
                project_combo.grid_remove()
                allocation_frame.grid()
            else:
                project_label.grid()
                project_combo.grid()
                allocation_frame.grid_remove()
            sync_select_all_projects()
            refresh_allocation_preview()
            layout_form()

        vars_["qty"].trace_add("write", calculate)
        vars_["price_basis"].trace_add("write", calculate)
        vars_["material_unit_price"].trace_add("write", calculate)
        vars_["tax_rate"].trace_add("write", calculate)
        vars_["freight"].trace_add("write", calculate)
        for key in ("settlement_mode", "net_weight", "weight_unit", "weight_unit_price", "settlement_total"):
            vars_[key].trace_add("write", calculate)
        category_combo.bind("<<ComboboxSelected>>", refresh_attribution_ui)
        attribution_combo.bind("<<ComboboxSelected>>", refresh_attribution_ui)
        refresh_attribution_ui()

        if not is_petty:
            def clear_product_details():
                for key in (
                    "material",
                    "spec",
                    "unit",
                    "material_unit_price",
                    "tax_rate",
                ):
                    vars_[key].set("")

            def load_products(*args):
                supplier_id = supplier_map.get(vars_["supplier"].get())
                products = master_data_service.list_supplier_offers(supplier_id=supplier_id) if supplier_id else []
                products_by_label.clear()
                for product in products:
                    label = f"{product['name']} · {product['specification']}"
                    if label in products_by_label:
                        label = f"{label} · ID {product['id']}"
                    products_by_label[label] = product
                product_combo["values"] = filter_supplier_offer_labels(
                    products_by_label, ""
                )
                vars_["product"].set("")
                clear_product_details()
                if products_by_label:
                    product_hint_var.set(
                        f"当前供应商有 {len(products_by_label)} 条报价；输入材料名称或规格筛选。"
                    )
                else:
                    if vars_["settlement_mode"].get() == "按数量":
                        product_hint_var.set("当前供应商没有可用材料报价，请先维护材料与供应商报价。")
                    else:
                        product_hint_var.set("可直接填写整批材料名称，无需先建立单件报价。")

            def filter_products(event=None):
                if event and event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
                    return
                query = vars_["product"].get().strip()
                if vars_["settlement_mode"].get() != "按数量":
                    vars_["material"].set(query)
                    return
                labels = filter_supplier_offer_labels(products_by_label, query)
                product_combo["values"] = labels
                if query not in products_by_label:
                    clear_product_details()
                if query:
                    if labels:
                        product_hint_var.set(
                            f"找到 {len(labels)} 条匹配；可按方向键选择并按回车确认。"
                        )
                        if event:
                            product_combo.after_idle(
                                lambda: product_combo.event_generate("<Down>")
                            )
                    else:
                        product_hint_var.set(
                            "没有匹配材料，请更换关键词或先到“材料与供应商报价”新增报价。"
                        )
                elif products_by_label:
                    product_hint_var.set(
                        f"当前供应商有 {len(products_by_label)} 条报价；输入材料名称或规格筛选。"
                    )

            def select_product(*args):
                product = products_by_label.get(vars_["product"].get())
                if product:
                    vars_["material"].set(product["name"])
                    vars_["spec"].set(product["specification"] or "")
                    if vars_["settlement_mode"].get() == "按数量":
                        vars_["unit"].set(product["unit"] or "")
                        vars_["price_basis"].set("含税价" if product.get("price_basis") == "inclusive" else "未税价")
                        vars_["material_unit_price"].set(str(product.get("quoted_price", product["price"]) or 0))
                    vars_["tax_rate"].set(str(product["tax_rate_percent"] or 0))
                    product_hint_var.set("按数量使用目录报价；过磅或总额采购请填写本次结算数据，不套用目录单价。")
            supplier_combo.bind("<<ComboboxSelected>>", load_products)
            product_combo.bind("<<ComboboxSelected>>", select_product)
            product_combo.bind("<KeyRelease>", filter_products)
            load_products()
            if order_id and edit_data.get("product_id"):
                for label, product in products_by_label.items():
                    if product["id"] == edit_data["product_id"]:
                        vars_["product"].set(label)
                        break
                select_product()
                # 历史成交单价以采购快照为准，不能被当前产品目录价格覆盖。
                vars_["price_basis"].set("含税价" if edit_data.get("price_basis") == "inclusive" else "未税价")
                vars_["material_unit_price"].set(
                    str(edit_data.get("tax_inclusive_unit_price_cents" if edit_data.get("price_basis") == "inclusive" else "material_unit_price_cents", 0) / 100)
                )
                vars_["tax_rate"].set(
                    str(edit_data.get("tax_rate_bps", 0) / 100)
                )
                vars_["qty"].set(str(edit_data.get("quantity", 1)))
                vars_["freight"].set(
                    str(edit_data.get("freight_amount_cents", 0) / 100)
                )

            if order_id and not edit_data.get("product_id"):
                vars_["product"].set(edit_data.get("material_name_snapshot", ""))
                vars_["material"].set(edit_data.get("material_name_snapshot", ""))
                vars_["spec"].set(edit_data.get("specification_snapshot", ""))
                vars_["tax_rate"].set(str(edit_data.get("tax_rate_bps", 0) / 100))

        calculate()

        def prepare_next_form(saved_material):
            nonlocal continuous_saved_count
            continuous_saved_count += 1
            reset_continuous_purchase_line(vars_)
            for key in ("net_weight", "weight_unit_price", "settlement_total", "weigh_ticket_no"):
                vars_[key].set("")
            product_combo["values"] = filter_supplier_offer_labels(
                products_by_label, ""
            )
            product_hint_var.set(
                f"当前供应商有 {len(products_by_label)} 条报价；继续输入下一种材料。"
            )
            continuous_feedback_var.set(
                f"已保存 {continuous_saved_count} 条，本次：{saved_material}"
            )
            body.yview_moveto(0)
            dialog.after_idle(product_combo.focus_set)

        batch_frame = ttk.Frame(footer)
        batch_frame.pack(fill=X, pady=4)
        batch_tree = ttk.Treeview(batch_frame, columns=("name", "quantity", "amount"), show="headings", height=3)
        for key, label in (("name", "本单已加入材料（双击修改）"), ("quantity", "数量 / 重量"), ("amount", "含税金额")):
            batch_tree.heading(key, text=label)
        batch_tree.pack(side=LEFT, fill=X, expand=True)
        batch_buttons = ttk.Frame(batch_frame)
        batch_buttons.pack(side=RIGHT, padx=8)
        attachment_after_save = ttk.BooleanVar(value=False)
        ttk.Checkbutton(batch_buttons, text="保存后上传整单附件", variable=attachment_after_save).pack(anchor=W)

        def refresh_batch():
            batch_tree.delete(*batch_tree.get_children())
            total = 0
            for index, item in enumerate(pending_items):
                amount = procurement_service.calculate_purchase_item_amounts({}, item)["line_amount_cents"]
                total += amount
                batch_tree.insert("", END, iid=str(index), values=(item["material_name_snapshot"], item.get("quantity", 1), self.money(amount)))
            continuous_feedback_var.set(f"本单已加入 {len(pending_items)} 条，含税材料合计 {self.money(total)} 元；运费整单只计一次。")
            if not vars_["material"].get().strip() and not vars_["product"].get().strip():
                vars_["material_gross"].set(f"{total / 100:.2f}")
                try:
                    freight = procurement_service.decimal_minor(vars_["freight"].get() or 0)
                    vars_["project_cost"].set(f"{(total + freight) / 100:.2f}")
                except ValueError:
                    vars_["project_cost"].set("--")

        def clear_material():
            nonlocal current_item_id
            current_item_id = None
            for key in ("product", "material", "spec", "material_unit_price", "net_weight", "weight_unit_price", "settlement_total", "weigh_ticket_no", "purpose"):
                vars_[key].set("")
            vars_["qty"].set("1")
            vars_["current_material_gross"].set("--")

        def edit_batch(_event=None):
            nonlocal current_item_id
            selected = batch_tree.selection()
            if not selected:
                return
            if vars_["product"].get().strip() or vars_["material"].get().strip():
                messagebox.showwarning("先处理当前材料", "请先将正在填写的材料加入本单，或清空当前材料。", parent=dialog)
                return
            item = pending_items.pop(int(selected[0]))
            current_item_id = item.get("item_id")
            for key, source in (("material", "material_name_snapshot"), ("spec", "specification_snapshot"), ("unit", "unit_snapshot"), ("qty", "quantity"), ("purpose", "purpose"), ("net_weight", "net_weight"), ("weight_unit_price", "weight_unit_price"), ("weigh_ticket_no", "weigh_ticket_no")):
                vars_[key].set(str(item.get(source) or ""))
            vars_["product"].set(next((label for label, product in products_by_label.items() if product["id"] == item.get("product_id")), item["material_name_snapshot"]))
            vars_["settlement_mode"].set(procurement_service.SETTLEMENT_MODES[item.get("settlement_mode", "quantity")])
            vars_["price_basis"].set("含税价" if item.get("price_basis") == "inclusive" else "未税价")
            price_key = "tax_inclusive_unit_price_cents" if item.get("price_basis") == "inclusive" else "material_unit_price_cents"
            vars_["material_unit_price"].set(str(item.get(price_key, 0) / 100))
            vars_["tax_rate"].set(str(item.get("tax_rate_bps", 0) / 100))
            vars_["weight_unit"].set(item.get("weight_unit") or "吨")
            vars_["settlement_total"].set(str((item.get("settlement_total_cents") or 0) / 100))
            refresh_batch()

        def remove_batch():
            selected = batch_tree.selection()
            if selected:
                pending_items.pop(int(selected[0]))
                refresh_batch()

        batch_tree.bind("<Double-1>", edit_batch)
        ttk.Button(batch_buttons, text="加入本单 / 继续填材料", command=lambda: save(stage_only=True)).pack(fill=X)
        ttk.Button(batch_buttons, text="修改选中材料", command=edit_batch).pack(fill=X)
        ttk.Button(batch_buttons, text="删除选中材料", command=remove_batch).pack(fill=X)
        ttk.Button(batch_buttons, text="清空当前材料", command=clear_material).pack(fill=X)
        refresh_batch()

        def save(close_after=True, stage_only=False):
            has_current = bool(vars_["material"].get().strip() or vars_["product"].get().strip())
            if not has_current and (stage_only or not pending_items):
                messagebox.showwarning("请填写材料", "采购单至少需要一条材料明细。", parent=dialog)
                return
            try:
                datetime.strptime(vars_["date"].get().strip(), "%Y-%m-%d")
                pricing = pricing_input() if has_current else pending_items[0]
                procurement_service.calculate_purchase_item_amounts(
                    {"freight_amount_cents": procurement_service.decimal_minor(vars_["freight"].get() or 0)}, pricing)
            except ValueError as error:
                messagebox.showwarning(
                    "提示",
                    str(error),
                    parent=dialog,
                )
                return
            supplier_id = None
            product_id = None
            if is_petty:
                merchant = vars_["merchant"].get().strip()
                material = vars_["material"].get().strip()
            else:
                supplier_id = supplier_map.get(vars_["supplier"].get())
                product = products_by_label.get(vars_["product"].get())
                product_id = product["id"] if product else None
                merchant = vars_["supplier"].get().strip()
                material = vars_["material"].get().strip()
                if pricing["settlement_mode"] != "quantity" and not product_id:
                    material = vars_["product"].get().strip()
            if not merchant or (not is_petty and not supplier_id):
                messagebox.showwarning("提示", "请选择供应商或填写商户名称", parent=dialog)
                return
            if has_current and not is_petty and not product_id and pricing["settlement_mode"] == "quantity":
                messagebox.showwarning(
                    "提示",
                    "输入材料名称或规格后，请从匹配结果中选择材料；没有结果时请先维护材料报价。",
                    parent=dialog,
                )
                product_combo.focus_set()
                return
            if has_current and not material:
                messagebox.showwarning("提示", "请完整填写供应商/商户和材料信息", parent=dialog)
                return
            use_equal_allocation = (
                vars_["category"].get()
                == procurement_service.TOOL_EQUIPMENT_CATEGORY
                and vars_["attribution"].get() == "多项目平均分摊"
            )
            allocation_project_ids = selected_allocation_project_ids()
            if use_equal_allocation and len(allocation_project_ids) < 2:
                messagebox.showwarning(
                    "提示",
                    "工具和设备多项目平均分摊至少需要勾选两个项目。",
                    parent=dialog,
                )
                return
            if not use_equal_allocation and vars_["project"].get() not in project_map:
                messagebox.showwarning(
                    "请选择项目",
                    "请从匹配列表中选择项目；暂时不归属项目时请选择“待归集（稍后分配）”。",
                    parent=dialog,
                )
                project_combo.focus_set()
                return
            selected_project_id = project_map.get(vars_["project"].get())
            if use_equal_allocation:
                allocation_method = "equal"
            elif selected_project_id:
                allocation_method = "direct"
            else:
                allocation_method = "unassigned"
            header = {
                "purchase_type": purchase_type,
                "project_id": None if use_equal_allocation else selected_project_id,
                "project_ids": allocation_project_ids if use_equal_allocation else [],
                "allocation_method": allocation_method,
                "supplier_id": supplier_id,
                "merchant_name_snapshot": merchant,
                "purchase_date": vars_["date"].get().strip(),
                "payment_method": vars_["payment_method"].get(),
                "payment_status": vars_["payment_status"].get(),
                "invoice_status": vars_["invoice"].get(),
                "purchaser": vars_["purchaser"].get().strip(),
                "freight_amount_cents": procurement_service.decimal_minor(vars_["freight"].get() or 0),
                "notes": vars_["notes"].get().strip(),
            }
            if (
                not order_id
                and not stage_only
                and header["allocation_method"] == "unassigned"
                and not messagebox.askyesno(
                    "确认暂不归集",
                    "这笔采购不会进入任何项目成本，将立即出现在“数据治理中心”。\n"
                    "确定仍以待归集状态保存吗？",
                    parent=dialog,
                )
            ):
                return
            item = {
                "item_id": current_item_id,
                "product_id": product_id,
                "material_name_snapshot": material,
                "specification_snapshot": vars_["spec"].get().strip(),
                "unit_snapshot": vars_["unit"].get().strip(),
                "cost_category": vars_["category"].get(),
                **pricing,
                "purpose": vars_["purpose"].get().strip(),
                "notes": vars_["notes"].get().strip(),
            }
            if stage_only:
                pending_items.append(item)
                clear_material()
                refresh_batch()
                return
            items = pending_items + ([item] if has_current else [])
            try:
                if order_id:
                    procurement_service.update_purchase_order(order_id, header, items)
                    saved_order_id = order_id
                else:
                    saved_order_id = procurement_service.add_purchase_order(header, items)
            except ValueError as error:
                messagebox.showwarning("无法保存", str(error), parent=dialog)
                return
            self.month_var.set(vars_["date"].get()[:7])
            if attachment_after_save.get():
                open_attachment_manager(self.parent, "purchase", saved_order_id, "采购单", on_change=self.refresh_all)
            if not close_after and not order_id and not is_petty:
                pending_items.clear()
                prepare_next_form(material)
                clear_material()
                refresh_batch()
                self.refresh_filters()
                self.refresh_all()
                return
            dialog.destroy()
            self.refresh_filters()
            self.refresh_all()

        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text=(
                "保存修改"
                if order_id
                else "保存采购记录"
                if is_petty
                else "保存整单并新增下一单"
            ),
            primary_command=(
                save
                if order_id or is_petty
                else lambda: save(close_after=False)
            ),
            secondary_text=(
                "保存并关闭" if not order_id and not is_petty else None
            ),
            secondary_command=(
                (lambda: save(close_after=True))
                if not order_id and not is_petty else None
            ),
        )

    def edit_selected_purchase(self, tree, purchase_type):
        ids = self.selected_order_ids(tree)
        if not ids:
            messagebox.showwarning("提示", "请先选择要修改的采购记录")
            return
        if len(ids) != 1:
            messagebox.showwarning("提示", "修改时只能选择一条采购记录")
            return
        if purchase_type is None:
            row = self.order_rows.get(tree, {}).get(str(ids[0]))
            if not row:
                return
            purchase_type = row["purchase_type"]
        self.open_purchase_dialog(purchase_type, ids[0])

    def selected_order_ids(self, tree):
        # iid 即采购单 id（fill_order_tree 以 str(row["id"]) 作为 iid）
        return list({int(item) for item in tree.selection()})

    def void_selected(self, tree):
        ids = self.selected_order_ids(tree)
        if not ids:
            messagebox.showwarning("提示", "请先选择采购记录")
            return
        if messagebox.askyesno("确认作废", f"确定作废选中的 {len(ids)} 张采购单？记录会保留审计痕迹。"):
            try:
                procurement_service.void_purchase_orders(ids)
            except Exception as error:
                messagebox.showwarning("无法作废", str(error))
                return
            self.refresh_all()

    def open_status_dialog(self, tree):
        ids = self.selected_order_ids(tree)
        if not ids:
            messagebox.showwarning("提示", "请先选择要更新的采购记录")
            return
        dialog = ttk.Toplevel(self.parent)
        dialog.title("更新支付与票据状态")
        body, footer = build_form_dialog(
            dialog, self.parent, 500, 330,
            min_width=460, min_height=300,
        )
        method_var = ttk.StringVar(value="未记录")
        payment_var = ttk.StringVar(value="未确认")
        invoice_var = ttk.StringVar(value="未确认")
        ttk.Label(body, text=f"将统一更新选中的 {len(ids)} 张采购单").grid(
            row=0, column=0, columnspan=2, sticky=W, pady=(0, 14)
        )
        for row, (label, variable, values) in enumerate([
            ("支付方式", method_var, ["现金", "微信", "支付宝", "对公转账", "员工垫付", "未记录"]),
            ("支付状态", payment_var, ["已付款", "未付款", "未确认"]),
            ("票据状态", invoice_var, ["有发票", "收据", "无发票", "未确认"]),
        ], 1):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky=E, padx=(0, 12), pady=8)
            ttk.Combobox(body, textvariable=variable, values=values, state="readonly").grid(
                row=row, column=1, sticky=EW, pady=8, ipady=4
            )
        body.columnconfigure(1, weight=1)

        def save():
            procurement_service.update_purchase_order_status(
                ids, method_var.get(), payment_var.get(), invoice_var.get()
            )
            dialog.destroy()
            self.refresh_all()

        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text="确认更新",
            primary_command=save,
        )

    def assign_selected(self):
        ids = self.selected_order_ids(self.unassigned_tree)
        if not ids:
            messagebox.showwarning("提示", "请先选择待归集记录")
            return
        projects = self._purchase_projects()
        if not projects:
            self.open_project_dialog(lambda project_id: self._assign(ids, project_id))
            return
        mapping = {f"{p['project_code']} · {p['name']}": p["id"] for p in projects}
        dialog = ttk.Toplevel(self.parent)
        dialog.title("归集到项目")
        body, footer = build_form_dialog(
            dialog, self.parent, 500, 260,
            min_width=460, min_height=240,
        )
        value = ttk.StringVar(value=next(iter(mapping)))
        ttk.Label(body, text=f"将 {len(ids)} 条采购记录归集到：").pack(anchor=W)
        ttk.Combobox(body, textvariable=value, values=list(mapping), state="readonly").pack(fill=X, pady=14, ipady=5)
        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text="确认归集",
            primary_command=lambda: (
                self._assign(ids, mapping[value.get()]),
                dialog.destroy(),
            ),
        )

    def _assign(self, ids, project_id):
        procurement_service.assign_purchase_project(ids, project_id)
        self.refresh_all()


# 新采购入口使用统一采购模型；旧类保留作迁移期兼容。
PurchasePage = PurchaseManagementPage
