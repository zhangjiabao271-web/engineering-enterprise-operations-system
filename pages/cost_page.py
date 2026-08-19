from datetime import datetime
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import cost_service, project_service
from ui.charts import DonutBreakdown, HorizontalBreakdown
from ui.components import (
    BottomToolbar,
    DataTable,
    DatePicker,
    FilterBar,
    KpiCard,
    PageHeader,
)
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders
from ui.attachments import open_attachment_manager
from ui.theme import SPACING


class CostLedgerPage:
    """Traceable project costs."""

    SOURCE_LABELS = {
        "purchase": "采购自动归集",
        "labor": "人工自动归集",
        "manual": "手工成本",
    }

    def __init__(self, parent):
        self.parent = parent
        self.project_map = {"全部项目": None}
        self.project_var = ttk.StringVar(value="全部项目")
        self.month_var = ttk.StringVar(value=datetime.now().strftime("%Y-%m"))
        self.total_var = ttk.StringVar(value="¥0.00")
        self.kpi_vars = {
            "total": ttk.StringVar(value="¥0.00"),
            "purchase": ttk.StringVar(value="¥0.00"),
            "labor": ttk.StringVar(value="¥0.00"),
            "manual": ttk.StringVar(value="¥0.00"),
        }
        self.kpi_hints = {
            "total": ttk.StringVar(value="上月 ¥0.00"),
            "purchase": ttk.StringVar(value="0 笔 · 0%"),
            "labor": ttk.StringVar(value="0 条工天 · 0%"),
            "manual": ttk.StringVar(value="0 笔 · 0%"),
        }
        self.build_ui()
        safe_init_loaders(
            "成本", [self.refresh_projects, self.refresh_months, self.refresh]
        )

    @staticmethod
    def money(value):
        amount = int(value or 0) / 100
        return f"{'-' if amount < 0 else ''}¥{abs(amount):,.2f}"

    def build_ui(self):
        PageHeader(
            self.parent,
            "成本看板",
            "采购、人工和其他成本统一汇总；点击图表可联动下方明细",
            actions=[
                ttk.Button(
                    self.parent, text="登记成本", bootstyle="primary",
                    command=self.open_cost_dialog,
                ),
            ],
        )

        self.month_combo = ttk.Combobox(
            self.parent, textvariable=self.month_var, state="readonly", width=9
        )
        self.month_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh()
        )
        self.project_combo = ttk.Combobox(
            self.parent, textvariable=self.project_var,
            state="readonly", width=26
        )
        self.project_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh()
        )
        self.detail_toggle_var = ttk.StringVar(value="收起明细")
        FilterBar(
            self.parent,
            ("统计期", self.month_combo),
            ("项目", self.project_combo),
            actions=[
                ttk.Button(
                    self.parent, textvariable=self.detail_toggle_var,
                    bootstyle="secondary-outline", command=self.toggle_details,
                ),
            ],
        )

        # ========== KPI 卡片行 ==========
        kpi_grid = ttk.Frame(self.parent)
        kpi_grid.pack(fill=X, pady=(0, SPACING["md"]))
        kpi_specs = [
            ("total", "本月总成本"),
            ("purchase", "采购成本"),
            ("labor", "人工成本"),
            ("manual", "手工 / 其他"),
        ]
        for index, (key, label) in enumerate(kpi_specs):
            KpiCard(kpi_grid, label, self.kpi_vars[key], self.kpi_hints[key]).grid(
                row=0, column=index, sticky=EW,
                padx=(0 if index == 0 else 6, 0 if index == 3 else 6),
            )
            kpi_grid.columnconfigure(index, weight=1)

        # ========== 中部图表区 ==========
        chart_row = ttk.Panedwindow(self.parent, orient=HORIZONTAL)
        chart_row.pack(fill=BOTH, expand=True, pady=(0, 12))

        source_card = ttk.Frame(
            chart_row, style="Card.TFrame", padding=(14, 12)
        )
        chart_row.add(source_card, weight=3)
        source_head = ttk.Frame(source_card)
        source_head.pack(fill=X, pady=(0, 6))
        ttk.Label(
            source_head, text="成本构成", style="CardTitle.TLabel"
        ).pack(side=LEFT)
        self.source_dim_var = ttk.StringVar(value="按来源")
        dims = ("按来源", "按分类")
        self.source_dim_combo = ttk.Combobox(
            source_head, textvariable=self.source_dim_var, values=dims,
            state="readonly", width=8,
        )
        self.source_dim_combo.pack(side=RIGHT)
        self.source_dim_combo.bind(
            "<<ComboboxSelected>>", lambda _event: self.refresh_charts()
        )
        self.donut = DonutBreakdown(source_card)
        self.donut.pack(fill=BOTH, expand=True)

        project_card = ttk.Frame(
            chart_row, style="Card.TFrame", padding=(14, 12)
        )
        chart_row.add(project_card, weight=2)
        ttk.Label(
            project_card, text="项目成本 TOP", style="CardTitle.TLabel"
        ).pack(anchor=W, pady=(0, 6))
        self.project_rank = HorizontalBreakdown(project_card, limit=6)
        self.project_rank.pack(fill=BOTH, expand=True)

        # ========== 底部明细区（可折叠）==========
        self.detail_container = ttk.Frame(self.parent)
        self.detail_container.pack(fill=BOTH, expand=True)

        ledger_tab = ttk.Frame(self.detail_container, padding=(0, 10, 0, 0))
        ledger_tab.pack(fill=BOTH, expand=True)

        BottomToolbar(
            ledger_tab,
            ttk.Button(
                ledger_tab, text="归集 / 重新分摊所选成本",
                bootstyle="primary-outline", command=self.assign_selected_cost,
            ),
            ttk.Button(
                ledger_tab, text="作废所选手工成本",
                bootstyle="danger-outline", command=self.void_selected_cost,
            ),
            ttk.Button(
                ledger_tab, text="成本附件",
                bootstyle="secondary-outline", command=self.open_cost_attachments,
            ),
        )
        self.cost_tree = DataTable(
            ledger_tab,
            specs=(
                ("date", "日期", 92, CENTER),
                ("project", "项目", 150, W),
                ("source", "来源", 105, CENTER),
                ("no", "来源单号", 145, W),
                ("category", "成本分类", 105, W),
                ("counterparty", "往来单位 / 人员", 170, W),
                ("vehicle", "车辆 / 车牌", 100, W),
                ("amount", "成本金额", 120, E),
            ),
            empty_text="当前条件下暂无成本记录，点击右上角「登记成本」",
            stretch=("project", "no", "category", "counterparty", "vehicle"),
        )

    def refresh_projects(self):
        current = self.project_var.get()
        self.project_map = {"全部项目": None}
        for row in project_service.list_projects():
            self.project_map[f"{row['name']} · {row['project_code']}"] = row["id"]
        self.project_combo.configure(values=list(self.project_map))
        self.project_var.set(
            current if current in self.project_map else "全部项目"
        )

    def refresh_months(self):
        from services import cost_service as cs
        months = cs.list_cost_months()
        current = self.month_var.get()
        if current not in months:
            months.insert(0, current)
        self.month_combo.configure(values=months)
        self.month_var.set(current)

    def toggle_details(self):
        collapsed = self.detail_toggle_var.get() == "展开明细"
        self.detail_toggle_var.set("收起明细" if collapsed else "展开明细")
        if collapsed:
            self.detail_container.pack(fill=BOTH, expand=True)
        else:
            self.detail_container.pack_forget()
        self.parent.update_idletasks()

    def refresh_charts(self):
        from services import cost_service as cs
        data = cs.get_cost_dashboard(
            month=self.month_var.get(),
            project_id=self.project_map.get(self.project_var.get()),
        )
        dim = self.source_dim_var.get()
        if dim == "按分类":
            items = data["by_category"]
        else:
            items = data["by_source"]
        self.donut.set_data(items)
        self.project_rank.set_data(data["by_project"])

    def refresh(self):
        project_id = self.project_map.get(self.project_var.get())
        project_names = {
            row["id"]: row["name"] for row in project_service.list_projects()
        }
        from services import cost_service as cs
        data = cs.get_cost_dashboard(
            month=self.month_var.get(), project_id=project_id
        )
        summary = data["summary"]
        total = summary["total_minor"]
        self.total_var.set(self.money(total))
        self.kpi_vars["total"].set(self.money(total))
        self.kpi_vars["purchase"].set(self.money(summary["purchase_minor"]))
        self.kpi_vars["labor"].set(self.money(summary["labor_minor"]))
        self.kpi_vars["manual"].set(self.money(summary["manual_minor"]))
        prev = summary["previous_total_minor"]
        hint = f"上月 {self.money(prev)}"
        if prev:
            change = (total - prev) / prev * 100
            hint += f" · {'+' if change >= 0 else ''}{change:.1f}%"
        self.kpi_hints["total"].set(hint)
        total_safe = total or 1
        self.kpi_hints["purchase"].set(
            f"{summary['purchase_count']} 笔 · "
            f"{summary['purchase_minor'] / total_safe * 100:.0f}%"
        )
        self.kpi_hints["labor"].set(
            f"{summary['labor_count']} 条工天 · "
            f"{summary['labor_minor'] / total_safe * 100:.0f}%"
        )
        self.kpi_hints["manual"].set(
            f"{summary['manual_count']} 笔 · "
            f"{summary['manual_minor'] / total_safe * 100:.0f}%"
            + (
                f" · 未归集 {self.money(summary['unassigned_minor'])}"
                if summary["unassigned_minor"]
                else ""
            )
        )
        self.refresh_charts()

        rows = cs.list_cost_ledger(project_id)
        self.cost_tree.refresh(
            rows,
            lambda row: (f"{row['source_type']}:{row['id']}", (
                row["business_date"],
                row.get("allocation_project_names")
                or project_names.get(row["project_id"], "待归集"),
                self.SOURCE_LABELS[row["source_type"]],
                row["source_no"],
                row["category"],
                row["counterparty"],
                row.get("vehicle_no", ""),
                self.money(row["amount_minor"]),
            )),
        )
    def _project_options(self, include_unassigned=False):
        mapping = {}
        if include_unassigned:
            mapping["待归集 · 暂不确定项目"] = None
        for row in project_service.list_projects():
            mapping[f"{row['name']} · {row['project_code']}"] = row["id"]
        return mapping

    def _build_allocation_editor(
        self, parent, amount_var, *, initial_method="unassigned", initial_lines=None
    ):
        projects = project_service.list_projects()
        project_names = {row["id"]: row["name"] for row in projects}
        project_labels = {
            row["id"]: f"{row['name']} · {row['project_code']}"
            for row in projects
        }
        label_to_method = {
            label: code for code, label in cost_service.ALLOCATION_METHODS.items()
        }
        method_var = ttk.StringVar(
            value=cost_service.ALLOCATION_METHODS.get(
                initial_method, cost_service.ALLOCATION_METHODS["unassigned"]
            )
        )
        direct_map = {
            label: project_id for project_id, label in project_labels.items()
        }
        direct_var = ttk.StringVar(value=next(iter(direct_map), ""))
        selected_vars = {row["id"]: ttk.BooleanVar(value=False) for row in projects}
        manual_vars = {row["id"]: ttk.StringVar() for row in projects}
        helper_var = ttk.StringVar()
        preview_status = ttk.StringVar()
        error_var = ttk.StringVar()

        for line in initial_lines or []:
            project_id = line["project_id"]
            if project_id in selected_vars:
                selected_vars[project_id].set(True)
                manual_vars[project_id].set(
                    f"{int(line['amount_minor']) / 100:.2f}"
                )
                direct_var.set(project_labels[project_id])

        section = ttk.Frame(parent, style="Card.TFrame", padding=14)
        ttk.Label(
            section, text="项目归集", style="CardTitle.TLabel"
        ).grid(row=0, column=0, columnspan=2, sticky=W, pady=(0, 10))
        ttk.Label(section, text="归集方式 *").grid(
            row=1, column=0, sticky=W, padx=(0, 12), pady=(0, 8)
        )
        method_combo = ttk.Combobox(
            section,
            textvariable=method_var,
            values=list(label_to_method),
            state="readonly",
        )
        method_combo.grid(row=1, column=1, sticky=EW, pady=(0, 8))
        ttk.Label(
            section,
            textvariable=helper_var,
            style="CardText.TLabel",
            wraplength=590,
        ).grid(row=2, column=0, columnspan=2, sticky=W, pady=(0, 10))

        direct_frame = ttk.Frame(section)
        ttk.Label(direct_frame, text="承担项目").pack(anchor=W, pady=(0, 5))
        ttk.Combobox(
            direct_frame,
            textvariable=direct_var,
            values=list(direct_map),
            state="readonly",
        ).pack(fill=X, ipady=4)
        ttk.Label(
            direct_frame,
            textvariable=preview_status,
            style="CardText.TLabel",
        ).pack(anchor=W, pady=(6, 0))

        multi_frame = ttk.Frame(section)
        ttk.Label(multi_frame, text="选择").grid(row=0, column=0, sticky=W)
        ttk.Label(multi_frame, text="项目").grid(
            row=0, column=1, sticky=W, padx=(8, 12)
        )
        ttk.Label(multi_frame, text="手工金额（元）").grid(row=0, column=2, sticky=W)
        manual_entries = []
        for row_index, project in enumerate(projects, start=1):
            ttk.Checkbutton(
                multi_frame,
                variable=selected_vars[project["id"]],
                command=lambda: refresh_preview(),
            ).grid(row=row_index, column=0, sticky=W, pady=3)
            ttk.Label(
                multi_frame,
                text=project_labels[project["id"]],
            ).grid(row=row_index, column=1, sticky=W, padx=(8, 12), pady=3)
            entry = ttk.Entry(
                multi_frame, textvariable=manual_vars[project["id"]], width=18
            )
            entry.grid(row=row_index, column=2, sticky=EW, pady=3, ipady=3)
            manual_entries.append(entry)
        multi_frame.columnconfigure(1, weight=1)

        preview_frame = ttk.Frame(section)
        ttk.Separator(preview_frame).pack(fill=X, pady=(8, 10))
        preview_header = ttk.Frame(preview_frame)
        preview_header.pack(fill=X, pady=(0, 6))
        ttk.Label(preview_header, text="分摊预览").pack(side=LEFT)
        ttk.Label(
            preview_header, textvariable=preview_status, style="CardText.TLabel"
        ).pack(side=LEFT, padx=(12, 0))
        preview_tree = ttk.Treeview(
            preview_frame,
            columns=("project", "amount", "ratio"),
            show="headings",
            height=4,
            bootstyle="primary",
        )
        for key, label, width, anchor in (
            ("project", "项目", 300, W),
            ("amount", "承担金额", 130, E),
            ("ratio", "占比", 90, E),
        ):
            preview_tree.heading(key, text=label)
            preview_tree.column(key, width=width, anchor=anchor)
        preview_tree.pack(fill=X)
        error_label = ttk.Label(
            section,
            textvariable=error_var,
            style="FormError.TLabel",
            wraplength=590,
        )
        error_label.grid(
            row=5, column=0, columnspan=2, sticky=W, pady=(8, 0)
        )
        error_label.grid_remove()

        def selection():
            method = label_to_method[method_var.get()]
            selected_ids = [
                project_id
                for project_id, selected in selected_vars.items()
                if selected.get()
            ]
            if method == "unassigned":
                plan = cost_service.build_allocation_plan(
                    amount_var.get(), "unassigned"
                )
                return method, [], [], plan
            if method == "direct":
                project_ids = [direct_map.get(direct_var.get())]
                plan = cost_service.build_allocation_plan(
                    amount_var.get(), method, project_ids=project_ids
                )
                return method, project_ids, [], plan
            if method == "equal":
                plan = cost_service.build_allocation_plan(
                    amount_var.get(), method, project_ids=selected_ids
                )
                return method, selected_ids, [], plan
            allocations = [
                {
                    "project_id": project_id,
                    "amount": manual_vars[project_id].get(),
                }
                for project_id in selected_ids
            ]
            plan = cost_service.build_allocation_plan(
                amount_var.get(), method, allocations=allocations
            )
            return method, selected_ids, allocations, plan

        def refresh_preview(show_error=False):
            error_var.set("")
            error_label.grid_remove()
            preview_tree.delete(*preview_tree.get_children())
            try:
                method, _project_ids, _allocations, plan = selection()
                total_minor = sum(item["amount_minor"] for item in plan)
                for item in plan:
                    ratio = (
                        item["amount_minor"] / total_minor * 100
                        if total_minor else 0
                    )
                    preview_tree.insert(
                        "",
                        END,
                        values=(
                            project_names.get(item["project_id"], "未知项目"),
                            self.money(item["amount_minor"]),
                            f"{ratio:.1f}%",
                        ),
                    )
                if method == "unassigned":
                    preview_status.set("")
                elif method == "direct":
                    preview_status.set(f"该项目承担 {self.money(total_minor)}")
                else:
                    preview_status.set(f"合计 {self.money(total_minor)}")
            except Exception as error:
                preview_status.set("请完善金额和项目")
                if show_error:
                    error_var.set(str(error))
                    error_label.grid()

        def show_error(message):
            error_var.set(str(message))
            error_label.grid()

        def update_layout(_event=None):
            method = label_to_method[method_var.get()]
            direct_frame.grid_forget()
            multi_frame.grid_forget()
            preview_frame.grid_forget()
            error_var.set("")
            error_label.grid_remove()
            if method == "direct":
                helper_var.set("整笔费用计入一个项目。")
                direct_frame.grid(row=3, column=0, columnspan=2, sticky=EW)
            elif method in ("equal", "manual"):
                helper_var.set(
                    "选择多个项目后自动计算；手工分摊的合计必须等于原费用。"
                )
                multi_frame.grid(row=3, column=0, columnspan=2, sticky=EW)
                state = "normal" if method == "manual" else "disabled"
                for entry in manual_entries:
                    entry.configure(state=state)
                preview_frame.grid(row=4, column=0, columnspan=2, sticky=EW)
            else:
                helper_var.set(
                    "先保存到待归集队列，项目确定后再分配；原费用只登记一次。"
                )
            refresh_preview()

        method_combo.bind("<<ComboboxSelected>>", update_layout)
        for variable in (amount_var, direct_var, *manual_vars.values()):
            variable.trace_add("write", lambda *_args: refresh_preview())
        section.columnconfigure(1, weight=1)
        update_layout()
        return section, selection, show_error

    def open_cost_dialog(self):
        dialog = ttk.Toplevel(self.parent)
        dialog.title("登记成本")
        body, footer = build_form_dialog(
            dialog, self.parent, 760, 480, min_width=640, min_height=460
        )
        variables = {
            "no": ttk.StringVar(),
            "date": ttk.StringVar(value=datetime.now().strftime("%Y-%m-%d")),
            "category": ttk.StringVar(value="用车"),
            "amount": ttk.StringVar(),
            "counterparty": ttk.StringVar(),
            "vehicle": ttk.StringVar(),
        }
        information = ttk.Frame(body, style="Card.TFrame", padding=14)
        information.pack(fill=X)
        ttk.Label(
            information, text="费用信息", style="CardTitle.TLabel"
        ).grid(row=0, column=0, columnspan=2, sticky=W, pady=(0, 8))
        specs = (
            ("成本日期 *", "date", None),
            ("成本分类 *", "category", cost_service.COST_CATEGORIES),
            ("金额（元）*", "amount", None),
            ("商家 / 收款方", "counterparty", None),
            ("车辆 / 车牌（用车可填）", "vehicle", None),
            ("成本单号", "no", None),
        )
        for index, (label, key, values) in enumerate(specs):
            field = ttk.Frame(information, style="Card.TFrame")
            field.grid(
                row=index // 2 + 1,
                column=index % 2,
                sticky=EW,
                padx=(0, 10) if index % 2 == 0 else (10, 0),
                pady=6,
            )
            ttk.Label(field, text=label, style="CardText.TLabel").pack(anchor=W)
            if values:
                widget = ttk.Combobox(
                    field, textvariable=variables[key],
                    values=values, state="readonly"
                )
            elif key == "date":
                widget = DatePicker(
                    field,
                    textvariable=variables[key],
                    popup_title="选择成本日期",
                )
            else:
                widget = ttk.Entry(field, textvariable=variables[key])
            pack_options = {"fill": X, "pady": (4, 0)}
            if not isinstance(widget, DatePicker):
                pack_options["ipady"] = 4
            widget.pack(**pack_options)
        information.columnconfigure(0, weight=1)
        information.columnconfigure(1, weight=1)

        allocation_section, allocation_selection, show_allocation_error = (
            self._build_allocation_editor(body, variables["amount"])
        )
        allocation_section.pack(fill=X, pady=(12, 0))

        notes_section = ttk.Frame(body, style="Card.TFrame", padding=14)
        notes_section.pack(fill=BOTH, expand=True, pady=(12, 0))
        ttk.Label(
            notes_section, text="补充说明", style="CardTitle.TLabel"
        ).pack(anchor=W, pady=(0, 8))
        notes = ttk.Text(notes_section, height=3, wrap="word")
        notes.pack(fill=BOTH, expand=True)
        form_error = ttk.StringVar()
        ttk.Label(
            body,
            textvariable=form_error,
            style="FormError.TLabel",
            wraplength=600,
        ).pack(anchor=W, pady=(8, 0))

        def save():
            form_error.set("")
            try:
                method, project_ids, allocations, _plan = allocation_selection()
            except Exception as error:
                show_allocation_error(error)
                return
            try:
                cost_service.create_cost(
                    {
                        "cost_no": variables["no"].get(),
                        "cost_date": variables["date"].get(),
                        "category": variables["category"].get(),
                        "amount": variables["amount"].get(),
                        "counterparty_name": variables["counterparty"].get(),
                        "vehicle_no": variables["vehicle"].get(),
                        "allocation_method": method,
                        "project_ids": project_ids,
                        "allocations": allocations,
                        "notes": notes.get("1.0", END).strip(),
                    }
                )
            except Exception as error:
                form_error.set(str(error))
                return
            dialog.destroy()
            self.refresh()

        add_form_actions(
            footer, cancel_command=dialog.destroy,
            primary_text="保存成本", primary_command=save,
        )

    def assign_selected_cost(self):
        selected = self.cost_tree.tree.selection()
        manual_ids = [
            int(item.split(":", 1)[1])
            for item in selected
            if item.startswith("manual:")
        ]
        ids = list(dict.fromkeys(manual_ids))
        if len(ids) != 1:
            messagebox.showwarning("提示", "请选择一条手工成本进行归集或重新分摊")
            return
        cost_id = ids[0]
        cost = cost_service.get_cost_entry(cost_id)
        allocation = cost_service.get_cost_allocations(cost_id)
        dialog = ttk.Toplevel(self.parent)
        dialog.title("归集 / 重新分摊成本")
        body, footer = build_form_dialog(
            dialog, self.parent, 720, 570, min_width=600, min_height=460
        )
        amount_var = ttk.StringVar(value=f"{cost['amount_minor'] / 100:.2f}")
        ttk.Label(
            body,
            text=f"{cost['cost_no']} · {cost['category']} · {self.money(cost['amount_minor'])}",
            style="CardTitle.TLabel",
        ).pack(anchor=W, pady=(0, 6))
        ttk.Label(
            body,
            text="重新分摊会保留旧版本作为审计记录，不会修改原费用金额。",
            style="CardText.TLabel",
        ).pack(anchor=W, pady=(0, 12))
        allocation_section, allocation_selection, show_allocation_error = (
            self._build_allocation_editor(
                body,
                amount_var,
                initial_method=allocation["method"],
                initial_lines=allocation["lines"],
            )
        )
        allocation_section.pack(fill=X)

        def save():
            try:
                method, project_ids, allocations, _plan = allocation_selection()
            except Exception as error:
                show_allocation_error(error)
                return
            try:
                cost_service.allocate_cost(
                    cost_id,
                    method,
                    project_ids=project_ids,
                    allocations=allocations,
                )
            except Exception as error:
                show_allocation_error(error)
                return
            dialog.destroy()
            self.refresh()

        add_form_actions(
            footer, cancel_command=dialog.destroy,
            primary_text="保存分摊", primary_command=save,
        )

    def void_selected_cost(self):
        selected = self.cost_tree.tree.selection()
        ids = [
            int(item.split(":", 1)[1])
            for item in selected
            if item.startswith("manual:")
        ]
        if not ids:
            messagebox.showwarning("提示", "采购和人工来源不能在此作废，请选择手工成本")
            return
        if messagebox.askyesno("确认作废", f"确定作废 {len(ids)} 条手工成本吗？"):
            cost_service.void_costs(ids)
            self.refresh()

    def open_cost_attachments(self):
        selected = self.cost_tree.tree.selection()
        if len(selected) != 1 or not selected[0].startswith("manual:"):
            messagebox.showwarning("提示", "请选择一条手工成本")
            return
        cost_id = int(selected[0].split(":", 1)[1])
        open_attachment_manager(
            self.parent, "cost", cost_id, "成本"
        )
