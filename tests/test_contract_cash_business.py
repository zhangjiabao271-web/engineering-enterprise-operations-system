"""Contract/cash business reconciliation on isolated synthetic ledgers only."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from db import connection, migration_runner
from db.schema import init_db
from services import contract_ledger_service as ledger
from services import contract_service, master_data_service, project_service


class ContractCashBusinessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='contract_cash_business_')
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / 'synthetic.db'
        for module in (connection, migration_runner):
            override = patch.object(module, 'DB_PATH', self.database)
            override.start()
            self.addCleanup(override.stop)
        init_db()
        self.customer = master_data_service.create_business_partner(
            {'legal_name': '合成现金业务客户', 'roles': ['customer']})

    def project(self, name='合成现金工程', *, mode='cash', agreed=None, entity=1, customer=None):
        identity = project_service.create_project(dict(name=name, status='进行中', business_mode=mode,
            customer_partner_id=customer or self.customer, cash_agreed_amount=agreed))
        if entity is not None:
            self.assign('projects', identity, entity)
        return identity

    def assign(self, kind, identity, entity):
        with connection.db_transaction() as conn:
            conn.execute('''INSERT OR REPLACE INTO record_entities
                (record_type,record_id,entity_id,source,updated_at) VALUES (?,?,?,'合成测试','2026-10-19')''',
                (kind, identity, entity))

    def insert(self, table, **fields):
        with connection.db_transaction() as conn:
            columns = ','.join(fields)
            marks = ','.join('?' for _ in fields)
            return conn.execute(f'INSERT INTO {table} ({columns}) VALUES ({marks})', tuple(fields.values())).lastrowid

    def income(self, project, amount, contract=None, *, status='active', entity=None):
        token = uuid4().hex
        identity = self.insert('settlements', public_id=token, organization_id=1, settlement_no=token,
            contract_id=contract, project_id=project, settlement_date='2026-10-01', amount_minor=amount,
            basis='合成客户确认', source_type='cash_job', status=status,
            created_at='2026-10-01', updated_at='2026-10-01')
        if entity is not None:
            self.assign('settlements', identity, entity)
        return identity

    def receipt(self, parts, *, status='active', entity=None):
        """parts: project, settlement, contract, cents[, allocation status]."""
        token = uuid4().hex
        identity = self.insert('receipts', public_id=token, organization_id=1, receipt_no=token,
            receipt_date='2026-10-02', amount_minor=sum(part[3] for part in parts), status=status,
            created_at='2026-10-02', updated_at='2026-10-02')
        for part in parts:
            project, settlement, contract, amount = part[:4]
            self.insert('receipt_allocations', public_id=uuid4().hex, receipt_id=identity,
                project_id=project, settlement_id=settlement, contract_id=contract,
                allocated_amount_minor=amount, status=part[4] if len(part) > 4 else 'active',
                created_at='2026-10-02')
        if entity is not None:
            self.assign('receipts', identity, entity)
        return identity

    def contract(self, project=None, *, year='2025', entity=1):
        return contract_service.create_contract(dict(name='合成零额据实合同', customer_partner_id=self.customer,
            project_id=project, amount='0', pricing_mode='actual', contract_type='annual',
            sign_date=year+'-01-01', status='active', entity_id=entity))

    def invoice(self, project, amount, contract=None, links=(), status='active', entity=None):
        token = uuid4().hex
        identity = self.insert('sales_invoices', public_id=token, organization_id=1, invoice_no=token,
            project_id=project, contract_id=contract, invoice_date='2026-10-03', amount_minor=amount,
            status=status, created_at='2026-10-03', updated_at='2026-10-03')
        for settlement, share in links:
            self.insert('invoice_settlement_allocations', public_id=uuid4().hex, invoice_id=identity,
                settlement_id=settlement, allocated_amount_minor=share,
                created_at='2026-10-03', updated_at='2026-10-03')
        if entity is not None:
            self.assign('sales_invoices', identity, entity)
        return identity

    def assert_details(self, details, expected=None):
        if expected is not None:
            self.assertEqual(details['summary'], expected)
        definitions = {'settlements': 'settlement_minor', 'receipts': 'receipt_minor',
                       'invoices': 'invoice_minor', 'outstanding': 'receivable_minor'}
        for kind, total_key in definitions.items():
            item = details['details'][kind]
            self.assertTrue(item['reconciled'], kind)
            self.assertEqual(sum(row[item['amount_key']] for row in item['rows']), details['summary'][total_key])
            self.assertEqual(item['total_minor'], item['detail_total_minor'])
        self.assertEqual(details['details']['receipts']['amount_key'], 'contract_receipt_minor')
        self.assertEqual(details['details']['invoices']['amount_key'], 'contract_invoice_minor')

    def assert_selection(self, **filters):
        overview = ledger.get_overview(**filters)
        self.assert_details(ledger.get_business_details(**filters), overview['summary'])
        for key in ledger.MONEY_KEYS:
            self.assertEqual(overview['summary'][key],
                overview['contract_summary'][key] + overview['project_business_summary'][key])
            self.assertEqual(overview['summary'][key], sum(row[key] for row in overview['customers']))
        return overview

    def test_three_cash_jobs_confirmed_income_and_advance_not_agreed_revenue(self):
        first = self.project('合成现金甲', agreed='78000')
        second = self.project('合成现金乙', agreed='97100')
        advance = self.project('合成前丁', agreed='86000')
        a = self.income(first, 7_800_000)
        b = self.income(second, 9_710_000)
        self.receipt([(first, a, None, 7_800_000)])
        self.receipt([(second, b, None, 9_710_000)])
        self.receipt([(advance, None, None, 4_000_000)])
        data = self.assert_selection()
        expected = dict(settlement_minor=17_510_000, receipt_minor=21_510_000,
                        invoice_minor=0, receivable_minor=0)
        self.assertEqual(data['business_scope'], 'all')
        self.assertEqual(data['summary'], expected)
        self.assertEqual(data['contracts'], [])
        self.assertEqual((data['contract_count'], data['project_business_count']), (0, 3))
        customer = data['customers'][0]
        self.assertEqual((customer['contract_count'], customer['cash_project_count']), (0, 3))
        self.assertEqual(customer['project_business_count'], 3)
        self.assertEqual(customer['contract_followup_count'], 0)
        self.assertEqual(customer['project_business_followup_count'], 3)
        self.assertEqual(customer['followup_count'], 3)
        row = next(row for row in data['project_business'] if row['project_id'] == advance)
        self.assertEqual((row['settlement_minor'], row['pending_receipt_minor']), (0, 4_000_000))
        self.assertEqual(row['project_status'], '进行中')
        self.assert_details(ledger.get_project_business_details(advance, scope_project_id=advance))
        selected = self.assert_selection(customer_key=f'customer:{self.customer}')
        self.assertEqual(selected['customer_name'], '合成现金业务客户')
        self.assertNotIn('details', data)
        self.assertNotIn('rows', row)

    def test_agreed_only_project_visible_without_income_or_receipt(self):
        project = self.project(agreed='86000')
        data = self.assert_selection(business_scope='uncontracted')
        self.assertEqual(data['project_business_count'], 1)
        self.assertEqual(data['summary'], dict.fromkeys(ledger.MONEY_KEYS, 0))
        self.assert_details(ledger.get_project_business_details(project))

    def test_latest_business_date_ignores_project_edits_unless_no_active_facts(self):
        income_project = self.project('合成仅收入')
        receipt_project = self.project('合成仅预收')
        invoice_project = self.project('合成仅发票')
        empty_project = self.project('合成仅约定', agreed='100')
        self.income(income_project, 10000)
        self.receipt([(receipt_project, None, None, 5000)])
        self.invoice(invoice_project, 3000)
        self.income(empty_project, 2000, status='void')
        self.receipt([(income_project, None, None, 1000)], status='void')
        self.invoice(income_project, 2000, status='void')
        with connection.db_transaction() as conn:
            conn.execute("UPDATE projects SET updated_at='2026-10-19T15:00:00'")
            conn.execute("UPDATE receipts SET receipt_date='2026-10-18' WHERE status='void'")
            conn.execute("UPDATE sales_invoices SET invoice_date='2026-10-18' WHERE status='void'")
        expected = {income_project: '2026-10-01', receipt_project: '2026-10-02',
                    invoice_project: '2026-10-03', empty_project: '2026-10-19'}
        data = self.assert_selection()
        self.assertEqual({row['project_id']: row['latest_date'] for row in data['project_business']}, expected)
        for project, day in expected.items():
            with self.subTest(project=project):
                self.assertEqual(ledger.get_project_business_details(project)['project']['latest_date'], day)

    def test_unsigned_contract_mode_project_is_business_without_a_signing_year(self):
        project = self.project('合成未签业务', mode='contract')
        self.income(project, 10001)
        data = self.assert_selection(business_scope='uncontracted', project_id=str(project))
        row = data['project_business'][0]
        self.assertEqual(row['business_mode'], 'contract')
        self.assertNotIn('sign_year', row)
        self.assertEqual(data['years'], [])
        self.assertEqual(data['cash_project_count'], 0)
        self.assertEqual(data['summary']['settlement_minor'], 10001)
        self.assertEqual(self.assert_selection(customer_key=None)['summary'], data['summary'])

    def test_large_integer_cents_do_not_pass_through_floats(self):
        project = self.project()
        amount = 9_007_199_254_740_993
        income = self.income(project, amount)
        self.receipt([(project, income, None, amount - 1)])
        self.invoice(project, amount - 2)
        data = self.assert_selection()
        self.assertEqual(data['summary'], dict(settlement_minor=amount, receipt_minor=amount - 1,
            invoice_minor=amount - 2, receivable_minor=1))

    def test_contract_and_cash_mix_does_not_assign_same_customer_income_to_contract(self):
        project = self.project(agreed='1000')
        contract = self.contract(project)
        signed = self.income(project, 20001, contract)
        cash = self.income(project, 30002)
        self.receipt([(project, signed, contract, 10001), (project, cash, None, 10002),
                      (project, None, None, 5000)])
        data = self.assert_selection()
        self.assertEqual(data['contracts'][0]['tax_inclusive_amount_minor'], 0)
        self.assertEqual(data['contracts'][0]['settlement_minor'], 20001)
        self.assertEqual(data['project_business'][0]['settlement_minor'], 30002)
        self.assertEqual(data['summary']['receivable_minor'], 30000)
        self.assertEqual(data['summary']['receipt_minor'], 25003)
        self.assert_details(ledger.get_details(contract), data['contract_summary'])
        detail = ledger.get_project_business_details(project)
        self.assert_details(detail, data['project_business_summary'])
        self.assertEqual([row['id'] for row in detail['details']['settlements']['rows']], [cash])
        for item in detail['details'].values():
            self.assertTrue(all(row['scope_label'] == '本项目未关联合同部分' for row in item['rows']))
        customer = data['customers'][0]
        self.assertEqual(customer['followup_count'], 2)
        self.assertEqual(customer['contract_followup_count'], 1)
        self.assertEqual(customer['project_business_followup_count'], 1)
        labels = {row['scope_label'] for row in ledger.get_business_details()['details']['receipts']['rows']}
        self.assertEqual(labels, {'本合同归属份额', '未关联合同项目业务'})
        self.assert_selection(business_scope='contracts')
        self.assert_selection(business_scope='uncontracted')

    def test_all_income_on_valid_contract_never_becomes_virtual_cash_income(self):
        project = self.project(agreed='100')
        contract = self.contract(project)
        self.income(project, 10000, contract)
        data = self.assert_selection()
        self.assertEqual(data['project_business'], [])
        self.assertEqual(data['summary']['settlement_minor'], 10000)

    def test_receipt_split_by_project_only_for_uncontracted_shares(self):
        first, second = self.project('合成甲'), self.project('合成乙')
        a, b = self.income(first, 10001), self.income(second, 20002)
        receipt = self.receipt([(first, a, None, 5001), (second, b, None, 10002),
                                (second, None, None, 30000)])
        data = self.assert_selection()
        self.assertEqual(data['summary']['receipt_minor'], 45003)
        self.assertEqual(data['summary']['receivable_minor'], 15000)
        for pid, expected in ((first, 5001), (second, 40002)):
            scoped = self.assert_selection(project_id=pid)
            self.assertEqual(scoped['summary']['receipt_minor'], expected)
            detail = ledger.get_project_business_details(pid)
            self.assert_details(detail, scoped['summary'])
            self.assertEqual([row['id'] for row in detail['details']['receipts']['rows']], [receipt])
        with self.assertRaisesRegex(ValueError, '当前范围'):
            ledger.get_project_business_details(first, scope_project_id=second)

    def test_real_contract_receipt_is_one_row_even_across_projects(self):
        first, second = self.project('合成甲'), self.project('合成乙')
        contract = self.contract(first)
        a, b = self.income(first, 10001, contract), self.income(second, 10002, contract)
        receipt = self.receipt([(first, a, contract, 5001), (second, b, contract, 5002)])
        data = ledger.get_details(contract)
        self.assert_details(data)
        self.assertEqual([row['id'] for row in data['details']['receipts']['rows']], [receipt])
        self.assertEqual(data['summary']['receipt_minor'], 10003)
        self.assertEqual(ledger.get_details(contract, project_id=second)['summary']['receipt_minor'], 5002)

    def test_invoice_partial_contract_shares_uncontracted_and_unlinked_remainder_conserve(self):
        project = self.project()
        first, second = self.contract(project), self.contract(project, year='2026')
        a, b, cash = self.income(project, 10000, first), self.income(project, 10000, second), self.income(project, 10000)
        split = self.invoice(project, 20003, links=[(a, 4001), (b, 4002), (cash, 3000)])
        self.invoice(project, 5001)  # No allocation rows is still an invoice fact.
        direct = self.invoice(project, 6001, first, links=[(a, 3000), (cash, 3001)])
        data = self.assert_selection()
        self.assertEqual(data['summary']['invoice_minor'], 31005)
        self.assertEqual(ledger.get_details(first)['summary']['invoice_minor'], 10002)
        self.assertEqual(ledger.get_details(second)['summary']['invoice_minor'], 4002)
        self.assertEqual(data['project_business_summary']['invoice_minor'], 17001)
        rows = ledger.get_business_details()['details']['invoices']['rows']
        self.assertEqual(sum(row['contract_invoice_minor'] for row in rows if row['id'] == split), 20003)
        self.assertEqual(sum(row['contract_invoice_minor'] for row in rows if row['id'] == direct), 6001)
        self.assert_details(ledger.get_project_business_details(project), data['project_business_summary'])

    def test_void_facts_excluded_but_active_receipt_to_void_income_is_visible_for_review(self):
        project = self.project()
        live = self.income(project, 10000)
        void = self.income(project, 90000, status='void')
        review = self.receipt([(project, void, None, 12000)])
        self.receipt([(project, live, None, 1000)], status='void')
        self.receipt([(project, live, None, 2000, 'void')])
        self.invoice(project, 99999, status='void')
        self.invoice(project, 5000, links=[(void, 5000)])
        data = self.assert_selection()
        self.assertEqual(data['summary'], dict(settlement_minor=10000, receipt_minor=12000,
            invoice_minor=5000, receivable_minor=10000))
        row = data['project_business'][0]
        self.assertTrue(row['needs_review'])
        self.assertEqual(row['review_receipt_minor'], 12000)
        self.assertEqual(row['pending_receipt_minor'], 12000)
        details = ledger.get_project_business_details(project)
        self.assert_details(details)
        self.assertEqual(details['details']['receipts']['rows'][0]['id'], review)

    def test_void_income_does_not_infer_a_contract_from_its_old_link(self):
        project = self.project()
        contract = self.contract(project)
        void = self.income(project, 90000, contract, status='void')
        self.receipt([(project, void, contract, 1000), (project, void, None, 2000)])
        data = self.assert_selection()
        self.assertEqual(data['summary']['receipt_minor'], 3000)
        self.assertEqual(data['contract_summary']['receipt_minor'], 1000)
        self.assertEqual(data['project_business_summary']['receipt_minor'], 2000)
        self.assertEqual(data['summary']['settlement_minor'], 0)
        self.assert_details(ledger.get_details(contract, project_id=str(project)), data['contract_summary'])
        self.assert_details(ledger.get_project_business_details(project), data['project_business_summary'])

    def test_entity_conflicting_receipt_is_arrival_only_not_income_offset(self):
        project = self.project(entity=1)
        income = self.income(project, 10000)
        self.receipt([(project, income, None, 4000)], entity=2)
        first = self.assert_selection(entity_id=1)
        second = self.assert_selection(entity_id=2)
        self.assertEqual(first['summary']['receivable_minor'], 10000)
        self.assertEqual(second['summary']['receipt_minor'], 4000)
        self.assertEqual(second['project_business'][0]['review_receipt_minor'], 4000)
        self.assertEqual(ledger.get_overview()['summary']['receivable_minor'], 10000)

    def test_invalid_invoice_split_preserves_face_amount_for_review(self):
        project = self.project()
        contract = self.contract(project)
        income = self.income(project, 10000, contract)
        self.invoice(project, 1000, links=[(income, 2000)])
        data = self.assert_selection()
        self.assertEqual(data['summary']['invoice_minor'], 1000)
        self.assertEqual(data['contract_summary']['invoice_minor'], 0)
        self.assertTrue(data['project_business'][0]['needs_review'])

    def test_project_or_contract_conflicting_receipt_does_not_offset_income(self):
        first, second = self.project('合成甲'), self.project('合成乙')
        contract = self.contract(first)
        income = self.income(first, 10000, contract)
        self.receipt([(second, income, None, 3000)])
        self.receipt([(first, income, self.contract(first, year='2026'), 4000)])
        data = self.assert_selection()
        self.assertEqual(data['summary']['receipt_minor'], 7000)
        self.assertEqual(data['summary']['receivable_minor'], 10000)
        self.assertTrue(all(row['needs_review'] for row in ledger.get_business_details()['details']['receipts']['rows']))

    def test_void_contract_facts_are_business_not_a_hidden_or_guessed_contract(self):
        project = self.project()
        contract = self.contract(project)
        self.income(project, 10001, contract)
        self.invoice(project, 20002, contract)
        with connection.db_transaction() as conn:
            conn.execute("UPDATE contracts SET status='void' WHERE id=?", (contract,))
        data = self.assert_selection()
        self.assertEqual(data['contracts'], [])
        self.assertEqual(data['project_business_summary']['settlement_minor'], 10001)
        self.assertEqual(data['project_business_summary']['invoice_minor'], 20002)

    def test_legacy_year_status_scope_and_validation(self):
        project = self.project()
        contract = self.contract(project, year='2023')
        self.income(project, 2000)
        income = self.income(project, 10000, contract)
        self.receipt([(project, income, contract, 3000)])
        for filters in ({'year': '2023'}, {'status': 'active'}, {'business_scope': 'contracts', 'year': '2023'}):
            data = self.assert_selection(**filters)
            self.assertEqual(data['business_scope'], 'contracts')
            self.assertEqual(data['summary']['receipt_minor'], 3000)
            self.assertEqual(data['summary']['settlement_minor'], 10000)
        self.assertEqual(ledger.get_overview(year='2026')['summary']['settlement_minor'], 0)
        for scope in ('all', 'uncontracted'):
            for filters in ({'year': '2026'}, {'status': 'active'}):
                with self.assertRaisesRegex(ValueError, '仅适用于正式合同'):
                    ledger.get_overview(business_scope=scope, **filters)
        for filters in ({'business_scope': 'bad'}, {'project_id': -1}, {'entity_id': 'bad'}, {'year': '2026-01'}):
            with self.assertRaises(ValueError):
                ledger.get_business_details(**filters)

    def test_entity_customer_keyword_and_project_filters_use_exact_fact_ownership(self):
        first = self.project('合成主体甲', entity=1)
        second = self.project('合成主体乙', entity=2)
        unknown = self.project('合成待确认', entity=None)
        self.income(first, 10001)
        self.income(first, 20002, entity=2)
        self.income(second, 30003)
        self.income(unknown, 40004)
        self.invoice(first, 5000, entity=2)
        for entity, amount in ((1, 10001), (2, 50005), ('unknown', 40004)):
            data = self.assert_selection(entity_id=entity)
            self.assertEqual(data['summary']['settlement_minor'], amount)
            self.assertTrue(all(str(row['entity_id'] or 'unknown') == str(entity) for row in data['project_business']))
        data = self.assert_selection(entity_id=2, project_id=first, keyword='主体甲')
        self.assertEqual(data['summary']['settlement_minor'], 20002)
        self.assertEqual(data['summary']['invoice_minor'], 5000)
        self.assertEqual(self.assert_selection(customer_key='customer:999')['summary']['settlement_minor'], 0)
        self.assertEqual(self.assert_selection(keyword='合成主体乙')['summary']['settlement_minor'], 30003)
        mixed = next(row for row in ledger.get_overview()['project_business'] if row['project_id'] == first)
        self.assertIsNone(mixed['entity_id'])
        self.assertEqual(mixed['ownership_status'], 'conflict')

    def test_all_entity_selects_contract_fact_shares_instead_of_header_owner(self):
        project = self.project()
        contract = self.contract(project, entity=1)
        header_only = self.contract(entity=1)
        other_owner = self.income(project, 10000, contract, entity=2)
        self.income(project, 3000, contract)
        self.income(project, 2000, entity=2)
        self.receipt([(project, other_owner, contract, 4000)], entity=2)
        self.invoice(project, 5000, contract, entity=2)
        data = self.assert_selection(business_scope='all', entity_id=2)
        self.assertEqual([row['id'] for row in data['contracts']], [contract])
        self.assertEqual(data['contracts'][0]['entity_id'], 1)
        self.assertEqual(data['summary'], dict(settlement_minor=12000, receipt_minor=4000,
            invoice_minor=5000, receivable_minor=8000))
        self.assertEqual(data['contract_summary']['settlement_minor'], 10000)
        own = self.assert_selection(business_scope='all', entity_id=1)
        self.assertEqual(own['summary']['settlement_minor'], 3000)
        self.assertIn(header_only, [row['id'] for row in own['contracts']])
        legacy = self.assert_selection(business_scope='contracts', entity_id=1)
        self.assertEqual(legacy['summary']['settlement_minor'], 13000)
        self.assertEqual(legacy['summary']['receipt_minor'], 4000)
        self.assertEqual(self.assert_selection(business_scope='contracts', entity_id=2)['contracts'], [])

    def test_project_candidates_include_explicit_receipt_and_invoice_after_link_voided(self):
        project = self.project()
        receipt_contract = self.contract(project)
        invoice_contract = self.contract(project)
        inactive_contract = self.contract(project)
        self.receipt([(project, None, receipt_contract, 4000)])
        self.invoice(project, 3500, invoice_contract)
        self.receipt([(project, None, inactive_contract, 9000)], status='void')
        self.invoice(project, 8000, inactive_contract, status='void')
        with connection.db_transaction() as conn:
            conn.execute("UPDATE contract_project_allocations SET status='void' WHERE project_id=?", (project,))
        for scope in ('all', 'contracts'):
            with self.subTest(scope=scope):
                data = self.assert_selection(project_id=project, business_scope=scope)
                self.assertEqual({row['id'] for row in data['contracts']}, {receipt_contract, invoice_contract})
                self.assertEqual(data['summary']['receipt_minor'], 4000)
                self.assertEqual(data['summary']['invoice_minor'], 3500)
                self.assertEqual(data['project_business'], [])
        self.assertEqual(ledger.get_details(receipt_contract, project_id=project)['summary']['receipt_minor'], 4000)
        self.assertEqual(ledger.get_details(invoice_contract, project_id=project)['summary']['invoice_minor'], 3500)
        other_project = self.project('合成不相关工程')
        self.assertEqual(self.assert_selection(project_id=other_project)['contract_count'], 0)

    def test_project_business_detail_entity_filter_matches_overview_and_keeps_scope_guard(self):
        project = self.project(entity=None)
        first = self.income(project, 3101, entity=1)
        second = self.income(project, 6202, entity=2)
        self.receipt([(project, first, None, 1001)], entity=1)
        self.receipt([(project, second, None, 2002), (project, None, None, 500)], entity=2)
        self.invoice(project, 100, entity=1)
        self.invoice(project, 200, entity=2)
        for entity, income, receipt in ((1, 3101, 1001), (2, 6202, 2502)):
            with self.subTest(entity=entity):
                data = self.assert_selection(project_id=project, entity_id=entity, business_scope='uncontracted')
                details = ledger.get_project_business_details(project, scope_project_id=project, entity_id=entity)
                self.assert_details(details, data['summary'])
                self.assertEqual(details['summary']['settlement_minor'], income)
                self.assertEqual(details['summary']['receipt_minor'], receipt)
                self.assertEqual(details['project']['entity_id'], entity)
                for item in details['details'].values():
                    self.assertTrue(all(row['entity_id'] == entity for row in item['rows']))
        with self.assertRaisesRegex(ValueError, '当前范围'):
            ledger.get_project_business_details(project, scope_project_id=project + 1, entity_id=2)
        with self.assertRaisesRegex(ValueError, '经营主体无效'):
            ledger.get_project_business_details(project, entity_id='bad')

    def test_project_keyword_includes_real_contract_and_uncontracted_income(self):
        project = self.project('合成独有项目检索名')
        contract = self.contract(project)
        self.income(project, 10000, contract)
        self.income(project, 2000)
        with connection.db_transaction() as conn:
            conn.execute("UPDATE projects SET project_code='EXPLICIT-PROJECT-KEY' WHERE id=?", (project,))
        for keyword in ('独有项目检索名', 'explicit-project-key'):
            with self.subTest(keyword=keyword):
                data = self.assert_selection(keyword=keyword)
                self.assertEqual(data['summary']['settlement_minor'], 12000)
                self.assertEqual([row['id'] for row in data['contracts']], [contract])
                self.assertEqual(data['contracts'][0]['project_names'], ['合成独有项目检索名'])
                self.assertEqual(data['contracts'][0]['project_codes'], ['EXPLICIT-PROJECT-KEY'])
                self.assertEqual(self.assert_selection(keyword=keyword, business_scope='contracts')['summary']['settlement_minor'], 10000)
                self.assertEqual(self.assert_selection(keyword=keyword, business_scope='uncontracted')['summary']['settlement_minor'], 2000)
        # The existing customer name is not an implicit project/contract link.
        unrelated = self.contract()
        self.assertNotIn(unrelated, [row['id'] for row in ledger.get_overview(keyword='独有项目检索名')['contracts']])

    def test_contract_details_entity_share_matches_business_row_without_changing_metadata(self):
        project = self.project()
        contract = self.contract(project, entity=1)
        income = self.income(project, 10000, contract, entity=2)
        self.income(project, 3000, contract)
        self.income(project, 500, entity=2)
        self.receipt([(project, income, contract, 2000)], entity=2)
        self.invoice(project, 4000, contract, entity=2)
        for entity, expected in ((1, dict(settlement_minor=3000, receipt_minor=0, invoice_minor=0, receivable_minor=3000)),
                                 (2, dict(settlement_minor=10000, receipt_minor=2000, invoice_minor=4000, receivable_minor=8000))):
            with self.subTest(entity=entity):
                data = self.assert_selection(project_id=project, business_scope='all', entity_id=entity)
                details = ledger.get_details(contract, project_id=project, entity_id=entity, business_scope='all')
                self.assert_details(details, expected)
                self.assertEqual(details['summary'], data['contract_summary'])
                self.assertEqual(details['contract']['entity_id'], 1)
                self.assertEqual(details['contract']['tax_inclusive_amount_minor'], 0)
                self.assertEqual(details['entity_scope'], str(entity))
                self.assertEqual(details['entity_basis'], 'fact_owner')
        legacy = ledger.get_details(contract, project_id=project)
        self.assert_details(legacy, dict(settlement_minor=13000, receipt_minor=2000,
            invoice_minor=4000, receivable_minor=11000))
        self.assertEqual(legacy['entity_basis'], 'contract_owner')
        self.assertEqual(ledger.get_details(contract, project_id=project, entity_id=1)['summary'], legacy['summary'])
        with self.assertRaisesRegex(ValueError, '当前范围'):
            ledger.get_details(contract, project_id=project, entity_id=2)
        with self.assertRaisesRegex(ValueError, '当前范围'):
            ledger.get_details(contract, project_id=project + 1, entity_id=2, business_scope='all')

    def test_unlinked_names_do_not_merge_projects_or_contracts(self):
        first, second = self.project('同名工程甲'), self.project('同名工程乙')
        contract = self.contract(first)
        self.income(first, 1000)
        self.income(second, 2000)
        with connection.db_transaction() as conn:
            conn.execute("UPDATE projects SET customer_partner_id=NULL,customer_name='同名客户'")
            conn.execute("UPDATE contracts SET customer_partner_id=NULL,customer_name_snapshot='同名客户' WHERE id=?", (contract,))
        data = self.assert_selection()
        self.assertEqual(data['customer_count'], 3)
        keys = {row['customer_key'] for row in data['customers']}
        self.assertEqual(len(keys), 3)
        for key in keys:
            self.assert_selection(customer_key=key)

    def test_independent_old_debt_not_counted_and_all_queries_are_read_only(self):
        project = self.project()
        contract = self.contract(project)
        self.income(project, 12345)
        old = self.insert('historical_receivable_projects', customer_id=self.customer, name='独立旧账',
            opening_minor=900000, created_at='2026-01-01', updated_at='2026-01-01')
        self.insert('historical_receipts', project_id=old, request_key=uuid4().hex, receipt_date='2026-10-01',
            amount_minor=800000, created_at='2026-10-01', updated_at='2026-10-01')
        with connection.db_read() as conn:
            before = list(conn.iterdump())
        self.assertEqual(self.assert_selection()['summary']['settlement_minor'], 12345)
        self.assert_details(ledger.get_details(contract))
        self.assert_details(ledger.get_project_business_details(project))
        with connection.db_read() as conn:
            self.assertEqual(before, list(conn.iterdump()))
            with connection.use_read_snapshot(conn):
                self.assertEqual(conn.execute('PRAGMA query_only').fetchone()[0], 1)
                self.assert_selection(business_scope='all')


if __name__ == '__main__':
    unittest.main()
