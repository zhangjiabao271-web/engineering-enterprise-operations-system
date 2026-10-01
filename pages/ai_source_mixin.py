"""AISourceMixin: extracted UI behavior from pages/ai_page.py."""

from collections import defaultdict

import ttkbootstrap as ttk
from ttkbootstrap.constants import *
from ui.charts import HorizontalBreakdown, MonthlyBarChart
from ui.components import DataTable
from ui.theme import style_dialog


class AISourceMixin:
    @staticmethod
    def _procurement_breakdowns(details):
        by_month = defaultdict(int)
        by_material = defaultdict(lambda: {"amount_minor": 0, "count": 0})
        by_project = defaultdict(lambda: {"amount_minor": 0, "count": 0})
        for item in details:
            amount = int(item.get("amount_cents") or 0)
            month = str(item.get("purchase_date") or "")[:7]
            if month:
                by_month[month] += amount
            material = item.get("material") or "未命名材料"
            by_material[material]["amount_minor"] += amount
            by_material[material]["count"] += 1
            project = item.get("project") or "未归集项目"
            by_project[project]["amount_minor"] += amount
            by_project[project]["count"] += 1

        monthly = [
            {"month": month, "amount_minor": amount}
            for month, amount in sorted(by_month.items())
        ]
        ranking_source = by_material
        ranking_title = "材料金额排行"
        ranking_subtitle = "按含税材料金额排序，不含运费"
        if len(by_material) < 2 and len(by_project) >= 2:
            ranking_source = by_project
            ranking_title = "项目材料金额排行"
            ranking_subtitle = "按项目汇总含税材料金额，不含运费"
        ranking = [
            {
                "label": label,
                "amount_minor": values["amount_minor"],
                "detail": f"{values['count']} 条明细",
            }
            for label, values in ranking_source.items()
        ]
        return monthly, ranking, ranking_title, ranking_subtitle

    def _source_kpis(self, source):
        if source.get("kpis"):
            return tuple(tuple(item) for item in source["kpis"][:4])
        summary = source.get("summary") or {}
        if source.get("view_type") == "labor":
            return (
                ("人工成本", self._money(summary.get("total_minor"))),
                ("工天记录", f"{int(summary.get('record_count') or 0)} 条"),
                ("涉及工人", f"{int(summary.get('worker_count') or 0)} 人"),
                ("合计工天", f"{summary.get('work_days') or 0:g} 工天"),
            )
        return (
            ("采购总额", self._money(summary.get("total_minor"))),
            ("含税材料", self._money(summary.get("material_minor"))),
            ("运费", self._money(summary.get("freight_minor"))),
            (
                "明细 / 材料",
                f"{int(summary.get('record_count') or 0)} 条 · "
                f"{int(summary.get('material_count') or 0)} 种",
            ),
        )

    def _source_table_spec(self, source):
        view_type = source.get("view_type") or "procurement"
        custom_columns = source.get("columns") or []
        if custom_columns:
            columns = tuple(item["key"] for item in custom_columns)
            headings = {item["key"]: item["label"] for item in custom_columns}
            widths = {
                item["key"]: max(90, min(180, len(item["label"]) * 22))
                for item in custom_columns
            }
            numeric = {
                item["key"]
                for item in custom_columns
                if item.get("kind") in ("money", "integer", "percent")
            }

            def values(row):
                rendered = []
                for item in custom_columns:
                    value = row.get(item["key"])
                    if item.get("kind") == "money":
                        value = self._money(value)
                    elif item.get("kind") == "percent":
                        value = "--" if value is None else f"{float(value):.1f}%"
                    elif item.get("kind") == "integer":
                        value = int(value or 0)
                    rendered.append("" if value is None else value)
                return tuple(rendered)

            return columns, headings, widths, numeric, values
        if view_type == "labor":
            columns = (
                "date", "worker", "project", "site", "work_type",
                "days", "overtime", "amount",
            )
            headings = {
                "date": "日期", "worker": "工人", "project": "项目",
                "site": "施工地点", "work_type": "工作内容",
                "days": "工天", "overtime": "加班", "amount": "人工金额",
            }
            widths = {
                "date": 92, "worker": 100, "project": 120, "site": 150,
                "work_type": 180, "days": 70, "overtime": 60, "amount": 105,
            }

            def values(item):
                return (
                    item.get("work_date") or "",
                    item.get("worker_name") or "",
                    item.get("project_name") or "待归集",
                    item.get("construction_site") or "",
                    item.get("work_type") or "",
                    f"{item.get('work_days') or 0:g}",
                    "是" if item.get("is_overtime") else "否",
                    self._money(item.get("amount_minor")),
                )
            numeric = {"days", "amount"}
            return columns, headings, widths, numeric, values

        columns = (
            "date", "order", "project", "supplier", "material",
            "spec", "quantity", "amount",
        )
        headings = {
            "date": "采购日期", "order": "采购单号", "project": "项目",
            "supplier": "供应商", "material": "材料", "spec": "规格",
            "quantity": "数量", "amount": "含税材料金额",
        }
        widths = {
            "date": 90, "order": 125, "project": 125, "supplier": 170,
            "material": 135, "spec": 135, "quantity": 90, "amount": 115,
        }

        def values(item):
            quantity = f"{item.get('quantity') or ''}{item.get('unit') or ''}"
            return (
                item.get("purchase_date") or "",
                item.get("order_no") or "",
                item.get("project") or "未归集项目",
                item.get("supplier") or "",
                item.get("material") or "",
                item.get("specification") or "",
                quantity,
                self._money(item.get("amount_cents")),
            )
        return columns, headings, widths, {"quantity", "amount"}, values

    def open_source_records(self, source):
        details = list(source.get("details") or [])
        view_type = source.get("view_type") or "procurement"
        dialog = ttk.Toplevel(self.parent)
        dialog.title(source.get("label") or "经营数据透视")
        style_dialog(
            dialog,
            self.parent,
            1120,
            720,
            resizable=True,
            min_width=900,
            min_height=600,
        )
        footer = ttk.Frame(dialog, padding=(14, 8))
        footer.pack(side=BOTTOM, fill=X)
        body = ttk.Frame(dialog, padding=(18, 14))
        body.pack(fill=BOTH, expand=True)
        ttk.Label(
            body,
            text=source.get("module") or "本地经营数据",
            style="CardTitle.TLabel",
        ).pack(anchor=W)
        ttk.Label(
            body,
            text=f"{source.get('scope_label') or '当前范围'} · 数据来自已生效台账",
            style="PageSub.TLabel",
        ).pack(anchor=W, pady=(2, 10))

        kpi_strip = ttk.Frame(body)
        kpi_strip.pack(fill=X, pady=(0, 10))
        for index, (label, value) in enumerate(self._source_kpis(source)):
            card = ttk.Frame(kpi_strip, style="Card.TFrame", padding=(12, 9))
            card.grid(
                row=0,
                column=index,
                sticky=EW,
                padx=(0 if index == 0 else 5, 0 if index == 3 else 5),
            )
            ttk.Label(card, text=label, style="KpiLabel.TLabel").pack(anchor=W)
            ttk.Label(card, text=value, style="SummaryValue.TLabel").pack(
                anchor=W, pady=(3, 0)
            )
            kpi_strip.columnconfigure(index, weight=1)

        if view_type == "labor":
            monthly = list(source.get("by_month") or [])
            ranking = list(source.get("by_rank") or [])
            monthly_title = "月度人工成本"
            monthly_subtitle = "按工天日期汇总，金额从零基线比较"
            ranking_title = "人员人工成本排行"
            ranking_subtitle = "按金额排序，同时显示工天与记录数"
        elif view_type == "procurement":
            monthly, ranking, ranking_title, ranking_subtitle = (
                self._procurement_breakdowns(details)
            )
            monthly_title = "月度含税材料金额"
            monthly_subtitle = "按采购日期汇总，不含运费"

        if view_type in ("labor", "procurement"):
            charts = ttk.Frame(body)
            charts.pack(fill=X, pady=(0, 10))
            trend_card = ttk.Frame(charts, style="Card.TFrame", padding=(12, 10))
            trend_card.grid(row=0, column=0, sticky=NSEW, padx=(0, 5))
            ttk.Label(trend_card, text=monthly_title, style="CardTitle.TLabel").pack(anchor=W)
            ttk.Label(trend_card, text=monthly_subtitle, style="CardText.TLabel").pack(
                anchor=W, pady=(2, 6)
            )
            trend_chart = MonthlyBarChart(trend_card, height=220)
            trend_chart.pack(fill=BOTH, expand=True)
            trend_chart.set_data(monthly)

            rank_card = ttk.Frame(charts, style="Card.TFrame", padding=(12, 10))
            rank_card.grid(row=0, column=1, sticky=NSEW, padx=(5, 0))
            ttk.Label(rank_card, text=ranking_title, style="CardTitle.TLabel").pack(anchor=W)
            ttk.Label(rank_card, text=ranking_subtitle, style="CardText.TLabel").pack(
                anchor=W, pady=(2, 6)
            )
            rank_chart = HorizontalBreakdown(
                rank_card,
                limit=6,
                height=220,
                empty_text=(
                    "当前范围暂无人工成本"
                    if view_type == "labor"
                    else "当前范围暂无材料采购"
                ),
                other_label="其他人员" if view_type == "labor" else "其他材料",
            )
            rank_chart.pack(fill=BOTH, expand=True)
            rank_chart.set_data(ranking)
            charts.columnconfigure(0, weight=1, uniform="source_chart")
            charts.columnconfigure(1, weight=1, uniform="source_chart")

        detail_header = ttk.Frame(body)
        detail_header.pack(fill=X, pady=(0, 6))
        ttk.Label(detail_header, text="详细台账", style="CardTitle.TLabel").pack(side=LEFT)
        ttk.Label(
            detail_header,
            text=f"共 {source.get('record_count', len(details))} 条，可滚动核对",
            style="CardText.TLabel",
        ).pack(side=LEFT, padx=(10, 0))
        columns, headings, widths, numeric, row_values = self._source_table_spec(source)
        source_table = DataTable(
            body,
            tuple((column, headings[column], widths[column],
                   E if column in numeric else W) for column in columns),
            empty_text="当前范围暂无逐条明细", stretch=(), horizontal=True,
            padding=0,
        )
        source_table.refresh(details, lambda item: (None, row_values(item)))
        ttk.Button(
            footer,
            text="关闭",
            bootstyle="secondary",
            command=dialog.destroy,
        ).pack(side=RIGHT)
        if self.navigate_to and source.get("page_key"):
            ttk.Button(
                footer,
                text="打开业务页面",
                bootstyle="primary",
                command=lambda: (
                    dialog.destroy(),
                    self.open_business_page(source.get("page_key")),
                ),
            ).pack(side=RIGHT, padx=(0, 8))
