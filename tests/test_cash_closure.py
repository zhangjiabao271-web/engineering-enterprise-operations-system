from tests import test_advance_receipts as advances


class CashClosureTests(advances.AdvanceReceiptTests):
    def case(self, income='98683', received='98600'):
        from services import cash_closure_service
        self.closure = cash_closure_service
        project = self.project()
        self.projects.update_project(project, dict(self.projects.get_project(project),
                                                   cash_agreed_amount='100000'))
        settlement = self.contracts.create_settlement({'project_id': project,
            'amount': income, 'settlement_date': '2026-09-17'})
        receipt = self.finance.create_receipt({'project_id': project,
            'amount': received, 'receipt_date': '2026-09-17'})
        return project, settlement, receipt

    def test_close_and_revoke(self):
        project, settlement, receipt = self.case()
        original = self.finance.get_receipt(receipt)
        before_profit = self.profit.get_project_summary(project)
        preview = self.closure.preview(project)
        self.assertEqual(preview['reduction_minor'], 8300)
        close_id = self.closure.close_collection(project, '抹零', preview)
        self.assertEqual(self.finance.get_receipt(receipt), original)
        self.assertEqual(self.contracts.get_settlement(settlement)['amount_minor'], 9860000)
        self.assertEqual(self.profit.get_project_summary(project)['gross_profit_minor'],
                         before_profit['gross_profit_minor'] - 8300)
        self.assertEqual(self.finance.get_finance_dashboard(project)['projects'][0]['collection_status'], '已结清')
        self.closure.revoke(close_id)
        self.assertEqual(self.contracts.get_settlement(settlement)['amount_minor'], 9868300)
        self.assertEqual(self.finance.get_receipt(receipt), original)
        self.assertEqual(self.closure.history(project)[0]['status'], 'revoked')

    def test_duplicate_stale_and_receipt_guard(self):
        project, settlement, receipt = self.case()
        stale = self.closure.preview(project)
        self.finance.update_receipt(receipt, {'amount': '98500', 'receipt_date': '2026-09-17'})
        with self.assertRaisesRegex(ValueError, '变化'):
            self.closure.close_collection(project, '抹零', stale)
        self.closure.close_collection(project, '抹零', self.closure.preview(project))
        with self.assertRaises(ValueError):
            self.closure.preview(project)
        with self.assertRaisesRegex(ValueError, '撤销'):
            self.finance.void_receipts([receipt])
        with self.assertRaisesRegex(Exception, '撤销'):
            self.contracts.update_settlement(settlement, {'amount': '99000', 'settlement_date': '2026-09-17'})

    def test_wholly_unpaid_confirmation_is_reversible(self):
        project, first, receipt = self.case('1000', '1000')
        second = self.contracts.create_settlement({'project_id': project,
            'amount': '50', 'settlement_date': '2026-09-18'})
        closure = self.closure.close_collection(project, '优惠', self.closure.preview(project))
        self.assertIsNone(self.contracts.get_settlement(second))
        self.assertEqual(self.contracts.get_settlement(first)['amount_minor'], 100000)
        self.closure.revoke(closure)
        self.assertEqual(self.contracts.get_settlement(second)['amount_minor'], 5000)
