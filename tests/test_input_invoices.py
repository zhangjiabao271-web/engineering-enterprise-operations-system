"""Isolated entity/input-invoice tests. No production writes or real attachments."""
import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db import connection
from db.migrations.operating_entities import add_operating_entities
from services import input_invoice_service as invoices
from services import operating_entity_service as entities


class InputInvoiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='input_invoice_test_')
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.path = self.root / 'test.db'
        self.patch = patch.object(connection, 'DB_PATH', self.path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.executescript('''
                CREATE TABLE business_partners(id INTEGER PRIMARY KEY, legal_name TEXT, status TEXT, unified_credit_code TEXT);
                CREATE TABLE partner_roles(partner_id INTEGER, role_code TEXT);
                CREATE TABLE projects(id INTEGER PRIMARY KEY,name TEXT,status TEXT,cash_agreed_amount_minor INTEGER);
                CREATE TABLE purchase_orders(id INTEGER PRIMARY KEY,order_no TEXT,project_id INTEGER,
                    supplier_partner_id INTEGER,merchant_name_snapshot TEXT,total_amount_cents INTEGER,
                    purchase_date TEXT,status TEXT);
                CREATE TABLE cost_entries(id INTEGER PRIMARY KEY,cost_no TEXT,project_id INTEGER,
                    counterparty_name_snapshot TEXT,amount_minor INTEGER,cost_date TEXT,status TEXT,source_type TEXT);
                CREATE TABLE business_attachments(file_path TEXT);
                CREATE TABLE construction_photos(file_path TEXT);
                INSERT INTO business_partners VALUES(1,'测试供应商','active',''),(2,'其他供应商','active','');
                INSERT INTO partner_roles VALUES(1,'supplier'),(2,'supplier');
                INSERT INTO projects VALUES(1,'测试项目','active',NULL);
                INSERT INTO purchase_orders VALUES(1,'CG-1',1,1,'测试供应商',11300,'2026-09-24','active');
                INSERT INTO purchase_orders VALUES(2,'CG-2',1,1,'测试供应商',11300,'2026-09-24','active');
                INSERT INTO cost_entries VALUES(1,'FY-1',1,'测试供应商',11300,'2026-09-24','active','manual');
            ''')
            add_operating_entities(conn)
            conn.commit()
        self.data = dict(invoice_no='12345678901234567890', invoice_date='2026-09-24',
                         entity_id=1,supplier_id=1,buyer_name='示例钢结构有限公司',
                         seller_name='测试供应商',net_amount='100',tax_amount='13',amount='113',tax_rate_label='13%')

    def read(self, sql, args=()):
        with connection.db_read() as conn:
            return [tuple(row) for row in conn.execute(sql,args)]

    def assign(self, kind='purchase_orders', ids=(1,), entity=1):
        entities.assign_records(kind,ids,entity)

    def link(self, amount='113', kind='purchase_orders', record_id=1):
        return {'kind':kind,'record_id':record_id,'amount':amount}

    def test_migration_seeds_entities_without_guessing_history(self):
        self.assertEqual([r['tax_identity'] for r in entities.list_entities()],['general','small'])
        self.assertEqual(self.read('SELECT * FROM record_entities'),[])
        self.assertEqual(entities.list_records('purchase_orders')[0]['entity_id'],None)

    def test_assignment_atomic_and_only_changes_labels(self):
        before=self.read('SELECT * FROM purchase_orders')
        with self.assertRaises(ValueError):
            self.assign(ids=(1,999))
        self.assertEqual(self.read('SELECT * FROM record_entities'),[])
        self.assign()
        self.assertEqual(self.read('SELECT * FROM purchase_orders'),before)
        self.assertEqual(len(self.read('SELECT * FROM operating_entity_audit')),1)

    def test_project_suggestion_is_not_automatic_attribution(self):
        self.assign('projects')
        rows=entities.list_records('purchase_orders')
        self.assertTrue(all(r['suggested_entity_id']==1 and r['entity_id'] is None for r in rows))

    def test_invoice_does_not_create_or_change_costs(self):
        before=self.read('SELECT * FROM purchase_orders'),self.read('SELECT * FROM cost_entries')
        self.assign()
        invoices.create_invoice(self.data,[self.link()])
        self.assertEqual(before,(self.read('SELECT * FROM purchase_orders'),self.read('SELECT * FROM cost_entries')))
        self.assertEqual(invoices.list_invoices()[0]['remaining_minor'],0)

    def test_multi_cost_link_and_partial_invoice(self):
        self.assign()
        self.assign('cost_entries')
        invoice=invoices.create_invoice(self.data,[self.link('50'),self.link('50','cost_entries')])
        self.assertEqual(invoices.list_invoices()[0]['remaining_minor'],1300)
        invoices.update_links(invoice,[])
        self.assertEqual(invoices.list_invoices()[0]['linked_minor'],0)

    def test_unknown_and_cross_entity_cost_rejected(self):
        with self.assertRaisesRegex(ValueError,'主体'):
            invoices.create_invoice(self.data,[self.link()])
        self.assign(entity=2)
        with self.assertRaisesRegex(ValueError,'主体'):
            invoices.create_invoice(self.data,[self.link()])
        self.assertEqual(invoices.list_invoices(),[])

    def test_wrong_supplier_and_expense_counterparty_rejected(self):
        self.assign()
        with self.assertRaisesRegex(ValueError,'供应商'):
            invoices.create_invoice(dict(self.data,supplier_id=2,seller_name='其他供应商'),[self.link()])
        self.assign('cost_entries')
        with self.assertRaisesRegex(ValueError,'往来单位'):
            invoices.create_invoice(dict(self.data,supplier_id=2,seller_name='其他供应商'),[self.link(kind='cost_entries')])

    def test_overlink_duplicate_and_reuse_rejected(self):
        self.assign(ids=(1,2))
        for links in ([self.link('114')],[self.link('50'),self.link('50')],
                      [self.link('100'),self.link('20',record_id=2)]):
            with self.assertRaises(ValueError):
                invoices.create_invoice(self.data,links)
        invoices.create_invoice(self.data,[self.link()])
        with self.assertRaises(ValueError):
            invoices.create_invoice(dict(self.data,invoice_no='22345678901234567890'),[self.link('1')])

    def test_duplicate_number_rejected(self):
        invoices.create_invoice(self.data)
        with self.assertRaisesRegex(ValueError,'重复'):
            invoices.create_invoice(self.data)

    def test_bad_amounts_identity_and_missing_rate(self):
        for change in ({'tax_amount':'14'},{'tax_amount':'-1'},{'amount':'NaN'},
                       {'tax_amount':'13.001'},{'buyer_name':'其他购买方'},
                       {'seller_name':'其他销售方'},{'tax_rate_label':''}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                invoices.create_invoice(dict(self.data,**change))

    def test_tax_number_mismatch(self):
        entities.update_tax_number(1,'123456789012345678')
        with self.assertRaisesRegex(ValueError,'税号'):
            invoices.create_invoice(dict(self.data,buyer_tax_number='223456789012345678'))

    def test_small_taxpayer_cannot_deduct(self):
        invoice=invoices.create_invoice(dict(self.data,entity_id=2,buyer_name='示例建筑安装队'))
        for status in ('eligible','deducted'):
            with self.assertRaisesRegex(ValueError,'小规模'):
                invoices.set_deduction(invoice,status,'2026-09' if status=='deducted' else '',reason='会计确认')

    def test_deduction_requires_reason_period_and_blocks_edit_void(self):
        invoice=invoices.create_invoice(self.data)
        with self.assertRaises(ValueError):
            invoices.set_deduction(invoice,'eligible')
        with self.assertRaises(ValueError):
            invoices.set_deduction(invoice,'deducted',reason='会计确认')
        invoices.set_deduction(invoice,'deducted','2026-09','会计确认')
        with self.assertRaises(ValueError):
            invoices.update_invoice(invoice,self.data)
        with self.assertRaises(ValueError):
            invoices.void_invoice(invoice,'错误记录')
        invoices.set_deduction(invoice,'ineligible',reason='会计已确认转出')
        invoices.void_invoice(invoice,'错误记录')
        self.assertEqual(invoices.list_invoices(),[])
        self.assertEqual(len(invoices.list_invoices(include_void=True)),1)

    def test_edit_cannot_bypass_small_taxpayer_guard(self):
        invoice=invoices.create_invoice(self.data)
        invoices.set_deduction(invoice,'eligible',reason='会计确认')
        with self.assertRaisesRegex(ValueError,'小规模'):
            invoices.update_invoice(invoice,dict(self.data,entity_id=2,buyer_name='示例建筑安装队'))

    def test_linked_cost_entity_change_rejected(self):
        self.assign()
        invoices.create_invoice(self.data,[self.link()])
        with self.assertRaisesRegex(ValueError,'其他主体'):
            self.assign(entity=2)

    def test_cost_change_warning_can_be_cleared(self):
        self.assign()
        invoice=invoices.create_invoice(self.data,[self.link()])
        with connection.db_transaction() as conn:
            conn.execute("UPDATE purchase_orders SET status='void' WHERE id=1")
        self.assertTrue(invoices.list_invoices()[0]['warning'])
        invoices.update_links(invoice,[])
        self.assertFalse(invoices.list_invoices()[0]['warning'])

    def test_edit_and_void_audited_without_cost_deletion(self):
        self.assign()
        invoice=invoices.create_invoice(self.data,[self.link()])
        invoices.update_invoice(invoice,dict(self.data,notes='补充说明'))
        invoices.void_invoice(invoice,'录入错误')
        self.assertEqual(self.read('SELECT action FROM input_invoice_audit'),[('create',),('edit',),('void',)])
        self.assertEqual(invoices.cost_candidates(1)[0]['remaining_minor'],11300)
        self.assertEqual(len(self.read('SELECT * FROM input_invoice_links')),1)

    def test_pdf_hash_and_duplicate_rollback(self):
        source=self.root/'source.pdf'
        source.write_bytes(b'%PDF-test-fixture')
        recognition={'source_path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
        storage=self.root/'attachments'
        with patch('services.attachment_service._storage_root',return_value=storage):
            invoices.create_invoice(self.data,recognition=recognition)
            with self.assertRaisesRegex(ValueError,'重复'):
                invoices.create_invoice(dict(self.data,invoice_no='22345678901234567890'),recognition=recognition)
            self.assertEqual(len(list(storage.iterdir())),1)
            with self.assertRaisesRegex(ValueError,'变化'):
                invoices.create_invoice(self.data,recognition=dict(recognition,sha256='wrong'))
        self.assertEqual(len(invoices.list_invoices()),1)

    def test_attach_pdf_and_backup_include_original(self):
        invoice=invoices.create_invoice(self.data)
        source=self.root/'source.pdf'
        source.write_bytes(b'%PDF-test-fixture')
        recognition=dict(self.data,source_path=str(source),sha256=hashlib.sha256(source.read_bytes()).hexdigest())
        with patch.object(invoices,'recognize_pdf',return_value=recognition),patch('services.attachment_service._storage_root',return_value=self.root/'files'):
            invoices.attach_pdf(invoice,source)
            with self.assertRaisesRegex(ValueError,'覆盖'):
                invoices.attach_pdf(invoice,source)
        from services.backup_service import create_backup_archive, inspect_backup_archive
        archive=self.root/'backup.zip'
        manifest=create_backup_archive(archive)
        self.assertEqual(len(manifest['files']),1)
        self.assertEqual(inspect_backup_archive(archive)['file_count'],1)

    def test_pdf_seller_recognition(self):
        from types import SimpleNamespace
        page=SimpleNamespace(extract_text=lambda **kw:'购 名称：示例钢结构有限公司    销 名称：测试供应商')
        with patch('services.invoice_pdf_service.recognize',return_value={'warnings':[]}),patch('pypdf.PdfReader',return_value=SimpleNamespace(pages=[page])):
            self.assertEqual(invoices.recognize_pdf('unused.pdf')['seller_name'],'测试供应商')

    def test_attached_pdf_locks_key_fields_but_allows_notes(self):
        source = self.root / 'locked.pdf'
        source.write_bytes(b'%PDF-test-fixture')
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        with patch('services.attachment_service._storage_root', return_value=self.root/'files'):
            invoice = invoices.create_invoice(self.data, recognition={'source_path':str(source), 'sha256':digest})
        changes = [
            {'amount':'114', 'net_amount':'101'},
            {'invoice_no':'22345678901234567890'},
            {'invoice_date':'2026-09-25'}, {'tax_rate_label':'免税'},
            {'buyer_tax_number':'123456789012345678'},
            {'supplier_id':2, 'seller_name':'其他供应商'},
            {'entity_id':2, 'buyer_name':'示例建筑安装队'},
        ]
        before = self.read('SELECT * FROM input_invoices')
        audit_before = self.read('SELECT * FROM input_invoice_audit')
        for change in changes:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, '原始PDF'):
                invoices.update_invoice(invoice, dict(self.data, **change))
            self.assertEqual(self.read('SELECT * FROM input_invoices'), before)
            self.assertEqual(self.read('SELECT * FROM input_invoice_audit'), audit_before)
        invoices.update_invoice(invoice, dict(self.data, notes='补充备注'))
        self.assertEqual(self.read('SELECT notes,file_sha256 FROM input_invoices'), [('补充备注',digest)])

    def test_manual_invoice_without_pdf_can_correct_amount(self):
        invoice = invoices.create_invoice(self.data)
        invoices.update_invoice(invoice, dict(self.data, amount='114',net_amount='101'))
        self.assertEqual(invoices.list_invoices()[0]['gross_minor'],11400)


if __name__=='__main__':
    unittest.main()
