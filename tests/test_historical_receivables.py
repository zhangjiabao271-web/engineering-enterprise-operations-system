import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from db import connection, migration_runner
from db.backup import backup_database

if not connection.DB_PATH.exists():
    # 开源环境无生产库：构建空库（基础表 + 全量迁移）作为测试基准
    import db.migration_runner as _runner_module

    _base_dir = tempfile.TemporaryDirectory(prefix="oss_base_")
    connection.DB_PATH = Path(_base_dir.name) / "supplier_data.db"
    _runner_module.DB_PATH = connection.DB_PATH
    from db.schema import init_db as _init_db

    _init_db()
from services import historical_receivable_service as history


@unittest.skipUnless((Path(__file__).resolve().parent.parent / "supplier_data.db").exists(), "依赖本地生产库数据，开源环境跳过")
class HistoricalReceivableTests(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        path=Path(folder.name)/'test.db'
        backup_database(connection.DB_PATH,path)
        migration_runner.run_migrations(path)
        mocked=patch.object(connection,'DB_PATH',path)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.project=history.save_project(26,'历史回款专项测试',None)

    def test_unknown_balance_and_idempotency(self):
        first=history.record_receipt(self.project,'2026-09-15','150000','test-key')
        self.assertEqual(first,history.record_receipt(self.project,'2026-09-15','150000','test-key'))
        row=next(row for row in history.list_projects() if row['id']==self.project)
        self.assertEqual(row['received_minor'],15000000)
        self.assertIsNone(row['remaining_minor'])
        self.assertEqual(row['state'],'总额待确认')
        self.assertEqual(history.list_receipts(self.project)[0]['payer_name_snapshot'],'乐平市赛复乐医药化工有限公司')
        with self.assertRaises(ValueError):
            history.record_receipt(self.project,'2026-09-15','10','test-key')

    def test_confirm_total_and_capacity(self):
        history.record_receipt(self.project,'2026-09-15','150000','test-key')
        with self.assertRaises(ValueError):
            history.save_project(26,'历史回款专项测试','149999',project_id=self.project)
        history.save_project(26,'历史回款专项测试','200000',project_id=self.project)
        with self.assertRaises(ValueError):
            history.record_receipt(self.project,'2026-09-15','50001','over-capacity')
        history.record_receipt(self.project,'2026-09-15','50000','final-payment')
        row=next(row for row in history.list_projects() if row['id']==self.project)
        self.assertEqual(row['remaining_minor'],0)
        self.assertEqual(row['state'],'已结清')

    def test_invalid_amount_date_and_customer(self):
        for amount in ('NaN','0','-1','0.001'):
            with self.assertRaises(ValueError):
                history.record_receipt(self.project,'2026-09-15',amount,'invalid')
        with self.assertRaises(ValueError):
            history.record_receipt(self.project,'2026-02-30','1','invalid-date')
        with self.assertRaises(ValueError):
            history.save_project(-1,'无效客户')

    def test_payer_override_is_snapshot(self):
        history.record_receipt(self.project,'2026-09-15','1','proxy',payer_name='个人代付测试')
        self.assertEqual(history.list_receipts(self.project)[0]['payer_name_snapshot'],'个人代付测试')
        with self.assertRaises(ValueError):
            history.record_receipt(self.project,'2026-09-15','1','proxy',payer_name='不同付款人')
