import math
from datetime import datetime
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import master_data_service, procurement_service, project_service
from ui.components import DataTable, DatePicker, FilterBar, KpiCard, PageHeader
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders
from ui.purchase_entry import reset_continuous_purchase_line
from ui.theme import COLORS, SPACING
from ui.typeahead import filter_supplier_offer_labels

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
        self.build_ui()
        safe_init_loaders("采购中心", [self.refresh_filters, self.refresh_all])

    def build_ui(self):
        # 1. 页面头部（组件化）
        PageHeader(
            self.parent,
            "采购中心",
            "统一记录采购业务，成本在到货或验收后确认",
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

        # 2. KPI 卡片行（4个，组件化，去掉图标色块）
        kpi_specs = [
            ("total", "本月采购总额", self.delta_var),
            ("formal", "正式采购", None),
            ("petty", "零星采购", None),
            ("merchants", "供应商 / 商户", None),
        ]
        kpi_grid = ttk.Frame(self.parent)
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
        risk_grid = ttk.Frame(self.parent)
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
                                          width=24, state="readonly")
        self.project_combo.bind("<<ComboboxSelected>>", lambda event: self.refresh_all())
        search_entry = ttk.Entry(self.parent, textvariable=self.search_var, width=22)
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
        self.notebook = ttk.Notebook(self.parent, bootstyle=PRIMARY)
        self.notebook.pack(fill=BOTH, expand=True)

        self.dashboard_tab = ttk.Frame(self.notebook, padding=12)
        self.formal_tab = ttk.Frame(self.notebook, padding=10)
        self.petty_tab = ttk.Frame(self.notebook, padding=10)
        self.unassigned_tab = ttk.Frame(self.notebook, padding=10)

        self.notebook.add(self.dashboard_tab, text="  采购看板  ")
        self.notebook.add(self.formal_tab, text="  正式采购  ")
        self.notebook.add(self.petty_tab, text="  零星采购  ")
        self.notebook.add(self.unassigned_tab, text="  待归集  ")

        # 看板：排行榜
        self.build_dashboard()

        # 正式采购：操作按钮 + 表格（工具条置于表格上方）
        self.formal_frame = self._build_order_table(self.formal_tab, "本月暂无正式采购记录")
        self.formal_tree = self.formal_frame.tree
        tools = self._build_order_tools(self.formal_tab, "正式采购", self.formal_tree)
        tools.pack(fill=X, pady=(0, 8), before=self.formal_frame)

        # 零星采购
        self.petty_frame = self._build_order_table(self.petty_tab, "本月暂无零星采购记录")
        self.petty_tree = self.petty_frame.tree
        tools = self._build_order_tools(self.petty_tab, "零星采购", self.petty_tree)
        tools.pack(fill=X, pady=(0, 8), before=self.petty_frame)

        # 待归集
        self.unassigned_frame = self._build_order_table(self.unassigned_tab, "暂无待归集采购，成本已全部归属项目")
        self.unassigned_tree = self.unassigned_frame.tree
        tools = self._build_unassigned_tools(self.unassigned_tab, self.unassigned_tree)
        tools.pack(fill=X, pady=(0, 8), before=self.unassigned_frame)

    def _build_order_tools(self, parent, purchase_type, tree):
        tools = ttk.Frame(parent)
        ttk.Label(
            tools,
            text="正式供应商和产品档案关联" if purchase_type == "正式采购" else "无需建立供应商或产品档案，可直接记录商户与材料",
            style="CardText.TLabel"
        ).pack(side=LEFT)
        ttk.Button(tools, text="新增", bootstyle=SUCCESS,
                   command=lambda: self.open_purchase_dialog(purchase_type)).pack(side=RIGHT, padx=4)
        ttk.Button(tools, text="作废选中", bootstyle=DANGER,
                   command=lambda: self.void_selected(tree)).pack(side=RIGHT, padx=4)
        ttk.Button(tools, text="更新支付 / 票据", bootstyle=INFO,
                   command=lambda: self.open_status_dialog(tree)).pack(side=RIGHT, padx=4)
        ttk.Button(tools, text="修改选中", bootstyle=WARNING,
                   command=lambda: self.edit_selected_purchase(tree, purchase_type)).pack(side=RIGHT, padx=4)
        return tools

    def _build_unassigned_tools(self, parent, tree):
        tools = ttk.Frame(parent)
        ttk.Label(tools, text="这些采购尚未归属项目，会影响项目成本准确性。", style="CardText.TLabel").pack(side=LEFT)
        ttk.Button(tools, text="归集到项目", bootstyle=SUCCESS,
                   command=self.assign_selected).pack(side=RIGHT, padx=4)
        return tools

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
        """三张采购单列表共用：列定义、多选与状态色令牌。"""
        table = DataTable(
            parent,
            specs=(
                ("no", "单号", 120, CENTER),
                ("date", "日期", 88, CENTER),
                ("project", "项目", 130, CENTER),
                ("merchant", "供应商 / 商户", 140, CENTER),
                ("material", "材料", 120, W),
                ("amount", "金额", 100, CENTER),
                ("payment", "支付", 80, CENTER),
                ("invoice", "票据", 80, CENTER),
                ("status", "状态", 90, CENTER),
            ),
            empty_text=empty_text,
        )
        tree = table.tree
        tree.configure(selectmode="extended")
        # 状态颜色统一走设计令牌，不再使用散落色值
        tree.tag_configure("status_green", foreground=COLORS["accent"])
        tree.tag_configure("status_orange", foreground=COLORS["warning"])
        tree.tag_configure("status_red", foreground=COLORS["danger"])
        return table

    def refresh_filters(self):
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
            self.project_filter_map[f"{project['project_code']} · {project['name']}"] = project["id"]
        self.project_combo["values"] = list(self.project_filter_map)
        if self.project_filter_var.get() not in self.project_filter_map:
            self.project_filter_var.set("全部项目")

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
        month = self.month_var.get()
        project_id = self.selected_project_id()
        keyword = self.search_var.get().strip()
        self.fill_order_tree(self.formal_frame, procurement_service.list_purchase_orders(month, "正式采购", project_id, keyword))
        self.fill_order_tree(self.petty_frame, procurement_service.list_purchase_orders(month, "零星采购", project_id, keyword))
        self.fill_order_tree(self.unassigned_frame, procurement_service.list_purchase_orders(month, project_id=None, keyword=keyword, unassigned_only=True))

    def fill_order_tree(self, table, rows):
        def mapper(row):
            invoice_status = row.get("invoice_status", "")
            payment_status = row.get("payment_status", "")

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
                row["material_name_snapshot"],
                self.money(row["project_cost_cents"]),
                row["payment_method"],
                row["invoice_status"],
                status_text,
            )
            # 行 id 直接作为 iid，取代旧版隐藏的 ID 列
            return str(row["id"]), values, (status_tag,)

        table.refresh(rows, mapper)

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
            dialog, self.parent, 650, 760,
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
            "project": ttk.StringVar(value=project_value),
            "date": ttk.StringVar(value=edit_data.get("purchase_date", datetime.now().strftime("%Y-%m-%d"))),
            "supplier": ttk.StringVar(value=supplier_value),
            "merchant": ttk.StringVar(value=edit_data.get("merchant_name_snapshot", "")),
            "material": ttk.StringVar(value=edit_data.get("material_name_snapshot", "")),
            "spec": ttk.StringVar(value=edit_data.get("specification_snapshot", "")),
            "unit": ttk.StringVar(value=edit_data.get("unit_snapshot", "件")),
            "qty": ttk.StringVar(value=str(edit_data.get("quantity", "1"))),
            "material_unit_price": ttk.StringVar(value=(
                str(edit_data.get("material_unit_price_cents", 0) / 100)
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
        select_all_projects_var = ttk.BooleanVar(value=False)
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
            ).pack(side=LEFT)

        row = 0
        def add_field(label, widget):
            nonlocal row
            label_widget = ttk.Label(body, text=label)
            label_widget.grid(row=row, column=0, sticky=E, padx=(0, 12), pady=6)
            grid_options = {"row": row, "column": 1, "sticky": EW, "pady": 6}
            if not isinstance(widget, DatePicker):
                grid_options["ipady"] = 4
            widget.grid(**grid_options)
            row += 1
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
            state="readonly",
        )
        project_label, project_combo = add_field("所属项目", project_combo)

        allocation_frame = ttk.Frame(body, style="Card.TFrame", padding=12)
        ttk.Label(
            allocation_frame,
            text="选择共同承担这笔工具和设备采购的项目",
            style="CardTitle.TLabel",
        ).pack(anchor=W, pady=(0, 6))
        project_labels = {
            project["id"]: f"{project['project_code']} · {project['name']}"
            for project in projects
        }
        ttk.Checkbutton(
            allocation_frame,
            text="全选项目",
            variable=select_all_projects_var,
            command=lambda: toggle_all_allocation_projects(),
        ).pack(anchor=W, pady=(0, 4))
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
        unit_row = ttk.Frame(body)
        ttk.Entry(unit_row, textvariable=vars_["qty"], width=12).pack(side=LEFT)
        ttk.Label(unit_row, text="  单位  ").pack(side=LEFT)
        ttk.Entry(unit_row, textvariable=vars_["unit"], width=10).pack(side=LEFT)
        add_field("数量 / 单位 *", unit_row)
        add_field("材料单价（未税，元）*", ttk.Entry(body, textvariable=vars_["material_unit_price"]))
        add_field("税率（%）*", ttk.Entry(body, textvariable=vars_["tax_rate"]))
        add_field(
            "含税单价（元）",
            ttk.Entry(body, textvariable=vars_["tax_inclusive_unit_price"], state="readonly"),
        )
        add_field(
            "未税材料额（元）",
            ttk.Entry(body, textvariable=vars_["material_amount"], state="readonly"),
        )
        add_field(
            "税额（元）",
            ttk.Entry(body, textvariable=vars_["tax_amount"], state="readonly"),
        )
        add_field("运费（元）", ttk.Entry(body, textvariable=vars_["freight"]))
        add_field(
            "计入项目成本（元）",
            ttk.Entry(body, textvariable=vars_["project_cost"], state="readonly"),
        )
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

        def calculate(*args):
            try:
                amounts = procurement_service.calculate_purchase_amounts(
                    vars_["qty"].get(),
                    round(float(vars_["material_unit_price"].get()) * 100),
                    round(float(vars_["tax_rate"].get()) * 100),
                    round(float(vars_["freight"].get() or 0) * 100),
                )
                vars_["tax_inclusive_unit_price"].set(
                    f"{amounts['tax_inclusive_unit_price_cents'] / 100:.2f}"
                )
                vars_["material_amount"].set(
                    f"{amounts['material_amount_cents'] / 100:.2f}"
                )
                vars_["tax_amount"].set(
                    f"{amounts['tax_amount_cents'] / 100:.2f}"
                )
                vars_["project_cost"].set(
                    f"{amounts['project_cost_cents'] / 100:.2f}"
                )
            except (ValueError, TypeError):
                for key in (
                    "tax_inclusive_unit_price",
                    "material_amount",
                    "tax_amount",
                    "project_cost",
                ):
                    vars_[key].set("--")
            refresh_allocation_preview()

        def selected_allocation_project_ids():
            return [
                project_id
                for project_id, selected in selected_project_vars.items()
                if selected.get()
            ]

        def sync_select_all_projects():
            selected_values = [
                selected.get() for selected in selected_project_vars.values()
            ]
            select_all_projects_var.set(
                bool(selected_values) and all(selected_values)
            )

        def toggle_all_allocation_projects():
            select_all = select_all_projects_var.get()
            for selected in selected_project_vars.values():
                selected.set(select_all)
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

        vars_["qty"].trace_add("write", calculate)
        vars_["material_unit_price"].trace_add("write", calculate)
        vars_["tax_rate"].trace_add("write", calculate)
        vars_["freight"].trace_add("write", calculate)
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
                    product_hint_var.set("当前供应商没有可用材料报价，请先维护材料与供应商报价。")

            def filter_products(event=None):
                if event and event.keysym in ("Up", "Down", "Return", "Escape", "Tab"):
                    return
                query = vars_["product"].get().strip()
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
                    vars_["unit"].set(product["unit"] or "")
                    vars_["material_unit_price"].set(str(product["price"] or 0))
                    vars_["tax_rate"].set(str(product["tax_rate_percent"] or 0))
                    product_hint_var.set("已选择材料报价，可继续填写数量、运费和用途。")
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
                vars_["material_unit_price"].set(
                    str(edit_data.get("material_unit_price_cents", 0) / 100)
                )
                vars_["tax_rate"].set(
                    str(edit_data.get("tax_rate_bps", 0) / 100)
                )
                vars_["qty"].set(str(edit_data.get("quantity", 1)))
                vars_["freight"].set(
                    str(edit_data.get("freight_amount_cents", 0) / 100)
                )

        def prepare_next_form(saved_material):
            nonlocal continuous_saved_count
            continuous_saved_count += 1
            reset_continuous_purchase_line(vars_)
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

        def save(close_after=True):
            try:
                datetime.strptime(vars_["date"].get().strip(), "%Y-%m-%d")
                quantity = float(vars_["qty"].get())
                material_unit_price = float(vars_["material_unit_price"].get())
                tax_rate = float(vars_["tax_rate"].get())
                freight = float(vars_["freight"].get() or 0)
                if (
                    not all(math.isfinite(value) for value in (
                        quantity, material_unit_price, tax_rate, freight
                    ))
                    or
                    quantity <= 0
                    or material_unit_price < 0
                    or tax_rate < 0
                    or tax_rate > 100
                    or freight < 0
                ):
                    raise ValueError
            except ValueError:
                messagebox.showwarning(
                    "提示",
                    "日期需为 YYYY-MM-DD；数量需大于 0；材料价和运费不能为负数；税率需在 0% 到 100% 之间。",
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
            if not merchant or (not is_petty and not supplier_id):
                messagebox.showwarning("提示", "请选择供应商或填写商户名称", parent=dialog)
                return
            if not is_petty and not product_id:
                messagebox.showwarning(
                    "提示",
                    "输入材料名称或规格后，请从匹配结果中选择材料；没有结果时请先维护材料报价。",
                    parent=dialog,
                )
                product_combo.focus_set()
                return
            if not material:
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
                "freight_amount_cents": round(freight * 100),
                "notes": vars_["notes"].get().strip(),
            }
            if (
                not order_id
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
                "product_id": product_id,
                "material_name_snapshot": material,
                "specification_snapshot": vars_["spec"].get().strip(),
                "unit_snapshot": vars_["unit"].get().strip(),
                "cost_category": vars_["category"].get(),
                "quantity": quantity,
                "material_unit_price_cents": round(material_unit_price * 100),
                "tax_rate_bps": round(tax_rate * 100),
                "purpose": vars_["purpose"].get().strip(),
                "notes": vars_["notes"].get().strip(),
            }
            try:
                if order_id:
                    procurement_service.update_purchase_order(order_id, header, item)
                else:
                    procurement_service.add_purchase_order(header, item)
            except ValueError as error:
                messagebox.showwarning("无法保存", str(error), parent=dialog)
                return
            self.month_var.set(vars_["date"].get()[:7])
            if not close_after and not order_id and not is_petty:
                prepare_next_form(material)
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
                else "保存并继续录入"
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
            procurement_service.void_purchase_orders(ids)
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
