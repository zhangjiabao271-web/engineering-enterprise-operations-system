"""FinanceReceiptDialogMixin: extracted UI behavior from pages/finance_page.py.

Structure:

- module-level pure functions hold the testable logic: option labels,
  help/summary texts, source-based field visibility and payload assembly;
- ``_ReceiptFormState`` carries the mutable state of one open dialog;
- the mixin wires everything: ``open_receipt_dialog`` orchestrates,
  ``_build_receipt_form`` creates the widgets, the ``_sync_*`` /
  ``_refresh_*`` methods handle widget interlock, and ``_save_receipt``
  validates and persists through the finance service.
"""

from datetime import datetime
from functools import partial
from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import *

from services import contract_service, finance_service, project_service
from ui.components import DatePicker
from ui.dialogs import add_form_actions, build_form_dialog
from ui.error_handling import show_unexpected_error
from pages.finance_receipt_allocation import FinanceReceiptAllocationMixin

SOURCE_FORMAL = "正式合同工程"
SOURCE_CASH = "零星现金工程"
AUTOMATIC_INVOICE_LABEL = "按客户时间顺序自动核销"
NEW_CASH_SETTLEMENT_LABEL = "新增完工金额确认（本次同步建立）"

_FORM_FIELDS = (
    ("回款来源 *", "source"),
    ("合同与项目 *", "allocation"),
    ("零星工程项目 *", "cash_project"),
    ("完工金额确认 *", "cash_settlement"),
    ("指定发票（可选）", "invoice"),
    ("收入确认分配", "settlement_distribution"),
    ("回款单号", "no"),
    ("回款日期 *", "date"),
    ("完工确认日期 *", "settlement_date"),
    ("完工金额（元）*", "settlement_amount"),
    ("回款金额（元）*", "amount"),
    ("付款方", "payer"),
    ("收款方式", "method"),
)

_PAYMENT_METHODS = ["银行转账", "现金", "票据", "其他"]


def default_receipt_source(editing_cash, has_formal_options):
    """Initial receipt source: cash when editing a cash receipt or when no
    formal contract allocation is available."""
    if editing_cash or not has_formal_options:
        return SOURCE_CASH
    return SOURCE_FORMAL


def allocation_option_label(row):
    """Combo label for a contract/project allocation option."""
    pricing = contract_service.PRICING_MODES[row["pricing_mode"]]
    return (
        f"{row['contract_no']} · {pricing} → "
        f"{row['project_code']} · {row['project_name']}"
    )


def cash_project_option_label(row):
    """Combo label for a cash-project option."""
    return f"{row['project_code']} · {row['name']}"


def invoice_option_label(invoice, available_minor, money):
    """Combo label for an invoice option with its available amount."""
    return f"{invoice['invoice_no']} · 可回款 {money(available_minor)}"


def cash_settlement_option_label(settlement, available_minor, money):
    """Combo label for a cash settlement option with its available amount."""
    return (
        f"{settlement['settlement_no']} · "
        f"{settlement['settlement_date']} · "
        f"可回款 {money(available_minor)}"
    )


def can_create_cash_settlement(project, editing):
    """Whether the form may offer a new settlement for the cash project."""
    if not project or editing:
        return False
    agreed_minor = project.get("cash_agreed_amount_minor")
    return (
        agreed_minor is None
        or int(project.get("cash_confirmed_minor") or 0) < int(agreed_minor)
    )


def distribution_summary(invoice_selected, manual_items, money, *, receipt_driven=False):
    """Summary text and button enabled state for the distribution row."""
    if invoice_selected:
        return "随所选发票自动关联收入确认", False
    if manual_items:
        total_minor = sum(item["amount_minor"] for item in manual_items)
        return f"手动分配 {len(manual_items)} 笔 · {money(total_minor)}", True
    if receipt_driven:
        return "自动抵扣已确认结算，超出部分同步补记实际结算", True
    return "自动抵扣收入，超出部分记为预收款", True


def invoice_help_text(available_minor, money):
    """Hint under the invoice selector; None means automatic matching."""
    if available_minor is None:
        return (
            "按同一客户、同一已确认主体的发票日期自动抵扣；"
            "未开票或主体待确认的金额保留为待匹配。"
        )
    return f"手动指定后，本次最多可核销 {money(available_minor)}。"


