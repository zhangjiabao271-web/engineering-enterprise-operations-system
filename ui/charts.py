"""Small dependency-free charts for the desktop operating dashboards."""

import tkinter as tk

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, LEFT, W, X
from ttkbootstrap.widgets.scrolled import ScrolledFrame

from ui.theme import COLORS, FONT_BODY, FONT_BODY_MEDIUM


def _money(amount_minor):
    amount = int(amount_minor or 0) / 100
    return f"{'-' if amount < 0 else ''}¥{abs(amount):,.2f}"


def _axis_money(amount_minor):
    amount = int(amount_minor or 0) / 100
    if abs(amount) >= 10000:
        return f"¥{amount / 10000:.1f}万"
    if abs(amount) >= 1000:
        return f"¥{amount / 1000:.1f}千"
    return f"¥{amount:,.0f}"


class MonthlyBarChart(ttk.Frame):
    """Zero-based monthly amount comparison with direct labels."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, style="Card.TFrame", **kwargs)
        self.items = []
        self.canvas = tk.Canvas(
            self,
            height=220,
            background=COLORS["surface"],
            highlightthickness=0,
        )
        self.canvas.pack(fill=BOTH, expand=True)
        self.canvas.bind("<Configure>", lambda _event: self._draw())

    def set_data(self, items):
        self.items = [
            {
                "month": str(item.get("month") or "")[:7],
                "amount_minor": int(item.get("amount_minor") or 0),
            }
            for item in items or []
            if str(item.get("month") or "")[:7]
        ]
        self.items.sort(key=lambda item: item["month"])
        self._draw()

    def _draw(self):
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 360)
        height = max(self.canvas.winfo_height(), 190)
        if not self.items:
            self.canvas.create_text(
                width / 2,
                height / 2,
                text="当前范围暂无月度数据",
                fill=COLORS["text_muted"],
                font=FONT_BODY_MEDIUM,
            )
            return

        left, right, top, bottom = 76, 16, 26, 34
        chart_width = max(1, width - left - right)
        chart_height = max(1, height - top - bottom)
        maximum = max(item["amount_minor"] for item in self.items) or 1
        for step in range(4):
            ratio = step / 3
            y = top + chart_height * (1 - ratio)
            self.canvas.create_line(
                left,
                y,
                width - right,
                y,
                fill=COLORS["border"],
                width=1,
            )
            self.canvas.create_text(
                left - 8,
                y,
                text=_axis_money(maximum * ratio),
                anchor="e",
                fill=COLORS["text_muted"],
                font=("Segoe UI", 8),
            )

        slot = chart_width / max(len(self.items), 1)
        bar_width = max(10, min(34, slot * 0.52))
        show_values = len(self.items) <= 8 and slot >= 48
        for index, item in enumerate(self.items):
            center = left + slot * (index + 0.5)
            ratio = item["amount_minor"] / maximum
            bar_height = chart_height * ratio
            y1 = top + chart_height - bar_height
            self.canvas.create_rectangle(
                center - bar_width / 2,
                y1,
                center + bar_width / 2,
                top + chart_height,
                fill=COLORS["primary"],
                outline=COLORS["primary_hover"],
                width=1,
            )
            month = item["month"].split("-")[-1].lstrip("0") or "0"
            self.canvas.create_text(
                center,
                height - 16,
                text=f"{month}月",
                fill=COLORS["text_muted"],
                font=FONT_BODY,
            )
            if show_values and item["amount_minor"]:
                self.canvas.create_text(
                    center,
                    max(10, y1 - 10),
                    text=_axis_money(item["amount_minor"]),
                    fill=COLORS["text"],
                    font=("Segoe UI", 8),
                )


class DonutBreakdown(ttk.Frame):
    """Cost composition donut with a text legend that does not rely on color."""

    def __init__(self, parent, colors=None, **kwargs):
        super().__init__(parent, style="Card.TFrame", **kwargs)
        self.colors = colors or (
            COLORS["primary"],
            COLORS["accent"],
            COLORS["cost_freight"],
            COLORS["cost_other"],
        )
        self.items = []
        self.canvas = tk.Canvas(
            self,
            width=230,
            height=230,
            background=COLORS["surface"],
            highlightthickness=0,
        )
        self.canvas.pack(side=LEFT, fill=BOTH, expand=True)
        self.legend = ttk.Frame(self)
        self.legend.pack(side=LEFT, fill=BOTH, expand=True, padx=(14, 0))
        self.canvas.bind("<Configure>", lambda _event: self._draw())

    def set_data(self, items):
        self.items = [
            (label, int(amount or 0))
            for label, amount in items
            if int(amount or 0) > 0
        ]
        self._build_legend()
        self._draw()

    def _build_legend(self):
        for child in self.legend.winfo_children():
            child.destroy()
        total = sum(amount for _label, amount in self.items)
        if not total:
            ttk.Label(
                self.legend,
                text="当前项目暂无成本",
                style="CardText.TLabel",
            ).pack(anchor=W, pady=(72, 0))
            return
        for index, (label, amount) in enumerate(self.items):
            row = ttk.Frame(self.legend)
            row.pack(fill=X, pady=(0, 2))
            swatch = tk.Canvas(
                row,
                width=10,
                height=10,
                background=COLORS["surface"],
                highlightthickness=0,
            )
            swatch.create_oval(
                1, 1, 9, 9,
                fill=self.colors[index % len(self.colors)],
                outline="",
            )
            swatch.pack(side=LEFT, padx=(0, 8))
            ttk.Label(
                row, text=label, style="CardText.TLabel"
            ).pack(side=LEFT)
            percent = amount / total * 100
            ttk.Label(
                row,
                text=f"{_money(amount)}  ·  {percent:.1f}%",
                style="SummaryValue.TLabel",
            ).pack(side=LEFT, padx=(8, 0))

    def _draw(self):
        self.canvas.delete("all")
        width = max(self.canvas.winfo_width(), 180)
        height = max(self.canvas.winfo_height(), 180)
        total = sum(amount for _label, amount in self.items)
        size = max(130, min(width - 24, height - 24, 210))
        left = (width - size) / 2
        top = (height - size) / 2
        bounds = (left, top, left + size, top + size)
        if total:
            start = 90
            for index, (_label, amount) in enumerate(self.items):
                extent = -(amount / total * 360)
                self.canvas.create_arc(
                    *bounds,
                    start=start,
                    extent=extent,
                    fill=self.colors[index % len(self.colors)],
                    outline=COLORS["surface"],
                    width=2,
                )
                start += extent
            inset = size * 0.27
            self.canvas.create_oval(
                left + inset,
                top + inset,
                left + size - inset,
                top + size - inset,
                fill=COLORS["surface"],
                outline=COLORS["surface"],
            )
            self.canvas.create_text(
                width / 2,
                height / 2 - 10,
                text="项目总成本",
                fill=COLORS["text_muted"],
                font=FONT_BODY,
            )
            self.canvas.create_text(
                width / 2,
                height / 2 + 14,
                text=_money(total),
                fill=COLORS["text"],
                font=("Bahnschrift SemiCondensed", 11, "bold"),
            )
        else:
            self.canvas.create_oval(
                *bounds,
                outline=COLORS["border"],
                width=16,
            )
            self.canvas.create_text(
                width / 2,
                height / 2,
                text="暂无成本",
                fill=COLORS["text_muted"],
                font=FONT_BODY_MEDIUM,
            )


class HorizontalBreakdown(ScrolledFrame):
    """Ranked material spend using readable labels, amounts and proportions."""

    def __init__(
        self,
        parent,
        limit=7,
        empty_text="当前项目暂无材料采购",
        other_label="其他材料",
        **kwargs,
    ):
        super().__init__(
            parent,
            autohide=True,
            bootstyle="secondary",
            style="Card.TFrame",
            **kwargs,
        )
        self.limit = limit
        self.empty_text = empty_text
        self.other_label = other_label

    def set_data(self, items):
        for child in self.winfo_children():
            child.destroy()
        rows = self._condense(items)
        total = sum(row["amount_minor"] for row in rows)
        if not total:
            ttk.Label(
                self,
                text=self.empty_text,
                style="CardText.TLabel",
            ).pack(anchor=W, pady=(72, 0))
            return
        maximum = max(row["amount_minor"] for row in rows)
        for index, row in enumerate(rows, 1):
            item = ttk.Frame(self)
            item.pack(fill=X, pady=(0, 11))
            head = ttk.Frame(item)
            head.pack(fill=X)
            ttk.Label(
                head,
                text=f"{index:02d}  {row['label']}",
                style="RankName.TLabel",
            ).pack(side=LEFT)
            percent = row["amount_minor"] / total * 100
            ttk.Label(
                head,
                text=f"{_money(row['amount_minor'])}  ·  {percent:.1f}%",
                style="SummaryValue.TLabel",
            ).pack(side=LEFT, padx=(12, 0))
            if row.get("detail"):
                ttk.Label(
                    item,
                    text=row["detail"],
                    style="CardText.TLabel",
                ).pack(anchor=W, pady=(2, 0))
            bar = tk.Canvas(
                item,
                height=6,
                background=COLORS["surface_muted"],
                highlightthickness=0,
            )
            bar.pack(fill=X, pady=(6, 0))
            ratio = row["amount_minor"] / maximum if maximum else 0
            bar.bind(
                "<Configure>",
                lambda event, canvas=bar, value=ratio: self._draw_bar(
                    canvas, event.width, value
                ),
            )
        self.after_idle(self.enable_scrolling)
        self.after_idle(lambda: self.yview_moveto(0.0))

    def _condense(self, items):
        rows = [
            {
                "label": (row.get("label") or "未命名材料").strip(),
                "amount_minor": int(row.get("amount_minor") or 0),
                "detail": (row.get("detail") or "").strip(),
            }
            for row in items
            if int(row.get("amount_minor") or 0) > 0
        ]
        rows.sort(key=lambda row: (-row["amount_minor"], row["label"]))
        if len(rows) <= self.limit:
            return rows
        visible = rows[: self.limit]
        visible.append(
            {
                "label": self.other_label,
                "amount_minor": sum(
                    row["amount_minor"] for row in rows[self.limit :]
                ),
                "detail": f"其余 {len(rows) - self.limit} 项合计",
            }
        )
        return visible

    @staticmethod
    def _draw_bar(canvas, width, ratio):
        canvas.delete("all")
        canvas.create_rectangle(
            0,
            0,
            max(2, width * ratio),
            6,
            fill=COLORS["primary"],
            outline="",
        )
