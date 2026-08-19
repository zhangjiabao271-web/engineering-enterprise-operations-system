"""Unified Treeview table with sortable columns and an empty-state hint.

Standardises the hand-built _tree() helpers duplicated across pages:
same heading style, row height, column scaling and empty message.
"""

import tkinter as tk

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, CENTER, E, END, LEFT, RIGHT, VERTICAL, W, X, Y

from ui.scaling import scale_treeview_columns
from ui.theme import SPACING


class DataTable(ttk.Frame):
    """Bordered table with sortable headings and an empty-state label.

    specs: tuple of (key, label, width, anchor) — same contract as the old
    per-page _tree() helpers, so pages can migrate mechanically.
    """

    def __init__(self, parent, specs, *, empty_text="暂无数据", stretch=None,
                 padding=10, height=None, pack_fill=BOTH, pack_expand=True, **kwargs):
        super().__init__(parent, style="Card.TFrame", padding=padding, **kwargs)
        self.pack(fill=pack_fill, expand=pack_expand)
        self.specs = specs
        self.empty_text = empty_text
        # 默认拉伸常见文本列；调用方可传 stretch=("label",) 等覆盖
        stretch_keys = set(stretch) if stretch is not None else {"project", "counterparty", "payee", "name"}
        self._sort_state = {}  # column key -> True(asc)

        self.tree = ttk.Treeview(
            self,
            columns=[spec[0] for spec in specs],
            show="headings",
            bootstyle="primary",
        )
        if height is not None:
            self.tree.configure(height=height)
        for key, label, width, anchor in specs:
            self.tree.heading(
                key, text=label, command=lambda k=key: self._sort_by(k)
            )
            self.tree.column(
                key,
                width=width,
                anchor=anchor,
                stretch=key in stretch_keys,
            )
        scrollbar = ttk.Scrollbar(
            self, orient=VERTICAL, command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=Y)

        self.empty_label = ttk.Label(
            self.tree, text=empty_text, style="CardText.TLabel",
        )
        self.tree.bind("<Configure>", lambda _e: self._place_empty())
        self.tree.bind("<Button-1>", lambda _e: self._place_empty(), add="+")

    # ---- population helpers ----
    def clear(self):
        self.tree.delete(*self.tree.get_children())
        self._place_empty()

    def insert_row(self, iid, values, *, tags=None):
        """iid=None 时由 Treeview 自动生成行 id；tags 用于行级颜色标记。"""
        if iid is None:
            self.tree.insert("", END, values=values, tags=tags or ())
        else:
            self.tree.insert("", END, iid=iid, values=values, tags=tags or ())

    def refresh(self, rows, value_mapper):
        """rows: iterable; value_mapper(row) -> (iid, values) 或 (iid, values, tags)."""
        self.clear()
        for row in rows:
            mapped = value_mapper(row)
            if len(mapped) == 3:
                iid, values, tags = mapped
                self.insert_row(iid, values, tags=tags)
            else:
                iid, values = mapped
                self.insert_row(iid, values)
        self._place_empty()

    def selected_id(self):
        selected = self.tree.selection()
        if len(selected) != 1:
            return None
        try:
            return int(selected[0])
        except ValueError:
            return selected[0]

    # ---- sorting ----
    def _sort_by(self, key):
        children = self.tree.get_children("")
        if not children:
            return
        rows = [(self.tree.set(child, key), child) for child in children]
        rows.sort(key=lambda pair: pair[0])
        if self._sort_state.get(key, True):
            rows.reverse()
            self._sort_state[key] = False
        else:
            self._sort_state[key] = True
        for index, (_value, child) in enumerate(rows):
            self.tree.move(child, "", index)

    def _place_empty(self):
        if not self.tree.get_children():
            self.empty_label.place(
                relx=0.5, rely=0.45, anchor=CENTER
            )
        else:
            self.empty_label.place_forget()

    def after_idle(self, *args, **kwargs):
        return self.tree.after_idle(*args, **kwargs)

    def scale_columns(self):
        scale_treeview_columns(self.tree)
