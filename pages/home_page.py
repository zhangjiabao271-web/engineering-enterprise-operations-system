from datetime import datetime

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, CENTER, E, END, EW, LEFT, RIGHT, W, X, Y

from services import operations_service
from ui.components import FilterBar, KpiCard, PageHeader
from ui.dialogs import safe_init_loaders
from ui.scaling import scale_px


class OperationsDashboardPage:
    """Owner-facing project operating cockpit."""

    def __init__(self, parent, navigate):
        self.parent = parent
        self.navigate = navigate
        self.month = datetime.now().strftime("%Y-%m")
        self.kpi_vars = {
            key: ttk.StringVar(value="--")
            for key in ("accountable", "profit", "receivable", "unassigned")
        }
        self.hint_vars = {
            key: ttk.StringVar(value="")
            for key in ("accountable", "profit", "receivable", "unassigned")
        }
        self.driver_vars = {
            key: ttk.StringVar(value="--")
            for key in (
                "settlement",
                "contract",
                "purchase",
                "labor",
                "inspection",
                "unassigned_purchase",
                "unassigned_labor",
            )
        }
        self.kpi_cards = []
        self.kpi_columns = 4
        self.build_ui()
        self.refresh()

    def build_ui(self):
        # ========== 1. 页面头部（组件化）==========
        PageHeader(
            self.parent,
            "经营驾驶舱",
            "从项目独立核算出发，查看利润、现金与待补数据",
        )

        # ========== 2. 工具栏（组件化）==========
        nav_buttons = [
            ttk.Button(
                self.parent, text="项目工作空间", bootstyle="primary",
                command=lambda: self.navigate("workspace"),
            ),
            ttk.Button(
                self.parent, text="合同与结算", bootstyle="primary-outline",
                command=lambda: self.navigate("contract"),
            ),
            ttk.Button(
                self.parent, text="开票与回款", bootstyle="secondary-outline",
                command=lambda: self.navigate("finance"),
            ),
            ttk.Button(
                self.parent, text="成本", bootstyle="secondary-outline",
                command=lambda: self.navigate("cost"),
            ),
        ]
        refresh_btn = ttk.Button(
            self.parent, text="刷新", bootstyle="secondary-outline",
            command=self.refresh,
        )
        self.stat_label = ttk.Label(
            self.parent, text=f"统计期 {self.month}", style="Toolbar.TLabel"
        )
        self.filter_bar = FilterBar(
            self.parent,
            *nav_buttons,
            actions=[refresh_btn, self.stat_label],
        )

        # ========== 3. KPI 卡片行（组件化）==========
        kpi_specs = [
            ("accountable", "项目可核算率"),
            ("profit", "已确认项目毛利"),
            ("receivable", "应收未收"),
            ("unassigned", "未归集成本"),
        ]
        self.kpi_cards = []
        self.kpi_grid = ttk.Frame(self.parent)
        self.kpi_grid.pack(fill=X, pady=(0, 16))
        for index, (key, label) in enumerate(kpi_specs):
            card = KpiCard(
                self.kpi_grid,
                label,
                self.kpi_vars[key],
                self.hint_vars[key],
            )
            card.grid(row=0, column=index, sticky=EW,
                      padx=(0 if index == 0 else 6, 0 if index == 3 else 6))
            self.kpi_grid.columnconfigure(index, weight=1)
            self.kpi_cards.append(card)
        self.parent.bind("<Configure>", self._layout_kpis, add="+")

        # ========== 4. 工程账本式闭环索引 ==========
        spine = ttk.Frame(
            self.parent, style="LedgerSpine.TFrame", padding=(14, 9)
        )
        spine.pack(fill=X, pady=(0, 16))
        spine_title = ttk.Frame(spine)
        spine_title.pack(side=LEFT)
        ttk.Label(
            spine_title, text="经营闭环索引", style="CardTitle.TLabel"
        ).pack(anchor=W)
        ttk.Label(
            spine_title,
            text="覆盖率越低，越应优先补齐",
            style="CardText.TLabel",
        ).pack(anchor=W, pady=(2, 0))

        steps = ttk.Frame(spine)
        steps.pack(side=RIGHT)
        step_specs = (
            ("contract", "合同分配"),
            ("purchase", "采购归集"),
            ("labor", "人工归集"),
            ("settlement", "结算确认"),
        )
        for index, (key, label) in enumerate(step_specs):
            if index:
                ttk.Label(
                    steps, text="────", style="SpineArrow.TLabel"
                ).pack(side=LEFT, padx=8)
            step = ttk.Frame(steps)
            step.pack(side=LEFT)
            ttk.Label(
                step, text=label, style="SpineLabel.TLabel"
            ).pack(anchor=CENTER)
            ttk.Label(
                step,
                textvariable=self.driver_vars[key],
                style="SpineValue.TLabel",
            ).pack(anchor=CENTER, pady=(2, 0))

        # ========== 5. 两栏布局 ==========
        body = ttk.Panedwindow(self.parent, orient="horizontal")
        body.pack(fill=BOTH, expand=True)

        left = ttk.Frame(body)
        right = ttk.Frame(body, width=scale_px(self.parent, 300))
        right.pack_propagate(False)
        body.add(left, weight=3)
        body.add(right, weight=1)

        # ---- 左侧：项目经营表格 ----
        project_card = ttk.Frame(
            left, style="Card.TFrame", padding=(14, 12)
        )
        project_card.pack(fill=BOTH, expand=True, padx=(0, 6))

        card_header = ttk.Frame(project_card, style="Card.TFrame")
        card_header.pack(fill=X, pady=(0, 10))
        title_group = ttk.Frame(card_header, style="Card.TFrame")
        title_group.pack(side=LEFT)
        ttk.Label(
            title_group, text="项目经营闭环", style="CardTitle.TLabel"
        ).pack(anchor=W)
        ttk.Label(
            title_group,
            text="每个地点独立核算；双击项目进入核算明细",
            style="CardText.TLabel",
        ).pack(anchor=W, pady=(3, 0))
        ttk.Button(
            card_header,
            text="打开项目核算",
            bootstyle="link",
            command=lambda: self.navigate("profit"),
        ).pack(side=RIGHT)

        tree_box = ttk.Frame(project_card, style="Card.TFrame")
        tree_box.pack(fill=BOTH, expand=True)

        columns = (
            "project",
            "stage",
            "settlement",
            "cost",
            "profit",
            "cash",
            "gap",
        )
        self.project_tree = ttk.Treeview(
            tree_box,
            columns=columns,
            show="headings",
            height=11,
            bootstyle="primary",
        )
        column_specs = (
            ("project", "项目", 155, W),
            ("stage", "阶段", 76, CENTER),
            ("settlement", "结算确认", 98, E),
            ("cost", "已归集成本", 98, E),
            ("profit", "确认毛利", 98, E),
            ("cash", "现金余额", 98, E),
            ("gap", "当前数据缺口", 190, W),
        )
        for column, title, width, anchor in column_specs:
            self.project_tree.heading(column, text=title)
            self.project_tree.column(
                column,
                width=width,
                minwidth=60,
                anchor=anchor,
                stretch=column in ("project", "gap"),
            )
        scrollbar = ttk.Scrollbar(
            tree_box, orient="vertical", command=self.project_tree.yview
        )
        self.project_tree.configure(yscrollcommand=scrollbar.set)
        self.project_tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.project_tree.bind(
            "<Double-1>", lambda _event: self.navigate("profit")
        )

        # ---- 右侧：可直接处理的经营待办 ----
        driver_card = ttk.Frame(
            right, style="Card.TFrame", padding=(14, 12)
        )
        driver_card.pack(fill=BOTH, expand=True, padx=(6, 0))

        ttk.Label(
            driver_card, text="今天优先处理", style="CardTitle.TLabel"
        ).pack(anchor=W)
        ttk.Label(
            driver_card,
            text="点击问题进入对应业务台账",
            style="CardText.TLabel",
        ).pack(anchor=W, pady=(3, 12))

        action_specs = [
            ("unassigned_purchase", "待归集采购", "purchase"),
            ("unassigned_labor", "未归集人工", "workday"),
            ("inspection", "待验收 / 整改", "construction"),
        ]
        for index, (key, label, destination) in enumerate(action_specs):
            row = ttk.Frame(driver_card, style="Card.TFrame")
            row.pack(fill=X, pady=4)
            ttk.Button(
                row,
                text=label,
                bootstyle="link",
                command=lambda page=destination: self.navigate(page),
            ).pack(side=LEFT)
            ttk.Label(
                row,
                textvariable=self.driver_vars[key],
                style="SummaryValue.TLabel",
            ).pack(side=RIGHT)
            if index < len(action_specs) - 1:
                ttk.Separator(driver_card).pack(fill=X, pady=2)

        ttk.Separator(driver_card).pack(fill=X, pady=(16, 10))
        ttk.Label(
            driver_card, text="核算口径", style="CardTitle.TLabel"
        ).pack(anchor=W)
        ttk.Label(
            driver_card,
            text=(
                "施工记录金额只表示现场产值，不直接计入收入。\n"
                "毛利与现金分别计算，不用回款倒推利润。"
            ),
            style="CardText.TLabel",
            justify=LEFT,
            wraplength=scale_px(self.parent, 220),
        ).pack(anchor=W, pady=(5, 0))

    @staticmethod
    def money(minor):
        amount = int(minor or 0) / 100
        sign = "-" if amount < 0 else ""
        return f"{sign}¥{abs(amount):,.2f}"

    @staticmethod
    def percent(value):
        return "--" if value is None else f"{value:.1f}%"

    def _layout_kpis(self, event):
        if event.widget is not self.parent or event.width < 100:
            return
        columns = 2 if event.width < 850 else 4
        if columns == self.kpi_columns:
            return
        self.kpi_columns = columns
        for index in range(4):
            self.kpi_grid.columnconfigure(
                index, weight=1 if index < columns else 0
            )
        for index, card in enumerate(self.kpi_cards):
            row, column = divmod(index, columns)
            card.grid_configure(
                row=row,
                column=column,
                sticky=EW,
                padx=(
                    0 if column == 0 else 5,
                    0 if column == columns - 1 else 5,
                ),
                pady=(0, 10 if row == 0 and columns == 2 else 0),
            )

    def refresh(self):
        safe_init_loaders("经营驾驶舱", [self._refresh_data])

    def _refresh_data(self):
        overview = operations_service.get_executive_overview(self.month)
        north_star = overview["north_star"]
        summary = overview["summary"]
        drivers = overview["drivers"]

        self.kpi_vars["accountable"].set(
            self.percent(north_star["percent"])
        )
        self.hint_vars["accountable"].set(
            f"{north_star['accountable_project_count']} / "
            f"{north_star['active_project_count']} 个在营项目"
        )
        self.kpi_vars["profit"].set(
            self.money(summary["confirmed_gross_profit_minor"])
        )
        self.hint_vars["profit"].set("仅统计已有结算确认的项目")
        self.kpi_vars["receivable"].set(
            self.money(summary["receivable_minor"])
        )
        self.hint_vars["receivable"].set("收入确认减已收回款")
        self.kpi_vars["unassigned"].set(
            self.money(summary["unassigned_cost_minor"])
        )
        self.hint_vars["unassigned"].set("采购与人工尚未归属项目")

        self.driver_vars["settlement"].set(
            self.percent(drivers["settlement_coverage_percent"])
        )
        self.driver_vars["contract"].set(
            self.percent(drivers["contract_coverage_percent"])
        )
        self.driver_vars["purchase"].set(
            self.percent(drivers["purchase_attribution_percent"])
        )
        self.driver_vars["labor"].set(
            self.percent(drivers["labor_attribution_percent"])
        )
        self.driver_vars["inspection"].set(
            f"{drivers['pending_inspection_count']} 条"
        )
        self.driver_vars["unassigned_purchase"].set(
            f"{drivers['unassigned_purchase_count']} 笔 · "
            f"{self.money(drivers['unassigned_purchase_minor'])}"
        )
        self.driver_vars["unassigned_labor"].set(
            f"{drivers['unassigned_labor_count']} 条 · "
            f"{self.money(drivers['unassigned_labor_minor'])}"
        )

        self.project_tree.delete(*self.project_tree.get_children())
        for row in overview["projects"]:
            profit = (
                self.money(row["gross_profit_minor"])
                if row["is_accountable"]
                else "待结算"
            )
            self.project_tree.insert(
                "",
                END,
                iid=str(row["project_id"]),
                values=(
                    row["project_name"],
                    row["stage_label"],
                    self.money(row["settlement_minor"]),
                    self.money(row["total_cost_minor"]),
                    profit,
                    self.money(row["cash_balance_minor"]),
                    row["gap_text"] or "资料已形成闭环",
                ),
            )
