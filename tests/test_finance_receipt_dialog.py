"""Unit tests for the pure helpers extracted from pages/finance_receipt_dialog.

These cover the module-level logic only (option labels, help/summary texts,
source-based visibility, payload assembly); no Tk widgets or database.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pages.finance_receipt_dialog import (
    SOURCE_CASH,
    SOURCE_FORMAL,
    allocation_option_label,
    build_receipt_payload,
    can_create_cash_settlement,
    cash_project_option_label,
    cash_settlement_help_text,
    cash_settlement_option_label,
    default_receipt_source,
    distribution_summary,
    invoice_help_text,
    invoice_option_label,
    resolve_payer_input,
    source_field_visibility,
    FinanceReceiptDialogMixin,
)


def money(amount_minor):
    return f"¥{amount_minor / 100:,.2f}"


class OptionLabelTests(unittest.TestCase):
    def test_allocation_option_label(self):
        row = {
            "pricing_mode": "fixed",
            "contract_no": "HT-001",
            "project_code": "P01",
            "project_name": "办公楼",
        }
        self.assertEqual(
            allocation_option_label(row),
            "HT-001 · 固定总价 → P01 · 办公楼",
        )

    def test_cash_project_option_label(self):
        row = {"project_code": "P02", "name": "零星维修"}
        self.assertEqual(cash_project_option_label(row), "P02 · 零星维修")

    def test_invoice_option_label(self):
        label = invoice_option_label({"invoice_no": "INV-9"}, 123456, money)
        self.assertEqual(label, "INV-9 · 可回款 ¥1,234.56")

    def test_cash_settlement_option_label(self):
        settlement = {"settlement_no": "JS-3", "settlement_date": "2026-08-01"}
        self.assertEqual(
            cash_settlement_option_label(settlement, 5000, money),
            "JS-3 · 2026-08-01 · 可回款 ¥50.00",
        )


class DefaultSourceTests(unittest.TestCase):
    def test_editing_cash_receipt_defaults_to_cash(self):
        self.assertEqual(default_receipt_source(True, True), SOURCE_CASH)

    def test_no_formal_options_defaults_to_cash(self):
        self.assertEqual(default_receipt_source(False, False), SOURCE_CASH)

    def test_new_receipt_with_allocations_defaults_to_formal(self):
        self.assertEqual(default_receipt_source(False, True), SOURCE_FORMAL)


class CanCreateCashSettlementTests(unittest.TestCase):
    def test_no_project(self):
        self.assertFalse(can_create_cash_settlement(None, editing=False))

    def test_editing_disables_creation(self):
        project = {"cash_agreed_amount_minor": None}
        self.assertFalse(can_create_cash_settlement(project, editing=True))

    def test_no_agreed_amount_allows_creation(self):
        project = {"cash_agreed_amount_minor": None}
        self.assertTrue(can_create_cash_settlement(project, editing=False))

    def test_confirmed_below_agreed_allows_creation(self):
        project = {
            "cash_agreed_amount_minor": 100000,
            "cash_confirmed_minor": 40000,
        }
        self.assertTrue(can_create_cash_settlement(project, editing=False))

    def test_confirmed_reaching_agreed_blocks_creation(self):
        project = {
            "cash_agreed_amount_minor": 100000,
            "cash_confirmed_minor": 100000,
        }
        self.assertFalse(can_create_cash_settlement(project, editing=False))


class DistributionSummaryTests(unittest.TestCase):
    def test_invoice_selected_disables_button(self):
        text, enabled = distribution_summary(True, None, money)
        self.assertEqual(text, "随所选发票自动关联收入确认")
        self.assertFalse(enabled)

    def test_manual_items_summarized_with_total(self):
        items = [
            {"settlement_id": 1, "amount_minor": 10000},
            {"settlement_id": 2, "amount_minor": 2500},
        ]
        text, enabled = distribution_summary(False, items, money)
        self.assertEqual(text, "手动分配 2 笔 · ¥125.00")
        self.assertTrue(enabled)

    def test_default_automatic_text(self):
        text, enabled = distribution_summary(False, None, money)
        self.assertEqual(text, "自动抵扣收入，超出部分记为预收款")
        self.assertTrue(enabled)

    def test_receipt_driven_contract_summary(self):
        text, enabled = distribution_summary(False, None, money, receipt_driven=True)
        self.assertIn('同步补记实际结算', text)
        self.assertNotIn('预收款', text)
        self.assertTrue(enabled)


class HelpTextTests(unittest.TestCase):
    def test_invoice_help_automatic(self):
        text = invoice_help_text(None, money)
        self.assertIn("自动抵扣", text)
        self.assertIn("待匹配", text)

    def test_invoice_help_manual_limit(self):
        text = invoice_help_text(123456, money)
        self.assertEqual(text, "手动指定后，本次最多可核销 ¥1,234.56。")

    def test_cash_help_without_project(self):
        self.assertEqual(cash_settlement_help_text(False), "请选择零星工程项目。")

    def test_cash_help_with_project(self):
        text = cash_settlement_help_text(True)
        self.assertIn("只登记实际收款", text)
        self.assertIn("预收款", text)


class SourceFieldVisibilityTests(unittest.TestCase):
    def test_cash_source(self):
        visible, hidden = source_field_visibility(SOURCE_CASH)
        self.assertEqual(visible, ("cash_project", "cash_settlement"))
        self.assertEqual(
            hidden, ("allocation", "invoice", "settlement_distribution")
        )

    def test_formal_source(self):
        visible, hidden = source_field_visibility(SOURCE_FORMAL)
        self.assertEqual(
            visible, ("allocation", "invoice", "settlement_distribution")
        )
        self.assertEqual(
            hidden,
            (
                "cash_project",
                "cash_settlement",
                "settlement_date",
                "settlement_amount",
            ),
        )


class ResolvePayerInputTests(unittest.TestCase):
    def test_empty_field_takes_suggestion(self):
        self.assertEqual(resolve_payer_input("", "旧建议", "新建议"), "新建议")

    def test_unchanged_suggestion_follows_new_one(self):
        self.assertEqual(resolve_payer_input("旧建议", "旧建议", "新建议"), "新建议")

    def test_hand_typed_payer_is_kept(self):
        self.assertEqual(
            resolve_payer_input("手填付款方", "旧建议", "新建议"), "手填付款方"
        )


class BuildReceiptPayloadTests(unittest.TestCase):
    base_kwargs = dict(
        source=SOURCE_FORMAL,
        allocation={"project_id": 11, "contract_id": 22},
        cash_project=None,
        invoice_id=None,
        manual_allocations=None,
        receipt_no="HK-1",
        receipt_date="2026-09-27",
        amount="100.00",
        payer_name="甲方公司",
        payment_method="银行转账",
        notes="备注",
    )

    def payload(self, **overrides):
        return build_receipt_payload(**{**self.base_kwargs, **overrides})

    def test_formal_automatic_payload(self):
        payload = self.payload()
        self.assertEqual(
            payload,
            {
                "receipt_no": "HK-1",
                "project_id": 11,
                "contract_id": 22,
                "invoice_id": None,
                "settlement_id": None,
                "allow_advance": False,
                "receipt_date": "2026-09-27",
                "amount": "100.00",
                "payer_name": "甲方公司",
                "payment_method": "银行转账",
                "notes": "备注",
            },
        )

    def test_formal_with_invoice_ignores_manual_allocations(self):
        payload = self.payload(
            invoice_id=7,
            manual_allocations=[{"settlement_id": 1, "amount_minor": 100}],
        )
        self.assertEqual(payload["invoice_id"], 7)
        self.assertNotIn("settlement_allocations", payload)

    def test_formal_manual_allocations_attached_without_invoice(self):
        items = [{"settlement_id": 1, "amount_minor": 10000}]
        payload = self.payload(manual_allocations=items)
        self.assertIs(payload["settlement_allocations"], items)

    def test_cash_payload_forces_project_only(self):
        payload = self.payload(
            source=SOURCE_CASH,
            allocation=None,
            cash_project={"id": 33},
            invoice_id=7,
            manual_allocations=[{"settlement_id": 1, "amount_minor": 100}],
        )
        self.assertEqual(payload["project_id"], 33)
        self.assertIsNone(payload["contract_id"])
        self.assertIsNone(payload["invoice_id"])
        self.assertIsNone(payload["settlement_id"])
        self.assertTrue(payload["allow_advance"])
        self.assertNotIn("settlement_allocations", payload)

    def test_missing_selection_rejected(self):
        with self.assertRaisesRegex(ValueError, "请选择有效的回款来源"):
            self.payload(allocation=None)
        with self.assertRaisesRegex(ValueError, "请选择有效的回款来源"):
            self.payload(source=SOURCE_CASH, cash_project=None)


class ReceiptSaveCallbackTests(unittest.TestCase):
    def setUp(self):
        self.page = FinanceReceiptDialogMixin()
        self.page.parent = Mock()
        self.page.refresh = Mock()
        self.page.notebook = Mock()
        values = {
            "source": SOURCE_FORMAL, "allocation": "合同 · 项目",
            "cash_project": "", "invoice": "", "no": "HK-1",
            "date": "2026-09-28", "amount": "12.00",
            "payer": "测试客户", "method": "银行转账",
        }
        variables = {
            key: SimpleNamespace(get=lambda value=value: value)
            for key, value in values.items()
        }
        self.ctx = SimpleNamespace(
            dialog=Mock(), editing=False, receipt_id=None,
            variables=variables, notes=Mock(), manual_items=None,
            allocation_map={"合同 · 项目": {"project_id": 1, "contract_id": 2}},
            cash_project_map={}, invoice_map={},
        )
        self.ctx.notes.get.return_value = ""

    def test_business_error_keeps_form_open(self):
        with patch("pages.finance_receipt_dialog.build_receipt_payload", return_value={}), \
             patch("pages.finance_receipt_dialog.finance_service.create_receipt", side_effect=ValueError("金额超出余额")), \
             patch("pages.finance_receipt_dialog.messagebox.showwarning") as warning, \
             patch("pages.finance_receipt_dialog.show_unexpected_error") as unexpected:
            self.page._save_receipt(self.ctx)
        warning.assert_called_once_with("无法保存", "金额超出余额", parent=self.ctx.dialog)
        unexpected.assert_not_called()
        self.ctx.dialog.destroy.assert_not_called()

    def test_unexpected_error_hides_details_and_keeps_form_open(self):
        with patch("pages.finance_receipt_dialog.build_receipt_payload", return_value={}), \
             patch("pages.finance_receipt_dialog.finance_service.create_receipt", side_effect=RuntimeError("private database detail")), \
             patch("pages.finance_receipt_dialog.messagebox.showwarning") as warning, \
             patch("pages.finance_receipt_dialog.show_unexpected_error") as unexpected:
            self.page._save_receipt(self.ctx)
        unexpected.assert_called_once_with("无法保存", parent=self.ctx.dialog)
        warning.assert_not_called()
        self.ctx.dialog.destroy.assert_not_called()

    def test_edit_uses_update_and_closes_after_success(self):
        self.ctx.editing = True
        self.ctx.receipt_id = 42
        with patch("pages.finance_receipt_dialog.build_receipt_payload", return_value={"amount": "12.00"}), \
             patch("pages.finance_receipt_dialog.finance_service.update_receipt") as update:
            self.page._save_receipt(self.ctx)
        update.assert_called_once_with(42, {"amount": "12.00"})
        self.ctx.dialog.destroy.assert_called_once_with()
        self.page.refresh.assert_called_once_with()
        self.page.notebook.select.assert_called_once_with(2)

    def test_refresh_failure_does_not_suggest_saving_again(self):
        self.page.refresh.side_effect = RuntimeError("refresh failed")
        with patch("pages.finance_receipt_dialog.build_receipt_payload", return_value={}), \
             patch("pages.finance_receipt_dialog.finance_service.create_receipt"), \
             patch("pages.finance_receipt_dialog.show_unexpected_error") as unexpected:
            self.page._save_receipt(self.ctx)
        self.ctx.dialog.destroy.assert_called_once_with()
        unexpected.assert_called_once_with(
            "回款已保存，界面刷新失败", parent=self.page.parent
        )


if __name__ == "__main__":
    unittest.main()
