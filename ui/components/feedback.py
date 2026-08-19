"""Feedback states: loading, error, success and empty — all textual."""

import tkinter as tk

import ttkbootstrap as ttk
from ttkbootstrap.constants import CENTER


class StateLabel(ttk.Frame):
    """Centred text block used for loading / error / empty feedback."""

    def __init__(self, parent, text, *, style="CardText.TLabel", **kwargs):
        super().__init__(parent, style="Card.TFrame", padding=24, **kwargs)
        self.pack(fill="x", pady=(8, 8))
        self.label = ttk.Label(self, text=text, style=style)
        self.label.pack(anchor=CENTER)

    def set_text(self, text):
        self.label.configure(text=text)


class EmptyHint(StateLabel):
    """Empty-state guidance line, e.g. '当前项目暂无成本'."""

    def __init__(self, parent, text="暂无数据", **kwargs):
        super().__init__(parent, text, **kwargs)


class ErrorBanner(StateLabel):
    """Inline error strip shown when a load fails (keeps page alive)."""

    def __init__(self, parent, text="数据加载失败", **kwargs):
        super().__init__(parent, text, style="FormError.TLabel", **kwargs)
