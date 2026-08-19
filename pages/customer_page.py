from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import master_data_service
from ui.components import BottomToolbar, DataTable, FilterBar, PageHeader
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders


STATUS_LABELS = {"active": "启用", "pending": "待确认", "inactive": "已停用"}
ENTITY_TYPE_LABELS = {
    "enterprise": "企业", "individual_business": "个体工商户", "individual": "个人"
}


class CustomerPage:
    """客户档案：维护客户主体、结算条款与信用额度。"""

    def __init__(self, parent):
        self.parent = parent
        self.build_ui()
        safe_init_loaders("客户档案", [self.load_data])

    def build_ui(self):
        PageHeader(
            self.parent,
            "客户档案",
            "维护客户主体资料、结算条款与信用额度；项目与合同按客户归集",
            actions=[
                ttk.Button(
                    self.parent, text="新增客户", bootstyle="primary",
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
            ("搜索名称 / 编码 / 信用代码", search_entry),
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
                ("code", "客户编码", 125, W),
                ("name", "客户名称", 200, W),
                ("category", "客户分类", 110, W),
                ("settlement", "结算条款", 130, W),
                ("credit", "信用额度（元）", 110, E),
                ("contact", "主要联系人", 105, W),
                ("phone", "联系电话", 120, W),
                ("status", "状态", 75, CENTER),
                ("notes", "备注", 170, W),
            ),
            empty_text="没有符合条件的客户",
            stretch=("name", "notes"),
        )
        self.table.tree.configure(selectmode="extended")
        self.table.tree.bind("<Double-1>", lambda _event: self.edit_selected())
        BottomToolbar(
            self.parent,
            ttk.Button(
                self.parent, text="新增客户", bootstyle="primary-outline",
                command=self.open_partner_dialog,
            ),
            ttk.Button(
                self.parent, text="修改档案", bootstyle="primary-outline",
                command=self.edit_selected,
            ),
            ttk.Button(
                self.parent, text="停用客户", bootstyle="danger-outline",
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
        rows = master_data_service.list_customers(
            keyword=self.search_var.get().strip(),
            active_only=False,
        )
        status_code = self._status_code()
        if status_code:
            rows = [row for row in rows if row["status"] == status_code]
        elif self.status_var.get() == "启用与待确认":
            rows = [row for row in rows if row["status"] in ("active", "pending")]

        def _credit(value):
            try:
                return f"{float(value):,.2f}"
            except (TypeError, ValueError):
                return "—"

        self.table.refresh(
            rows,
            lambda row: (
                str(row["id"]),
                (
                    row["partner_code"], row["name"],
                    row["customer_category"] or "—",
                    row["settlement_terms"] or "—",
                    _credit(row.get("credit_limit")),
                    row["contact"], row["contact_phone"],
                    STATUS_LABELS.get(row["status"], row["status"]),
                    row["notes"],
                ),
            ),
        )

    def _single_selected_id(self):
        selected = self.table.tree.selection()
        if len(selected) != 1:
            messagebox.showwarning("提示", "请只选择一个客户档案")
            return None
        return int(selected[0])

    def edit_selected(self):
        partner_id = self._single_selected_id()
        if partner_id:
            self.open_partner_dialog(partner_id)

    def deactivate_selected(self):
        selected = self.table.tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择需要停用的客户")
            return
        if not messagebox.askyesno(
            "确认停用",
            f"确定停用选中的 {len(selected)} 个客户？\n历史项目、合同、回款仍会保留。",
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
        dialog.title("修改客户档案" if partner_id else "新增客户档案")
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
                "customer_category", "settlement_terms", "credit_limit", "notes",
                "entity_type",
            )
        }
        variables["short_name"].set(data.get("short_name") or "")
        variables["credit_limit"].set(str(data.get("credit_limit", 0) or 0))
        variables["entity_type"].set(
            ENTITY_TYPE_LABELS.get(data.get("entity_type", "enterprise"), "企业")
        )
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
        name_entry = self._entry(common, variables, "legal_name", "客户名称 *", 0, 0)
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
        ttk.Label(common, text="主体类型").grid(
            row=5, column=0, sticky=E, padx=(0, 10), pady=6
        )
        ttk.Combobox(
            common, textvariable=variables["entity_type"],
            values=tuple(ENTITY_TYPE_LABELS.values()), state="readonly", width=24,
        ).grid(row=5, column=1, sticky=EW, pady=6, padx=(0, 18))

        customer = ttk.Frame(body, style="Card.TFrame", padding=(16, 10))
        customer.grid(row=2, column=0, sticky=EW, pady=(0, 12))
        ttk.Label(customer, text="客户资料", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky=W, pady=(0, 5)
        )
        customer.columnconfigure(1, weight=1)
        self._entry(customer, variables, "customer_category", "客户分类", 1, 0, width=24)
        self._entry(customer, variables, "settlement_terms", "结算条款", 2, 0, width=24)
        self._entry(customer, variables, "credit_limit", "信用额度（元）", 3, 0, width=24)

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
            payload["entity_type"] = next(
                code for code, label in ENTITY_TYPE_LABELS.items()
                if label == variables["entity_type"].get()
            )
            payload["roles"] = data.get("roles") or {"customer"}
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
            primary_text="保存客户档案", primary_command=save,
        )
        name_entry.focus_set()
