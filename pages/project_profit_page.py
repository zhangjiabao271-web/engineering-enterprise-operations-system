import ttkbootstrap as ttk
from ttkbootstrap.constants import (
    BOTH,
    CENTER,
    E,
    END,
    EW,
    LEFT,
    RIGHT,
    W,
    X,
)

from services import project_profit_service, project_service
from ui.components import DataTable, FilterBar, KpiCard, PageHeader
from ui.theme import SPACING


class ProjectProfitPage:
    """Project-level operating profit and cash view with explicit metric scope."""

    def __init__(self, parent):
        self.parent = parent
        self.project_var = ttk.StringVar()
        self.guardrail_var = ttk.StringVar()
        self.project_map = {}
        self.kpi_vars = {
            key: ttk.StringVar(value="--")
            for key in ("settlement", "cost", "profit", "cash")
        }
        self.kpi_hint_vars = {
            key: ttk.StringVar(value="")
            for key in ("settlement", "cost", "profit", "cash")
        }
        self.build_ui()
        self.refresh_projects()
        self.refresh_all()

    @staticmethod
    def money(minor):
        value = int(minor or 0) / 100
        sign = "-" if value < 0 else ""
        return f"{sign}¥{abs(value):,.2f}"

    @staticmethod
    def percent(value):
        return "--" if value is None else f"{value:,.1f}%"

    def build_ui(self):
        PageHeader(
            self.parent,
            "项目经营核算",
            "分别核算每个项目的确认收入、采购、人工、其他成本与经营现金",
            actions=[
                ttk.Label(
                    self.parent, text="利润与现金分开核算",
                    style="StatusChip.TLabel",
                ),
            ],
        )

        self.project_combo = ttk.Combobox(
            self.parent,
            textvariable=self.project_var,
            state="readonly",
            width=28,
        )
        self.project_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh_all()
        )
        FilterBar(
            self.parent,
            ("当前项目", self.project_combo),
            ttk.Label(
                self.parent,
                text="业务事实请在合同、开票回款和成本台账中登记",
                style="Toolbar.TLabel",
            ),
            actions=[
                ttk.Button(
                    self.parent, text="刷新", bootstyle="secondary-outline",
                    command=self.refresh_all,
                ),
            ],
        )

        guardrail = ttk.Frame(
            self.parent, style="Card.TFrame", padding=(14, 9)
        )
        guardrail.pack(fill=X, pady=(0, SPACING["md"]))
        ttk.Label(
            guardrail,
            textvariable=self.guardrail_var,
            style="CardText.TLabel",
            wraplength=850,
            justify=LEFT,
        ).pack(anchor=W)

        self.notebook = ttk.Notebook(self.parent)
        self.notebook.pack(fill=BOTH, expand=True)
        detail_tab = ttk.Frame(self.notebook, padding=(0, 10, 0, 0))
        portfolio_tab = ttk.Frame(self.notebook, padding=(0, 10, 0, 0))
        self.notebook.add(detail_tab, text="项目经营明细")
        self.notebook.add(portfolio_tab, text="全部项目对比")

        self._build_detail_tab(detail_tab)
        self._build_portfolio_tab(portfolio_tab)

    def _build_detail_tab(self, parent):
        kpis = ttk.Frame(parent)
        kpis.pack(fill=X, pady=(0, SPACING["md"]))
        specs = [
            ("settlement", "已确认收入"),
            ("cost", "项目总成本"),
            ("profit", "确认口径毛利"),
            ("cash", "经营现金净额"),
        ]
        for index, (key, label) in enumerate(specs):
            KpiCard(
                kpis, label, self.kpi_vars[key], self.kpi_hint_vars[key],
                hint_wraplength=220,
            ).grid(
                row=0,
                column=index,
                sticky=EW,
                padx=(0 if index == 0 else 6, 0 if index == 3 else 6),
            )
            kpis.columnconfigure(index, weight=1)

        overview = ttk.Panedwindow(parent, orient="horizontal")
        overview.pack(fill=X, pady=(0, SPACING["md"]))
        stage_card = ttk.Frame(
            overview, style="Card.TFrame", padding=12
        )
        cost_card = ttk.Frame(
            overview, style="Card.TFrame", padding=12
        )
        overview.add(stage_card, weight=1)
        overview.add(cost_card, weight=1)

        ttk.Label(
            stage_card, text="收入与履约阶段", style="CardTitle.TLabel"
        ).pack(anchor=W, pady=(0, 6))
        self.stage_tree = DataTable(
            stage_card,
            specs=(
                ("stage", "阶段", 105, W),
                ("amount", "金额", 105, E),
                ("note", "口径说明", 190, W),
            ),
            empty_text="暂无数据",
            stretch=("stage", "note"),
            padding=0,
            height=7,
            pack_fill=X,
            pack_expand=False,
        )

        ttk.Label(
            cost_card, text="成本与现金支出", style="CardTitle.TLabel"
        ).pack(anchor=W, pady=(0, 6))
        self.cost_tree = DataTable(
            cost_card,
            specs=(
                ("category", "类别", 105, W),
                ("amount", "金额", 105, E),
                ("note", "口径说明", 190, W),
            ),
            empty_text="暂无数据",
            stretch=("category", "note"),
            padding=0,
            height=7,
            pack_fill=X,
            pack_expand=False,
        )

        ledger = ttk.Frame(
            parent, style="Card.TFrame", padding=12
        )
        ledger.pack(fill=BOTH, expand=True)
        ledger_header = ttk.Frame(ledger, style="Card.TFrame")
        ledger_header.pack(fill=X, pady=(0, 6))
        ttk.Label(
            ledger_header, text="迁移前手工经营事项（只读）", style="CardTitle.TLabel"
        ).pack(side=LEFT)
        ttk.Label(
            ledger_header,
            text="采购、工天和施工记录自动读取，不要在这里重复登记",
            style="CardText.TLabel",
        ).pack(side=RIGHT)
        self.entry_tree = DataTable(
            ledger,
            specs=(
                ("date", "日期", 90, CENTER),
                ("type", "事项类型", 90, CENTER),
                ("category", "分类", 90, CENTER),
                ("amount", "金额", 105, E),
                ("reference", "单号/依据", 110, CENTER),
                ("counterparty", "往来单位", 120, W),
                ("notes", "说明", 200, W),
            ),
            empty_text="暂无手工经营事项",
            stretch=("counterparty", "notes"),
            padding=0,
            height=6,
        )

    def _build_portfolio_tab(self, parent):
        card = ttk.Frame(parent, style="Card.TFrame", padding=12)
        card.pack(fill=BOTH, expand=True)
        ttk.Label(
            card, text="项目经营结果对比", style="CardTitle.TLabel"
        ).pack(anchor=W)
        ttk.Label(
            card,
            text="双击项目可切换到该项目明细；未登记结算的项目不会把施工产值当作收入。",
            style="CardText.TLabel",
        ).pack(anchor=W, pady=(3, 8))
        self.portfolio_tree = DataTable(
            card,
            specs=(
                ("project", "项目", 120, W),
                ("status", "状态", 60, CENTER),
                ("contract", "合同分配额", 90, E),
                ("settlement", "已结算", 85, E),
                ("cost", "总成本", 85, E),
                ("profit", "毛利", 85, E),
                ("margin", "毛利率", 65, E),
                ("receipt", "已回款", 85, E),
                ("receivable", "应收未收", 85, E),
            ),
            empty_text="暂无项目经营数据",
            stretch=("project",),
            padding=0,
        )
        self.portfolio_tree.tree.bind(
            "<Double-1>", lambda _event: self.open_portfolio_project()
        )

    def refresh_projects(self):
        projects = project_service.list_projects()
        self.project_map = {
            f"{project['name']} · {project['project_code']}": project["id"]
            for project in projects
        }
        self.project_combo["values"] = list(self.project_map)
        if self.project_var.get() not in self.project_map and self.project_map:
            self.project_var.set(next(iter(self.project_map)))

    def selected_project_id(self):
        return self.project_map.get(self.project_var.get())

    def refresh_all(self):
        self.refresh_projects()
        project_id = self.selected_project_id()
        if not project_id:
            self.guardrail_var.set(
                "请先在项目管理中建立项目，利润中心才能开始核算。"
            )
            return
        summary = project_profit_service.get_project_summary(project_id)
        self.refresh_summary(summary)
        self.refresh_entries(project_id)
        self.refresh_portfolio()

    def refresh_summary(self, data):
        self.kpi_vars["settlement"].set(
            self.money(data["settlement_minor"])
        )
        progress = self.percent(data["settlement_progress_percent"])
        self.kpi_hint_vars["settlement"].set(
            f"合同分配额 {self.money(data['contract_minor'])} · 结算进度 {progress}"
        )
        self.kpi_vars["cost"].set(self.money(data["total_cost_minor"]))
        self.kpi_hint_vars["cost"].set(
            f"采购 {self.money(data['purchase_cost_minor'])} · "
            f"其中运费 {self.money(data['purchase_freight_minor'])} · "
            f"人工 {self.money(data['labor_cost_minor'])}"
        )
        self.kpi_vars["profit"].set(self.money(data["gross_profit_minor"]))
        profit_state = "毛利" if data["gross_profit_minor"] >= 0 else "亏损"
        self.kpi_hint_vars["profit"].set(
            f"{profit_state} · 毛利率 {self.percent(data['gross_margin_percent'])}"
        )
        self.kpi_vars["cash"].set(self.money(data["cash_balance_minor"]))
        self.kpi_hint_vars["cash"].set(
            f"回款 {self.money(data['receipt_minor'])} · "
            f"已登记支出 {self.money(data['cash_out_minor'])}"
        )

        purchase_risk = data["unassigned_purchase"]
        labor_risk = data["unassigned_labor"]
        self.guardrail_var.set(
            "口径提示：施工金额只作为现场产值，验收后仍需结算确认才计入收入；"
            f"全系统还有 {purchase_risk['order_count']} 笔采购"
            f"（{self.money(purchase_risk['amount_minor'])}）和 "
            f"{labor_risk['record_count']} 条工天"
            f"（{self.money(labor_risk['amount_minor'])}）未归入任何项目，"
            "这些金额未计入项目利润。"
        )

        stage_rows = [
            ("合同分配额", data["contract_minor"], "当前项目分配口径"),
            ("现场记录产值", data["recorded_minor"], "仅作履约参考"),
            ("已验收产值", data["accepted_minor"], "尚不等同确认收入"),
            ("已确认收入", data["settlement_minor"], "利润计算的收入"),
            ("已开票", data["invoice_minor"], "销项开票记录"),
            ("已回款", data["receipt_minor"], "实际收到现金"),
            ("应收未收", data["receivable_minor"], "已结算减已回款"),
        ]
        self.stage_tree.refresh(
            stage_rows,
            lambda row: (None, (row[0], self.money(row[1]), row[2])),
        )

        cost_rows = [
            (
                "采购材料（未税）",
                data["purchase_material_minor"],
                f"{data['purchase_order_count']} 笔已归集采购",
            ),
            (
                "采购税额",
                data["purchase_tax_minor"],
                "按采购时税率快照计算",
            ),
            (
                "采购运费",
                data["purchase_freight_minor"],
                "按采购单归入所属项目",
            ),
            (
                "采购成本合计",
                data["purchase_cost_minor"],
                "含税材料额 + 运费",
            ),
            (
                "人工成本",
                data["labor_cost_minor"],
                f"{data['labor_record_count']} 条唯一匹配工天",
            ),
            ("其他成本", data["other_cost_minor"], "手工登记成本"),
            ("项目总成本", data["total_cost_minor"], "利润计算的成本"),
            ("已付款采购", data["purchase_paid_minor"], "采购现金流出口"),
            ("现金支出合计", data["cash_out_minor"], "不含未登记人工付款"),
        ]
        self.cost_tree.refresh(
            cost_rows,
            lambda row: (None, (row[0], self.money(row[1]), row[2])),
        )

    def refresh_entries(self, project_id):
        self.entry_tree.refresh(
            project_profit_service.list_entries(project_id),
            lambda row: (None, (
                row["entry_date"],
                project_profit_service.ENTRY_TYPES[row["entry_type"]],
                row.get("category") or "--",
                self.money(row["amount_minor"]),
                row.get("reference_no") or "--",
                row.get("counterparty_name") or "--",
                row.get("notes") or "",
            )),
        )

    def refresh_portfolio(self):
        result = project_profit_service.get_portfolio_summary()
        self.portfolio_tree.refresh(
            result["projects"],
            lambda data: (str(data["project"]["id"]), (
                data["project"]["name"],
                data["project"]["status"],
                self.money(data["contract_minor"]),
                self.money(data["settlement_minor"]),
                self.money(data["total_cost_minor"]),
                self.money(data["gross_profit_minor"]),
                self.percent(data["gross_margin_percent"]),
                self.money(data["receipt_minor"]),
                self.money(data["receivable_minor"]),
            )),
        )

    def open_portfolio_project(self):
        selected = self.portfolio_tree.tree.selection()
        if not selected:
            return
        project_id = int(selected[0])
        label = next(
            (
                label
                for label, value in self.project_map.items()
                if value == project_id
            ),
            None,
        )
        if label:
            self.project_var.set(label)
            self.refresh_all()
            self.notebook.select(0)
