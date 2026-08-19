import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from tkinter import messagebox
from services import master_data_service
from ui.theme import COLORS


class ComparePage:
    def __init__(self, parent):
        self.parent = parent
        self.build_ui()

    def build_ui(self):
        header = ttk.Frame(self.parent)
        header.pack(fill=X, pady=(0, 16))
        title_box = ttk.Frame(header)
        title_box.pack(side=LEFT)
        ttk.Label(title_box, text="报价对比", style="PageTitle.TLabel").pack(anchor=W)
        ttk.Label(
            title_box, text="按含税采购价比较供应商报价，同时保留未税材料价",
            style="PageSub.TLabel",
        ).pack(anchor=W, pady=(4, 0))

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

        # 结果表格
        table_frame = ttk.Frame(self.parent, style="Card.TFrame", padding=1)
        table_frame.pack(fill=BOTH, expand=True, pady=(0, 10))

        cols = ("recommend", "supplier", "category", "product", "spec", "price", "tax_rate", "tax_price", "unit", "quality", "price_level", "export", "notes")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", bootstyle=PRIMARY, selectmode="extended")
        headings = {
            "recommend": "推荐",
            "supplier": "供应商", "category": "产品范围", "product": "产品名称", "spec": "规格",
            "price": "材料价（未税）", "tax_rate": "税率", "tax_price": "含税价",
            "unit": "单位", "quality": "质量情况", "price_level": "价格水平",
            "export": "是否出口", "notes": "备注"
        }
        widths = {
            "recommend": 88,
            "supplier": 145, "category": 75, "product": 105, "spec": 110,
            "price": 100, "tax_rate": 55, "tax_price": 80,
            "unit": 60, "quality": 70, "price_level": 70, "export": 70, "notes": 180
        }
        for col in cols:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor=CENTER)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)

        scrollbar = ttk.Scrollbar(table_frame, orient=VERTICAL, command=self.tree.yview)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.tree.configure(yscrollcommand=scrollbar.set)

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
        self.tree.delete(*self.tree.get_children())
        min_price = None
        if rows:
            min_price = rows[0]["tax_inclusive_price_minor"]
        for row in rows:
            item_id = self.tree.insert("", END, values=(
                "最低含税价" if min_price is not None and row["tax_inclusive_price_minor"] == min_price else "",
                row["supplier_name"], row["category"], row["name"], row["specification"],
                f"{row['price']:.2f}", f"{row['tax_rate_percent']:g}%",
                f"{row['tax_inclusive_price']:.2f}", row["unit"], row["quality"],
                row["price_level"], row["export"], row["notes"]
            ))
            if min_price is not None and row["tax_inclusive_price_minor"] == min_price:
                self.tree.item(item_id, tags=("lowest",))
        self.tree.tag_configure("lowest", foreground=COLORS["accent"])

    def clear(self):
        self.name_entry.delete(0, END)
        self.spec_entry.delete(0, END)
        self.tree.delete(*self.tree.get_children())
