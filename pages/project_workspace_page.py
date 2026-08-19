import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from ui.charts import DonutBreakdown, HorizontalBreakdown
from services import (
    contract_service,
    cost_service,
    finance_service,
    operations_service,
    project_profit_service,
    project_service,
)
from ui.components import DataTable, FilterBar, KpiCard, PageHeader
from ui.dialogs import safe_init_loaders
from ui.theme import SPACING


class ProjectWorkspacePage:
    """One project, all operating facts."""

    def __init__(self, parent, navigate):
        self.parent = parent
        self.navigate = navigate
        self.project_map = {}
        self.project_var = ttk.StringVar()
        self.stage_var = ttk.StringVar(value="--")
        self.gap_var = ttk.StringVar(value="")
        self.kpi_vars = {
            key: ttk.StringVar(value="--")
            for key in ("settlement", "cost", "profit", "cash")
        }
        self.build_ui()
        safe_init_loaders("项目工作空间", [self.refresh_projects])

    @staticmethod
    def money(value):
        amount = int(value or 0) / 100
        return f"{'-' if amount < 0 else ''}¥{abs(amount):,.2f}"

    @staticmethod
    def percent(value):
        return f"{float(value or 0):.1f}%"

    def build_ui(self):
        PageHeader(
            self.parent,
            "项目工作空间",
            "一个项目内查看合同、履约、成本、结算、开票和回款",
            actions=[
                ttk.Button(
                    self.parent, text="项目台账", bootstyle="secondary-outline",
                    command=lambda: self.navigate("project"),
                ),
            ],
        )

        # 工具栏（组件化）：当前项目 + 经营阶段 + 跨页入口
        self.project_combo = ttk.Combobox(
            self.parent, textvariable=self.project_var,
            state="readonly", width=32
        )
        self.project_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh()
        )
        stage_label = ttk.Label(
            self.parent, textvariable=self.stage_var, style="DriverValue.TLabel"
        )
        FilterBar(
            self.parent,
            ("当前项目", self.project_combo),
            ("经营阶段", stage_label),
            actions=[
                ttk.Button(
                    self.parent, text="开票回款", bootstyle="link",
                    command=lambda: self.navigate("finance"),
                ),
                ttk.Button(
                    self.parent, text="合同结算", bootstyle="link",
                    command=lambda: self.navigate("contract"),
                ),
            ],
        )
        ttk.Label(
            self.parent, textvariable=self.gap_var, style="Toolbar.TLabel"
        ).pack(fill=X, pady=(0, SPACING["md"]))

        kpis = ttk.Frame(self.parent)
        kpis.pack(fill=X, pady=(0, SPACING["md"]))
        specs = (
            ("settlement", "结算确认"),
            ("cost", "项目总成本"),
            ("profit", "确认毛利"),
            ("cash", "经营现金余额"),
        )
        for index, (key, label) in enumerate(specs):
            KpiCard(kpis, label, self.kpi_vars[key]).grid(
                row=0, column=index, sticky=EW,
                padx=(0 if index == 0 else 6, 0 if index == 3 else 6),
            )
            kpis.columnconfigure(index, weight=1)

        self.notebook = ttk.Notebook(self.parent)
        self.notebook.pack(fill=BOTH, expand=True)
        contract_tab = ttk.Frame(self.notebook, padding=(0, 10, 0, 0))
        cost_tab = ttk.Frame(self.notebook, padding=(0, 10, 0, 0))
        cash_tab = ttk.Frame(self.notebook, padding=(0, 10, 0, 0))
        self.notebook.add(contract_tab, text="合同与结算")
        self.notebook.add(cost_tab, text="成本来源")
        self.notebook.add(cash_tab, text="开票与回款")

        contract_pane = ttk.Panedwindow(contract_tab, orient=VERTICAL)
        contract_pane.pack(fill=BOTH, expand=True)
        allocation_box = ttk.Frame(contract_pane)
        settlement_box = ttk.Frame(contract_pane)
        contract_pane.add(allocation_box, weight=1)
        contract_pane.add(settlement_box, weight=1)
        self.allocation_tree = self._section_table(
            allocation_box,
            "合同分配",
            (
                ("contract", "合同", 250, W),
                ("amount", "分配金额", 130, E),
                ("notes", "说明", 360, W),
            ),
            empty_text="暂无合同分配记录",
            stretch=("contract", "notes"),
        )
        self.settlement_tree = self._section_table(
            settlement_box,
            "结算确认",
            (
                ("date", "结算日期", 110, CENTER),
                ("no", "结算编号", 170, W),
                ("contract", "合同", 180, W),
                ("amount", "结算金额", 130, E),
                ("invoice", "已开票 / 比例", 170, E),
                ("remaining", "待开票", 120, E),
            ),
            empty_text="暂无结算确认记录",
            stretch=("no", "contract"),
        )
        cost_notebook = ttk.Notebook(cost_tab)
        cost_notebook.pack(fill=BOTH, expand=True)
        cost_structure_tab = ttk.Frame(
            cost_notebook, padding=(0, 10, 0, 0)
        )
        cost_detail_tab = ttk.Frame(
            cost_notebook, padding=(0, 10, 0, 0)
        )
        cost_notebook.add(cost_structure_tab, text="成本结构")
        cost_notebook.add(cost_detail_tab, text="原始成本明细")

        structure = ttk.Panedwindow(cost_structure_tab, orient=HORIZONTAL)
        structure.pack(fill=BOTH, expand=True)
        composition_card = ttk.Frame(
            structure, style="Card.TFrame", padding=(14, 12)
        )
        material_card = ttk.Frame(
            structure, style="Card.TFrame", padding=(14, 12)
        )
        structure.add(composition_card, weight=4)
        structure.add(material_card, weight=6)
        ttk.Label(
            composition_card, text="项目成本结构", style="CardTitle.TLabel"
        ).pack(anchor=W)
        ttk.Label(
            composition_card,
            text="材料、人工、采购运费与其他成本",
            style="CardText.TLabel",
        ).pack(anchor=W, pady=(3, 8))
        self.cost_composition = DonutBreakdown(composition_card)
        self.cost_composition.pack(fill=BOTH, expand=True)

        ttk.Label(
            material_card, text="材料费用排行", style="CardTitle.TLabel"
        ).pack(anchor=W)
        ttk.Label(
            material_card,
            text="按含税材料金额统计，显示材料费占比",
            style="CardText.TLabel",
        ).pack(anchor=W, pady=(3, 14))
        self.material_breakdown = HorizontalBreakdown(material_card, limit=7)
        self.material_breakdown.pack(fill=BOTH, expand=True)

        self.cost_tree = self._section_table(
            cost_detail_tab,
            "成本明细",
            (
                ("date", "日期", 100, CENTER),
                ("source", "来源", 110, CENTER),
                ("no", "来源单号", 170, W),
                ("category", "分类", 120, W),
                ("counterparty", "往来单位 / 人员", 220, W),
                ("amount", "金额", 130, E),
            ),
            empty_text="暂无成本明细",
            stretch=("no", "category", "counterparty"),
        )

        cash_pane = ttk.Panedwindow(cash_tab, orient=VERTICAL)
        cash_pane.pack(fill=BOTH, expand=True)
        invoice_box = ttk.Frame(cash_pane)
        receipt_box = ttk.Frame(cash_pane)
        cash_pane.add(invoice_box, weight=1)
        cash_pane.add(receipt_box, weight=1)
        self.invoice_tree = self._section_table(
            invoice_box,
            "销项发票",
            (
                ("date", "开票日期", 110, CENTER),
                ("no", "发票号码", 190, W),
                ("contract", "合同", 180, W),
                ("settlement", "收入确认", 150, W),
                ("buyer", "购买方", 180, W),
                ("amount", "价税合计", 130, E),
            ),
            empty_text="暂无销项发票",
            stretch=("no", "contract", "settlement", "buyer"),
        )
        self.receipt_tree = self._section_table(
            receipt_box,
            "回款记录",
            (
                ("date", "回款日期", 110, CENTER),
                ("no", "回款单号", 190, W),
                ("contract", "合同", 180, W),
                ("payer", "付款方", 220, W),
                ("amount", "回款金额", 130, E),
            ),
            empty_text="暂无回款记录",
            stretch=("no", "contract", "payer"),
        )

    @staticmethod
    def _section_table(parent, title, specs, *, empty_text="暂无数据", stretch=None):
        """带标题卡片区的小表：CardTitle + DataTable（height=5，仅展示）。"""
        card = ttk.Frame(parent, style="Card.TFrame", padding=(10, 8))
        card.pack(fill=BOTH, expand=True, pady=(0, 5))
        ttk.Label(
            card, text=title, style="CardTitle.TLabel"
        ).pack(anchor=W, pady=(0, 6))
        table = DataTable(card, specs=specs, empty_text=empty_text, stretch=stretch)
        table.tree.configure(height=5)
        return table

    def refresh_projects(self):
        projects = project_service.list_projects()
        self.project_map = {
            f"{row['name']} · {row['project_code']}": row["id"]
            for row in projects
        }
        self.project_combo.configure(values=list(self.project_map))
        if not projects:
            return
        if self.project_var.get() not in self.project_map:
            self.project_var.set(next(iter(self.project_map)))
        self.refresh()

    def refresh(self):
        project_id = self.project_map.get(self.project_var.get())
        if not project_id:
            return
        summary = project_profit_service.get_project_summary(project_id)
        overview = operations_service.get_executive_overview()
        project_state = next(
            row for row in overview["projects"]
            if row["project_id"] == project_id
        )
        self.stage_var.set(project_state["stage_label"])
        self.gap_var.set(
            f"待补：{project_state['gap_text']}"
            if project_state["gap_text"] else "经营资料已形成闭环"
        )
        self.kpi_vars["settlement"].set(
            self.money(summary["settlement_minor"])
        )
        self.kpi_vars["cost"].set(self.money(summary["total_cost_minor"]))
        self.kpi_vars["profit"].set(
            self.money(summary["gross_profit_minor"])
            if project_state["is_accountable"] else "待结算"
        )
        self.kpi_vars["cash"].set(
            self.money(summary["cash_balance_minor"])
        )

        self.cost_composition.set_data(
            (
                ("材料", summary["purchase_tax_inclusive_material_minor"]),
                ("人工", summary["labor_cost_minor"]),
                ("采购运费", summary["purchase_freight_minor"]),
                ("其他成本", summary["other_cost_minor"]),
            )
        )
        self.material_breakdown.set_data(
            summary["purchase_material_breakdown"]
        )

        self.allocation_tree.refresh(
            contract_service.list_allocations(project_id=project_id),
            lambda row: (None, (
                f"{row['contract_no']} · {row['contract_name']}",
                self.money(row["allocated_amount_minor"]),
                row["notes"] or "",
            )),
        )
        self.settlement_tree.refresh(
            contract_service.list_settlements(project_id=project_id),
            lambda row: (None, (
                row["settlement_date"],
                row["settlement_no"],
                row["contract_no"],
                self.money(row["amount_minor"]),
                (
                    f"{self.money(row['invoiced_minor'])} · "
                    f"{self.percent(row['invoice_rate_percent'])}"
                ),
                self.money(row["uninvoiced_minor"]),
            )),
        )

        source_names = {
            "purchase": "采购",
            "labor": "人工",
            "manual": "其他成本",
        }
        self.cost_tree.refresh(
            cost_service.list_cost_ledger(project_id),
            lambda row: (None, (
                row["business_date"],
                source_names[row["source_type"]],
                row["source_no"],
                row["category"],
                row["counterparty"],
                self.money(row["amount_minor"]),
            )),
        )

        self.invoice_tree.refresh(
            finance_service.list_invoices(project_id),
            lambda row: (None, (
                row["invoice_date"],
                row["invoice_no"],
                row["contract_no"],
                row["settlement_no"] or "未关联",
                row["buyer_name_snapshot"],
                self.money(row["amount_minor"]),
            )),
        )
        self.receipt_tree.refresh(
            finance_service.list_receipts(project_id),
            lambda row: (None, (
                row["receipt_date"],
                row["receipt_no"],
                row["contract_no"],
                row["payer_name_snapshot"],
                self.money(row["allocated_amount_minor"]),
            )),
        )
