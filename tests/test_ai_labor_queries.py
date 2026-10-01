import sqlite3
import unittest
from contextlib import contextmanager
from datetime import date
from unittest.mock import patch

from services import ai_labor_query_service as query
from services import labor_service


class LaborQueryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE workers(id INTEGER, name TEXT);
            CREATE TABLE projects(id INTEGER, name TEXT);
            CREATE TABLE work_logs(id INTEGER, worker_id INTEGER, project_id INTEGER,
              work_date TEXT, construction_site TEXT, work_type TEXT, work_days REAL,
              is_overtime INTEGER, daily_rate_minor INTEGER, daily_rate REAL,
              amount_minor INTEGER, amount REAL, status TEXT);
            INSERT INTO workers VALUES(1,'示例工人甲'),(2,'其他工人');
            INSERT INTO projects VALUES(7,'测试工程');
            INSERT INTO work_logs VALUES
              (1,1,7,'2026-09-19','工地','安装',1,0,30000,300,30000,300,'active'),
              (2,1,7,'2026-09-19','工地','安装',0.5,1,30000,300,15000,150,'active'),
              (3,1,7,'2026-09-20','工地','安装',1,0,30000,300,30000,300,'active'),
              (4,1,7,'2026-09-18','工地','安装',1,0,30000,300,30000,300,'void'),
              (5,2,7,'2026-09-19','工地','安装',1,0,30000,300,30000,300,'active'),
              (6,1,7,'2026-08-01','工地','安装',1,0,30000,300,30000,300,'active');
        """)
        self.addCleanup(self.db.close)

        @contextmanager
        def read():
            yield self.db

        for target, kwargs in (
            ('services.labor_service.db_read', {'side_effect': read}),
            ('services.labor_service.get_workers', {'return_value': [dict(r) for r in self.db.execute('SELECT * FROM workers')]}),
            ('services.project_service.list_projects', {'return_value': [{'id': 7, 'name': '测试工程', 'project_code': 'P7'}]}),
        ):
            mocked = patch(target, **kwargs)
            mocked.start()
            self.addCleanup(mocked.stop)

    def ask(self, question, **kwargs):
        return query.retrieve_labor_query(question, today=date(2026, 9, 19), **kwargs)

    def test_days_include_today_half_days_and_overtime_not_void_or_future(self):
        result = self.ask('示例工人甲今年做了几工')
        self.assertIn('共 2.5 工，其中加班 0.5 工', result['answer'])
        self.assertEqual(result['sources'][0]['record_count'], 3)
        self.assertEqual({r['id'] for r in result['sources'][0]['details']}, {1, 2, 6})

    def test_followup_and_person_switch(self):
        result = self.ask('示例工人甲今年做了几工')
        context = result['context_updates']
        self.assertIn('共 1 工', self.ask('那上个月呢？', conversation_context=context)['answer'])
        self.assertIn('共 1 工', self.ask('其他工人今年几工', conversation_context=context)['answer'])
        self.assertIsNone(self.ask('今年采购多少钱', conversation_context=context))
        self.assertIsNone(self.ask('工天成本为什么这么高', conversation_context=context))
        self.assertIn('共 2.5 工', self.ask('示例工人甲今年做了多少天')['answer'])

    def test_unknown_and_ambiguous_do_not_invent_zero(self):
        self.assertIn('完整姓名', self.ask('陌生人今年几工')['answer'])
        with patch.object(labor_service, 'get_workers', return_value=[{'id': 1, 'name': '示例工人甲'}, {'id': 3, 'name': '示例工人甲'}]):
            self.assertIn('多名工人', self.ask('示例工人甲今年几工')['answer'])

    def test_dates_and_scope(self):
        self.assertIn('共 1 工', self.ask('示例工人甲2026年8月几工')['answer'])
        self.assertIn('共 1.5 工', self.ask('示例工人甲今天几工')['answer'])
        self.assertIn('没有有效', self.ask('示例工人甲去年几工')['answer'])
        self.assertIn('先指定', self.ask('示例工人甲最近三个月几工')['answer'])
        self.assertIn('测试工程', self.ask('测试工程示例工人甲今年几工')['sources'][0]['scope_label'])
        self.assertIn('全公司', self.ask('全公司示例工人甲今年几工', project_id=7)['sources'][0]['scope_label'])

    def test_engine_does_not_call_model(self):
        import ai_engine
        with patch.object(ai_engine, 'make_ai_client', side_effect=AssertionError('must stay local')):
            result = ai_engine.ask_ai_turn('示例工人甲2026年8月几工')
        self.assertEqual(result['answer_mode'], 'local')
        self.assertIn('共 1 工', result['answer'])
