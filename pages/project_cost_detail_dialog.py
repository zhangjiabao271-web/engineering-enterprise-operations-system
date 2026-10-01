"""Read-only project cost drill-down dialog."""

import ttkbootstrap as ttk
from ttkbootstrap.constants import BOTH, CENTER, E, LEFT, W, X

from services import project_cost_detail_service
from ui.components import DataTable
from ui.theme import SPACING, style_dialog


def _money(minor):
    value = int(minor or 0) / 100
    sign = "-" if value < 0 else ""
    return f"{sign}¥{abs(value):,.2f}"


def _allocation_label(method):
    return {"equal": "多项目均摊", "manual": "手工分摊"}.get(method, "直接归集")


def show_project_cost_details(parent, project_id, *, section="purchase"):
    details = project_cost_detail_service.get_project_cost_details(project_id)
    dialog = ttk.Toplevel(parent)
    dialog.title(f"项目成本明细 · {details['project']['name']}")

    body = ttk.Frame(dialog, padding=SPACING["lg"])
    body.pack(fill=BOTH, expand=True)
    ttk.Label(
        body,
        text=f"{details['project']['name']} · 全周期成本明细",
        style="CardTitle.TLabel",
    ).pack(anchor=W)
    ttk.Label(
        body,
        text=(
            f"项目总成本 {_money(details['total_cost_minor'])}  ·  "
            "只读归集口径，不代表实际付款；未归集到本项目的成本不在此列。"
        ),
        style="CardText.TLabel",
    ).pack(anchor=W, pady=(4, SPACING["md"]))

    tabs = ttk.Notebook(body)
    tabs.pack(fill=BOTH, expand=True)
    tab_specs = (
        ("purchase", "采购", details["purchase"], (
            ("date", "日期", 100, CENTER),
            ("source", "采购单号", 150, W),
            ("party", "供应商 / 门店", 170, W),
            ("category", "分类", 95, W),
            ("material", "材料 / 费用", 155, W),
            ("spec", "规格", 125, W),
            ("quantity", "数量", 80, E),
            ("purpose", "用途", 145, W),
            ("net", "未税材料", 105, E),
            ("tax", "税额", 95, E),
            ("freight", "运费", 95, E),
            ("amount", "本项目金额", 115, E),
            ("paid", "付款标记", 90, CENTER),
            ("allocation", "归集方式", 100, CENTER),
        )),
        ("labor", "人工", details["labor"], (
            ("date", "日期", 100, CENTER),
            ("source", "工天编号", 120, W),
            ("worker", "工人", 105, W),
            ("days", "工天", 70, E),
            ("overtime", "加班", 75, CENTER),
            ("type", "工种", 110, W),
            ("site", "施工地点", 180, W),
            ("notes", "备注", 180, W),
            ("amount", "本项目金额", 120, E),
        )),
        ("other", "其他成本", details["other"], (
            ("date", "日期", 100, CENTER),
            ("source", "成本单号", 145, W),
            ("category", "分类", 130, W),
            ("party", "往来单位 / 人员", 170, W),
            ("vehicle", "车牌", 100, W),
            ("notes", "说明", 240, W),
            ("amount", "本项目金额", 120, E),
            ("allocation", "归集方式", 100, CENTER),
        )),
    )
    tab_by_section = {}
    for key, title, rows, columns in tab_specs:
        tab = ttk.Frame(tabs, padding=(0, SPACING["sm"], 0, 0))
        tabs.add(tab, text=f"{title}  {_money(details['totals'][key])}")
        tab_by_section[key] = tab
        if key == "purchase":
            hint = "逐材料列出未税额、税额和运费；多项目均摊的材料行按单内金额比例只读拆分，整单项目份额才是正式归集金额。"
        elif key == "labor":
            hint = "仅计入明确归属本项目的有效工天；加班标记不额外增加出勤天数。"
        else:
            hint = "仅显示有效成本记录中归属本项目的份额；多项目均摊不重复计入整笔费用。"
        ttk.Label(tab, text=hint, style="CardText.TLabel").pack(anchor=W, pady=(0, SPACING["sm"]))
        table = DataTable(
            tab, specs=columns, empty_text=f"本项目暂无{title}记录",
            stretch=("party", "material", "site", "notes"),
            padding=0, horizontal=True,
        )
        if key == "purchase":
            table.refresh(rows, lambda row: (None, (
                row["date"], row["source_no"], row["counterparty"],
                row["category"], row["description"], row["specification"],
                f"{row['quantity']:g} {row['unit']}" if row["quantity"] is not None else "--",
                row["purpose"], _money(row["material_minor"]),
                _money(row["tax_minor"]), _money(row["freight_minor"]),
                _money(row["amount_minor"]), row["payment_status"],
                _allocation_label(row["allocation_method"]),
            )))
        elif key == "labor":
            table.refresh(rows, lambda row: (None, (
                row["date"], row["source_no"], row["worker"],
                f"{row['days']:g}", "是" if row["is_overtime"] else "否",
                row["work_type"], row["site"], row["notes"],
                _money(row["amount_minor"]),
            )))
        else:
            table.refresh(rows, lambda row: (None, (
                row["date"], row["source_no"], row["category"],
                row["counterparty"], row["vehicle_no"], row["notes"],
                _money(row["amount_minor"]),
                _allocation_label(row["allocation_method"]),
            )))

    tabs.select(tab_by_section.get(section, tab_by_section["purchase"]))
    footer = ttk.Frame(dialog, padding=(SPACING["lg"], SPACING["sm"]))
    footer.pack(fill=X)
    close_button = ttk.Button(
        footer, text="关闭", bootstyle="secondary-outline", command=dialog.destroy
    )
    close_button.pack(side="right")
    style_dialog(dialog, parent, 1160, 680, min_width=760, min_height=430)
    close_button.focus_set()
    return dialog