def cash_settlement_help_text(has_project):
    """Hint under the cash settlement selector."""
    if not has_project:
        return "请选择零星工程项目。"
    return (
        "只登记实际收款，无需填写完工金额。\n"
        "已有收入自动抵扣，剩余记为预收款；以后确认收入时自动抵扣。"
    )


def source_field_visibility(source):
    """(visible_keys, hidden_keys) for the two receipt source modes."""
    if source == SOURCE_CASH:
        return (
            ("cash_project", "cash_settlement"),
            ("allocation", "invoice", "settlement_distribution"),
        )
    return (
        ("allocation", "invoice", "settlement_distribution"),
        (
            "cash_project",
            "cash_settlement",
            "settlement_date",
            "settlement_amount",
        ),
    )


def resolve_payer_input(current, previous_suggestion, new_suggestion):
    """Keep a hand-typed payer; follow the suggestion while untouched."""
    if not current or current == previous_suggestion:
        return new_suggestion
    return current


def build_receipt_payload(
    *,
    source,
    allocation,
    cash_project,
    invoice_id,
    manual_allocations,
    receipt_no,
    receipt_date,
    amount,
    payer_name,
    payment_method,
    notes,
):
    """Assemble the create/update payload from receipt form values.

    Cash receipts attach to a project only and allow advances; formal
    receipts attach to a contract allocation and may pin an invoice, or a
    manual settlement distribution when no invoice is selected.
    """
    is_cash = source == SOURCE_CASH
    selected = cash_project if is_cash else allocation
    if not selected:
        raise ValueError("请选择有效的回款来源")
    payload = {
        "receipt_no": receipt_no,
        "project_id": (
            cash_project["id"] if is_cash else allocation["project_id"]
        ),
        "contract_id": None if is_cash else allocation["contract_id"],
        "invoice_id": None if is_cash else invoice_id,
        "settlement_id": None,
        "allow_advance": is_cash,
        "receipt_date": receipt_date,
        "amount": amount,
        "payer_name": payer_name,
        "payment_method": payment_method,
        "notes": notes,
    }
    if not is_cash and invoice_id is None and manual_allocations is not None:
        payload["settlement_allocations"] = manual_allocations
    return payload


class _ReceiptFormState:
    """Mutable state of one open receipt dialog, shared by its handlers."""

    def __init__(self, *, receipt, receipt_id, selected_project_id):
        self.receipt = receipt
        self.receipt_id = receipt_id
        self.selected_project_id = selected_project_id
        self.current_settlement_id = (
            receipt["settlement_id"] if receipt else None
        )
        self.default_source = SOURCE_FORMAL
        self.current_allocation = ""
        self.current_cash_project = ""
        self.allocation_map = {}
        self.cash_project_map = {}
        self.cash_settlements_by_project = {}
        self.cash_settlement_map = {}
        self.invoices = []
        self.invoice_map = {AUTOMATIC_INVOICE_LABEL: None}
        self.invoice_available_map = {AUTOMATIC_INVOICE_LABEL: None}
        self.manual_items = None
        self.last_suggested_payer = ""
        self.dialog = None
        self.variables = {}
        self.widgets = {}
        self.field_labels = {}
        self.notes = None
        self.distribution_button = None
        self.invoice_help = None
        self.settlement_help = None
        self.invoice_help_var = None
        self.settlement_help_var = None
        self.allocation_summary_var = None

    @property
    def editing(self):
        return self.receipt is not None


