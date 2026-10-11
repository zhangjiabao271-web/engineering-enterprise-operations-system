"""页面模块统一导出。"""
from .purchase_management_page import PurchaseManagementPage, PurchasePage
from .supplier_page import SupplierPage
from .customer_page import CustomerPage
from .product_page import ProductPage
from .compare_page import ComparePage
from .import_export_page import ImportExportPage
from .ai_page import AIAssistantPage
from .workday_page import WorkdayDashboardPage
from .construction_page import ConstructionRecordPage
from .home_page import OperationsDashboardPage
from .project_page import ProjectManagementPage
from .project_profit_page import ProjectProfitPage
from .contract_page import ContractManagementPage
from .finance_page import ReceivablePage
from .cost_page import CostLedgerPage
from .project_workspace_page import ProjectWorkspacePage
from .data_governance_page import DataGovernancePage

__all__ = [
    "SupplierPage",
    "CustomerPage",
    "ProductPage",
    "ComparePage",
    "ImportExportPage",
    "AIAssistantPage",
    "WorkdayDashboardPage",
    "PurchaseManagementPage",
    "PurchasePage",
    "ConstructionRecordPage", "OperationsDashboardPage", "ProjectManagementPage",
    "ProjectProfitPage",
    "ContractManagementPage",
    "ReceivablePage",
    "CostLedgerPage",
    "ProjectWorkspacePage",
    "DataGovernancePage",
]
