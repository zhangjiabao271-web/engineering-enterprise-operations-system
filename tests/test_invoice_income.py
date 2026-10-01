from uuid import uuid4

from tests import test_contract_pricing_modes as pricing_tests


class InvoiceIncomeTests(pricing_tests.ContractPricingServiceTests):
    def setup_case(self, mode='invoice'):
        project = self._project('自动收入')
        contract = self._contract('自动年度', pricing_mode='actual', income_mode=mode)
        self.contracts.create_allocation({'contract_id': contract, 'project_id': project})
        return {'project_id': project, 'contract_id': contract,
                'invoice_no': uuid4().hex, 'invoice_date': '2026-09-17', 'amount': '1000'}

    def test_lifecycle_and_no_duplicate(self):
        data = self.setup_case()
        invoice = self.finance.create_invoice(data)
        settlement = self.finance.get_invoice(invoice)['settlement_id']
        with self.assertRaises(ValueError):
            self.finance.create_invoice(data)
        self.finance.update_invoice(invoice, dict(data, amount='1500'))
        self.assertEqual(self.contracts.get_settlement(settlement)['amount_minor'], 150000)
        self.finance.void_invoices([invoice])
        self.assertIsNone(self.contracts.get_settlement(settlement))
        self.finance.update_invoice(invoice, data)
        self.assertEqual(self.finance.get_invoice(invoice)['settlement_id'], settlement)
        self.assertEqual(self.contracts.get_contract(data['contract_id'])['settled_minor'], 100000)

    def test_manual_guard_and_project_guard(self):
        data = self.setup_case()
        invoice = self.finance.create_invoice(data)
        settlement = self.finance.get_invoice(invoice)['settlement_id']
        confirmation = dict(data, settlement_date='2026-09-17')
        with self.assertRaisesRegex(ValueError, '自动'):
            self.contracts.create_settlement(confirmation)
        with self.assertRaisesRegex(ValueError, '自动'):
            self.contracts.update_settlement(settlement, confirmation)
        with self.assertRaisesRegex(ValueError, '自动'):
            self.contracts.void_settlements([settlement])
        with self.assertRaises(ValueError):
            self.finance.update_invoice(invoice, dict(data, project_id=self._project('其他')))

    def test_receipt_protects_income(self):
        data = self.setup_case()
        invoice = self.finance.create_invoice(data)
        self.finance.create_receipt(dict(data, receipt_date='2026-09-17', amount='600',
                                         payment_method='银行转账'))
        with self.assertRaisesRegex(ValueError, '回款'):
            self.finance.update_invoice(invoice, dict(data, amount='500'))
        with self.assertRaisesRegex(ValueError, '回款'):
            self.finance.void_invoices([invoice])
        self.finance.update_invoice(invoice, dict(data, amount='700'))
        self.assertEqual(self.contracts.get_contract(data['contract_id'])['settled_minor'], 70000)

    def test_adopt_preserves_existing_income(self):
        data = self.setup_case('manual')
        settlement = self.contracts.create_settlement(dict(data, settlement_date='2026-09-01'))
        invoice = self.finance.create_invoice(data)
        contract = self.contracts.get_contract(data['contract_id'])
        self.contracts.update_contract(contract['id'], dict(contract, income_mode='invoice'))
        self.assertEqual(self.finance.get_invoice(invoice)['settlement_id'], settlement)
        self.assertEqual(self.contracts.get_settlement(settlement)['settlement_date'], '2026-09-01')
        self.assertEqual(self.contracts.get_contract(contract['id'])['settled_minor'], 100000)

    def test_unmatched_income_blocks_switch(self):
        data = self.setup_case('manual')
        self.contracts.create_settlement(dict(data, settlement_date='2026-09-17'))
        contract = self.contracts.get_contract(data['contract_id'])
        with self.assertRaisesRegex(ValueError, '未对应'):
            self.contracts.update_contract(contract['id'], dict(contract, income_mode='invoice'))
        self.assertEqual(self.contracts.get_contract(contract['id'])['income_mode'], 'manual')

    def test_invoice_target_without_income(self):
        from pages.finance_page import ReceivablePage
        from unittest.mock import Mock
        data = self.setup_case()
        page = Mock()
        page.selected_project_id.return_value = data['project_id']
        targets = ReceivablePage._invoice_target_map(page)
        self.assertEqual(len(targets), 1)
        self.assertEqual(next(iter(targets.values()))['income_mode'], 'invoice')

    def test_multi_project_isolation(self):
        data = self.setup_case()
        second = self._project('年度合同第二项目')
        self.contracts.create_allocation({'contract_id': data['contract_id'], 'project_id': second})
        first_invoice = self.finance.create_invoice(data)
        second_invoice = self.finance.create_invoice(dict(data, project_id=second,
                                                          invoice_no=uuid4().hex, amount='800'))
        self.finance.update_invoice(first_invoice, dict(data, amount='1200'))
        self.assertEqual(self.contracts.list_settlements(project_id=second)[0]['amount_minor'], 80000)
        self.finance.void_invoices([first_invoice, second_invoice])
        self.assertEqual(self.contracts.get_contract(data['contract_id'])['settled_minor'], 0)

    def test_partial_adoption_rolls_back(self):
        from db.connection import get_connection
        data = self.setup_case('manual')
        self.contracts.create_settlement(dict(data, settlement_date='2026-09-17'))
        invoice = self.finance.create_invoice(data)
        self.contracts.create_settlement(dict(data, settlement_date='2026-09-17', amount='200'))
        contract = self.contracts.get_contract(data['contract_id'])
        with self.assertRaises(ValueError):
            self.contracts.update_contract(contract['id'], dict(contract, income_mode='invoice'))
        conn = get_connection()
        try:
            self.assertIsNone(conn.execute('SELECT * FROM invoice_income_links WHERE invoice_id=?',
                                           (invoice,)).fetchone())
        finally:
            conn.close()

    def test_duplicate_update_rolls_back_income(self):
        data = self.setup_case()
        invoice = self.finance.create_invoice(data)
        second = dict(data, invoice_no=uuid4().hex, amount='300')
        self.finance.create_invoice(second)
        with self.assertRaises(ValueError):
            self.finance.update_invoice(invoice, dict(data, invoice_no=second['invoice_no'], amount='400'))
        self.assertEqual(self.contracts.get_contract(data['contract_id'])['settled_minor'], 130000)

    def test_nonannual_and_fixed_cannot_enable(self):
        for extra in ({'contract_type': 'project', 'pricing_mode': 'actual'},
                      {'contract_type': 'annual', 'pricing_mode': 'fixed', 'amount': '1000'}):
            with self.assertRaisesRegex(ValueError, '年度框架'):
                self._contract('不符合自动收入条件', income_mode='invoice', **extra)

    def test_manual_mode_still_requires_confirmation(self):
        data = self.setup_case('manual')
        with self.assertRaisesRegex(ValueError, '先为该项目登记收入确认'):
            self.finance.create_invoice(data)

    def test_advance_receipt_then_invoice_confirms_income(self):
        data = self.setup_case()
        receipt = self.finance.create_receipt(dict(data, amount='1500', receipt_date='2026-09-16'))
        self.assertEqual(self.finance.get_receipt(receipt)['pending_allocation_minor'], 150000)
        self.finance.create_invoice(data)
        self.assertEqual(self.finance.get_receipt(receipt)['pending_allocation_minor'], 50000)
        self.assertEqual(self.contracts.get_contract(data['contract_id'])['settled_minor'], 100000)

    def test_receipt_mode_preserves_invoice_income_without_double_counting(self):
        data = self.setup_case()
        self.finance.create_invoice(dict(data, amount='228'))
        contract = self.contracts.get_contract(data['contract_id'])
        self.contracts.update_contract(contract['id'], dict(contract, income_mode='receipt'))
        self.assertEqual(self.contracts.get_contract(contract['id'])['income_mode'], 'receipt')
        receipt = self.finance.create_receipt(dict(data, amount='300', receipt_date='2026-09-30'))
        self.assertEqual(self.finance.get_receipt(receipt)['pending_allocation_minor'], 0)
        self.assertEqual(self.contracts.get_contract(contract['id'])['settled_minor'], 30000)
        invoice = dict(data, invoice_no=uuid4().hex, amount='72', invoice_date='2026-10-01')
        self.finance.create_invoice(invoice)
        with self.assertRaisesRegex(ValueError, '已经登记过'):
            self.finance.create_invoice(invoice)
        self.assertEqual(self.contracts.get_contract(contract['id'])['settled_minor'], 30000)

    def test_receipt_mode_confirmation_is_independent_of_receipt_corrections(self):
        data = self.setup_case('receipt')
        receipt = self.finance.create_receipt(dict(data, amount='300', receipt_date='2026-09-30'))
        self.finance.update_receipt(receipt, dict(data, amount='350', receipt_date='2026-09-30'))
        self.assertEqual(self.contracts.get_contract(data['contract_id'])['settled_minor'], 35000)
        self.assertEqual(self.finance.get_receipt(receipt)['pending_allocation_minor'], 0)
        self.finance.update_receipt(receipt, dict(data, amount='200', receipt_date='2026-09-30'))
        self.finance.void_receipts([receipt])
        self.assertEqual(self.contracts.get_contract(data['contract_id'])['settled_minor'], 35000)

    def test_receipt_mode_requires_confirmed_income_for_invoices_and_isolates_projects(self):
        data = self.setup_case('receipt')
        with self.assertRaisesRegex(ValueError, '先为该项目登记收入确认'):
            self.finance.create_invoice(data)
        self.finance.create_receipt(dict(data, receipt_date='2026-09-30'))
        self.finance.create_invoice(data)
        second = self._project('独立回款项目')
        self.contracts.create_allocation({'project_id': second, 'contract_id': data['contract_id']})
        receipt = self.finance.create_receipt(dict(data, project_id=second,
                                                  amount='50', receipt_date='2026-09-30'))
        self.assertEqual(self.finance.get_receipt(receipt)['pending_allocation_minor'], 0)
        self.assertEqual(self.contracts.list_settlements(project_id=second)[0]['amount_minor'], 5000)
        self.assertEqual(self.contracts.get_contract(data['contract_id'])['settled_minor'], 105000)

    def test_receipt_mode_rejects_unreviewed_historical_advances(self):
        data = self.setup_case()
        self.finance.create_receipt(dict(data, receipt_date='2026-09-30'))
        contract = self.contracts.get_contract(data['contract_id'])
        with self.assertRaisesRegex(ValueError, '预收或待分配'):
            self.contracts.update_contract(contract['id'], dict(contract, income_mode='receipt'))
        self.assertEqual(self.contracts.get_contract(contract['id'])['income_mode'], 'invoice')

    def test_duplicate_receipt_rolls_back_confirmation(self):
        data = self.setup_case('receipt')
        receipt_data = dict(data, receipt_no=uuid4().hex, receipt_date='2026-09-30')
        self.finance.create_receipt(receipt_data)
        with self.assertRaisesRegex(ValueError, '回款单号已经存在'):
            self.finance.create_receipt(dict(receipt_data, amount='2000'))
        self.assertEqual(self.contracts.get_contract(data['contract_id'])['settled_minor'], 100000)

    def test_receipt_mode_preview_and_invoice_target_explain_effective_policy(self):
        from pages.finance_page import ReceivablePage
        from unittest.mock import Mock
        data = self.setup_case('receipt')
        preview = self.finance.preview_receipt_allocations(
            dict(data, receipt_date='2026-09-30'))
        self.assertEqual(preview[0]['settlement_no'], '保存后同步补足实际结算')
        self.finance.create_receipt(dict(data, receipt_date='2026-09-30'))
        page = Mock()
        page.selected_project_id.return_value = data['project_id']
        targets = ReceivablePage._invoice_target_map(page)
        self.assertEqual(next(iter(targets.values()))['income_mode'], 'receipt')
