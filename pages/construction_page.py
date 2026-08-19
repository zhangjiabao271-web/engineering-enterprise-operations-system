import os
import shutil
import uuid
from datetime import datetime
from tkinter import messagebox, filedialog, Listbox, END as TK_END
from tkinter.scrolledtext import ScrolledText

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import construction_service as db
from ui.components import DatePicker
from ui.dialogs import add_form_actions, build_form_dialog, safe_init_loaders
from ui.theme import style_dialog


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTACHMENTS_DIR = os.path.join(BASE_DIR, "attachments", "construction")
IMAGE_TYPES = [("图片文件", "*.jpg *.jpeg *.png *.bmp *.webp"), ("所有文件", "*.*")]


class ConstructionRecordPage:
    """Project-linked work quantities, amounts, photos and acceptance."""

    def __init__(self, parent):
        self.parent = parent
        self.month_var = ttk.StringVar(value=datetime.now().strftime("%Y-%m"))
        self.project_var = ttk.StringVar(value="全部项目")
        self.status_var = ttk.StringVar(value="全部状态")
        self.search_var = ttk.StringVar()
        self.project_map = {"全部项目": None}
        self.kpi_vars = {
            "records": ttk.StringVar(value="0"),
            "total_amount": ttk.StringVar(value="¥0"),
            "accepted_amount": ttk.StringVar(value="¥0"),
            "pending": ttk.StringVar(value="0"),
            "rectification": ttk.StringVar(value="0"),
            "photos": ttk.StringVar(value="0"),
        }
        os.makedirs(ATTACHMENTS_DIR, exist_ok=True)
        self.build_ui()
        safe_init_loaders("施工与验收", [self.refresh_filters, self.refresh_all])

    def build_ui(self):
        header = ttk.Frame(self.parent)
        header.pack(fill=X, pady=(0, 16))
        title_box = ttk.Frame(header)
        title_box.pack(side=LEFT)
        ttk.Label(title_box, text="施工记录与验收", style="PageTitle.TLabel").pack(anchor=W)
        ttk.Label(
            title_box,
            text="按项目记录每个具体作业位置的施工周期、安装明细、金额和验收结果",
            style="PageSub.TLabel",
        ).pack(anchor=W, pady=(4, 0))
        actions = ttk.Frame(header)
        actions.pack(side=RIGHT)
        ttk.Button(actions, text="新增施工记录", bootstyle=PRIMARY,
                   command=self.open_record_dialog).pack(side=LEFT, padx=4)
        ttk.Button(actions, text="验收选中", bootstyle="secondary-outline",
                   command=self.inspect_selected).pack(side=LEFT, padx=4)
        ttk.Button(actions, text="查看照片", bootstyle="secondary-outline",
                   command=self.photos_selected).pack(side=LEFT, padx=4)

        filters = ttk.Frame(self.parent, style="Card.TFrame", padding=(14, 10))
        filters.pack(fill=X, pady=(0, 12))
        ttk.Label(filters, text="月份", style="CardText.TLabel").pack(side=LEFT)
        self.month_combo = ttk.Combobox(filters, textvariable=self.month_var, width=10, state="readonly")
        self.month_combo.pack(side=LEFT, padx=(7, 16), ipady=3)
        self.month_combo.bind("<<ComboboxSelected>>", lambda event: self.refresh_all())
        ttk.Label(filters, text="项目", style="CardText.TLabel").pack(side=LEFT)
        self.project_combo = ttk.Combobox(
            filters, textvariable=self.project_var, width=18, state="readonly"
        )
        self.project_combo.pack(side=LEFT, padx=(7, 16), ipady=3)
        self.project_combo.bind("<<ComboboxSelected>>", lambda event: self.refresh_all())
        ttk.Label(filters, text="验收状态", style="CardText.TLabel").pack(side=LEFT)
        self.status_combo = ttk.Combobox(
            filters, textvariable=self.status_var,
            values=["全部状态", "待验收", "已验收", "需整改"], width=11, state="readonly"
        )
        self.status_combo.pack(side=LEFT, padx=(7, 16), ipady=3)
        self.status_combo.bind("<<ComboboxSelected>>", lambda event: self.refresh_records())
        ttk.Label(filters, text="搜索", style="CardText.TLabel").pack(side=LEFT)
        ttk.Entry(filters, textvariable=self.search_var, width=20).pack(side=LEFT, padx=7, ipady=3)
        ttk.Button(filters, text="查询", bootstyle=INFO, command=self.refresh_records).pack(side=LEFT)
        ttk.Button(filters, text="清空", bootstyle=SECONDARY, command=self.clear_filters).pack(side=LEFT, padx=5)

        kpis = ttk.Frame(self.parent)
        kpis.pack(fill=X, pady=(0, 12))
        cards = [
            ("本月记录", "records", "条"), ("记录金额", "total_amount", ""),
            ("已验收金额", "accepted_amount", ""), ("待验收", "pending", "条"),
            ("需整改", "rectification", "条"), ("现场照片", "photos", "张"),
        ]
        for index, (label, key, suffix) in enumerate(cards):
            card = ttk.Frame(kpis, style="Card.TFrame", padding=(14, 10))
            card.grid(row=0, column=index, sticky=EW, padx=4)
            ttk.Label(card, text=label, style="KpiLabel.TLabel").pack(anchor=W)
            value_row = ttk.Frame(card, style="Card.TFrame")
            value_row.pack(anchor=W, pady=(4, 0))
            ttk.Label(value_row, textvariable=self.kpi_vars[key], style="KpiValue.TLabel").pack(side=LEFT)
            ttk.Label(value_row, text=suffix, style="CardText.TLabel").pack(side=LEFT, padx=(4, 0), pady=(10, 0))
            kpis.columnconfigure(index, weight=1)

        overview = ttk.Panedwindow(self.parent, orient=HORIZONTAL)
        overview.pack(fill=X, pady=(0, 12))
        site_card = ttk.Frame(overview, style="Card.TFrame", padding=12)
        item_card = ttk.Frame(overview, style="Card.TFrame", padding=12)
        overview.add(site_card, weight=1)
        overview.add(item_card, weight=1)
        ttk.Label(site_card, text="项目施工概览", style="CardTitle.TLabel").pack(anchor=W, pady=(0, 6))
        self.site_rank = ttk.Treeview(
            site_card, columns=("site", "records", "pending", "amount"),
            show="headings", height=3,
        )
        ttk.Label(item_card, text="作业位置金额汇总", style="CardTitle.TLabel").pack(anchor=W, pady=(0, 6))
        self.area_rank = ttk.Treeview(
            item_card, columns=("area", "records", "amount"),
            show="headings", height=3,
        )
        for tree, definitions in [
            (self.site_rank, [("site", "项目", 120), ("records", "记录", 60), ("pending", "待验收", 65), ("amount", "记录金额", 90)]),
            (self.area_rank, [("area", "具体作业位置", 180), ("records", "记录", 70), ("amount", "记录金额", 100)]),
        ]:
            for col, text, width in definitions:
                tree.heading(col, text=text)
                tree.column(col, width=width, anchor=CENTER)
            tree.pack(fill=X)

        table_tools = ttk.Frame(self.parent)
        table_tools.pack(fill=X, pady=(0, 7))
        ttk.Label(table_tools, text="施工位置明细").pack(side=LEFT)
        ttk.Button(table_tools, text="修改", bootstyle="secondary-outline", command=self.edit_selected).pack(side=RIGHT, padx=4)
        ttk.Button(table_tools, text="作废", bootstyle="danger-outline", command=self.void_selected).pack(side=RIGHT, padx=4)
        ttk.Button(table_tools, text="照片", bootstyle="secondary-outline", command=self.photos_selected).pack(side=RIGHT, padx=4)
        ttk.Button(table_tools, text="验收", bootstyle="secondary-outline", command=self.inspect_selected).pack(side=RIGHT, padx=4)

        table_frame = ttk.Frame(self.parent)
        table_frame.pack(fill=BOTH, expand=True)
        cols = ("id", "project", "period", "area", "details", "amount", "status", "photos")
        self.tree = ttk.Treeview(
            table_frame, columns=cols, show="headings", selectmode="extended"
        )
        for col, text, width in [
            ("id", "ID", 45), ("project", "所属项目", 100),
            ("period", "施工周期", 145), ("area", "具体作业位置", 120),
            ("details", "安装明细", 260), ("amount", "工程金额", 90),
            ("status", "验收状态", 75),
            ("photos", "照片", 50),
        ]:
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=CENTER)
        self.tree.pack(side=LEFT, fill=BOTH, expand=True)
        scroll = ttk.Scrollbar(table_frame, orient=VERTICAL, command=self.tree.yview)
        scroll.pack(side=RIGHT, fill=Y)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", lambda event: self.photos_selected())

    def refresh_filters(self):
        today = datetime.now()
        months = []
        for offset in range(-2, 37):
            total = today.year * 12 + today.month - 1 - offset
            year, month_index = divmod(total, 12)
            months.append(f"{year:04d}-{month_index + 1:02d}")
        self.month_combo["values"] = sorted(set(months), reverse=True)
        projects = db.get_projects(active_only=True)
        name_counts = {}
        for project in projects:
            name_counts[project["name"]] = name_counts.get(project["name"], 0) + 1
        self.project_map = {"全部项目": None}
        for project in projects:
            label = project["name"]
            if name_counts[label] > 1:
                label = f"{label} · {project['project_code']}"
            self.project_map[label] = project["id"]
        self.project_combo["values"] = list(self.project_map)
        if self.project_var.get() not in self.project_map:
            self.project_var.set("全部项目")

    def selected_project_id(self):
        return self.project_map.get(self.project_var.get())

    def selected_record_id(self):
        selected = self.tree.selection()
        return int(self.tree.item(selected[0], "values")[0]) if selected else None

    def refresh_all(self):
        data = db.get_construction_dashboard(
            self.month_var.get(), self.selected_project_id()
        )
        summary = data["summary"]
        self.kpi_vars["records"].set(str(summary.get("record_count", 0) or 0))
        self.kpi_vars["total_amount"].set(
            self.money(summary.get("total_amount_cents", 0))
        )
        self.kpi_vars["accepted_amount"].set(
            self.money(summary.get("accepted_amount_cents", 0))
        )
        self.kpi_vars["pending"].set(str(summary.get("pending_count", 0) or 0))
        self.kpi_vars["rectification"].set(
            str(summary.get("rectification_count", 0) or 0)
        )
        self.kpi_vars["photos"].set(str(summary.get("photo_count", 0) or 0))
        self.site_rank.delete(*self.site_rank.get_children())
        for row in data["by_site"]:
            self.site_rank.insert(
                "", END,
                values=(
                    row["label"], row["record_count"], row["pending_count"],
                    self.money(row["amount_cents"]),
                ),
            )
        self.area_rank.delete(*self.area_rank.get_children())
        for row in data["by_area"]:
            area_label = row["label"]
            if not self.selected_project_id():
                area_label = f"{row['project_name']} · {area_label}"
            self.area_rank.insert(
                "", END,
                values=(
                    area_label, row["record_count"],
                    self.money(row["amount_cents"]),
                ),
            )
        self.refresh_records()

    def refresh_records(self):
        status = "" if self.status_var.get() == "全部状态" else self.status_var.get()
        rows = db.get_construction_records(
            self.month_var.get(), self.selected_project_id(), status,
            self.search_var.get().strip()
        )
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            self.tree.insert("", END, values=(
                row["id"],
                row["project_name"],
                self.period_text(
                    row.get("start_date") or row["record_date"],
                    row.get("end_date") or row["record_date"],
                ),
                row["work_area"],
                self.details_preview(row.get("work_details") or row["work_item"]),
                self.money(row.get("work_amount_cents", 0)),
                row["inspection_status"],
                row["photo_count"],
            ))

    @staticmethod
    def number(value):
        value = float(value or 0)
        return f"{value:.0f}" if value.is_integer() else f"{value:.3f}".rstrip("0").rstrip(".")

    @staticmethod
    def money(cents):
        return f"¥{int(cents or 0) / 100:,.2f}"

    @staticmethod
    def details_preview(value, limit=80):
        text = "；".join(
            line.strip() for line in (value or "").splitlines() if line.strip()
        )
        return text if len(text) <= limit else f"{text[:limit]}…"

    @staticmethod
    def period_text(start_date, end_date):
        return start_date if start_date == end_date else f"{start_date} 至 {end_date}"

    def clear_filters(self):
        self.project_var.set("全部项目")
        self.status_var.set("全部状态")
        self.search_var.set("")
        self.refresh_all()

    def open_record_dialog(self, record_id=None):
        data = db.get_construction_record(record_id) if record_id else {}
        projects = db.get_projects(active_only=not bool(record_id))
        if not projects:
            messagebox.showwarning("提示", "请先在项目管理中建立澄湖等项目。")
            return
        project_map = {
            f"{project['name']} · {project['project_code']}": project["id"]
            for project in projects
        }
        dialog = ttk.Toplevel(self.parent)
        dialog.title("修改施工记录" if record_id else "新增施工记录")
        body, footer = build_form_dialog(
            dialog, self.parent, 680, 720,
            min_width=580, min_height=480,
        )

        preferred_project_id = (
            data.get("project_id") or self.selected_project_id()
        )
        project_label = next(
            (
                label for label, project_id in project_map.items()
                if project_id == preferred_project_id
            ),
            next(iter(project_map)),
        )
        today = datetime.now().strftime("%Y-%m-%d")
        variables = {
            "project": ttk.StringVar(value=project_label),
            "start_date": ttk.StringVar(
                value=data.get("start_date") or data.get("record_date") or today
            ),
            "end_date": ttk.StringVar(
                value=data.get("end_date") or data.get("record_date") or today
            ),
            "area": ttk.StringVar(value=data.get("work_area", "")),
            "amount": ttk.StringVar(
                value=(
                    f"{int(data.get('work_amount_cents') or 0) / 100:.2f}"
                    if record_id else ""
                )
            ),
            "team": ttk.StringVar(value=data.get("team_name", "")),
        }
        pending_files = []
        fields = [
            ("所属项目 / 大地点 *", "project"),
            ("开始日期 *", "start_date"),
            ("结束日期 *", "end_date"),
            ("具体作业位置 *", "area"),
            ("班组 / 人员", "team"),
        ]
        widgets = {}
        for row, (label, key) in enumerate(fields):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky=E, padx=(0, 12), pady=7)
            if key == "project":
                widget = ttk.Combobox(
                    body, textvariable=variables[key],
                    values=list(project_map), state="readonly",
                )
            elif key == "area":
                widget = ttk.Combobox(
                    body,
                    textvariable=variables[key],
                    values=db.get_construction_work_areas(
                        project_map[variables["project"].get()]
                    ),
                    state="normal",
                )
            elif key in ("start_date", "end_date"):
                widget = DatePicker(
                    body,
                    textvariable=variables[key],
                    popup_title=f"选择{label.rstrip(' *')}",
                )
            else:
                widget = ttk.Entry(body, textvariable=variables[key])
            grid_options = {"row": row, "column": 1, "sticky": EW, "pady": 7}
            if not isinstance(widget, DatePicker):
                grid_options["ipady"] = 5
            widget.grid(**grid_options)
            widgets[key] = widget

        def refresh_area_suggestions(_event=None):
            project_id = project_map.get(variables["project"].get())
            widgets["area"]["values"] = db.get_construction_work_areas(project_id)

        widgets["project"].bind("<<ComboboxSelected>>", refresh_area_suggestions)

        details_row = len(fields)
        ttk.Label(body, text="安装明细 *").grid(
            row=details_row, column=0, sticky=NE, padx=(0, 12), pady=7
        )
        details_box = ttk.Frame(body)
        details_box.grid(row=details_row, column=1, sticky=NSEW, pady=7)
        ttk.Label(
            details_box,
            text="材料、规格、长度、数量等都可自由填写；建议每种材料单独一行。",
            bootstyle=SECONDARY,
        ).pack(anchor=W, pady=(0, 6))
        details_text = ScrolledText(
            details_box,
            height=10,
            wrap="word",
            font=("Microsoft YaHei UI", 10),
            relief="solid",
            borderwidth=1,
            undo=True,
        )
        details_text.pack(fill=BOTH, expand=True)
        existing_details = data.get("work_details") or ""
        if existing_details:
            details_text.insert("1.0", existing_details)

        amount_row = details_row + 1
        ttk.Label(body, text="工程金额（元）").grid(
            row=amount_row, column=0, sticky=E, padx=(0, 12), pady=7
        )
        amount_entry = ttk.Entry(body, textvariable=variables["amount"])
        amount_entry.grid(row=amount_row, column=1, sticky=EW, pady=7, ipady=5)

        photo_row_index = amount_row + 1
        ttk.Label(body, text="现场照片").grid(
            row=photo_row_index, column=0, sticky=NE, padx=(0, 12), pady=7
        )
        photo_box = ttk.Frame(body)
        photo_box.grid(row=photo_row_index, column=1, sticky=NSEW, pady=7)
        file_list = Listbox(photo_box, height=4, font=("Microsoft YaHei UI", 9))
        file_list.pack(fill=X)
        photo_buttons = ttk.Frame(photo_box)
        photo_buttons.pack(fill=X, pady=(6, 0))

        def choose_files():
            paths = filedialog.askopenfilenames(title="选择现场照片", filetypes=IMAGE_TYPES, parent=dialog)
            for path in paths:
                if path not in pending_files:
                    pending_files.append(path)
                    file_list.insert(TK_END, os.path.basename(path))

        def remove_pending():
            selected = list(file_list.curselection())
            for index in reversed(selected):
                file_list.delete(index)
                pending_files.pop(index)

        ttk.Button(photo_buttons, text="选择照片", bootstyle=INFO, command=choose_files).pack(side=LEFT)
        ttk.Button(photo_buttons, text="移除", bootstyle=SECONDARY, command=remove_pending).pack(side=LEFT, padx=6)
        if record_id:
            ttk.Label(photo_buttons, text=f"已有 {data.get('photo_count', 0)} 张，保存后追加", bootstyle=SECONDARY).pack(side=RIGHT)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(photo_row_index, weight=1)

        def save():
            try:
                start_date = datetime.strptime(
                    variables["start_date"].get().strip(), "%Y-%m-%d"
                )
                end_date = datetime.strptime(
                    variables["end_date"].get().strip(), "%Y-%m-%d"
                )
                amount = float(variables["amount"].get().strip() or 0)
                if end_date < start_date or amount < 0:
                    raise ValueError
            except ValueError:
                messagebox.showwarning(
                    "提示",
                    "日期请使用 YYYY-MM-DD，结束日期不能早于开始日期；"
                    "工程金额必须是非负数字。",
                    parent=dialog,
                )
                return
            work_details = details_text.get("1.0", TK_END).strip()
            if not variables["area"].get().strip() or not work_details:
                messagebox.showwarning(
                    "提示", "请填写具体作业位置和安装明细。",
                    parent=dialog,
                )
                return
            payload = {
                "project_id": project_map[variables["project"].get()],
                "start_date": variables["start_date"].get().strip(),
                "end_date": variables["end_date"].get().strip(),
                "work_area": variables["area"].get().strip(),
                "work_details": work_details,
                "work_item": self.details_preview(work_details, 120),
                "quantity": 0,
                "unit": "批",
                "work_amount_cents": round(amount * 100),
                "team_name": variables["team"].get().strip(),
                "description": "",
            }
            if record_id:
                db.update_construction_record(record_id, payload)
                saved_id = record_id
            else:
                saved_id = db.add_construction_record(payload)
            self.store_photos(saved_id, pending_files, "施工现场")
            self.month_var.set(payload["end_date"][:7])
            dialog.destroy()
            self.refresh_all()

        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text="保存施工记录",
            primary_command=save,
        )

    def store_photos(self, record_id, paths, photo_type):
        if not paths:
            return
        record = db.get_construction_record(record_id)
        month_dir = os.path.join(ATTACHMENTS_DIR, record["record_date"][:7])
        os.makedirs(month_dir, exist_ok=True)
        for source in paths:
            extension = os.path.splitext(source)[1].lower() or ".jpg"
            filename = f"record_{record_id}_{uuid.uuid4().hex}{extension}"
            destination = os.path.join(month_dir, filename)
            shutil.copy2(source, destination)
            relative_path = os.path.relpath(destination, BASE_DIR)
            db.add_construction_photo(record_id, relative_path, os.path.basename(source), photo_type)

    def edit_selected(self):
        record_id = self.selected_record_id()
        if not record_id:
            messagebox.showwarning("提示", "请先选择一条施工记录")
            return
        self.open_record_dialog(record_id)

    def void_selected(self):
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("提示", "请先选择要作废的施工记录")
            return
        ids = [int(self.tree.item(item, "values")[0]) for item in selected]
        if messagebox.askyesno("确认作废", f"确定作废选中的 {len(ids)} 条施工记录？照片和验收痕迹会保留。"):
            db.void_construction_records(ids)
            self.refresh_all()

    def inspect_selected(self):
        record_id = self.selected_record_id()
        if not record_id:
            messagebox.showwarning("提示", "请先选择一条施工记录")
            return
        data = db.get_construction_record(record_id)
        dialog = ttk.Toplevel(self.parent)
        dialog.title("工程量验收")
        body, footer = build_form_dialog(
            dialog, self.parent, 650, 650,
            min_width=560, min_height=500,
        )
        ttk.Label(
            body,
            text=f"{data['project_name']} · {data['work_area']}",
            font=("Microsoft YaHei UI", 14, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky=W)
        ttk.Label(
            body,
            text=(
                f"施工周期：{self.period_text(data['start_date'], data['end_date'])}　"
                f"工程金额：{self.money(data['work_amount_cents'])}"
            ),
            wraplength=560,
            justify=LEFT,
        ).grid(row=1, column=0, columnspan=2, sticky=W, pady=(5, 16))
        ttk.Label(body, text="安装明细").grid(
            row=2, column=0, sticky=NE, padx=(0, 12), pady=8
        )
        details_view = ScrolledText(
            body,
            height=8,
            wrap="word",
            font=("Microsoft YaHei UI", 9),
            relief="solid",
            borderwidth=1,
        )
        details_view.grid(row=2, column=1, sticky=NSEW, pady=8)
        details_view.insert(
            "1.0", data.get("work_details") or data.get("work_item", "")
        )
        details_view.configure(state="disabled")

        status_var = ttk.StringVar(value=data.get("inspection_status", "待验收"))
        inspector_var = ttk.StringVar(value=data.get("inspector", ""))
        date_var = ttk.StringVar(value=data.get("inspection_date") or datetime.now().strftime("%Y-%m-%d"))
        notes_var = ttk.StringVar(value=data.get("inspection_notes", ""))
        for row, (label, variable, values) in enumerate([
            ("验收结论 *", status_var, ["待验收", "已验收", "需整改"]),
            ("验收人", inspector_var, None), ("验收日期", date_var, None), ("验收意见", notes_var, None),
        ], 3):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky=E, padx=(0, 12), pady=8)
            if values:
                widget = ttk.Combobox(
                    body, textvariable=variable, values=values, state="readonly"
                )
            elif label == "验收日期":
                widget = DatePicker(
                    body,
                    textvariable=variable,
                    allow_empty=True,
                    popup_title="选择验收日期",
                )
            else:
                widget = ttk.Entry(body, textvariable=variable)
            grid_options = {"row": row, "column": 1, "sticky": EW, "pady": 8}
            if not isinstance(widget, DatePicker):
                grid_options["ipady"] = 5
            widget.grid(**grid_options)
        acceptance_files = []
        ttk.Label(body, text="验收照片").grid(row=7, column=0, sticky=NE, padx=(0, 12), pady=8)
        photo_row = ttk.Frame(body)
        photo_row.grid(row=7, column=1, sticky=EW, pady=8)
        photo_label = ttk.Label(photo_row, text="未选择照片")
        photo_label.pack(side=LEFT)

        def choose():
            acceptance_files[:] = filedialog.askopenfilenames(title="选择验收照片", filetypes=IMAGE_TYPES, parent=dialog)
            photo_label.config(text=f"已选择 {len(acceptance_files)} 张")
        ttk.Button(photo_row, text="选择照片", bootstyle=INFO, command=choose).pack(side=RIGHT)
        body.columnconfigure(1, weight=1)

        def save():
            if date_var.get().strip():
                try:
                    datetime.strptime(date_var.get().strip(), "%Y-%m-%d")
                except ValueError:
                    messagebox.showwarning("提示", "验收日期格式必须为 YYYY-MM-DD", parent=dialog)
                    return
            db.update_construction_inspection(record_id, {
                "inspection_status": status_var.get(), "inspector": inspector_var.get().strip(),
                "inspection_date": date_var.get().strip(), "inspection_notes": notes_var.get().strip(),
            })
            self.store_photos(record_id, acceptance_files, "验收照片")
            dialog.destroy()
            self.refresh_all()

        add_form_actions(
            footer,
            cancel_command=dialog.destroy,
            primary_text="保存验收结果",
            primary_command=save,
        )

    def photos_selected(self):
        record_id = self.selected_record_id()
        if not record_id:
            messagebox.showwarning("提示", "请先选择一条施工记录")
            return
        self.open_photo_manager(record_id)

    def open_photo_manager(self, record_id):
        record = db.get_construction_record(record_id)
        dialog = ttk.Toplevel(self.parent)
        dialog.title(
            f"现场照片 · {record['project_name']} · "
            f"{record['work_area']}"
        )
        style_dialog(
            dialog, self.parent, 720, 480,
            min_width=600, min_height=380,
        )
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill=BOTH, expand=True)
        ttk.Label(body, text="双击照片可用系统图片查看器打开", bootstyle=SECONDARY).pack(anchor=W, pady=(0, 8))
        tree = ttk.Treeview(body, columns=("id", "type", "name", "path"), show="headings", bootstyle=PRIMARY)
        for col, text, width in [("id", "ID", 45), ("type", "类型", 90), ("name", "原文件名", 220), ("path", "保存位置", 300)]:
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor=CENTER)
        tree.pack(fill=BOTH, expand=True)

        def refresh():
            tree.delete(*tree.get_children())
            for photo in db.get_construction_photos(record_id):
                tree.insert("", END, values=(photo["id"], photo["photo_type"], photo["original_name"], photo["file_path"]))

        def open_selected(event=None):
            selected = tree.selection()
            if not selected:
                return
            path = os.path.abspath(os.path.join(BASE_DIR, tree.item(selected[0], "values")[3]))
            if os.path.exists(path):
                os.startfile(path)
            else:
                messagebox.showwarning("照片缺失", f"找不到文件：\n{path}", parent=dialog)

        def add_more():
            paths = filedialog.askopenfilenames(title="追加现场照片", filetypes=IMAGE_TYPES, parent=dialog)
            self.store_photos(record_id, paths, "施工现场")
            refresh()
            self.refresh_all()

        def delete_selected():
            selected = tree.selection()
            if not selected:
                return
            if not messagebox.askyesno("确认删除", "确定删除选中的照片文件？此操作不可恢复。", parent=dialog):
                return
            for item in selected:
                photo = db.delete_construction_photo(int(tree.item(item, "values")[0]))
                if photo:
                    path = os.path.abspath(os.path.join(BASE_DIR, photo["file_path"]))
                    if os.path.commonpath([path, os.path.abspath(ATTACHMENTS_DIR)]) == os.path.abspath(ATTACHMENTS_DIR) and os.path.exists(path):
                        os.remove(path)
            refresh()
            self.refresh_all()

        tree.bind("<Double-1>", open_selected)
        buttons = ttk.Frame(body)
        buttons.pack(fill=X, pady=(10, 0))
        ttk.Button(buttons, text="打开照片", bootstyle=PRIMARY, command=open_selected).pack(side=LEFT)
        ttk.Button(buttons, text="追加照片", bootstyle=SUCCESS, command=add_more).pack(side=LEFT, padx=6)
        ttk.Button(buttons, text="删除照片", bootstyle=DANGER, command=delete_selected).pack(side=RIGHT)
        refresh()
