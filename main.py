import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ttkbootstrap.widgets.scrolled import ScrolledFrame
from db.schema import init_db
from pages import (
    AIAssistantPage,
    ComparePage,
    ConstructionRecordPage,
    ContractManagementPage,
    CostLedgerPage,
    CustomerPage,
    DataGovernancePage,
    FundsPage,
    ImportExportPage,
    OperationsDashboardPage,
    ProductPage,
    ProjectProfitPage,
    ProjectManagementPage,
    ProjectWorkspacePage,
    PurchasePage,
    ReceivablePage,
    SupplierPage,
    WorkdayDashboardPage,
)
from ui.scaling import configure_main_window, scale_px
from ui.theme import configure_design_system
from ui.desktop_style import install_desktop_style, navigation_icon
from ui.page_transition import PageTransition
from ui.dialogs import safe_init_loaders
from services.database_maintenance import start_backup_worker

class SupplierManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("工程企业经营系统")
        configure_main_window(self.root, 1400, 900, 1200, 800)

        init_db()
        configure_design_system(self.root)
        install_desktop_style(self.root)

        # 左侧导航
        self.nav_frame = ttk.Frame(
            self.root,
            width=scale_px(self.root, 224),
            style="Sidebar.TFrame",
        )
        self.nav_frame.pack(side=LEFT, fill=Y)
        self.nav_frame.pack_propagate(False)

        brand = ttk.Frame(self.nav_frame, style="Sidebar.TFrame")
        brand.pack(fill=X, padx=scale_px(self.root, 20), pady=(scale_px(self.root, 22), 14))
        ttk.Label(brand, text="工程经营", style="Brand.TLabel").pack(anchor=W)
        ttk.Label(brand, text="业务与资金工作台", style="BrandSub.TLabel").pack(anchor=W, pady=(5, 0))

        self.page_commands = {
            "home": self.show_home_page,
            "governance": self.show_governance_page,
            "supplier": self.show_supplier_page,
            "customer": self.show_customer_page,
            "project": self.show_project_page,
            "profit": self.show_profit_page,
            "workspace": self.show_workspace_page,
            "contract": self.show_contract_page,
            "finance": self.show_finance_page,
            "funds": self.show_funds_page,
            "cost": self.show_cost_page,
            "product": self.show_product_page,
            "compare": self.show_compare_page,
            "purchase": self.show_purchase_page,
            "workday": self.show_workday_page,
            "construction": self.show_construction_page,
            "import_export": self.show_import_export_page,
            "ai": self.show_ai_page,
        }
        self.nav_buttons = {}
        self.nav_icons = {}
        navigation = ScrolledFrame(
            self.nav_frame, autohide=True, bootstyle="secondary", style="Sidebar.TFrame",
        )
        navigation.pack(fill=BOTH, expand=True, padx=scale_px(self.root, 10))
        navigation.container.configure(style="Sidebar.TFrame")
        nav_groups = [
            ("经营决策", [
                ("home", "经营驾驶舱"),
                ("governance", "数据治理中心"),
                ("profit", "项目经营核算"),
            ]),
            ("合同资金", [
                ("contract", "合同与结算"),
                ("finance", "开票与回款"),
                ("funds", "资金预算"),
                ("cost", "成本"),
            ]),
            ("项目履约", [
                ("project", "项目台账"),
                ("construction", "施工与验收"),
                ("purchase", "采购管理"),
                ("workday", "人工与工天"),
            ]),
            ("供应链资料", [
                ("supplier", "供应商"),
                ("customer", "客户"),
                ("product", "材料与报价"),
                ("compare", "报价对比"),
            ]),
            ("数据工具", [
                ("ai", "AI 经营助手"),
                ("import_export", "数据导入导出"),
            ]),
        ]
        for group_name, items in nav_groups:
            ttk.Label(navigation, text=group_name, style="NavSection.TLabel").pack(
                fill=X, padx=scale_px(self.root, 12), pady=(10, 4)
            )
            for key, text in items:
                row = ttk.Frame(navigation, style="Sidebar.TFrame")
                row.pack(fill=X, pady=1)
                normal_icon = navigation_icon(self.root, key, "#63636B")
                active_icon = navigation_icon(self.root, key, "#FFFFFF")
                self.nav_icons[key] = (normal_icon, active_icon)
                btn = ttk.Button(
                    row,
                    text=text,
                    style="Nav.TButton",
                    image=normal_icon,
                    compound=LEFT,
                    command=lambda page_key=key: self.navigate_to(page_key),
                )
                btn.pack(side=LEFT, fill=X, expand=True)
                self.nav_buttons[key] = btn

        status = ttk.Frame(self.nav_frame, style="Sidebar.TFrame")
        status.pack(
            side=BOTTOM,
            fill=X,
            padx=18,
            pady=(4, scale_px(self.root, 8)),
        )
        ttk.Separator(status).pack(fill=X, pady=(0, 4))
        ttk.Label(
            status,
            text="数据保存在本机",
            style="SidebarStatus.TLabel",
        ).pack(anchor=W)

        # A stable host clips the short entrance animation of each new page.
        self.content_host = ttk.Frame(
            self.root, style="App.TFrame",
        )
        self.content_host.pack(side=LEFT, fill=BOTH, expand=True)
        # Reuse five common synchronous views, never dialogs or AI jobs.
        self._page_views = {}
        self.page_transition = PageTransition(self.content_host, self._retire_content)
        self.motion_var = ttk.BooleanVar(value=self.page_transition.enabled)
        self.root._motion_enabled = self.motion_var
        motion_toggle = ttk.Checkbutton(
            status, text="页面切换动效", variable=self.motion_var,
            bootstyle="round-toggle",
            command=lambda: self.page_transition.set_enabled(self.motion_var.get()),
        )
        toggle_style = motion_toggle.cget("style")
        toggle_layout = self.root.style.layout(toggle_style)[0][1]["children"]
        self.root.style.layout("Sidebar.Round.Toggle", toggle_layout)
        self.root.style.configure("Sidebar.Round.Toggle", background="#F2F2F5", foreground="#63636B")
        self.root.style.map(
            "Sidebar.Round.Toggle",
            background=[("!disabled", "#F2F2F5")],
            foreground=[("!disabled", "#63636B")],
        )
        motion_toggle.configure(style="Sidebar.Round.Toggle")
        motion_toggle.pack(anchor=W, pady=(8, 0))

        # 当前页面标记
        self.current_page = None
        self.show_home_page()

    def navigate_to(self, page_key, project_id=None):
        if page_key == self.current_page and project_id is None:
            return
        command = self.page_commands.get(page_key)
        if command:
            if project_id is not None and page_key in {"profit", "contract", "finance"}:
                command(project_id=project_id)
            else:
                command()

    def set_active_nav(self, page_key):
        for key, button in self.nav_buttons.items():
            button.configure(style="NavActive.TButton" if key == page_key else "Nav.TButton")
            button.configure(image=self.nav_icons[key][int(key == page_key)])

    def _retire_content(self, frame):
        if any(frame is entry[0] for entry in self._page_views.values()):
            frame.place_forget()
        else:
            frame.destroy()

    def clear_content(self, frame=None):
        self.content_frame = self.page_transition.new_page(frame)

    def _show_reusable_page(self, key, factory, loaders, project_id=None):
        entry = self._page_views.get(key)
        self.set_active_nav(key)
        self.clear_content(entry[0] if entry is not None else None)
        self.current_page = key
        if entry is None:
            page = factory(self.content_frame)
            self._page_views[key] = (self.content_frame, page)
        else:
            page = entry[1]
            if project_id is not None:
                page.initial_project_id = project_id
            # Always reload facts; only widgets and user filters are reused.
            safe_init_loaders(key, [getattr(page, name) for name in loaders])

    def show_home_page(self):
        self._show_reusable_page(
            "home", lambda frame: OperationsDashboardPage(frame, self.navigate_to),
            ("refresh",),
        )

    def show_governance_page(self):
        self.clear_content()
        self.current_page = "governance"
        self.set_active_nav("governance")
        DataGovernancePage(self.content_frame, self.navigate_to)

    def show_supplier_page(self):
        self._show_reusable_page("supplier", SupplierPage, ("load_data",))

    def show_customer_page(self):
        self.clear_content()
        self.current_page = "customer"
        self.set_active_nav("customer")
        CustomerPage(self.content_frame)

    def show_project_page(self):
        self.clear_content()
        self.current_page = "project"
        self.set_active_nav("project")
        ProjectManagementPage(self.content_frame)

    def show_profit_page(self, project_id=None):
        self.clear_content()
        self.current_page = "profit"
        self.set_active_nav("profit")
        ProjectProfitPage(
            self.content_frame, navigate=self.navigate_to,
            initial_project_id=project_id,
        )

    def show_workspace_page(self):
        self.clear_content()
        self.current_page = "workspace"
        self.set_active_nav("workspace")
        ProjectWorkspacePage(self.content_frame, self.navigate_to)

    def show_contract_page(self, project_id=None):
        self.clear_content()
        self.current_page = "contract"
        self.set_active_nav("contract")
        ContractManagementPage(self.content_frame, initial_project_id=project_id)

    def show_finance_page(self, project_id=None):
        self._show_reusable_page(
            "finance", lambda frame: ReceivablePage(frame, initial_project_id=project_id),
            ("refresh",), project_id=project_id,
        )

    def show_funds_page(self):
        self.clear_content()
        self.current_page = "funds"
        self.set_active_nav("funds")
        FundsPage(self.content_frame, self.navigate_to)

    def show_cost_page(self):
        self.clear_content()
        self.current_page = "cost"
        self.set_active_nav("cost")
        CostLedgerPage(self.content_frame)

    def show_product_page(self):
        self.clear_content()
        self.current_page = "product"
        self.set_active_nav("product")
        ProductPage(self.content_frame)

    def show_purchase_page(self):
        self._show_reusable_page(
            "purchase", PurchasePage, ("refresh_filters", "refresh_all"),
        )

    def show_workday_page(self):
        self._show_reusable_page(
            "workday", WorkdayDashboardPage, ("refresh_months", "refresh_all"),
        )

    def show_construction_page(self):
        self.clear_content()
        self.current_page = "construction"
        self.set_active_nav("construction")
        ConstructionRecordPage(self.content_frame)

    def show_compare_page(self):
        self.clear_content()
        self.current_page = "compare"
        self.set_active_nav("compare")
        ComparePage(self.content_frame)

    def show_import_export_page(self):
        self.clear_content()
        self.current_page = "import_export"
        self.set_active_nav("import_export")
        ImportExportPage(self.content_frame)

    def show_ai_page(self):
        self.clear_content()
        self.current_page = "ai"
        self.set_active_nav("ai")
        AIAssistantPage(self.content_frame, self.navigate_to)

def main():
    root = ttk.Window(themename="flatly")
    app = SupplierManagerApp(root)
    stop_backup = start_backup_worker()
    try:
        root.mainloop()
    finally:
        stop_backup.set()


if __name__ == "__main__":
    main()
