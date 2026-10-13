import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class AssistantEntryTests(unittest.TestCase):
    def setUp(self):
        from db import connection,migration_runner
        from db.schema import init_db
        from services import assistant_entry_service,web_workbench_service
        from web_workbench.synthetic import seed
        temp=tempfile.TemporaryDirectory(prefix='assistant_entries_')
        self.addCleanup(temp.cleanup)
        for module in (connection,migration_runner):
            override=patch.object(module,'DB_PATH',Path(temp.name)/'synthetic.db')
            override.start();self.addCleanup(override.stop)
        init_db();seed();self.entries=assistant_entry_service;self.business=web_workbench_service

    def records(self):
        return [
            {'module':'costs','data':{'project':'厂房钢结构','date':'2026-10-13','amount':'470',
                'category':'管理与财务 / 业务招待','description':'买烟','paid':True}},
            {'module':'purchases','data':{'project':'厂房钢结构','supplier':'钢材供应商','date':'2026-10-13',
                'payment_status':'已付款','payment_method':'现金','invoice_status':'有发票',
                'items':[{'name':'油漆','price_basis':'inclusive','tax_percent':'1','total_amount':'3650'}]}},
        ]

    def facts(self):
        from db.connection import db_read
        from services.web_workbench_operations import database_fingerprint
        with db_read() as conn:return database_fingerprint(conn)

    def test_preview_does_not_write_facts_and_explicit_approval_saves_once(self):
        before=self.facts();draft=self.entries.preview(self.records(),request_key='synthetic-entry-once')
        self.assertTrue(draft['can_apply'],draft['questions']);self.assertEqual(self.facts(),before)
        with self.assertRaisesRegex(ValueError,'明确确认'):self.entries.apply(draft['token'])
        saved=self.entries.apply(draft['token'],confirmed=True)
        self.assertEqual(len(saved),2)
        after=self.facts();self.assertNotEqual(after,before)
        self.assertEqual(self.entries.apply(draft['token'],confirmed=True),saved)
        self.assertEqual(self.facts(),after)
        order=self.business.get_record('purchases',saved[1]['id'])
        self.assertEqual(order['total_amount_cents'],365000)
        self.assertEqual(order['items'][0]['tax_amount_cents'],3614)

    def test_missing_fields_are_combined_and_never_defaulted(self):
        records=self.records();records[0]['data']['date']=None
        records[1]['data']['items'][0]['tax_percent']=None
        before=self.facts();draft=self.entries.preview(records,request_key='synthetic-entry-missing')
        self.assertFalse(draft['can_apply']);self.assertEqual(len(draft['questions']),2)
        self.assertEqual(self.facts(),before)
        with self.assertRaises(ValueError):self.entries.apply(draft['token'],confirmed=True)
        self.assertEqual(self.facts(),before)

    def test_ledger_change_requires_new_preview(self):
        draft=self.entries.preview(self.records(),request_key='synthetic-entry-stale')
        self.business.save_record('costs',{'project_id':1,'category':'工程施工 / 材料辅料',
                                         'amount':'1','cost_date':'2026-10-13'})
        before=self.facts()
        with self.assertRaisesRegex(ValueError,'已变化'):self.entries.apply(draft['token'],confirmed=True)
        self.assertEqual(self.facts(),before)

    def test_second_service_failure_rolls_back_first_record(self):
        draft=self.entries.preview(self.records(),request_key='synthetic-entry-rollback')
        before=self.facts()
        with patch.object(self.business.procurement,'add_purchase_order',side_effect=ValueError('synthetic failure')):
            with self.assertRaisesRegex(ValueError,'synthetic failure'):self.entries.apply(draft['token'],confirmed=True)
        self.assertEqual(self.facts(),before)
        self.assertEqual(self.entries.get_draft(draft['token'])['state'],'draft')

    def test_scope_and_unknown_fields_rejected(self):
        records=self.records();records[0]['data']['entity_id']=999
        draft=self.entries.preview(records,request_key='synthetic-entry-unauthorized')
        self.assertFalse(draft['can_apply']);self.assertIn('未授权',draft['questions'][0])
        self.assertFalse(self.entries.preview(self.records(),request_key='synthetic-entry-scope',project_id=999)['can_apply'])

    def test_discard_and_model_extraction_do_not_execute(self):
        class Client:
            def business_query_plan(inner,messages):return {'records':self.records(),'questions':[]}
        before=self.facts();plan=self.entries.extract('合成费用和油漆',client=Client())
        self.assertEqual(plan['records'],self.records());self.assertEqual(self.facts(),before)
        draft=self.entries.preview(plan['records'],request_key='synthetic-entry-discard')
        self.entries.discard(draft['token'])
        with self.assertRaisesRegex(ValueError,'取消'):self.entries.apply(draft['token'],confirmed=True)
        self.assertEqual(self.facts(),before)

    def test_relative_date_is_bound_to_the_reviewed_day(self):
        from datetime import datetime,timezone,timedelta
        records=self.records()[:1];records[0]['data']['date']='today'
        with patch('services.assistant_entry_service.today',return_value='2026-10-13'):
            draft=self.entries.preview(records,request_key='synthetic-entry-date-bound')
        saved=self.entries.apply(draft['token'],confirmed=True)
        self.assertEqual(self.business.get_record('costs',saved[0]['id'])['cost_date'],'2026-10-13')

    def test_scope_blocker_cannot_be_bypassed_by_calling_apply(self):
        before=self.facts()
        draft=self.entries.preview(self.records(),request_key='synthetic-scope-apply',project_id=999)
        with self.assertRaisesRegex(ValueError,'其他项目'):self.entries.apply(draft['token'],confirmed=True)
        self.assertEqual(self.facts(),before)

    def test_model_questions_require_explicit_review_and_are_preserved(self):
        before=self.facts()
        questions=['另外一笔多项目均摊不在本模式支持范围内']
        draft=self.entries.preview(self.records(),request_key='synthetic-model-warning',model_questions=questions)
        self.assertFalse(draft['can_apply'])
        with self.assertRaisesRegex(ValueError,'模型提示'):
            self.entries.apply(draft['token'],confirmed=True)
        self.assertEqual(self.facts(),before)
        reviewed=self.entries.preview(self.records(),request_key='synthetic-model-reviewed',
                                      model_questions=questions,model_reviewed=True)
        self.assertTrue(reviewed['can_apply'])
        self.assertEqual(self.entries.get_draft(reviewed['token'])['model_questions'],questions)

    def test_receipt_advance_is_disclosed_and_worklogs_are_saved_together(self):
        worker=self.business.save_record('workers',{'name':'合成演示 · 第二工人','daily_rate':'300'})
        records=[{'module':'receipts','data':{'project':'厂房钢结构','date':'2026-10-13',
                     'amount':'120000','payment_method':'银行转账'}},
                 {'module':'worklogs','data':{'project':'厂房钢结构','date':'2026-10-13',
                     'workers':['安装工','第二工人'],'days':'0.5','site':'合成工地','is_overtime':False}}]
        before=self.facts()
        draft=self.entries.preview(records,request_key='synthetic-receipt-labor')
        self.assertTrue(draft['can_apply'],draft['questions'])
        self.assertEqual(self.facts(),before)
        self.assertIn('其中预收款（不新增确认收入）：20000.00元',draft['prepared'][0]['effects'])
        saved=self.entries.apply(draft['token'],confirmed=True)
        self.assertEqual(len(saved),3)
        from db.connection import db_read
        with db_read() as conn:
            self.assertEqual(conn.execute("SELECT SUM(amount_minor) FROM settlements WHERE status='active'").fetchone()[0],10000000)
            self.assertEqual(conn.execute("SELECT SUM(allocated_amount_minor) FROM receipt_allocations WHERE settlement_id IS NULL").fetchone()[0],2000000)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM work_logs WHERE work_date='2026-10-13' AND status='active'").fetchone()[0],2)

    def test_duplicate_across_requests_is_not_silently_saved(self):
        draft=self.entries.preview(self.records(),request_key='synthetic-duplicate-first')
        self.entries.apply(draft['token'],confirmed=True)
        before=self.facts()
        repeated=self.entries.preview(self.records(),request_key='synthetic-duplicate-second')
        self.assertFalse(repeated['can_apply'])
        self.assertIn('相同助手录入',repeated['questions'][0])
        self.assertEqual(self.facts(),before)

    def test_request_key_cannot_reuse_a_review_under_another_scope(self):
        self.entries.preview(self.records(),request_key='synthetic-reused-scope',project_id=999)
        with self.assertRaisesRegex(ValueError,'范围'):
            self.entries.preview(self.records(),request_key='synthetic-reused-scope',project_id=None)
