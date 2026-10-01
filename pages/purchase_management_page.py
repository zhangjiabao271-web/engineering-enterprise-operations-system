from datetime import datetime
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.widgets import ToolTip
from ttkbootstrap.constants import *

from services import attachment_service, master_data_service, procurement_service, project_service
from ui.components import DataTable, FilterBar, KpiCard, PageHeader
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders
from ui.theme import COLORS, SPACING
from ui.scaling import scale_px, working_area
from ui.attachments import open_attachment_manager

from pages.purchase_order_dialog import PurchaseOrderDialogMixin


class PurchaseManagementPage(PurchaseOrderDialogMixin):
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
        self.project_combo.bind("<Unmap>", lambda event: self.project_tooltip.leave(), add="+")
        self.project_combo.bind("<Unmap>", lambda event: self.project_tooltip.leave(), add="+")
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
                ("date", "日期", 120, CENTER),
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
