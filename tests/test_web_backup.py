"""Archive, restore and receipt-void checks use only temporary synthetic ledgers."""

import base64
import json
import io
import hashlib
import os
import sqlite3
import unittest
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from tests import test_web_workbench as fixtures
from web_workbench import backup


class WebBackupTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.WebWorkbenchTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.WebWorkbenchTests.tearDownClass.__func__)
    stop = fixtures.WebWorkbenchTests.stop
    request = fixtures.WebWorkbenchTests.request

    def setUp(self):
        fixtures.WebWorkbenchTests.setUp(self)
        self.storage = Path(self.temp.name) / (self.id().split('.')[-1] + '-attachments')
        self.storage.mkdir(exist_ok=True)
        environment = patch.dict(os.environ, SUPPLY_CHAIN_ATTACHMENTS_PATH=str(self.storage))
        environment.start()
        self.addCleanup(environment.stop)

    def okay(self, path, data):
        status, response = self.request(path, data)
        self.assertEqual(status, 200, response)
        return response['result']

    def archive(self):
        result = self.okay('backup/create', {})
        with urllib.request.urlopen(self.server.origin + '/api/backup/download?token=' + result['token']) as response:
            raw = response.read()
        return raw, result['review']

    def upload(self, raw):
        started = self.okay('backup/restore_start', {'name':'账本.zip', 'size':len(raw)})
        for offset in range(0, len(raw), started['chunk_size']):
            self.okay('backup/restore_chunk', {'token':started['token'], 'offset':offset,
                       'content':base64.b64encode(raw[offset:offset + started['chunk_size']]).decode()})
        return started['token']

    def test_create_download_includes_cashflow_source_and_changes_no_database(self):
        from services import document_cashflow_service
        from services.document_cashflow_extract import read_document
        source = self.storage / '条款.txt'
        source.write_text('合同签订后支付10元。', encoding='utf-8')
        document_cashflow_service.save_document({**read_document(source), 'name':'条款.txt','kind':'合同正文'}, project_id=1)
        before = backup.fingerprint()
        raw, review = self.archive()
        self.assertEqual(before, backup.fingerprint())
        self.assertEqual(review['file_count'], 1)
        saved = self.storage / 'download.zip'
        saved.write_bytes(raw)
        with zipfile.ZipFile(saved) as archive:
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(Path(manifest['files'][0]['original_path']).resolve(), source.resolve())
        self.assertEqual(self.request('backup/download?token=unknown')[0], 400)

    def test_restore_is_confirmed_restores_attachments_and_invalidates_old_forms(self):
        from services.attachment_service import add_attachment
        source = self.storage / '合同.txt'
        source.write_bytes(b'original document')
        attachment = add_attachment('contract', 1, source)
        from services.attachment_service import list_attachments, attachment_path
        target = attachment_path(next(row for row in list_attachments('contract',1) if row['id']==attachment)['file_path'])
        before = backup.fingerprint()
        raw, _ = self.archive()
        self.business.save_record('costs', {'project_id':1,'category':'管理费','amount':'12.34','cost_date':'2026-10-05'})
        target.write_bytes(b'changed document')
        token = self.upload(raw)
        self.okay('backup/restore_preview', {'token':token})
        changed = backup.fingerprint()
        self.assertEqual(self.request('backup/restore_apply', {'token':token,'confirmed':False,'confirmation':'恢复该账本'})[0],400)
        self.assertEqual(backup.fingerprint(), changed)
        old_csrf = self.server.token
        result = self.okay('backup/restore_apply', {'token':token,'confirmed':True,'confirmation':'恢复该账本'})
        self.assertEqual(backup.fingerprint(), before)
        self.assertEqual(target.read_bytes(), b'original document')
        self.assertTrue(Path(result['safety_backup']).is_file())
        self.assertTrue((Path(result['safety_backup']).parent / 'rollback_manifest.json').exists())
        self.assertNotEqual(self.server.token, old_csrf)
        stale = urllib.request.Request(self.server.origin + '/api/backup/create',
            data=b'{}', headers={'Origin':self.server.origin, 'X-Workbench-Token':old_csrf,
                                'Content-Type':'application/json'})
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(stale)
        self.assertEqual(rejected.exception.code, 403)
        self.assertEqual(self.okay('backup/restore_apply', {'token':token,'confirmed':True,'confirmation':'恢复该账本'}), result)

    def test_old_or_future_schema_is_rejected_without_changing_ledger(self):
        raw, _ = self.archive()
        before = backup.fingerprint()
        with zipfile.ZipFile(io.BytesIO(raw)) as packed:
            manifest = json.loads(packed.read('manifest.json'))
            database = packed.read('supplier_data.db')
        for future in (False, True):
            path = self.storage / ('future.db' if future else 'old.db')
            path.write_bytes(database)
            with closing(sqlite3.connect(path)) as conn, conn:
                if future:
                    conn.execute("INSERT INTO schema_migrations(version,description,applied_at) VALUES(9999,'future','2026-10-05')")
                else:
                    conn.execute('DELETE FROM schema_migrations WHERE version=630')
            modified = path.read_bytes()
            manifest['database_sha256'] = hashlib.sha256(modified).hexdigest()
            payload = io.BytesIO()
            with zipfile.ZipFile(payload, 'w') as packed:
                packed.writestr('supplier_data.db', modified)
                packed.writestr('manifest.json', json.dumps(manifest))
            token = self.upload(payload.getvalue())
            status, response = self.request('backup/restore_preview', {'token':token})
            self.assertEqual(status, 400)
            self.assertIn('版本', response['error'])
            self.assertEqual(before, backup.fingerprint())

    def test_isolated_restore_cannot_target_live_attachment_directory(self):
        from services import backup_service
        from db.connection import PROJECT_ROOT
        target = PROJECT_ROOT / 'attachments' / 'must-not-be-touched.pdf'
        with self.assertRaisesRegex(ValueError, '附件目录'):
            backup_service._restore_targets({'files':[{'original_path':str(target)}]})

    def test_current_cost_category_and_legacy_record_notes_can_be_edited(self):
        from services import cost_service
        category = next(field for field in self.business.schema()['costs']['fields'] if field['name']=='category')
        self.assertEqual(category['choices'], cost_service.COST_CATEGORIES)
        self.assertTrue(set(category['choices']).issubset(cost_service.SUPPORTED_COST_CATEGORIES))
        for value in ('工程施工 / 材料辅料','外包施工费'):
            cost = self.business.save_record('costs', {'category':value,'amount':'100.01','cost_date':'2026-10-05'})
            original = self.business.get_record('costs',cost)
            self.business.save_record('costs', {'category':value,'notes':'只补充说明'},cost,original['_version'])
            actual = self.business.get_record('costs',cost)
            self.assertEqual((actual['category'],actual['amount_minor'],actual['notes']),(value,10001,'只补充说明'))

    def test_missing_manifest_and_zip_traversal_are_rejected(self):
        from services import backup_service
        for illegal in (False, True):
            path = self.storage / ('traversal.zip' if illegal else 'missing.zip')
            with zipfile.ZipFile(path, 'w') as packed:
                packed.writestr('supplier_data.db', b'not a database')
                if illegal:
                    packed.writestr('manifest.json', '{}')
                    packed.writestr('../escape.txt', b'forbidden')
            with self.assertRaises(ValueError):
                backup_service.inspect_backup_archive(path)

    def test_chunk_duplicate_is_exact_and_cancel_is_nonmutating(self):
        raw, _ = self.archive()
        before = backup.fingerprint()
        started = self.okay('backup/restore_start', {'name':'ledger.zip','size':len(raw)})
        payload = {'token':started['token'],'offset':0,'content':base64.b64encode(raw).decode()}
        self.assertEqual(self.okay('backup/restore_chunk', payload), self.okay('backup/restore_chunk', payload))
        self.assertEqual(self.request('backup/restore_chunk', {**payload,'offset':1})[0],400)
        self.okay('backup/restore_discard', {'token':started['token']})
        self.assertEqual(self.request('backup/restore_preview', {'token':started['token']})[0],400)
        self.assertEqual(before, backup.fingerprint())

    def test_changed_ledger_or_uploaded_file_rejects_stale_confirmation(self):
        raw, _ = self.archive()
        token = self.upload(raw)
        self.okay('backup/restore_preview', {'token':token})
        self.business.save_record('plans', {'direction':'out','title':'已改变','amount':'1'})
        after = backup.fingerprint()
        self.assertEqual(self.request('backup/restore_apply', {'token':token,'confirmed':True,'confirmation':'恢复该账本'})[0],400)
        self.assertEqual(after, backup.fingerprint())
        self.okay('backup/restore_preview', {'token':token})
        self.server.backups[token]['path'].write_bytes(b'changed')
        self.assertEqual(self.request('backup/restore_apply', {'token':token,'confirmed':True,'confirmation':'恢复该账本'})[0],400)
        self.assertEqual(after, backup.fingerprint())

    def test_readonly_can_back_up_but_cannot_restore(self):
        self.server.read_only = True
        before = backup.fingerprint()
        raw, _ = self.archive()
        token = self.upload(raw)
        self.okay('backup/restore_preview', {'token':token})
        self.assertEqual(self.request('backup/restore_apply', {'token':token,'confirmed':True,'confirmation':'恢复该账本'})[0],403)
        self.assertEqual(before, backup.fingerprint())

    def test_restore_failure_closes_write_gate(self):
        raw, _ = self.archive()
        token = self.upload(raw)
        self.okay('backup/restore_preview', {'token':token})
        real = backup.run
        def fail(action, payload):
            if action == 'restore':
                raise ValueError('模拟恢复失败，安全副本已保留')
            return real(action, payload)
        with patch.object(backup, 'run', side_effect=fail):
            self.assertEqual(self.request('backup/restore_apply', {'token':token,'confirmed':True,'confirmation':'恢复该账本'})[0],400)
        self.assertTrue(self.server.read_only)
        self.assertEqual(self.request('operation_apply', {'token':'unknown','confirmed':True})[0],403)

    def test_receipt_void_retains_evidence_and_releases_invoice_balance(self):
        receipt = self.business.save_record('receipts', {'project_id':1,'contract_id':1,'settlement_id':1,'amount':'100.01','receipt_date':'2026-10-05'})
        old_count = len(self.business.rows('receipts'))
        preview = self.okay('operation_preview', {'name':'receipt_void','record':{'id':receipt,'reason':'重复登记，按原票据更正'}})
        before = backup.fingerprint()
        self.okay('operation_apply', {'token':preview['token'],'confirmed':True})
        after = backup.fingerprint()
        self.assertNotEqual(after, before)
        self.okay('operation_apply', {'token':preview['token'],'confirmed':True})
        self.assertEqual(after, backup.fingerprint())
        self.assertEqual(len(self.business.rows('receipts')), old_count-1)
        with closing(sqlite3.connect(self.path)) as conn:
            row = conn.execute('SELECT status,notes FROM receipts WHERE id=?',(receipt,)).fetchone()
            self.assertEqual(row[0], 'void')
            self.assertIn('重复登记', row[1])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM receipt_revisions WHERE receipt_id=? AND action=\'void\'',(receipt,)).fetchone()[0],1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM receipt_allocations WHERE receipt_id=? AND status='active'",(receipt,)).fetchone()[0],0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM invoice_receipt_allocations WHERE receipt_id=? AND status='active'",(receipt,)).fetchone()[0],0)

    def test_receipt_with_fund_transaction_is_not_voided(self):
        receipt = self.business.save_record('receipts', {'project_id':1,'contract_id':1,'settlement_id':1,'amount':'100.01','receipt_date':'2026-10-05'})
        self.business.funds.record_transaction({'category':'receipt','source_id':receipt,'to_account_id':1,'amount':'100.01','transaction_date':'2026-10-05','request_key':'test-receipt-funds'})
        before = backup.fingerprint()
        status, response = self.request('operation_preview', {'name':'receipt_void','record':{'id':receipt,'reason':'测试拒绝'}})
        self.assertEqual(status,400)
        self.assertIn('资金流水', response['error'])
        self.assertEqual(before, backup.fingerprint())


class WriterLeaseTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'nt', 'Windows app lease')
    def test_old_and_new_entrypoints_cannot_hold_same_writer_lease(self):
        from db.application_lock import ledger_writer
        with ledger_writer('qa/lease-only-no-db.db'):
            with self.assertRaisesRegex(ValueError, '已有录入窗口'):
                with ledger_writer('qa/lease-only-no-db.db'):
                    self.fail('second interactive writer')
        with ledger_writer('qa/lease-only-no-db.db'):
            pass
