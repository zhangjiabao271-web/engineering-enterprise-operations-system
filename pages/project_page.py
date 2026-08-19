from datetime import datetime
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, CENTER, E, END, EW, LEFT, RIGHT, W, X

from services import project_service
from services import master_data_service
from ui.components import DataTable, DatePicker, FilterBar, PageHeader
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders
from ui.theme import COLORS, SPACING


PROJECT_STATUSES = ("筹备中", "进行中", "已完工", "已关闭")
BUSINESS_MODE_LABELS = {"contract": "正式合同工程", "cash": "零星现金工程"}
INVOICE_POLICY_LABELS = {
    "required": "需要开票", "not_required": "不要求开票", "pending": "待确认"
}
ENTITY_TYPE_LABELS = {
    "enterprise": "企业", "individual_business": "个体工商户", "individual": "个人"
}


class ProjectManagementPage:
    """Project master data and project-site maintenance."""

    def __init__(self, parent):
        self.parent = parent
        self.keyword_var = ttk.StringVar()
        self.status_var = ttk.StringVar(value="全部状态")
        self.selected_project_id = None
        self.build_ui()
        safe_init_loaders("项目管理", [self.load_projects])

    def build_ui(self):
        PageHeader(
            self.parent,
            "项目管理",
            "项目是采购、人工、施工、合同和经营分析的统一归集维度",
            actions=[
                ttk.Button(
                    self.parent, text="新增项目", bootstyle="primary",
                    command=self.open_project_dialog,
                ),
                ttk.Button(
                    self.parent, text="编辑项目", bootstyle="secondary-outline",
                    command=self.edit_selected_project,
                ),
                ttk.Button(
                    self.parent, text="关闭项目", bootstyle="secondary-outline",
                    command=self.close_selected_projects,
                ),
            ],
        )

        keyword_entry = ttk.Entry(self.parent, textvariable=self.keyword_var, width=18)
        keyword_entry.bind("<Return>", lambda _event: self.load_projects())
        status_combo = ttk.Combobox(
            self.parent,
            textvariable=self.status_var,
            values=("全部状态", *PROJECT_STATUSES),
            width=8,
            state="readonly",
        )
        status_combo.bind("<<ComboboxSelected>>", lambda _event: self.load_projects())
        self.count_var = ttk.StringVar(value="0 个项目")
        FilterBar(
            self.parent,
            ("搜索项目", keyword_entry),
            ("状态", status_combo),
            ttk.Button(
                self.parent, text="查询", bootstyle="primary",
                command=self.load_projects,
            ),
            ttk.Button(
                self.parent, text="重置", bootstyle="secondary-outline",
                command=self.clear_filters,
            ),
            actions=[
                ttk.Label(
                    self.parent, textvariable=self.count_var,
                    style="Toolbar.TLabel",
                ),
            ],
        )

        workspace = ttk.Panedwindow(self.parent, orient="vertical")
        workspace.pack(fill=BOTH, expand=True)

        project_card = ttk.Frame(workspace, style="Card.TFrame", padding=1)
        site_card = ttk.Frame(workspace, style="Card.TFrame", padding=12)
        workspace.add(project_card, weight=3)
        workspace.add(site_card, weight=2)

        self.project_tree = DataTable(
            project_card,
            specs=(
                ("code", "项目编码", 110, CENTER),
                ("name", "项目名称", 185, W),
                ("customer", "客户", 145, W),
                ("manager", "负责人", 85, CENTER),
                ("status", "状态", 75, CENTER),
                ("mode", "业务模式", 105, CENTER),
                ("sites", "地点", 55, CENTER),
                ("schedule", "计划工期", 190, CENTER),
            ),
            empty_text="没有符合条件的项目，点击右上角「新增项目」",
            stretch=("name", "customer"),
            padding=0,
        )
        self.project_tree.tree.configure(selectmode="extended")
        self.project_tree.tree.tag_configure("closed", foreground=COLORS["text_muted"])
        self.project_tree.tree.bind("<<TreeviewSelect>>", self.on_project_select)
        self.project_tree.tree.bind("<Double-1>", lambda _event: self.edit_selected_project())

        site_header = ttk.Frame(site_card, style="Card.TFrame")
        site_header.pack(fill=X, pady=(0, 8))
        self.site_title_var = ttk.StringVar(value="施工地点 · 请先选择项目")
        ttk.Label(
            site_header, textvariable=self.site_title_var, style="CardTitle.TLabel"
        ).pack(side=LEFT)
        ttk.Button(
            site_header, text="新增地点", bootstyle="primary-outline", command=self.open_site_dialog
        ).pack(side=RIGHT)
        ttk.Button(
            site_header, text="编辑地点", bootstyle="secondary-outline",
            command=self.edit_selected_site,
        ).pack(side=RIGHT, padx=7)
        ttk.Button(
            site_header, text="停用地点", bootstyle="secondary-outline",
            command=self.deactivate_selected_sites,
        ).pack(side=RIGHT)

        self.site_tree = DataTable(
            site_card,
            specs=(
                ("code", "地点编码", 120, CENTER),
                ("name", "施工地点", 180, W),
                ("address", "地址", 380, W),
                ("status", "状态", 70, CENTER),
            ),
            empty_text="暂无施工地点",
            stretch=("name", "address"),
            padding=0,
        )
        self.site_tree.tree.configure(selectmode="extended", height=5)
        self.site_tree.tree.tag_configure("inactive", foreground=COLORS["text_muted"])
        self.site_tree.tree.bind("<Double-1>", lambda _event: self.edit_selected_site())

    def clear_filters(self):
        self.keyword_var.set("")
        self.status_var.set("全部状态")
        self.load_projects()

    def load_projects(self, select_id=None):
        status = "" if self.status_var.get() == "全部状态" else self.status_var.get()
        rows = project_service.list_projects(
            keyword=self.keyword_var.get().strip(), status=status
        )

        def mapper(row):
            tags = ("closed",) if row["status"] == "已关闭" else ()
            return str(row["id"]), (
                row["project_code"],
                row["name"],
                row["customer_name"],
                row["manager"],
                row["status"],
                BUSINESS_MODE_LABELS.get(row["business_mode"], row["business_mode"]),
                row["site_count"],
                self._schedule_text(
                    row["planned_start_date"], row["planned_end_date"]
                ),
            ), tags

        self.project_tree.refresh(rows, mapper)
        self.count_var.set(f"{len(rows)} 个项目")
        selected_item = next(
            (str(row["id"]) for row in rows if select_id and row["id"] == int(select_id)),
            None,
        )
        if selected_item:
            self.project_tree.tree.selection_set(selected_item)
            self.project_tree.tree.focus(selected_item)
            self.project_tree.tree.see(selected_item)
            self.on_project_select()
        elif not rows:
            self.selected_project_id = None
            self.site_title_var.set("施工地点 · 请先建立项目")
            self.site_tree.clear()

    def selected_project_ids(self):
        return [int(item) for item in self.project_tree.tree.selection()]

    def on_project_select(self, _event=None):
        selected = self.project_tree.tree.selection()
        if not selected:
            self.selected_project_id = None
            self.site_title_var.set("施工地点 · 请先选择项目")
            self.site_tree.clear()
            return
        self.selected_project_id = int(selected[0])
        values = self.project_tree.tree.item(selected[0], "values")
        self.site_title_var.set(f"施工地点 · {values[1]}")
        self.load_sites()

    def load_sites(self, select_id=None):
        if not self.selected_project_id:
            self.site_tree.clear()
            return
        rows = project_service.list_project_sites(
            self.selected_project_id, include_inactive=True
        )
        self.site_tree.refresh(
            rows,
            lambda row: (str(row["id"]), (
                row["site_code"],
                row["site_name"],
                row["address"],
                "启用" if row["is_active"] else "已停用",
            ), () if row["is_active"] else ("inactive",)),
        )
        selected_item = next(
            (str(row["id"]) for row in rows if select_id and row["id"] == int(select_id)),
            None,
        )
        if selected_item:
            self.site_tree.tree.selection_set(selected_item)
            self.site_tree.tree.focus(selected_item)

    @staticmethod
    def _valid_date(value):
        value = value.strip()
        if not value:
            return True
        try:
            datetime.strptime(value, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    @staticmethod
    def _schedule_text(start, end):
        if start and end:
            return f"{start} 至 {end}"
        return start or end or ""

    def open_project_dialog(self, project_id=None):
        data = project_service.get_project(project_id) if project_id else {}
        if project_id and not data:
            messagebox.showwarning("提示", "项目不存在或已被关闭。")
            return
        dialog = ttk.Toplevel(self.parent)
        dialog.title("编辑项目" if project_id else "新增项目")
        body, footer = build_form_dialog(
            dialog, self.parent, 680, 620,
            min_width=560, min_height=420,
        )

        ttk.Label(
            body, text="编辑项目" if project_id else "建立新项目",
            style="PageTitle.TLabel",
        ).grid(row=0, column=0, columnspan=4, sticky=W)
        ttk.Label(
            body, text="保存后即可用于采购归集、工天和施工记录。",
            style="PageSub.TLabel",
        ).grid(row=1, column=0, columnspan=4, sticky=W, pady=(3, 16))

        customers = master_data_service.list_customers(active_only=True)
        if data.get("customer_partner_id") and not any(
            row["id"] == data["customer_partner_id"] for row in customers
        ):
            historical = next(
                (
                    row for row in master_data_service.list_customers()
                    if row["id"] == data["customer_partner_id"]
                ),
                None,
            )
            if historical:
                customers.append(historical)
        customer_rows = {row["name"]: row for row in customers}
        customer_map = {name: row["id"] for name, row in customer_rows.items()}
        fields = {}
        specs = (
            ("项目编码", "project_code", 2, 0),
            ("项目名称 *", "name", 2, 2),
            ("客户名称", "customer_name", 3, 0),
            ("项目负责人", "manager", 3, 2),
            ("计划开始", "planned_start_date", 4, 0),
            ("计划结束", "planned_end_date", 4, 2),
            ("项目地址", "address", 5, 0),
        )
        for label, key, row, column in specs:
            ttk.Label(body, text=label).grid(
                row=row, column=column, sticky=E, padx=(0, 8), pady=7
            )
            if key == "customer_name":
                entry = ttk.Combobox(
                    body, values=list(customer_map), state="normal", width=25
                )
            elif key in ("planned_start_date", "planned_end_date"):
                date_var = ttk.StringVar(value=data.get(key) or "")
                entry = DatePicker(
                    body,
                    textvariable=date_var,
                    allow_empty=True,
                    popup_title=f"选择{label}",
                )
            else:
                entry = ttk.Entry(body, width=25)
            span = 3 if key == "address" else 1
            entry.grid(
                row=row, column=column + 1, columnspan=span,
                sticky=EW, padx=(0, 14 if span == 1 else 0), pady=7,
            )
            if key not in ("planned_start_date", "planned_end_date"):
                entry.insert(0, data.get(key) or "")
            fields[key] = entry
        if not project_id:
            fields["project_code"].insert(0, "")

        ttk.Label(body, text="项目状态").grid(
            row=7, column=2, sticky=E, padx=(0, 8), pady=7
        )
        status_var = ttk.StringVar(value=data.get("status", "筹备中"))
        ttk.Combobox(
            body, textvariable=status_var, values=PROJECT_STATUSES,
            state="readonly", width=22,
        ).grid(row=7, column=3, sticky=EW, pady=7)

        mode_by_label = {label: key for key, label in BUSINESS_MODE_LABELS.items()}
        policy_by_label = {label: key for key, label in INVOICE_POLICY_LABELS.items()}
        entity_by_label = {label: key for key, label in ENTITY_TYPE_LABELS.items()}
        mode_var = ttk.StringVar(
            value=BUSINESS_MODE_LABELS.get(
                data.get("business_mode", "contract"), "正式合同工程"
            )
        )
        policy_var = ttk.StringVar(
            value=INVOICE_POLICY_LABELS.get(
                data.get("invoice_policy", "required"), "需要开票"
            )
        )
        entity_var = ttk.StringVar(
            value=ENTITY_TYPE_LABELS.get(
                data.get("customer_entity_type", "enterprise"), "企业"
            )
        )
        choice_widgets = {}
        for label, key, variable, values, row, column in (
            ("业务模式 *", "mode", mode_var, tuple(mode_by_label), 6, 0),
            ("开票要求 *", "policy", policy_var, tuple(policy_by_label), 6, 2),
            ("客户主体", "entity", entity_var, tuple(entity_by_label), 7, 0),
        ):
            ttk.Label(body, text=label).grid(
                row=row, column=column, sticky=E, padx=(0, 8), pady=7
            )
            combo = ttk.Combobox(
                body, textvariable=variable, values=values,
                state="readonly", width=22,
            )
            combo.grid(
                row=row, column=column + 1, sticky=EW,
                padx=(0, 14 if column == 0 else 0), pady=7,
            )
            choice_widgets[key] = combo

        def sync_policy(_event=None):
            if mode_by_label[mode_var.get()] == "cash":
                policy_var.set(INVOICE_POLICY_LABELS["not_required"])

        def sync_customer_entity(_event=None):
            customer = customer_rows.get(fields["customer_name"].get().strip())
            if customer:
                entity_var.set(
                    ENTITY_TYPE_LABELS.get(customer["entity_type"], "企业")
                )

        fields["customer_name"].bind("<<ComboboxSelected>>", sync_customer_entity)
        choice_widgets["mode"].bind("<<ComboboxSelected>>", sync_policy)

        ttk.Label(body, text="备注").grid(
            row=8, column=0, sticky="ne", padx=(0, 8), pady=7
        )
        notes = ttk.Text(body, height=5, width=55, wrap="word")
        notes.grid(row=8, column=1, columnspan=3, sticky=EW, pady=7)
        notes.insert("1.0", data.get("notes", ""))
        body.columnconfigure(1, weight=1)
        body.columnconfigure(3, weight=1)

        def save():
            values = {key: entry.get().strip() for key, entry in fields.items()}
            values["customer_partner_id"] = customer_map.get(
                values["customer_name"]
            )
            values["status"] = status_var.get()
            values["business_mode"] = mode_by_label[mode_var.get()]
            values["invoice_policy"] = policy_by_label[policy_var.get()]
            values["customer_entity_type"] = entity_by_label[entity_var.get()]
            values["notes"] = notes.get("1.0", END).strip()
            if not values["name"]:
                messagebox.showwarning("提示", "项目名称不能为空。", parent=dialog)
                fields["name"].focus_set()
                return
            if project_id and not values["project_code"]:
                messagebox.showwarning("提示", "项目编码不能为空。", parent=dialog)
                fields["project_code"].focus_set()
                return
            for key, label in (
                ("planned_start_date", "计划开始日期"),
                ("planned_end_date", "计划结束日期"),
            ):
                if not self._valid_date(values[key]):
                    messagebox.showwarning(
                        "提示", f"{label}请使用 YYYY-MM-DD 格式。", parent=dialog
                    )
                    fields[key].focus_set()
                    return
            try:
                if project_id:
                    project_service.update_project(project_id, values)
                    saved_id = project_id
                else:
                    saved_id = project_service.create_project(values)
            except Exception as exc:
                messagebox.showerror(
                    "保存失败", f"项目未保存：{exc}\n请检查项目编码是否重复。",
                    parent=dialog,
                )
                return
            dialog.destroy()
            self.load_projects(select_id=saved_id)
            messagebox.showinfo("保存成功", "项目资料已更新。")

        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text="保存项目",
            primary_command=save,
        )
        fields["name"].focus_set()

    def edit_selected_project(self):
        ids = self.selected_project_ids()
        if len(ids) != 1:
            messagebox.showwarning("提示", "请选择一个需要编辑的项目。")
            return
        self.open_project_dialog(ids[0])

    def close_selected_projects(self):
        ids = self.selected_project_ids()
        if not ids:
            messagebox.showwarning("提示", "请先选择需要关闭的项目。")
            return
        if not messagebox.askyesno(
            "确认关闭",
            f"确定关闭选中的 {len(ids)} 个项目吗？\n历史采购和施工记录会继续保留。",
        ):
            return
        project_service.close_projects(ids)
        self.load_projects()

    def open_site_dialog(self, site_id=None):
        if not self.selected_project_id:
            messagebox.showwarning("提示", "请先选择一个项目。")
            return
        data = {}
        if site_id:
            data = next(
                (
                    row
                    for row in project_service.list_project_sites(
                        self.selected_project_id, include_inactive=True
                    )
                    if row["id"] == int(site_id)
                ),
                {},
            )
        dialog = ttk.Toplevel(self.parent)
        dialog.title("编辑施工地点" if site_id else "新增施工地点")
        body, footer = build_form_dialog(
            dialog, self.parent, 520, 360,
            min_width=480, min_height=300,
        )
        ttk.Label(
            body, text="编辑施工地点" if site_id else "新增施工地点",
            style="PageTitle.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky=W, pady=(0, 15))

        entries = {}
        for row, (label, key) in enumerate(
            (("地点编码", "site_code"), ("地点名称 *", "site_name"), ("详细地址", "address")),
            start=1,
        ):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky=E, padx=(0, 10), pady=8)
            entry = ttk.Entry(body, width=40)
            entry.grid(row=row, column=1, sticky=EW, pady=8)
            entry.insert(0, data.get(key, ""))
            entries[key] = entry
        ttk.Label(
            body, text="地点将同步出现在“施工与验收”的地点选择中。",
            style="PageSub.TLabel",
        ).grid(row=4, column=1, sticky=W, pady=(3, 12))
        body.columnconfigure(1, weight=1)

        def save():
            values = {key: entry.get().strip() for key, entry in entries.items()}
            if not values["site_name"]:
                messagebox.showwarning("提示", "地点名称不能为空。", parent=dialog)
                entries["site_name"].focus_set()
                return
            if site_id and not values["site_code"]:
                messagebox.showwarning("提示", "地点编码不能为空。", parent=dialog)
                entries["site_code"].focus_set()
                return
            try:
                if site_id:
                    project_service.update_project_site(site_id, values)
                    saved_id = site_id
                else:
                    saved_id = project_service.create_project_site(
                        self.selected_project_id, values
                    )
            except Exception as exc:
                messagebox.showerror(
                    "保存失败", f"施工地点未保存：{exc}\n请检查地点编码是否重复。",
                    parent=dialog,
                )
                return
            dialog.destroy()
            self.load_sites(select_id=saved_id)
            self.load_projects(select_id=self.selected_project_id)

        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text="保存地点",
            primary_command=save,
        )
        entries["site_name"].focus_set()

    def edit_selected_site(self):
        selected = self.site_tree.tree.selection()
        if len(selected) != 1:
            messagebox.showwarning("提示", "请选择一个需要编辑的施工地点。")
            return
        self.open_site_dialog(int(selected[0]))

    def deactivate_selected_sites(self):
        selected = self.site_tree.tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择需要停用的施工地点。")
            return
        if not messagebox.askyesno(
            "确认停用",
            f"确定停用选中的 {len(selected)} 个地点吗？\n历史施工记录会继续保留。",
        ):
            return
        project_service.deactivate_project_sites([int(item) for item in selected])
        self.load_sites()
        self.load_projects(select_id=self.selected_project_id)