class FinanceReceiptDialogMixin(FinanceReceiptAllocationMixin):
    def open_receipt_dialog(self, receipt_id=None):
        receipt = (
            finance_service.get_receipt(receipt_id) if receipt_id else None
        )
        if receipt_id and not receipt:
            messagebox.showwarning("提示", "有效回款记录不存在")
            return
        editing = receipt is not None
        ctx = _ReceiptFormState(
            receipt=receipt,
            receipt_id=receipt_id,
            selected_project_id=(
                receipt["project_id"] if editing else self.selected_project_id()
            ),
        )
        self._load_receipt_options(ctx)
        if not ctx.allocation_map and not ctx.cash_project_map:
            messagebox.showwarning(
                "提示", "请先登记可回款的收入确认，或建立零星现金工程项目"
            )
            return

        ctx.invoices = finance_service.list_invoices()
        ctx.default_source = default_receipt_source(
            editing and receipt["business_mode"] == "cash",
            bool(ctx.allocation_map),
        )
        ctx.current_allocation = next(
            (
                label
                for label, row in ctx.allocation_map.items()
                if editing
                and row["project_id"] == receipt["project_id"]
                and row["contract_id"] == receipt["contract_id"]
            ),
            "",
        )
        ctx.current_cash_project = next(
            (
                label
                for label, row in ctx.cash_project_map.items()
                if editing and row["id"] == receipt["project_id"]
            ),
            "",
        )

        footer = self._build_receipt_form(ctx)

        ctx.widgets["allocation"].bind(
            "<<ComboboxSelected>>", partial(self._refresh_receipt_invoices, ctx)
        )
        ctx.widgets["invoice"].bind(
            "<<ComboboxSelected>>", partial(self._sync_receipt_invoice_help, ctx)
        )
        ctx.widgets["cash_project"].bind(
            "<<ComboboxSelected>>",
            partial(self._refresh_receipt_cash_settlements, ctx),
        )
        ctx.widgets["cash_settlement"].bind(
            "<<ComboboxSelected>>",
            partial(self._sync_receipt_settlement_fields, ctx),
        )
        ctx.widgets["source"].bind(
            "<<ComboboxSelected>>", partial(self._sync_receipt_source, ctx)
        )
        self._refresh_receipt_invoices(
            ctx, selected_invoice_id=receipt["invoice_id"] if editing else None
        )
        self._refresh_receipt_cash_settlements(
            ctx, selected_settlement_id=ctx.current_settlement_id
        )
        self._sync_receipt_source(ctx)
        for key in ("source", "allocation", "cash_project"):
            ctx.variables[key].trace_add(
                "write", partial(self._sync_receipt_payer, ctx)
            )
        self._sync_receipt_payer(ctx)
        if editing:
            for key in (
                "source",
                "allocation",
                "cash_project",
                "cash_settlement",
            ):
                ctx.widgets[key].configure(state="disabled")

        add_form_actions(
            footer,
            cancel_command=ctx.dialog.destroy,
            primary_text="保存修改" if editing else "保存回款",
            primary_command=lambda: self._save_receipt(ctx),
            primary_style="primary" if editing else "success",
        )

    def _load_receipt_options(self, ctx):
        receipt = ctx.receipt
        editing = ctx.editing
        project_id = ctx.selected_project_id
        for settlement in contract_service.list_settlements(
            project_id=project_id
        ):
            is_current_pair = (
                editing
                and settlement["project_id"] == receipt["project_id"]
                and settlement["contract_id"] == receipt["contract_id"]
            )
            if (
                settlement["source_type"] != "contract"
                or not (settlement["unreceived_minor"] > 0 or is_current_pair)
            ):
                continue
            ctx.allocation_map[allocation_option_label(settlement)] = settlement
        for row in contract_service.list_allocations(project_id=project_id):
            if row["contract_status"] == "void":
                continue
            ctx.allocation_map.setdefault(allocation_option_label(row), row)
        cash_projects = [
            row
            for row in project_service.list_projects(active_only=False)
            if row["business_mode"] == "cash"
            and (
                row["status"] != "已关闭"
                or (editing and row["id"] == project_id)
            )
            and (not project_id or row["id"] == project_id)
        ]
        ctx.cash_project_map.update(
            (cash_project_option_label(row), row) for row in cash_projects
        )
        for settlement in contract_service.list_settlements(
            project_id=project_id
        ):
            if settlement["source_type"] == "cash_job" and (
                settlement["unreceived_minor"] > 0
                or settlement["id"] == ctx.current_settlement_id
            ):
                ctx.cash_settlements_by_project.setdefault(
                    settlement["project_id"], []
                ).append(settlement)

    def _build_receipt_form(self, ctx):
        receipt = ctx.receipt
        editing = ctx.editing
        dialog = ttk.Toplevel(self.parent)
        dialog.title("修改回款" if editing else "登记回款")
        body, footer = build_form_dialog(
            dialog, self.parent, 730, 760, min_width=610, min_height=520
        )
        ctx.dialog = dialog
        today = datetime.now().strftime("%Y-%m-%d")
        ctx.variables = {
            "source": ttk.StringVar(value=ctx.default_source),
            "allocation": ttk.StringVar(
                value=ctx.current_allocation or next(iter(ctx.allocation_map), "")
            ),
            "cash_project": ttk.StringVar(
                value=ctx.current_cash_project
                or next(iter(ctx.cash_project_map), "")
            ),
            "cash_settlement": ttk.StringVar(),
            "invoice": ttk.StringVar(value=AUTOMATIC_INVOICE_LABEL),
            "no": ttk.StringVar(value=receipt["receipt_no"] if editing else ""),
            "date": ttk.StringVar(
                value=receipt["receipt_date"] if editing else today
            ),
            "settlement_date": ttk.StringVar(value=today),
            "settlement_amount": ttk.StringVar(),
            "amount": ttk.StringVar(
                value=f"{receipt['allocated_amount_minor'] / 100:.2f}"
                if editing else ""
            ),
            "payer": ttk.StringVar(
                value=receipt["payer_name_snapshot"] if editing else ""
            ),
            "method": ttk.StringVar(
                value=receipt["payment_method"] if editing else "银行转账"
            ),
        }
        ctx.invoice_help_var = ttk.StringVar()
        ctx.allocation_summary_var = ttk.StringVar(
            value="系统按确认日期自动分配"
        )

        combo_values = {
            "source": [SOURCE_FORMAL, SOURCE_CASH],
            "allocation": list(ctx.allocation_map),
            "cash_project": list(ctx.cash_project_map),
            "cash_settlement": [],
            "invoice": list(ctx.invoice_map),
            "method": _PAYMENT_METHODS,
        }
        for row, (label, key) in enumerate(_FORM_FIELDS):
            label_widget = ttk.Label(body, text=label)
            label_widget.grid(
                row=row, column=0, sticky=E, padx=(0, 12), pady=7
            )
            if key == "settlement_distribution":
                widget = ttk.Frame(body)
                ttk.Label(
                    widget,
                    textvariable=ctx.allocation_summary_var,
                    style="Muted.TLabel",
                ).pack(side=LEFT, fill=X, expand=True)
                ctx.distribution_button = ttk.Button(
                    widget,
                    text="查看 / 调整",
                    bootstyle="secondary-outline",
                    command=lambda: self._open_receipt_distribution_dialog(ctx),
                )
                ctx.distribution_button.pack(side=RIGHT, padx=(10, 0))
            elif key in combo_values:
                widget = ttk.Combobox(
                    body, textvariable=ctx.variables[key],
                    values=combo_values[key], state="readonly"
                )
            elif key in ("date", "settlement_date"):
                widget = DatePicker(
                    body,
                    textvariable=ctx.variables[key],
                    popup_title=(
                        "选择回款日期" if key == "date" else "选择完工确认日期"
                    ),
                )
            else:
                widget = ttk.Entry(body, textvariable=ctx.variables[key])
            grid_options = {"row": row, "column": 1, "sticky": EW, "pady": 7}
            if not isinstance(widget, DatePicker):
                grid_options["ipady"] = 4
            widget.grid(**grid_options)
            ctx.widgets[key] = widget
            ctx.field_labels[key] = label_widget

        ctx.settlement_help_var = ttk.StringVar()
        ctx.settlement_help = ttk.Label(
            body,
            textvariable=ctx.settlement_help_var,
            style="Muted.TLabel",
            wraplength=480,
            justify=LEFT,
        )
        ctx.invoice_help = ttk.Label(
            body,
            textvariable=ctx.invoice_help_var,
            style="Muted.TLabel",
            wraplength=480,
            justify=LEFT,
        )
        for help_widget in (ctx.settlement_help, ctx.invoice_help):
            help_widget.grid(
                row=len(_FORM_FIELDS), column=1, sticky=W, pady=(0, 5)
            )

        ttk.Label(body, text="备注").grid(
            row=len(_FORM_FIELDS) + 1, column=0, sticky=NE, padx=(0, 12), pady=7
        )
        ctx.notes = ttk.Text(body, height=5, wrap="word")
        ctx.notes.grid(row=len(_FORM_FIELDS) + 1, column=1, sticky=EW, pady=7)
        if editing and receipt["notes"]:
            ctx.notes.insert("1.0", receipt["notes"])
        body.columnconfigure(1, weight=1)
        return footer

    def _selected_formal_allocation(self, ctx):
        return ctx.allocation_map.get(ctx.variables["allocation"].get())

    def _sync_receipt_distribution_summary(self, ctx):
        invoice_id = ctx.invoice_map.get(ctx.variables["invoice"].get())
        allocation = self._selected_formal_allocation(ctx) or {}
        text, enabled = distribution_summary(
            bool(invoice_id), ctx.manual_items, self.money,
            receipt_driven=allocation.get('income_mode') == 'receipt',
        )
        ctx.allocation_summary_var.set(text)
        ctx.distribution_button.configure(
            state="normal" if enabled else "disabled"
        )

    def _sync_receipt_invoice_help(self, ctx, _event=None):
        available = ctx.invoice_available_map.get(
            ctx.variables["invoice"].get()
        )
        ctx.invoice_help_var.set(invoice_help_text(available, self.money))
        if available is not None:
            ctx.manual_items = None
        self._sync_receipt_distribution_summary(ctx)

    def _refresh_receipt_invoices(self, ctx, _event=None, selected_invoice_id=None):
        if _event is not None:
            ctx.manual_items = None
        ctx.invoice_map.clear()
        ctx.invoice_available_map.clear()
        ctx.invoice_map[AUTOMATIC_INVOICE_LABEL] = None
        ctx.invoice_available_map[AUTOMATIC_INVOICE_LABEL] = None
        selected_label = AUTOMATIC_INVOICE_LABEL
        allocation = self._selected_formal_allocation(ctx)
        editing = ctx.editing
        current_by_invoice = {
            match["invoice_id"]: match["allocated_amount_minor"]
            for match in (
                ctx.receipt.get("invoice_matches", []) if editing else []
            )
        }
        if allocation:
            for row in ctx.invoices:
                current_receipt_amount = current_by_invoice.get(
                    row["id"], 0
                )
                available_minor = (
                    row["unreceived_minor"] + current_receipt_amount
                )
                if (
                    row["project_id"] == allocation["project_id"]
                    and row["contract_id"] == allocation["contract_id"]
                    and (available_minor > 0 or row["id"] == selected_invoice_id)
                ):
                    label = invoice_option_label(
                        row, available_minor, self.money
                    )
                    ctx.invoice_map[label] = row["id"]
                    ctx.invoice_available_map[label] = available_minor
                    if row["id"] == selected_invoice_id:
                        selected_label = label
        ctx.widgets["invoice"].configure(values=list(ctx.invoice_map))
        ctx.variables["invoice"].set(selected_label)
        self._sync_receipt_invoice_help(ctx)

    def _sync_receipt_cash_help(self, ctx):
        project = ctx.cash_project_map.get(ctx.variables["cash_project"].get())
        ctx.settlement_help_var.set(
            cash_settlement_help_text(bool(project))
        )

    def _sync_receipt_settlement_fields(self, ctx, _event=None):
        for key in ("cash_settlement", "settlement_date", "settlement_amount"):
            for widget in (ctx.field_labels[key], ctx.widgets[key]):
                widget.grid_remove()
        if ctx.variables["source"].get() == SOURCE_CASH:
            ctx.settlement_help.grid()
        else:
            ctx.settlement_help.grid_remove()
        self._sync_receipt_cash_help(ctx)

    def _refresh_receipt_cash_settlements(
        self, ctx, _event=None, selected_settlement_id=None
    ):
        project = ctx.cash_project_map.get(ctx.variables["cash_project"].get())
        ctx.cash_settlement_map.clear()
        selected_label = ""
        if project:
            for settlement in ctx.cash_settlements_by_project.get(
                project["id"], []
            ):
                available_minor = settlement["unreceived_minor"] + (
                    ctx.receipt["allocated_amount_minor"]
                    if ctx.editing
                    and settlement["id"] == ctx.current_settlement_id
                    else 0
                )
                label = cash_settlement_option_label(
                    settlement, available_minor, self.money
                )
                ctx.cash_settlement_map[label] = settlement
                if settlement["id"] == selected_settlement_id:
                    selected_label = label
        if can_create_cash_settlement(project, ctx.editing):
            ctx.cash_settlement_map[NEW_CASH_SETTLEMENT_LABEL] = None
        ctx.widgets["cash_settlement"].configure(
            values=list(ctx.cash_settlement_map)
        )
        ctx.variables["cash_settlement"].set(
            selected_label or next(iter(ctx.cash_settlement_map), "")
        )
        self._sync_receipt_settlement_fields(ctx)

    def _sync_receipt_source(self, ctx, _event=None):
        is_cash = ctx.variables["source"].get() == SOURCE_CASH
        visible_keys, hidden_keys = source_field_visibility(
            ctx.variables["source"].get()
        )
        for key in visible_keys:
            ctx.field_labels[key].grid()
            ctx.widgets[key].grid()
        for key in hidden_keys:
            ctx.field_labels[key].grid_remove()
            ctx.widgets[key].grid_remove()
        if is_cash:
            ctx.invoice_help.grid_remove()
            ctx.settlement_help.grid()
            self._sync_receipt_settlement_fields(ctx)
        else:
            ctx.settlement_help.grid_remove()
            ctx.invoice_help.grid()
            self._sync_receipt_distribution_summary(ctx)
        if not ctx.editing:
            ctx.variables["method"].set("现金" if is_cash else "银行转账")

    def _sync_receipt_payer(self, ctx, *_args):
        if ctx.editing:
            return
        if ctx.variables["source"].get() == SOURCE_CASH:
            selected = (
                ctx.cash_project_map.get(ctx.variables["cash_project"].get())
                or {}
            )
            suggestion = finance_service.default_receipt_payer(
                selected.get("id")
            )
        else:
            selected = self._selected_formal_allocation(ctx) or {}
            suggestion = finance_service.default_receipt_payer(
                selected.get("project_id"), selected.get("contract_id")
            )
        current = ctx.variables["payer"].get().strip()
        updated = resolve_payer_input(
            current, ctx.last_suggested_payer, suggestion
        )
        if updated != current or not current:
            ctx.variables["payer"].set(updated)
        ctx.last_suggested_payer = suggestion

    def _save_receipt(self, ctx):
        try:
            payload = build_receipt_payload(
                source=ctx.variables["source"].get(),
                allocation=self._selected_formal_allocation(ctx),
                cash_project=ctx.cash_project_map.get(
                    ctx.variables["cash_project"].get()
                ),
                invoice_id=ctx.invoice_map.get(ctx.variables["invoice"].get()),
                manual_allocations=ctx.manual_items,
                receipt_no=ctx.variables["no"].get(),
                receipt_date=ctx.variables["date"].get(),
                amount=ctx.variables["amount"].get(),
                payer_name=ctx.variables["payer"].get(),
                payment_method=ctx.variables["method"].get(),
                notes=ctx.notes.get("1.0", END).strip(),
            )
        except ValueError as error:
            messagebox.showwarning("提示", str(error), parent=ctx.dialog)
            return
        except Exception:
            show_unexpected_error("无法保存", parent=ctx.dialog)
            return
        try:
            if ctx.editing:
                finance_service.update_receipt(ctx.receipt_id, payload)
            else:
                finance_service.create_receipt(payload)
        except ValueError as error:
            messagebox.showwarning("无法保存", str(error), parent=ctx.dialog)
            return
        except Exception:
            show_unexpected_error("无法保存", parent=ctx.dialog)
            return
        try:
            ctx.dialog.destroy()
            self.refresh()
            self.notebook.select(2)
        except Exception:
            show_unexpected_error("回款已保存，界面刷新失败", parent=self.parent)
