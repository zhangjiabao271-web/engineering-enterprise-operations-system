"""Budget retirement must not break actual payments or leave stale navigation."""

import unittest
from unittest.mock import Mock, patch


class CashBudgetRetirementTests(unittest.TestCase):
    def test_budget_implementation_is_retired(self):
        import main
        import pages
        from pages import cash_budget_page, funds_page
        from services import cash_budget_service

        self.assertFalse(hasattr(main.SupplierManagerApp, "show_funds_page"))
        self.assertNotIn("FundsPage", pages.__all__)
        self.assertFalse(hasattr(funds_page, "FundsPage"))
        self.assertFalse(hasattr(cash_budget_page, "CashBudgetPage"))
        self.assertFalse(hasattr(cash_budget_service, "forecast"))

    def test_payment_dialog_still_records_actual_payment_without_budget_plan(self):
        from pages import fund_payment_dialog as dialog

        source = {"id": 7, "period": "", "state": "待支付", "remaining_minor": 10000,
                  "total_minor": 10000, "paid_minor": 0, "label": "测试采购",
                  "legacy_status": "未付款", "verified": True, "counterparty": "测试商户"}
        form = Mock()
        with patch.object(dialog.funds, "list_sources", return_value=[source]), \
             patch.object(dialog.funds, "list_accounts", return_value=[{
                 "name": "公司总账户", "id": 3, "status": "active", "balance_minor": 50000,
             }]), patch.object(dialog, "FundForm", return_value=form), \
             patch.object(dialog.ttk, "BooleanVar") as confirmed, \
             patch.object(dialog.ttk, "Checkbutton"), \
             patch.object(dialog.funds, "record_transaction") as record:
            confirmed.return_value.get.return_value = True
            result = dialog.open_source_payment(Mock(), "purchase", 7)
            self.assertIs(result, form)
            self.assertNotIn("plan", [call.args[0] for call in form.field.call_args_list])
            account = next(call.kwargs["choices"] for call in form.field.call_args_list
                           if call.args[0] == "account")
            save = form.submit.call_args.args[0]
            save({"account": next(iter(account)), "date": "2026-10-01", "amount": "100", "notes": "测试"})
            data = record.call_args.args[0]
            self.assertEqual(data["source_id"], 7)
            self.assertEqual(data["from_account_id"], 3)
            self.assertEqual(data["amount"], "100")
            self.assertNotIn("plan_id", data)


if __name__ == "__main__":
    unittest.main()
