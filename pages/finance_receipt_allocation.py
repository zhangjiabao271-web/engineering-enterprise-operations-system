"""Income-confirmation distribution subdialog for receipt entry."""

from tkinter import messagebox

import ttkbootstrap as ttk
from ttkbootstrap.constants import E, EW, RIGHT, W

from services import contract_service, finance_service
from ui.dialogs import add_form_actions, build_form_dialog


class FinanceReceiptAllocationMixin:
    def _build_distribution_form(
        self, ctx, automatic, available_settlements, current_by_settlement
    ):
        """Render editable rows without changing the receipt or its allocations."""
        allocation_dialog = ttk.Toplevel(ctx.dialog)
        allocation_dialog.title("调整收入确认分配")
        allocation_body, allocation_footer = build_form_dialog(
            allocation_dialog,
            ctx.dialog,
            760,
            560,
            min_width=680,
            min_height=420,
        )
        ttk.Label(
            allocation_body,
            text="本次回款如何计入收入确认",
            style="CardTitle.TLabel",
        ).grid(row=0, column=0, columnspan=5, sticky=W, pady=(0, 3))
        ttk.Label(
            allocation_body,
            text="默认按确认日期从早到晚分配；只有需要调整时才修改右侧金额。",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=5, sticky=W, pady=(0, 12))
        for column, (text, sticky) in enumerate(
            (
                ("确认编号", W),
                ("日期", ""),
                ("确认金额", E),
                ("可回款", E),
                ("本次分配（元）", E),
            )
        ):
            ttk.Label(
                allocation_body, text=text, style="SpineLabel.TLabel"
            ).grid(
                row=2,
                column=column,
                sticky=sticky,
                padx=(0, 10) if column < 4 else 0,
                pady=(0, 6),
            )

        planned_minor = {
            row["settlement_id"]: row["amount_minor"] for row in automatic
        }
        if ctx.manual_items:
            planned_minor = {
                row["settlement_id"]: row["amount_minor"]
                for row in ctx.manual_items
            }
        amount_vars = {}
        for index, settlement in enumerate(available_settlements, start=3):
            available_minor = settlement["unreceived_minor"] + (
                current_by_settlement.get(settlement["id"], 0)
            )
            amount_minor = planned_minor.get(settlement["id"], 0)
            amount_var = ttk.StringVar(
                value=f"{amount_minor / 100:.2f}" if amount_minor else ""
            )
            amount_vars[settlement["id"]] = amount_var
            values = (
                settlement["settlement_no"],
                settlement["settlement_date"],
                self.money(settlement["amount_minor"]),
                self.money(available_minor),
            )
            for column, value in enumerate(values):
                ttk.Label(allocation_body, text=value).grid(
                    row=index,
                    column=column,
                    sticky=W if column == 0 else E,
                    padx=(0, 10),
                    pady=5,
                )
            ttk.Entry(
                allocation_body,
                textvariable=amount_var,
                width=16,
                justify=RIGHT,
            ).grid(row=index, column=4, sticky=EW, pady=5, ipady=3)
        allocation_body.columnconfigure(0, weight=1)
        allocation_body.columnconfigure(4, weight=1)
        return allocation_dialog, allocation_footer, amount_vars

    def _open_receipt_distribution_dialog(self, ctx):
        receipt = ctx.receipt
        editing = ctx.editing
        allocation = self._selected_formal_allocation(ctx)
        if not allocation:
            messagebox.showwarning(
                "提示", "请先选择合同与项目", parent=ctx.dialog
            )
            return
        if ctx.invoice_map.get(ctx.variables["invoice"].get()):
            messagebox.showinfo(
                "收入确认分配",
                "已选择发票，系统会按照发票对应的收入确认自动关联。",
                parent=ctx.dialog,
            )
            return
        base_payload = {
            "project_id": allocation["project_id"],
            "contract_id": allocation["contract_id"],
            "amount": ctx.variables["amount"].get(),
        }
        try:
            automatic = finance_service.preview_receipt_allocations(
                base_payload,
                exclude_receipt_id=ctx.receipt_id if editing else None,
            )
        except Exception as error:
            messagebox.showwarning(
                "无法分配", str(error), parent=ctx.dialog
            )
            return

        available_settlements = contract_service.list_settlements(
            project_id=allocation["project_id"],
            contract_id=allocation["contract_id"],
        )
        current_by_settlement = {
            row["settlement_id"]: row["allocated_amount_minor"]
            for row in (receipt.get("allocations", []) if editing else [])
            if row["settlement_id"]
        }
        available_settlements = [
            row for row in available_settlements
            if row["source_type"] == "contract"
            and (
                row["unreceived_minor"] > 0
                or current_by_settlement.get(row["id"], 0) > 0
            )
        ]
        if not available_settlements:
            messagebox.showwarning(
                "无法分配", "当前合同项目没有可回款的收入确认", parent=ctx.dialog
            )
            return

        allocation_dialog, allocation_footer, amount_vars = self._build_distribution_form(
            ctx, automatic, available_settlements, current_by_settlement
        )

        def use_manual_distribution():
            requested = [
                {
                    "settlement_id": settlement_id,
                    "amount": amount_var.get(),
                }
                for settlement_id, amount_var in amount_vars.items()
                if amount_var.get().strip()
            ]
            try:
                validated = finance_service.preview_receipt_allocations(
                    {**base_payload, "settlement_allocations": requested},
                    exclude_receipt_id=ctx.receipt_id if editing else None,
                )
            except Exception as error:
                messagebox.showwarning(
                    "无法使用该分配", str(error), parent=allocation_dialog
                )
                return
            ctx.manual_items = [
                {
                    "settlement_id": row["settlement_id"],
                    "amount_minor": row["amount_minor"],
                }
                for row in validated
            ]
            allocation_dialog.destroy()
            self._sync_receipt_distribution_summary(ctx)

        def use_automatic_distribution():
            ctx.manual_items = None
            allocation_dialog.destroy()
            self._sync_receipt_distribution_summary(ctx)

        add_form_actions(
            allocation_footer,
            cancel_command=allocation_dialog.destroy,
            secondary_text="恢复自动分配",
            secondary_command=use_automatic_distribution,
            primary_text="使用该分配",
            primary_command=use_manual_distribution,
        )
