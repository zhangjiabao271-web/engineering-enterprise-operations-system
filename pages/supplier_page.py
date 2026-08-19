from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import master_data_service
from ui.components import BottomToolbar, DataTable, FilterBar, PageHeader
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders


SUPPLIER_CATEGORY_SUGGESTIONS = (
    "钢材", "瓦料", "通风", "工业耗材", "钢材+瓦料", "油漆涂料",
    "防腐材料", "五金工具", "中性硅酮结构胶",
)
STATUS_LABELS = {"active": "启用", "pending": "待确认", "inactive": "已停用"}


class SupplierPage:
    """供应商档案：维护供应商主体、产品范围、税率与报价基准。"""

    def __init__(self, parent):
        self.parent = parent
        self.build_ui()
        safe_init_loaders("供应商档案", [self.load_data])

    def build_ui(self):
        PageHeader(
            self.parent,
            "供应商档案",
            "维护供应商主体资料、产品范围、默认税率与价格交期质量基准",
            actions=[
                ttk.Button(
                    self.parent, text="新增供应商", bootstyle="primary",
                    command=self.open_partner_dialog,
                )
            ],
        )

        self.search_var = ttk.StringVar()
        self.status_var = ttk.StringVar(value="启用与待确认")
        search_entry = ttk.Entry(self.parent, textvariable=self.search_var, width=28)
        search_entry.bind("<Return>", lambda _event: self.load_data())
        status_combo = ttk.Combobox(
            self.parent, textvariable=self.status_var,
            values=("启用与待确认", "仅启用", "待确认", "已停用", "全部状态"),
            width=14, state="readonly",
        )
        status_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_data())
        FilterBar(
            self.parent,
            ("搜索名称 / 编码 / 联系人", search_entry),
            ("状态", status_combo),
            actions=[
                ttk.Button(
                    self.parent, text="重置", bootstyle="secondary-outline",
                    command=self.reset_filters,
                )
            ],
        )

        self.table = DataTable(
            self.parent,
            specs=(
                ("code", "供应商编码", 125, W),
                ("name", "供应商名称", 210, W),
                ("category", "产品范围", 110, W),
                ("tax", "默认税率", 80, CENTER),
                ("price", "价格水平", 80, CENTER),
                ("delivery", "交期", 70, CENTER),
                ("quality", "质量", 70, CENTER),
                ("export", "出口", 60, CENTER),
                ("contact", "主要联系人", 105, W),
                ("status", "状态", 75, CENTER),
                ("notes", "备注", 180, W),
            ),
            empty_text="没有符合条件的供应商",
            stretch=("name", "notes"),
        )
        self.table.tree.configure(selectmode="extended")
        self.table.tree.bind("<Double-1>", lambda _event: self.edit_selected())
        BottomToolbar(
            self.parent,
            ttk.Button(
                self.parent, text="新增供应商", bootstyle="primary-outline",
                command=self.open_partner_dialog,
            ),
            ttk.Button(
                self.parent, text="修改档案", bootstyle="primary-outline",
                command=self.edit_selected,
            ),
            ttk.Button(
                self.parent, text="停用供应商", bootstyle="danger-outline",
                command=self.deactivate_selected,
            ),
        )

    def reset_filters(self):
        self.search_var.set("")
        self.status_var.set("启用与待确认")
        self.load_data()

    def _status_code(self):
        return {
            "仅启用": "active",
            "待确认": "pending",
            "已停用": "inactive",
        }.get(self.status_var.get(), "")

    def load_data(self):
        rows = master_data_service.list_suppliers(
            keyword=self.search_var.get().strip(),
            active_only=False,
        )
        status_code = self._status_code()
        if status_code:
            rows = [row for row in rows if row["status"] == status_code]
        elif self.status_var.get() == "启用与待确认":
            rows = [row for row in rows if row["status"] in ("active", "pending")]
        self.table.refresh(
            rows,
            lambda row: (
                str(row["id"]),
                (
                    row["partner_code"], row["name"], row["category"],
                    f"{row['default_tax_rate_percent']:g}%",
                    row["price_level"] or "—",
                    row["delivery"] or "—",
                    row["quality"] or "—",
                    row["export"] or "—",
                    row["contact"], STATUS_LABELS.get(row["status"], row["status"]),
                    row["notes"],
                ),
            ),
        )

    def _single_selected_id(self):
        selected = self.table.tree.selection()
        if len(selected) != 1:
            messagebox.showwarning("提示", "请只选择一个供应商档案")
            return None
        return int(selected[0])

    def edit_selected(self):
        partner_id = self._single_selected_id()
        if partner_id:
            self.open_partner_dialog(partner_id)

    def deactivate_selected(self):
        selected = self.table.tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择需要停用的供应商")
            return
        if not messagebox.askyesno(
            "确认停用",
            f"确定停用选中的 {len(selected)} 个供应商？\n历史采购、报价仍会保留。",
        ):
            return
        master_data_service.deactivate_business_partners(
            [int(partner_id) for partner_id in selected]
        )
        self.load_data()

    @staticmethod
    def _entry(parent, variables, key, label, row, column, *, width=26):
        ttk.Label(parent, text=label).grid(
            row=row, column=column * 2, sticky=E, padx=(0, 10), pady=6
        )
        widget = ttk.Entry(parent, textvariable=variables[key], width=width)
        widget.grid(row=row, column=column * 2 + 1, sticky=EW, pady=6, padx=(0, 18))
        return widget

    def open_partner_dialog(self, partner_id=None):
        data = master_data_service.get_business_partner(partner_id) if partner_id else {}
        data = data or {}
        dialog = ttk.Toplevel(self.parent)
        dialog.title("修改供应商档案" if partner_id else "新增供应商档案")
        body, footer = build_form_dialog(
            dialog, self.parent, 860, 700, min_width=700, min_height=560
        )

        variables = {
            key: ttk.StringVar(value=str(data.get(key) or ""))
            for key in (
                "legal_name", "short_name", "unified_credit_code",
                "registered_address", "business_address", "invoice_phone",
                "bank_name", "bank_account", "contact_name", "contact_department",
                "contact_title", "contact_phone", "contact_email", "contact_wechat",
                "supplier_category", "default_tax_rate_percent", "price_level",
                "delivery_rating", "quality_rating", "export_capability", "notes",
            )
        }
        variables["short_name"].set(data.get("short_name") or "")
        variables["default_tax_rate_percent"].set(
            str(data.get("default_tax_rate_percent", 0) or 0)
        )
        variables["price_level"].set(data.get("price_level") or "中")
        variables["delivery_rating"].set(data.get("delivery_rating") or "一般")
        variables["quality_rating"].set(data.get("quality_rating") or "良")
        variables["export_capability"].set(data.get("export_capability") or "否")
        status_var = ttk.StringVar(
            value=STATUS_LABELS.get(data.get("status", "active"), "启用")
        )

        heading = ttk.Frame(body)
        heading.grid(row=0, column=0, sticky=EW, pady=(0, 12))
        ttk.Label(heading, text="主体与银行资料", style="CardTitle.TLabel").pack(side=LEFT)

        common = ttk.Frame(body, style="Card.TFrame", padding=(16, 10))
        common.grid(row=1, column=0, sticky=EW, pady=(0, 12))
        for column in range(4):
            common.columnconfigure(column, weight=1 if column % 2 else 0)
        name_entry = self._entry(common, variables, "legal_name", "供应商名称 *", 0, 0)
        self._entry(common, variables, "short_name", "简称", 0, 1)
        self._entry(common, variables, "unified_credit_code", "统一社会信用代码", 1, 0)
        ttk.Label(common, text="档案状态").grid(row=1, column=2, sticky=E, padx=(0, 10), pady=6)
        ttk.Combobox(
            common, textvariable=status_var,
            values=("启用", "待确认", "已停用"), state="readonly", width=24,
        ).grid(row=1, column=3, sticky=EW, pady=6, padx=(0, 18))
        self._entry(common, variables, "registered_address", "注册地址", 2, 0)
        self._entry(common, variables, "business_address", "经营地址", 2, 1)
        self._entry(common, variables, "invoice_phone", "开票电话", 3, 0)
        self._entry(common, variables, "bank_name", "开户行", 3, 1)
        self._entry(common, variables, "bank_account", "银行账号", 4, 0)
        self._entry(common, variables, "notes", "备注", 4, 1)

        supplier = ttk.Frame(body, style="Card.TFrame", padding=(16, 10))
        supplier.grid(row=2, column=0, sticky=EW, pady=(0, 12))
        ttk.Label(supplier, text="供应商资料", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky=W, pady=(0, 5)
        )
        supplier.columnconfigure(1, weight=1)
        ttk.Label(supplier, text="产品范围").grid(row=1, column=0, sticky=E, padx=(0, 10), pady=6)
        ttk.Combobox(
            supplier, textvariable=variables["supplier_category"],
            values=SUPPLIER_CATEGORY_SUGGESTIONS, state="normal", width=24,
        ).grid(row=1, column=1, sticky=EW, pady=6)
        self._entry(supplier, variables, "default_tax_rate_percent", "默认税率（%）", 2, 0, width=24)
        for row, (label, key, values) in enumerate(
            (
                ("价格水平", "price_level", ("高", "中高", "中", "中低", "低")),
                ("交期", "delivery_rating", ("快", "较快", "一般", "较慢", "慢")),
                ("质量", "quality_rating", ("优", "良", "中", "差")),
                ("出口能力", "export_capability", ("是", "否")),
            ),
            3,
        ):
            ttk.Label(supplier, text=label).grid(row=row, column=0, sticky=E, padx=(0, 10), pady=6)
            ttk.Combobox(
                supplier, textvariable=variables[key], values=values,
                state="readonly", width=24,
            ).grid(row=row, column=1, sticky=EW, pady=6)

        contact = ttk.Frame(body, style="Card.TFrame", padding=(16, 10))
        contact.grid(row=3, column=0, sticky=EW)
        ttk.Label(contact, text="主要联系人", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=4, sticky=W, pady=(0, 5)
        )
        for column in range(4):
            contact.columnconfigure(column, weight=1 if column % 2 else 0)
        self._entry(contact, variables, "contact_name", "姓名", 1, 0)
        self._entry(contact, variables, "contact_phone", "电话", 1, 1)
        self._entry(contact, variables, "contact_department", "部门", 2, 0)
        self._entry(contact, variables, "contact_title", "职务", 2, 1)
        self._entry(contact, variables, "contact_email", "邮箱", 3, 0)
        self._entry(contact, variables, "contact_wechat", "微信", 3, 1)

        body.columnconfigure(0, weight=1)

        def save():
            payload = {key: variable.get().strip() for key, variable in variables.items()}
            payload["roles"] = data.get("roles") or {"supplier"}
            payload["status"] = next(
                (code for code, label in STATUS_LABELS.items() if label == status_var.get()),
                "active",
            )
            try:
                if partner_id:
                    master_data_service.update_business_partner(partner_id, payload)
                else:
                    master_data_service.create_business_partner(payload)
            except Exception as error:
                messagebox.showwarning("无法保存", str(error), parent=dialog)
                return
            dialog.destroy()
            self.load_data()

        add_form_actions(
            footer, cancel_command=dialog.destroy,
            primary_text="保存供应商档案", primary_command=save,
        )
        name_entry.focus_set()
