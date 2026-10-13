import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch


class OperatingAttentionTests(unittest.TestCase):
    def setUp(self):
        from db import connection, migration_runner
        from db.schema import init_db
        from services import operating_attention_service, web_workbench_service
        from web_workbench.synthetic import seed
        temp = tempfile.TemporaryDirectory(prefix='operating_attention_')
        self.addCleanup(temp.cleanup)
        database = Path(temp.name)/'synthetic.db'
        for module in (connection,migration_runner):
            override=patch.object(module,'DB_PATH',database)
            override.start();self.addCleanup(override.stop)
        init_db();seed()
        self.service=operating_attention_service
        self.business=web_workbench_service
        self.time=patch.object(self.service,'clock',return_value=datetime(2026,10,13,18,tzinfo=timezone(timedelta(hours=8))))
        self.clock_mock=self.time.start();self.addCleanup(self.time.stop)

    def snapshot(self):
        from db.connection import db_read
        with db_read() as conn:return list(conn.iterdump())

    def test_read_only_overview_defaults_and_does_not_write(self):
        before=self.snapshot()
        result=self.service.overview()
        self.assertEqual([row['status'] for row in result['checks']],['pending','pending'])
        self.assertEqual(self.snapshot(),before)

    def test_completion_is_manual_persistent_and_idempotent(self):
        self.service.start_day()
        row=self.service.overview()['checks'][0]
        self.service.set_check('worklogs',row['day'],'none',row['basis'])
        before=self.snapshot()
        self.assertFalse(self.service.set_check('worklogs',row['day'],'none',row['basis'])['changed'])
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(self.service.overview()['checks'][0]['status'],'none')
        self.service.set_check('worklogs',row['day'],'pending',row['basis'])
        self.assertEqual(self.service.overview()['checks'][0]['status'],'pending')

    def test_new_record_reopens_check_and_cannot_be_marked_no_business(self):
        row=self.service.overview()['checks'][0]
        self.service.set_check('worklogs',row['day'],'done',row['basis'])
        self.business.save_record('worklogs',{'worker_id':1,'project_id':1,'construction_site':'合成工地',
                                             'work_date':'2026-10-13','work_days':'1'})
        actual=self.service.overview()['checks'][0]
        self.assertEqual(actual['status'],'pending');self.assertTrue(actual['changed_after_check'])
        self.assertEqual(actual['count'],1)
        with self.assertRaisesRegex(ValueError,'变化'):self.service.set_check('worklogs',row['day'],'done',row['basis'])
        with self.assertRaisesRegex(ValueError,'已有'):self.service.set_check('worklogs',row['day'],'none',actual['basis'])

    def test_next_day_has_independent_tasks_and_keeps_prior_unfinished(self):
        self.service.start_day()
        row=self.service.overview()['checks'][0]
        self.service.set_check('worklogs',row['day'],'done',row['basis'])
        self.clock_mock.return_value=datetime(2026,10,14,9,tzinfo=timezone(timedelta(hours=8)))
        result=self.service.overview()
        self.assertEqual(result['day'],'2026-10-14')
        self.assertEqual([row['status'] for row in result['checks']],['pending','pending'])
        self.assertEqual([(row['day'],row['kind']) for row in result['previous_checks']],[('2026-10-13','purchases')])

    def test_ignore_and_restore_do_not_change_financial_records(self):
        row=self.service.overview()['attention'][0]
        self.service.set_attention(row['key'],'ignored',row['basis'])
        result=self.service.overview()
        self.assertIn(row['key'],[item['key'] for item in result['hidden']])
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'变化'):self.service.set_attention(row['key'],'ignored','invalid')
        self.assertEqual(self.snapshot(),before)
        self.service.set_attention(row['key'],'pending',row['basis'])
        self.assertIn(row['key'],[item['key'] for item in self.service.overview()['attention']])

    def test_atomic_composed_services_roll_back_as_one_unit(self):
        from db.connection import db_transaction,use_write_transaction
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'rollback'):
            with db_transaction(immediate=True) as conn,use_write_transaction(conn):
                self.business.save_record('costs',{'project_id':1,'category':'工程施工 / 材料辅料',
                                                 'amount':'10','cost_date':'2026-10-13'})
                raise ValueError('rollback')
        self.assertEqual(self.snapshot(),before)

    def test_snoozed_reminder_reopens_when_due_and_scope_does_not_hide_daily(self):
        result=self.service.overview()
        row=next(item for item in result['attention'] if item['key']=='contract_attachment:1')
        self.service.set_attention(row['key'],'snoozed',row['basis'],'2026-10-14')
        self.assertIn(row['key'],[item['key'] for item in self.service.overview()['hidden']])
        self.clock_mock.return_value=datetime(2026,10,14,9,tzinfo=timezone(timedelta(hours=8)))
        self.assertIn(row['key'],[item['key'] for item in self.service.overview()['attention']])
        scoped=self.service.overview(999)
        self.assertEqual(scoped['attention'],[])
        self.assertEqual(len(scoped['checks']),2)
