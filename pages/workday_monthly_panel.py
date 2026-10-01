"""Period-based worker/month attendance matrix and drill-down."""

from collections import Counter
from datetime import date
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, CENTER, LEFT, W, X

from services import labor_service
from ui.components import DataTable, DatePicker
from ui.error_handling import show_unexpected_error
from ui.theme import SPACING, style_dialog


def format_days(value):
    return f"{float(value or 0):,.2f}".rstrip("0").rstrip(".")


class WorkdayMonthlyPanel(ttk.Frame):
    """One row per worker; month cells reveal the underlying attendance."""

    def __init__(self, parent):
        super().__init__(parent, padding=10)
        self.pack(fill=BOTH, expand=True)
        today = date.today()
        self.start_var = ttk.StringVar(value=f"{today.year}-01-01")
        self.end_var = ttk.StringVar(value=today.isoformat())
        self.summary_var = ttk.StringVar(value="选择日期后查看工天")
        self.summary = None
        self.table = None
        self._months = ()
        self._name_counts = Counter()

        filters = ttk.Frame(self)
        filters.pack(fill=X, pady=(0, SPACING["md"]))
        ttk.Label(filters, text="开始日期").pack(side=LEFT, padx=(0, 8))
        start_picker = DatePicker(
            filters, textvariable=self.start_var, popup_title="选择汇总开始日期"
        )
        start_picker.pack(side=LEFT, padx=(0, 16))
        ttk.Label(filters, text="结束日期").pack(side=LEFT, padx=(0, 8))
        end_picker = DatePicker(
            filters, textvariable=self.end_var, popup_title="选择汇总结束日期"
        )
        end_picker.pack(side=LEFT, padx=(0, 16))
        ttk.Button(
            filters, text="查询工天", bootstyle="info", command=self.refresh
        ).pack(side=LEFT)
        for picker in (start_picker, end_picker):
            picker.bind("<<DateSelected>>", self._mark_dates_changed)

        ttk.Label(
            self,
            text="本页日期独立于右上角月份。加班已计入总工天；点击月份数字查看逐日出勤。",
            style="PageSub.TLabel",
        ).pack(anchor=W, pady=(0, 6))
        ttk.Label(self, textvariable=self.summary_var, style="PageSub.TLabel").pack(
            anchor=W, pady=(0, SPACING["sm"])
        )
        self.table_host = ttk.Frame(self)
        self.table_host.pack(fill=BOTH, expand=True)

    def _mark_dates_changed(self, _event):
        self.summary = None
        self.summary_var.set("日期已更改，点击“查询工天”更新")
        if self.table is not None:
            self.table.empty_label.configure(text="点击“查询工天”显示所选时间段")
            self.table.clear()

    def refresh(self):
        try:
            summary = labor_service.get_worker_monthly_workdays(
                self.start_var.get(), self.end_var.get()
            )
        except ValueError as error:
            messagebox.showwarning("日期有误", str(error), parent=self)
            return
        except Exception:
            show_unexpected_error("月度工天加载失败", parent=self)
            return

        months = tuple(summary["months"])
        if self.table is None or months != self._months:
            if self.table is not None:
                self.table.destroy()
            self._months = months
            specs = (
                ("worker", "工人", 170, W),
                ("status", "状态", 70, CENTER),
                ("total", "合计工天", 100, CENTER),
                *((month, month, 100, CENTER) for month in months),
            )
            self.table = DataTable(
                self.table_host,
                specs=specs,
                empty_text="该时间段暂无有效工天记录",
                stretch=(),
                horizontal=True,
            )
            self.table.tree.bind("<ButtonRelease-1>", self._open_cell)

        self.summary = summary
        self._name_counts = Counter(
            worker["worker_name"] for worker in summary["workers"]
        )
        self.table.empty_label.configure(text="该时间段暂无有效工天记录")
        self.table.refresh(
            summary["workers"],
            lambda worker: (
                str(worker["worker_id"]),
                (
                    self._worker_label(worker),
                    worker["worker_status"],
                    format_days(worker["work_days"]),
                    *(format_days(worker["months"][month]) if month in worker["months"] else ""
                      for month in months),
                ),
            ),
        )
        self.summary_var.set(
            f"{summary['start_date']} 至 {summary['end_date']}  ·  "
            f"{summary['worker_count']} 名工人  ·  "
            f"{format_days(summary['work_days'])} 工天"
            f"（其中加班 {format_days(summary['overtime_days'])} 工天）"
        )

    def _worker_label(self, worker):
        name = worker["worker_name"]
        if self._name_counts[name] > 1:
            return f"{name}（档案{worker['worker_id']}）"
        return name

    def _open_cell(self, event):
        if self.summary is None:
            return
        tree = self.table.tree
        if tree.identify_region(event.x, event.y) != "cell":
            return
        row_id = tree.identify_row(event.y)
        column = tree.identify_column(event.x)
        if not row_id or not column:
            return
        month_index = int(column[1:]) - 4  # First three columns are identity and total.
        if not 0 <= month_index < len(self._months):
            return
        month = self._months[month_index]
        worker = next(
            (item for item in self.summary["workers"] if str(item["worker_id"]) == row_id),
            None,
        )
        if worker is None or month not in worker["months"]:
            return
        self.open_month_details(worker, month)

    def open_month_details(self, worker, month):
        period = self.summary
        try:
            result = labor_service.get_worker_month_work_logs(
                worker["worker_id"], month, period["start_date"], period["end_date"]
            )
        except ValueError as error:
            messagebox.showwarning("无法查看明细", str(error), parent=self)
            return
        except Exception:
            show_unexpected_error("工天明细加载失败", parent=self)
            return

        dialog = ttk.Toplevel(self)
        worker_name = self._worker_label(worker)
        dialog.title(f"{worker_name} · {month} 工天明细")
        style_dialog(dialog, self, 960, 620, min_width=650, min_height=420)
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill=BOTH, expand=True)
        ttk.Label(
            body, text=f"{worker_name}  ·  {month}", style="CardTitle.TLabel"
        ).pack(anchor=W, pady=(0, SPACING["sm"]))
        ttk.Label(
            body,
            text=(
                f"本时间段内 {result['record_count']} 条出勤 · "
                f"{format_days(result['work_days'])} 工天"
                f"（其中加班 {format_days(result['overtime_days'])} 工天）"
            ),
            style="PageSub.TLabel",
        ).pack(anchor=W, pady=(0, SPACING["md"]))
        table = DataTable(
            body,
            specs=(
                ("date", "日期", 120, CENTER),
                ("project", "所属项目", 180, W),
                ("site", "施工工地", 180, W),
                ("work_type", "工作内容", 170, W),
                ("attendance", "出勤类型", 90, CENTER),
                ("days", "工天", 75, CENTER),
            ),
            empty_text="当前月份没有有效工天记录",
            stretch=("project", "site", "work_type"),
            horizontal=True,
        )
        table.refresh(
            result["details"],
            lambda row: (
                str(row["id"]),
                (
                    row["work_date"], row["project_name"],
                    row["construction_site"], row["work_type"],
                    "加班" if row["is_overtime"] else "正常",
                    format_days(row["work_days"]),
                ),
            ),
        )
        footer = ttk.Frame(dialog, padding=(16, 0, 16, 16))
        footer.pack(fill=X)
        ttk.Button(
            footer, text="关闭", bootstyle="secondary-outline", command=dialog.destroy
        ).pack(side="right")
