from uuid import uuid4
from tests import test_cash_projects as cash_tests


class AdvanceReceiptTests(cash_tests.CashProjectTests):
    def project(self):
        return self.projects.create_project({'name': '预收测试-' + uuid4().hex[:8],
            'business_mode': 'cash', 'invoice_policy': 'not_required',
            'cash_agreed_amount': '86000', 'status': '进行中'})

    def test_advance_then_income_and_edit(self):
        project = self.project()
        data = {'project_id': project, 'amount': '40000', 'receipt_date': '2026-09-17'}
        receipt = self.finance.create_receipt(data)
        self.assertEqual(self.contracts.list_settlements(project_id=project), [])
        self.assertEqual(self.finance.get_receipt(receipt)['pending_allocation_minor'], 4000000)
        settlement = self.contracts.create_settlement({'project_id': project,
            'amount': '30000', 'settlement_date': '2026-09-18'})
        self.assertEqual(self.finance.get_receipt(receipt)['pending_allocation_minor'], 1000000)
        self.assertEqual(self.contracts.get_settlement(settlement)['received_minor'], 3000000)
        self.finance.update_receipt(receipt, dict(data, amount='45000'))
        self.assertEqual(self.finance.get_receipt(receipt)['pending_allocation_minor'], 1500000)
        self.contracts.update_settlement(settlement, {'amount': '86000',
            'settlement_date': '2026-09-18'})
        self.assertEqual(self.finance.get_receipt(receipt)['pending_allocation_minor'], 0)
        self.assertEqual(self.contracts.get_settlement(settlement)['unreceived_minor'], 4100000)

    def test_fifo_void_and_isolation(self):
        project = self.project()
        other = self.project()
        first = self.finance.create_receipt({'project_id': project, 'amount': '100',
                                            'receipt_date': '2026-09-01'})
        second = self.finance.create_receipt({'project_id': project, 'amount': '200',
                                             'receipt_date': '2026-09-02'})
        self.contracts.create_settlement({'project_id': other, 'amount': '1000',
                                         'settlement_date': '2026-09-17'})
        self.assertEqual(self.finance.get_receipt(first)['pending_allocation_minor'], 10000)
        self.contracts.create_settlement({'project_id': project, 'amount': '150',
                                         'settlement_date': '2026-09-17'})
        self.assertEqual(self.finance.get_receipt(second)['pending_allocation_minor'], 15000)
        self.finance.void_receipts([first])
        self.assertEqual(self.finance.get_receipt(second)['pending_allocation_minor'], 5000)
