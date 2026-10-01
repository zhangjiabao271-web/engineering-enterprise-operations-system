import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from tkinter import messagebox
from services import master_data_service
from ui.components import DataTable, PageHeader
from ui.theme import COLORS


class ComparePage:
    def __init__(self, parent):
        self.parent = parent
        self.build_ui()

    def build_ui(self):
        PageHeader(self.parent, "报价对比", "按含税采购价比较供应商报价，同时保留未税材料价")

        search_frame = ttk.Frame(self.parent, style="Toolbar.TFrame", padding=(12, 9))
        search_frame.pack(fill=X, pady=(0, 12))

        ttk.Label(search_frame, text="材料名称：").pack(side=LEFT)
        self.name_entry = ttk.Entry(search_frame, width=20)
        self.name_entry.pack(side=LEFT, padx=5)
        self.name_entry.bind("<Return>", lambda _event: self.search())

        ttk.Label(search_frame, text="规格：").pack(side=LEFT, padx=(15, 0))
        self.spec_entry = ttk.Entry(search_frame, width=20)
        self.spec_entry.pack(side=LEFT, padx=5)

        ttk.Button(search_frame, text="查询对比", bootstyle=PRIMARY, command=self.search).pack(side=LEFT, padx=15)
        ttk.Button(search_frame, text="重置", bootstyle="secondary-outline", command=self.clear).pack(side=LEFT)

        self.table = DataTable(
            self.parent,
            (("recommend", "推荐", 88, W), ("supplier", "供应商", 145, W),
             ("category", "产品范围", 75, W), ("product", "产品名称", 105, W),
             ("spec", "规格", 110, W), ("price", "材料价（未税）", 115, E),
             ("tax_rate", "税率", 55, E), ("tax_price", "含税价", 80, E),
             ("unit", "单位", 60, CENTER), ("quality", "质量情况", 70, W),
             ("price_level", "价格水平", 70, W), ("export", "是否出口", 70, CENTER),
             ("notes", "备注", 180, W)),
            empty_text="请输入材料名称查询供应商报价",
            stretch=(), horizontal=True, padding=1,
        )
        self.tree = self.table.tree

        ttk.Label(self.parent, text="结果按含税价从低到高排列；“最低含税价”使用明确文字标识。", style="PageSub.TLabel").pack(anchor=W)

    def search(self):
        name = self.name_entry.get().strip()
        spec = self.spec_entry.get().strip()
        if not name:
            messagebox.showwarning("提示", "请输入材料名称")
            return
        rows = master_data_service.list_supplier_offers(keyword=name)
        if spec:
            rows = [
                row for row in rows
                if spec.casefold() in (row.get("specification") or "").casefold()
            ]
        rows.sort(
            key=lambda row: (
                row.get("tax_inclusive_price_minor") or 0,
                row.get("price_minor") or 0,
            )
        )
        self.table.empty_label.configure(text="没有匹配的供应商报价，请调整材料名称或规格")
        min_price = rows[0]["tax_inclusive_price_minor"] if rows else None

        def table_row(row):
            is_lowest = min_price is not None and row["tax_inclusive_price_minor"] == min_price
            return None, (
                "最低含税价" if is_lowest else "",
                row["supplier_name"], row["category"], row["name"], row["specification"],
                f"{row['price']:.2f}", f"{row['tax_rate_percent']:g}%",
                f"{row['tax_inclusive_price']:.2f}", row["unit"], row["quality"],
                row["price_level"], row["export"], row["notes"],
            ), ("lowest",) if is_lowest else ()

        self.table.refresh(rows, table_row)
        self.tree.tag_configure("lowest", foreground=COLORS["accent"])

    def clear(self):
        self.name_entry.delete(0, END)
        self.spec_entry.delete(0, END)
        self.table.empty_label.configure(text="请输入材料名称查询供应商报价")
        self.table.clear()
