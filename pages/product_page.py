import math
from tkinter import messagebox, filedialog, scrolledtext

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import master_data_service
from ui.dialogs import safe_init_loaders


PRODUCT_NAME_PRESETS = (
    "钢材",
    "瓦料",
    "通风设备",
    "工业耗材",
    "中性硅酮结构胶",
    "其他",
)


class ProductPage:
    def __init__(self, parent):
        self.parent = parent
        self.selected_id = None
        self.build_ui()
        safe_init_loaders(
            "材料与报价",
            [self.load_suppliers, self.load_name_suggestions, self.load_data],
        )

    def build_ui(self):
        # 1. Header
        header = ttk.Frame(self.parent)
        header.pack(fill=X, pady=(0, 16))
        title_box = ttk.Frame(header)
        title_box.pack(side=LEFT)
        ttk.Label(title_box, text="材料与报价", style="PageTitle.TLabel").pack(anchor=W)
        ttk.Label(
            title_box,
            text="维护材料目录与供应商报价信息",
            style="PageSub.TLabel",
        ).pack(anchor=W, pady=(4, 0))

        header_btns = ttk.Frame(header)
        header_btns.pack(side=RIGHT)
        ttk.Button(
            header_btns,
            text="新增材料",
            bootstyle=SUCCESS,
            command=self.add,
        ).pack(side=LEFT)

        # 2. 搜索栏
        search_frame = ttk.Frame(self.parent, style="Toolbar.TFrame", padding=(12, 9))
        search_frame.pack(fill=X, pady=(0, 12))
        ttk.Label(
            search_frame, text="搜索材料 / 规格 / 供应商", style="Toolbar.TLabel"
        ).pack(side=LEFT)
        self.search_entry = ttk.Entry(search_frame, width=30)
        self.search_entry.pack(side=LEFT, padx=8)
        self.search_entry.bind("<Return>", lambda _event: self.load_data())
        ttk.Button(
            search_frame, text="查询", bootstyle=PRIMARY, command=self.load_data
        ).pack(side=LEFT)
        ttk.Button(
            search_frame,
            text="重置",
            bootstyle="secondary-outline",
            command=self.clear_search,
        ).pack(side=LEFT, padx=6)

        # 3. 数据表格 Card
        table_card = ttk.Frame(self.parent, style="Card.TFrame", padding=(16, 12))
        table_card.pack(fill=BOTH, expand=True, pady=(0, 12))

        ttk.Label(table_card, text="材料报价列表", style="CardTitle.TLabel").pack(
            anchor=W, pady=(0, 8)
        )

        tv_frame = ttk.Frame(table_card, style="Card.TFrame")
        tv_frame.pack(fill=BOTH, expand=True)

        cols = (
            "id",
            "supplier_name",
            "name",
            "specification",
            "unit",
            "price",
            "tax_rate",
            "tax_inclusive_price",
            "notes",
        )
        self.tree = ttk.Treeview(
            tv_frame,
            columns=cols,
            show="headings",
            bootstyle=PRIMARY,
            selectmode="extended",
        )
        headings = {
            "id": "ID",
            "supplier_name": "供应商",
            "name": "产品名称",
            "specification": "规格",
            "unit": "单位",
            "price": "材料价（未税）",
            "tax_rate": "税率",
            "tax_inclusive_price": "含税价",
            "notes": "备注",
        }
        widths = {
            "id": 45,
            "supplier_name": 150,
            "name": 120,
            "specification": 135,
            "unit": 55,
            "price": 100,
            "tax_rate": 60,
            "tax_inclusive_price": 85,
            "notes": 150,
        }
        for col in cols:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], anchor=CENTER)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)

        scrollbar = ttk.Scrollbar(
            tv_frame, orient=VERTICAL, command=self.tree.yview
        )
        scrollbar.pack(side=RIGHT, fill=Y)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.bind("<<TreeviewSelect>>", self.on_select)

        # 表格底部按钮
        table_btn_frame = ttk.Frame(table_card, style="Card.TFrame")
        table_btn_frame.pack(fill=X, pady=(8, 0))
        ttk.Button(
            table_btn_frame, text="新增", bootstyle=PRIMARY, command=self.add
        ).pack(side=LEFT, padx=(0, 6))
        ttk.Button(
            table_btn_frame,
            text="修改",
            bootstyle="primary-outline",
            command=self.update,
        ).pack(side=LEFT, padx=(0, 6))
        ttk.Button(
            table_btn_frame, text="删除", bootstyle=DANGER, command=self.delete
        ).pack(side=LEFT, padx=(0, 6))
        ttk.Button(
            table_btn_frame,
            text="全选",
            bootstyle="secondary-outline",
            command=self.select_all,
        ).pack(side=LEFT, padx=(0, 6))
        ttk.Button(
            table_btn_frame,
            text="清空表单",
            bootstyle="secondary-outline",
            command=self.clear_form,
        ).pack(side=LEFT)

        # 4. 表单区 Card
        form_card = ttk.Frame(self.parent, style="Card.TFrame", padding=(16, 12))
        form_card.pack(fill=X)

        ttk.Label(form_card, text="报价信息", style="CardTitle.TLabel").pack(
            anchor=W, pady=(0, 12)
        )

        form_grid = ttk.Frame(form_card, style="Card.TFrame")
        form_grid.pack(fill=X)

        # row 0
        ttk.Label(form_grid, text="所属供应商：").grid(
            row=0, column=0, padx=5, pady=8, sticky=E
        )
        self.supplier_var = ttk.StringVar()
        self.supplier_combo = ttk.Combobox(
            form_grid,
            textvariable=self.supplier_var,
            state="readonly",
            width=25,
        )
        self.supplier_combo.grid(row=0, column=1, padx=5, pady=8, sticky=W)
        self.supplier_combo.bind(
            "<<ComboboxSelected>>", self.use_supplier_default_tax
        )

        ttk.Label(form_grid, text="产品名称：").grid(
            row=0, column=2, padx=5, pady=8, sticky=E
        )
        self.name_var = ttk.StringVar()
        self.name_entry = ttk.Combobox(
            form_grid,
            textvariable=self.name_var,
            values=list(PRODUCT_NAME_PRESETS),
            width=20,
            state="normal",
        )
        self.name_entry.grid(row=0, column=3, padx=5, pady=8, sticky=W)
        ttk.Label(
            form_grid,
            text="可直接输入新产品名称",
            bootstyle=SECONDARY,
        ).grid(row=0, column=4, padx=(3, 8), pady=8, sticky=W)

        # row 1
        ttk.Label(form_grid, text="规格：").grid(
            row=1, column=0, padx=5, pady=8, sticky=E
        )
        self.spec_entry = ttk.Entry(form_grid, width=22)
        self.spec_entry.grid(row=1, column=1, padx=5, pady=8, sticky=W)

        ttk.Label(form_grid, text="单位：").grid(
            row=1, column=2, padx=5, pady=8, sticky=E
        )
        self.unit_entry = ttk.Entry(form_grid, width=22)
        self.unit_entry.grid(row=1, column=3, padx=5, pady=8, sticky=W)

        # row 2
        ttk.Label(form_grid, text="材料单价（未税）：").grid(
            row=2, column=0, padx=5, pady=8, sticky=E
        )
        self.price_entry = ttk.Entry(form_grid, width=22)
        self.price_entry.grid(row=2, column=1, padx=5, pady=8, sticky=W)
        self.price_entry.bind("<KeyRelease>", self.calculate_tax_inclusive_price)

        ttk.Label(form_grid, text="税率（%）：").grid(
            row=2, column=2, padx=5, pady=8, sticky=E
        )
        self.tax_rate_var = ttk.StringVar(value="0")
        self.tax_rate_entry = ttk.Entry(
            form_grid, textvariable=self.tax_rate_var, width=22
        )
        self.tax_rate_entry.grid(row=2, column=3, padx=5, pady=8, sticky=W)
        self.tax_rate_var.trace_add("write", self.calculate_tax_inclusive_price)

        # row 3
        ttk.Label(form_grid, text="含税单价：").grid(
            row=3, column=0, padx=5, pady=8, sticky=E
        )
        self.tax_inclusive_price_var = ttk.StringVar(value="0.00")
        ttk.Entry(
            form_grid,
            textvariable=self.tax_inclusive_price_var,
            width=22,
            state="readonly",
        ).grid(row=3, column=1, padx=5, pady=8, sticky=W)

        ttk.Label(form_grid, text="备注：").grid(
            row=3, column=2, padx=5, pady=8, sticky=E
        )
        self.notes_entry = ttk.Entry(form_grid, width=22)
        self.notes_entry.grid(row=3, column=3, padx=5, pady=8, sticky=W)

        # 表单底部操作按钮
        form_btn_frame = ttk.Frame(form_card, style="Card.TFrame")
        form_btn_frame.pack(fill=X, pady=(12, 0))
        ttk.Button(
            form_btn_frame,
            text="新增材料",
            bootstyle=SUCCESS,
            command=self.add,
        ).pack(side=LEFT, padx=(0, 6))
        ttk.Button(
            form_btn_frame,
            text="保存修改",
            bootstyle="primary-outline",
            command=self.update,
        ).pack(side=LEFT, padx=(0, 6))
        ttk.Button(
            form_btn_frame,
            text="清空表单",
            bootstyle="secondary-outline",
            command=self.clear_form,
        ).pack(side=LEFT)

    def load_suppliers(self):
        suppliers = master_data_service.list_suppliers()
        self.supplier_map = {f"{s['id']} - {s['name']}": s["id"] for s in suppliers}
        self.supplier_details = {s["id"]: s for s in suppliers}
        self.supplier_combo["values"] = list(self.supplier_map.keys())
        if self.supplier_combo["values"]:
            self.supplier_combo.current(0)
            self.use_supplier_default_tax()

    def load_name_suggestions(self):
        """已有产品仅作为输入建议，不限制用户录入新名称。"""
        names = sorted({
            str(product.get("name", "")).strip()
            for product in master_data_service.list_supplier_offers()
            if str(product.get("name", "")).strip()
        })
        combined = list(PRODUCT_NAME_PRESETS) + [n for n in names if n not in PRODUCT_NAME_PRESETS]
        self.name_entry["values"] = combined

    def get_form_data(self):
        supplier_text = self.supplier_var.get()
        supplier_id = self.supplier_map.get(supplier_text)
        price_str = self.price_entry.get().strip()
        try:
            price = float(price_str) if price_str else 0
            tax_rate = float(self.tax_rate_var.get().strip() or 0)
        except ValueError:
            raise ValueError("材料单价和税率必须填写数字")
        if not math.isfinite(price) or price < 0:
            raise ValueError("材料单价不能为负数")
        if not math.isfinite(tax_rate) or tax_rate < 0 or tax_rate > 100:
            raise ValueError("税率必须在 0% 到 100% 之间")
        return {
            "supplier_id": supplier_id,
            "name": self.name_var.get().strip(),
            "specification": self.spec_entry.get().strip(),
            "unit": self.unit_entry.get().strip(),
            "price": price,
            "tax_rate_percent": tax_rate,
            "notes": self.notes_entry.get().strip(),
        }

    def set_form_data(self, data):
        supplier_id = data.get("supplier_id")
        for text, sid in self.supplier_map.items():
            if sid == supplier_id:
                self.supplier_var.set(text)
                break
        self.name_var.set(data.get("name", ""))
        self.spec_entry.delete(0, END) or self.spec_entry.insert(0, data.get("specification", ""))
        self.unit_entry.delete(0, END) or self.unit_entry.insert(0, data.get("unit", ""))
        self.price_entry.delete(0, END) or self.price_entry.insert(0, str(data.get("price", "")))
        self.tax_rate_var.set(str(data.get("tax_rate_percent", 0)))
        self.notes_entry.delete(0, END) or self.notes_entry.insert(0, data.get("notes", ""))
        self.calculate_tax_inclusive_price()

    def clear_form(self):
        self.selected_id = None
        self.name_var.set("")
        self.spec_entry.delete(0, END)
        self.unit_entry.delete(0, END)
        self.price_entry.delete(0, END)
        self.use_supplier_default_tax()
        self.notes_entry.delete(0, END)
        self.tree.selection_remove(self.tree.selection())

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def clear_search(self):
        self.search_entry.delete(0, END)
        self.load_data()

    def add(self):
        try:
            data = self.get_form_data()
        except ValueError as error:
            messagebox.showwarning("提示", str(error))
            return
        if not data["supplier_id"]:
            messagebox.showwarning("提示", "请先选择供应商")
            return
        if not data["name"]:
            messagebox.showwarning("提示", "产品名称不能为空")
            return
        master_data_service.create_supplier_offer(data)
        messagebox.showinfo("成功", "产品添加成功")
        self.clear_form()
        self.load_name_suggestions()
        self.load_data()

    def update(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择要修改的产品")
            return
        if len(selected) > 1:
            messagebox.showwarning("提示", "修改时只能选择一个产品")
            return
        self.selected_id = self.tree.item(selected[0], "values")[0]
        try:
            data = self.get_form_data()
        except ValueError as error:
            messagebox.showwarning("提示", str(error))
            return
        if not data["supplier_id"]:
            messagebox.showwarning("提示", "请先选择供应商")
            return
        if not data["name"]:
            messagebox.showwarning("提示", "产品名称不能为空")
            return
        master_data_service.update_supplier_offer(self.selected_id, data)
        messagebox.showinfo("成功", "产品修改成功")
        self.clear_form()
        self.load_name_suggestions()
        self.load_data()

    def delete(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择要删除的产品")
            return
        ids = [self.tree.item(item, "values")[0] for item in selected]
        if messagebox.askyesno("确认", f"确定停用选中的 {len(ids)} 条供应商报价？"):
            master_data_service.deactivate_supplier_offers([int(value) for value in ids])
            self.clear_form()
            self.load_data()

    def load_data(self):
        keyword = self.search_entry.get().strip()
        rows = master_data_service.list_supplier_offers(keyword=keyword)
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", END, values=(
                row["id"], row["supplier_name"], row["name"], row["specification"],
                row["unit"], f"{row['price']:.2f}", f"{row['tax_rate_percent']:g}%",
                f"{row['tax_inclusive_price']:.2f}", row["notes"]
            ))

    def on_select(self, event):
        selected = self.tree.selection()
        if selected:
            item = self.tree.item(selected[0])
            self.selected_id = item["values"][0]
            data = master_data_service.get_supplier_offer(self.selected_id)
            if data:
                self.set_form_data(data)
        else:
            self.selected_id = None

    def use_supplier_default_tax(self, _event=None):
        supplier_id = self.supplier_map.get(self.supplier_var.get())
        supplier = self.supplier_details.get(supplier_id, {})
        self.tax_rate_var.set(str(supplier.get("default_tax_rate_percent", 0)))

    def calculate_tax_inclusive_price(self, *_args):
        try:
            price = float(self.price_entry.get().strip() or 0)
            tax_rate = float(self.tax_rate_var.get().strip() or 0)
            if price < 0 or tax_rate < 0:
                raise ValueError
            self.tax_inclusive_price_var.set(
                f"{price * (1 + tax_rate / 100):.2f}"
            )
        except ValueError:
            self.tax_inclusive_price_var.set("--")
