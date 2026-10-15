"""Explicit native review of receipt-owned income; cancellation never writes."""
from decimal import Decimal
from tkinter import messagebox
import ttkbootstrap as ttk
from ttkbootstrap.constants import X

from services import receipt_income_service as income
from ui.dialogs import build_form_dialog, add_form_actions


def review_receipt_change(parent, receipt_id, amount=None):
    impact = income.public_preview(receipt_id, amount)
    if not impact['requires_decision']:
        return {}
    dialog = ttk.Toplevel(parent)
    dialog.title('核对回款及自动补足收入')
    body, footer = build_form_dialog(dialog, parent, 720, 540, min_width=640, min_height=400)
    result = {}
    action = ttk.StringVar(value='')
    reason = ttk.StringVar(value='')
    ttk.Label(body, text=impact['notice'], wraplength=580).pack(fill=X, pady=(0,12))
    for row in impact['rows']:
        text = (f"{row['settlement_no']}：原收入 {Decimal(row['before_minor'])/100:,.2f} 元；"
                f"若撤销误补，拟保留 {Decimal(row['after_minor'])/100:,.2f} 元。{row['restriction']}")
        ttk.Label(body, text=text, wraplength=580).pack(fill=X, pady=(0,8))
    for value, label in income.ACTIONS.items():
        radio = ttk.Radiobutton(body, text=label, variable=action, value=value)
        radio.pack(fill=X, pady=6)
        if value == 'reverse' and not impact['can_reverse']:
            radio.configure(state='disabled')
    ttk.Label(body, text='本次更正说明（必填）').pack(fill=X, pady=(16,4))
    entry = ttk.Entry(body, textvariable=reason)
    entry.pack(fill=X)

    def confirm():
        try:
            income.validate_choice(impact, action.get(), reason.get())
        except ValueError as error:
            messagebox.showwarning('尚未确认', str(error), parent=dialog)
            entry.focus_set()
            return
        result.update(receipt_income_action=action.get(), receipt_income_reason=reason.get().strip())
        dialog.destroy()

    add_form_actions(footer, cancel_command=dialog.destroy, primary_text='确认本次更正', primary_command=confirm)
    parent.wait_window(dialog)
    return result or None
