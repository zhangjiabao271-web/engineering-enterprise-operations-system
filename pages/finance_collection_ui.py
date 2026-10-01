"""FinanceCollectionMixin: extracted UI behavior from pages/finance_page.py."""

from datetime import datetime
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import collection_service
from ui.components import DatePicker, FilterBar
from ui.dialogs import add_form_actions, build_form_dialog


class FinanceCollectionMixin:
    def _build_collection_workbench(self, parent):
        self.collection_view_var = ttk.StringVar(value="项目视图")
        self.collection_action_filter_var = ttk.StringVar(value="全部应收")
        self.collection_customer_filter = None
        self.collection_project_rows = []
        self.collection_customer_rows = []
        self.collection_hint_var = ttk.StringVar()

        view_combo = ttk.Combobox(
            parent,
            textvariable=self.collection_view_var,
            values=("项目视图", "客户视图"),
            state="readonly",
            width=10,
        )
        view_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._refresh_collection_workbench(),
        )
        action_combo = ttk.Combobox(
            parent,
            textvariable=self.collection_action_filter_var,
            values=("全部应收", "今日需跟进", "承诺逾期", "90天以上"),
            state="readonly",
            width=12,
        )
        action_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._refresh_collection_workbench(),
        )
        FilterBar(
            parent,
            ("查看", view_combo),
            ("筛选", action_combo),
            ttk.Label(
                parent,
                textvariable=self.collection_hint_var,
                style="CardText.TLabel",
            ),
        )
        actions = ttk.Frame(parent)
        actions.pack(fill=X, pady=(0, 8))
        for text, style, command in (
            ("更新跟进", "primary-outline", self.open_collection_case_dialog),
            ("查看跟进历史", "secondary-outline", self.open_collection_history),
            ("显示全部客户", "secondary-outline", self.clear_collection_customer_filter),
        ):
            ttk.Button(
                actions,
                text=text,
                bootstyle=style,
                command=command,
            ).pack(side=RIGHT, padx=(6, 0))

        self.collection_table_host = ttk.Frame(parent)
        self.collection_table_host.pack(fill=BOTH, expand=True)
        self.collection_project_tree = self._table(
            self.collection_table_host,
            (
                ("project", "项目 / 客户", 320, W),
                ("receivable", "未回款", 120, E),
                ("schedule", "承诺 / 跟进", 180, CENTER),
                ("owner", "责任人", 90, CENTER),
                ("status", "状态 / 账龄", 130, CENTER),
                ("action", "下一步动作", 240, W),
            ),
            empty_text="当前没有待回款项目",
            stretch=("project", "action"),
            padding=10,
        )
        self.collection_project_tree.tree.bind(
            "<Double-1>", lambda _event: self.open_collection_case_dialog()
        )
        self.collection_customer_tree = self._table(
            self.collection_table_host,
            (
                ("customer", "客户", 280, W),
                ("receivable", "未回款", 130, E),
                ("aging", "最老账龄", 90, CENTER),
                ("followup", "最近应跟进", 110, CENTER),
                ("owner", "责任人", 100, CENTER),
                ("status", "当前状态", 120, CENTER),
                ("action", "下一步动作", 250, W),
            ),
            empty_text="当前没有待回款客户",
            stretch=("customer", "action"),
            padding=10,
        )
        self.collection_customer_tree.tree.bind(
            "<Double-1>", lambda _event: self.drill_collection_customer()
        )
        self.collection_customer_tree.pack_forget()

    def _collection_filtered_rows(self):
        rows = list(self.collection_project_rows)
        if self.collection_customer_filter is not None:
            rows = [
                row for row in rows
                if row["customer_key"] == self.collection_customer_filter
            ]
        filter_label = self.collection_action_filter_var.get()
        if filter_label == "今日需跟进":
            rows = [row for row in rows if row["needs_action_today"]]
        elif filter_label == "承诺逾期":
            rows = [
                row for row in rows
                if row["attention_status"] == "承诺逾期"
            ]
        elif filter_label == "90天以上":
            rows = [
                row for row in rows
                if (row["aging_days"] or 0) > 90
            ]
        return rows

    def _refresh_collection_workbench(self, project_id=None):
        if not hasattr(self, "collection_project_tree"):
            return
        if project_id is None:
            project_id = self.selected_project_id()
        if project_id is not None:
            self.collection_customer_filter = None
        self.collection_project_rows = collection_service.list_project_cases(
            project_id
        )
        rows = self._collection_filtered_rows()
        summary = collection_service.summarize_project_cases(rows)
        self.collection_hint_var.set(
            f"{summary['project_count']} 个项目 · "
            f"未回 {self.money(summary['receivable_minor'])} · "
            f"今日需处理 {summary['action_count']} 个"
        )
        self.notebook.tab(3, text=f"回款跟进 · {len(self.collection_project_rows)}")

        if self.collection_view_var.get() == "客户视图":
            self.collection_project_tree.pack_forget()
            self.collection_customer_tree.pack(fill=BOTH, expand=True)
            self.collection_customer_rows = collection_service.list_customer_cases(
                rows
            )
            self.collection_customer_tree.refresh(
                self.collection_customer_rows,
                lambda row: (row["customer_key"], (
                    f"{row['customer_name']} · {row['project_count']}个项目",
                    self.money(row["receivable_minor"]),
                    row["aging_bucket"],
                    row["next_followup_date"] or "未安排",
                    row["owner_name"] or "未指定",
                    (
                        f"{row['attention_status']} · {row['action_count']}项"
                        if row["action_count"]
                        else row["attention_status"]
                    ),
                    row["next_action"] or "未填写",
                )),
            )
            return

        self.collection_customer_tree.pack_forget()
        self.collection_project_tree.pack(fill=BOTH, expand=True)
        self.collection_project_tree.refresh(
            rows,
            lambda row: (str(row["project_id"]), (
                f"{row['project_name']} · {row['customer_name']}",
                self.money(row["receivable_minor"]),
                (
                    f"{row['promised_date'] or '未承诺'} / "
                    f"{row['next_followup_date'] or '未安排'}"
                ),
                row["owner_name"] or "未指定",
                f"{row['attention_status']} · {row['aging_bucket']}",
                row["next_action"] or "未填写",
            )),
        )

    def clear_collection_customer_filter(self):
        self.collection_customer_filter = None
        self._refresh_collection_workbench()

    def drill_collection_customer(self):
        selected = self.collection_customer_tree.tree.selection()
        if len(selected) != 1:
            return
        key = selected[0]
        customer = next(
            (
                row for row in self.collection_customer_rows
                if row["customer_key"] == key
            ),
            None,
        )
        if not customer:
            return
        self.collection_customer_filter = customer["customer_key"]
        self.collection_view_var.set("项目视图")
        self._refresh_collection_workbench()

    def _selected_collection_project_id(self):
        if self.collection_view_var.get() != "项目视图":
            return None
        return self.selected_id(self.collection_project_tree)

    def open_collection_case_dialog(self):
        project_id = self._selected_collection_project_id()
        if not project_id:
            messagebox.showwarning(
                "提示", "请在项目视图中选择一个待回款项目"
            )
            return
        case = collection_service.get_project_case(project_id)
        if not case or case["receivable_minor"] <= 0:
            messagebox.showwarning("提示", "该项目当前没有待回款余额")
            return

        dialog = ttk.Toplevel(self.parent)
        dialog.title("更新回款跟进")
        body, footer = build_form_dialog(
            dialog, self.parent, 840, 700, min_width=700, min_height=560
        )
        status_labels = {
            label: code
            for code, label in collection_service.CASE_STATUSES.items()
            if code != "closed"
        }
        variables = {
            "due_date": ttk.StringVar(
                value=case["due_date"] or case["oldest_unpaid_date"] or ""
            ),
            "promised_date": ttk.StringVar(value=case["promised_date"] or ""),
            "next_followup_date": ttk.StringVar(
                value=case["next_followup_date"] or ""
            ),
            "owner_name": ttk.StringVar(value=case["owner_name"] or ""),
            "status": ttk.StringVar(
                value=collection_service.CASE_STATUSES.get(
                    case["case_status"], "待跟进"
                )
            ),
            "followup_date": ttk.StringVar(
                value=datetime.now().strftime("%Y-%m-%d")
            ),
        }

        summary = ttk.Frame(body, style="Card.TFrame", padding=14)
        summary.pack(fill=X)
        ttk.Label(
            summary, text=case["project_name"], style="CardTitle.TLabel"
        ).pack(anchor=W)
        ttk.Label(
            summary,
            text=(
                f"{case['customer_name']} · 未回 {self.money(case['receivable_minor'])}"
                f" · {case['aging_bucket']} · {case['attention_status']}"
            ),
            style="CardText.TLabel",
        ).pack(anchor=W, pady=(4, 0))

        plan = ttk.Frame(body, style="Card.TFrame", padding=14)
        plan.pack(fill=X, pady=(12, 0))
        ttk.Label(plan, text="跟进计划", style="CardTitle.TLabel").grid(
            row=0, column=0, columnspan=2, sticky=W, pady=(0, 8)
        )
        fields = (
            ("应收到期日", "due_date", "date"),
            ("客户承诺付款日", "promised_date", "date"),
            ("下次跟进日", "next_followup_date", "date"),
            ("责任人", "owner_name", "text"),
            ("跟进状态", "status", "status"),
            ("本次跟进日期", "followup_date", "date"),
        )
        for index, (label, key, kind) in enumerate(fields):
            field = ttk.Frame(plan, style="Card.TFrame")
            field.grid(
                row=index // 2 + 1,
                column=index % 2,
                sticky=EW,
                padx=(0, 10) if index % 2 == 0 else (10, 0),
                pady=6,
            )
            ttk.Label(field, text=label, style="CardText.TLabel").pack(anchor=W)
            if kind == "date":
                widget = DatePicker(
                    field,
                    textvariable=variables[key],
                    allow_empty=key != "followup_date",
                    popup_title=f"选择{label}",
                )
                widget.pack(fill=X, pady=(4, 0))
            elif kind == "status":
                ttk.Combobox(
                    field,
                    textvariable=variables[key],
                    values=tuple(status_labels),
                    state="readonly",
                ).pack(fill=X, pady=(4, 0), ipady=4)
            else:
                ttk.Entry(field, textvariable=variables[key]).pack(
                    fill=X, pady=(4, 0), ipady=4
                )
        plan.columnconfigure(0, weight=1)
        plan.columnconfigure(1, weight=1)

        details = ttk.Frame(body, style="Card.TFrame", padding=14)
        details.pack(fill=BOTH, expand=True, pady=(12, 0))
        text_fields = {}
        for label, key, height, initial in (
            ("本次跟进记录（未联系可留空）", "followup_content", 3, ""),
            ("下一步动作", "next_action", 2, case["next_action"] or ""),
            ("逾期原因", "overdue_reason", 2, case["overdue_reason"] or ""),
            ("补充说明", "notes", 2, case["notes"] or ""),
        ):
            ttk.Label(details, text=label, style="CardText.TLabel").pack(
                anchor=W, pady=(8 if text_fields else 0, 4)
            )
            widget = ttk.Text(details, height=height, wrap="word")
            widget.pack(fill=X)
            widget.insert("1.0", initial)
            text_fields[key] = widget

        form_error = ttk.StringVar()
        ttk.Label(
            body,
            textvariable=form_error,
            style="FormError.TLabel",
            wraplength=700,
        ).pack(anchor=W, pady=(8, 0))

        def save():
            form_error.set("")
            try:
                collection_service.save_project_case(
                    project_id,
                    {
                        "due_date": variables["due_date"].get(),
                        "promised_date": variables["promised_date"].get(),
                        "next_followup_date": variables["next_followup_date"].get(),
                        "owner_name": variables["owner_name"].get(),
                        "status": status_labels[variables["status"].get()],
                        "followup_date": variables["followup_date"].get(),
                        **{
                            key: widget.get("1.0", END).strip()
                            for key, widget in text_fields.items()
                        },
                    },
                )
            except Exception as error:
                form_error.set(str(error))
                return
            dialog.destroy()
            self.refresh()

        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text="保存跟进",
            primary_command=save,
        )

    def open_collection_history(self):
        project_id = self._selected_collection_project_id()
        if not project_id:
            messagebox.showwarning(
                "提示", "请在项目视图中选择一个待回款项目"
            )
            return
        case = collection_service.get_project_case(project_id)
        logs = collection_service.list_followup_logs(project_id)
        dialog = ttk.Toplevel(self.parent)
        dialog.title("回款跟进历史")
        body, footer = build_form_dialog(
            dialog, self.parent, 920, 560, min_width=760, min_height=480
        )
        ttk.Label(
            body,
            text=(
                f"{case['project_name']} · {case['customer_name']} · "
                f"未回 {self.money(case['receivable_minor'])}"
            ),
            style="CardTitle.TLabel",
        ).pack(anchor=W, pady=(0, 8))
        history = self._table(
            body,
            (
                ("date", "跟进日期", 100, CENTER),
                ("content", "跟进内容", 300, W),
                ("promised", "承诺付款日", 105, CENTER),
                ("next", "下次跟进", 100, CENTER),
                ("action", "下一步动作", 240, W),
            ),
            empty_text="尚无跟进历史",
            stretch=("content", "action"),
            padding=0,
        )
        history.refresh(
            logs,
            lambda row: (str(row["id"]), (
                row["followup_date"],
                row["content"],
                row["promised_date"] or "—",
                row["next_followup_date"] or "—",
                row["next_action"] or "—",
            )),
        )
        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text="关闭",
            primary_command=dialog.destroy,
        )

    def _build_collection_panel(self, parent, queue):
        project_card = ttk.Frame(
            parent, style="Card.TFrame", padding=(10, 8)
        )
        project_card.pack(fill=BOTH, expand=True)
        project_header = ttk.Frame(project_card, style="Card.TFrame")
        project_header.pack(fill=X, pady=(0, 6))
        ttk.Label(
            project_header,
            text=("待收项目资金进度" if queue == "pending" else "已结清项目"),
            style="CardTitle.TLabel",
        ).pack(side=LEFT)
        status_var = ttk.StringVar()
        self.collection_status_vars[queue] = status_var
        ttk.Button(project_header, text='收款完毕', bootstyle='primary-outline',
                   command=lambda: self.open_cash_closure(queue)).pack(side=LEFT, padx=(12, 4))
        ttk.Button(project_header, text='结清记录 / 撤销', bootstyle='secondary-outline',
                   command=lambda: self.open_cash_closure(queue, history=True)).pack(side=LEFT, padx=4)
        ttk.Label(
            project_header,
            textvariable=status_var,
            style="CardText.TLabel",
        ).pack(side=RIGHT)
        project_tree = self._table(
            project_card,
            (
                ("project", "项目", 180, W),
                ("mode", "业务类型", 90, CENTER),
                ("settlement", "确认收入", 120, E),
                ("invoice", "开票进度", 180, E),
                ("receipt", "回款进度", 180, E),
                ("receivable", "未回款", 120, E),
                ("status", "回款状态", 90, CENTER),
            ),
            empty_text=(
                "当前没有待回款或部分回款项目"
                if queue == "pending" else "当前没有已结清项目"
            ),
            stretch=("project",),
            padding=0,
            expand=True,
        )
        project_tree.tree.bind(
            "<Double-1>",
            lambda event, source=project_tree: self._filter_selected_project(
                event, source
            ),
        )
        self.collection_trees[queue] = project_tree

    def open_cash_closure(self, queue, history=False):
        from services import cash_closure_service
        selected = self.collection_trees[queue].tree.selection()
        if not selected:
            messagebox.showinfo('提示', '请先在下方选择一个零星工程项目', parent=self.parent)
            return
        project_id = int(selected[0])
        try:
            if history:
                records = cash_closure_service.history(project_id)
                if not records:
                    messagebox.showinfo('结清记录', '该项目尚无抹零结清记录', parent=self.parent)
                    return
                active = next((r for r in records if r['status'] == 'active'), None)
                text = '\n'.join(
                    f"{r['created_at'][:10]} · {r['reason']} · 调减 {self.money(r['reduction_minor'])}"
                    f" · {'有效' if r['status'] == 'active' else '已撤销'}\n{r['notes']}"
                    for r in records)
                if not active:
                    messagebox.showinfo('结清历史', text, parent=self.parent)
                elif messagebox.askyesno('结清记录 / 撤销', text + '\n\n是否撤销当前结清调整？\n'
                                        '将恢复调整前的确认收入和应收差额，实际回款不变。', parent=self.parent):
                    cash_closure_service.revoke(active['id'])
                    self.refresh()
                return
            data = cash_closure_service.preview(project_id)
        except Exception as error:
            messagebox.showwarning('无法结清', str(error), parent=self.parent)
            return
        dialog = ttk.Toplevel(self.parent)
        dialog.title('收款完毕 · 确认最终结算')
        body, footer = build_form_dialog(dialog, self.parent, 650, 500, min_width=550, min_height=400)
        text = (f"项目：{data['project_name']}\n\n"
                f"原确认收入：{self.money(data['original_income_minor'])}\n"
                f"累计实际收到：{self.money(data['received_minor'])}\n"
                f"不再收取的差额：{self.money(data['reduction_minor'])}\n\n"
                '确认后按实收金额调减收入，应收归零；实际回款及施工状态不变。')
        ttk.Label(body, text=text, wraplength=500, justify=LEFT).pack(fill=X, pady=10)
        ttk.Label(body, text='结清原因 *').pack(anchor=W)
        reason = ttk.StringVar(value='抹零')
        ttk.Combobox(body, textvariable=reason, values=cash_closure_service.REASONS,
                     state='readonly').pack(fill=X, pady=6)
        ttk.Label(body, text='备注').pack(anchor=W)
        notes = ttk.Entry(body)
        notes.pack(fill=X, pady=6)
        confirmed = ttk.BooleanVar(value=False)
        ttk.Checkbutton(body, text='我确认剩余差额不再收取，按上述实收金额结清',
                        variable=confirmed).pack(anchor=W, pady=12)

        def save():
            if not confirmed.get():
                messagebox.showwarning('请确认', '请先核对差额并勾选确认', parent=dialog)
                return
            try:
                cash_closure_service.close_collection(project_id, reason.get(), data, notes.get())
            except Exception as error:
                messagebox.showwarning('无法结清', str(error), parent=dialog)
                return
            dialog.destroy()
            self.refresh()
            self.collection_notebook.select(1)

        add_form_actions(footer, cancel_command=dialog.destroy,
                         primary_text='确认结清', primary_command=save)

    def _current_collection_queue(self):
        index = self.collection_notebook.index(
            self.collection_notebook.select()
        )
        return ("pending", "settled")[index]
