"""Metric cards for report-led dashboards (numbers first, no color blocks)."""

import ttkbootstrap as ttk
from ttkbootstrap.constants import EW, W

from ui.theme import SPACING


class KpiCard(ttk.Frame):
    """A single KPI: label, big value, optional hint line.

    Numbers use the narrow data font (Bahnschrift) to keep the report feel.
    hint_wraplength: 长 hint 文本的换行宽度（像素），防止窄卡片截字。
    """

    def __init__(self, parent, label, value_var, hint_var=None, *, hint_wraplength=None, **kwargs):
        super().__init__(parent, style="Card.TFrame", padding=(16, 12), **kwargs)
        ttk.Label(self, text=label, style="KpiLabel.TLabel").pack(anchor=W)
        ttk.Label(
            self, textvariable=value_var, style="KpiValue.TLabel"
        ).pack(anchor=W, pady=(10, 2))
        hint_options = {}
        if hint_wraplength:
            hint_options["wraplength"] = hint_wraplength
        ttk.Label(
            self,
            textvariable=hint_var if hint_var is not None else ttk.StringVar(value=""),
            style="KpiHintMuted.TLabel",
            **hint_options,
        ).pack(anchor=W)


class StatCard(ttk.Frame):
    """Smaller stat row used inside panels (label left, value right)."""

    def __init__(self, parent, label, value_var, **kwargs):
        super().__init__(parent, **kwargs)
        self.pack(fill=X, pady=(0, SPACING["sm"]))
        ttk.Label(
            self, text=label, style="SummaryLabel.TLabel"
        ).pack(side=LEFT)
        ttk.Label(
            self, textvariable=value_var, style="SummaryValue.TLabel"
        ).pack(side=RIGHT)


def kpi_grid(parent, specs):
    """Build a uniform KPI card row.

    specs: list of (key, label) tuples; returns a dict key -> KpiCard.
    """
    grid = ttk.Frame(parent)
    grid.pack(fill=X, pady=(0, SPACING["md"]))
    cards = {}
    for index, (key, label) in enumerate(specs):
        card = ttk.Frame(grid, style="Card.TFrame", padding=(16, 12))
        card.grid(
            row=0, column=index, sticky=EW,
            padx=(0 if index == 0 else 6, 0 if index == len(specs) - 1 else 6),
        )
        cards[key] = card
        grid.columnconfigure(index, weight=1)
    return grid, cards
