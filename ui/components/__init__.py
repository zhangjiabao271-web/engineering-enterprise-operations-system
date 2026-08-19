"""Public component library for report-led pages.

Modules:
- layout:   PageHeader, FilterBar, SectionPanel, BottomToolbar
- metrics:  KpiCard, StatCard, kpi_grid
- table:    DataTable (sortable, empty-state)
- feedback: EmptyHint, ErrorBanner, StateLabel
"""

from ui.components.feedback import EmptyHint, ErrorBanner, StateLabel
from ui.components.date_picker import DatePicker
from ui.components.layout import (
    BottomToolbar,
    FilterBar,
    PageHeader,
    SectionPanel,
)
from ui.components.metrics import KpiCard, StatCard, kpi_grid
from ui.components.table import DataTable

__all__ = [
    "BottomToolbar",
    "DataTable",
    "DatePicker",
    "EmptyHint",
    "ErrorBanner",
    "FilterBar",
    "KpiCard",
    "PageHeader",
    "SectionPanel",
    "StateLabel",
    "StatCard",
    "kpi_grid",
]
