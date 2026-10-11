"""Synthetic, local end-to-end checks of the extended migration adapters."""
import base64
import json
import os
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def synthetic_invoice_pdf(path, number='12345678901234567890', buyer='合成建设单位', seller='合成供应商', *, net='100.00', tax='13.00', gross='113.00'):
    """A real text PDF with a Unicode CMap; the production parser reads its bytes."""
    from pypdf import PdfWriter
    from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, DecodedStreamObject, TextStringObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    cmap = DecodedStreamObject()
    cmap.set_data(b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def /CMapName /Identity-UCS def /CMapType 2 def 1 begincodespacerange <0000> <FFFF> endcodespacerange 1 beginbfrange <0000> <FFFF> <0000> endbfrange endcmap CMapName currentdict /CMap defineresource pop end end')
    cid = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/CIDFontType0'),NameObject('/BaseFont'):NameObject('/HeiseiMin-W3'),NameObject('/DW'):NumberObject(1000),NameObject('/CIDSystemInfo'):DictionaryObject({NameObject('/Registry'):TextStringObject('Adobe'),NameObject('/Ordering'):TextStringObject('Japan1'),NameObject('/Supplement'):NumberObject(0)})})
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type0'),NameObject('/BaseFont'):NameObject('/HeiseiMin-W3'),NameObject('/Encoding'):NameObject('/Identity-H'),NameObject('/DescendantFonts'):ArrayObject([writer._add_object(cid)]),NameObject('/ToUnicode'):writer._add_object(cmap)})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    lines = ['合成测试增值税发票', '发票号码: '+number, '开票日期: 2026年10月03日', '购买方名称: '+buyer, '销售方名称: '+seller, '合计 '+net+' '+tax, '税率 13%', '价税合计（小写）'+gross]
    stream = DecodedStreamObject()
    stream.set_data(('BT /F1 10 Tf 40 780 Td ' + ' 0 -24 Td '.join('<'+line.encode('utf-16-be').hex()+'> Tj' for line in lines) + ' ET').encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)


class ExtendedWorkbenchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import db.connection as connection
        import db.migration_runner as runner
        from db.schema import init_db
        from web_workbench.synthetic import seed
        cls.temp = tempfile.TemporaryDirectory(prefix='extended_synthetic_')
        cls.template = Path(cls.temp.name) / 'template.db'
        with patch.object(connection,'DB_PATH',cls.template), patch.object(runner,'DB_PATH',cls.template):
            init_db()
            seed()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        import db.connection as connection
        from web_workbench.server import WorkbenchServer
        path = Path(self.temp.name) / (self.id().split('.')[-1] + '.db')
        with closing(sqlite3.connect(self.template)) as source, closing(sqlite3.connect(path)) as target:
            source.backup(target)
        database_patch = patch.object(connection,'DB_PATH',path)
        database_patch.start()
        self.addCleanup(database_patch.stop)
        self.server = WorkbenchServer(0, read_only=False, demo=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = self.server.origin
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, data=None):
        if path in ('save','action','photos_upload','upload_attachment') and data and not data.get('form_ticket'):
            from urllib.parse import urlencode
            values={k:v for k,v in {'kind':path,'entity':data.get('entity'),'module':data.get('module'),'action':data.get('action'),'id':data.get('id') or data.get('record',{}).get('id'),'expected':data.get('expected')}.items() if v is not None}
            ticket=self.request('write_ticket?'+urlencode(values))
            data={**data,'form_ticket':ticket['token']}
        headers = {}
        if data is not None:
            headers = {'Content-Type':'application/json','Origin':self.origin,'X-Workbench-Token':self.server.token}
        request = Request(self.origin+'/api/'+path, data=None if data is None else json.dumps(data).encode(), headers=headers)
        try:
            with urlopen(request) as response:
                result = json.load(response)
                return result if data is None else result['result']
        except HTTPError as error:
            raise ValueError(json.load(error)['error']) from None

    def upload(self, path):
        return {'name':path.name,'content':base64.b64encode(path.read_bytes()).decode()}

    def test_csv_full_batch_duplicate_cancel_stale_and_atomic_failure(self):
        from services import web_workbench_service as business
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'synthetic.csv'
            path.write_text('legal_name,credit_limit\n合成CSV全部客户甲,89999999999999.93\n合成CSV全部客户乙,1.01\n',encoding='utf-8-sig')
            preview = self.request('csv_preview',dict(module='customers',**self.upload(path)))
            self.assertEqual(preview['review']['count'],2)
            self.assertFalse(any(row['legal_name']=='合成CSV全部客户甲' for row in business.rows('customers')))
            self.request('csv_discard',{'token':preview['token']})
            with self.assertRaisesRegex(ValueError,'取消'):self.request('csv_apply',{'token':preview['token'],'confirmed':True})
            preview = self.request('csv_preview',dict(module='customers',**self.upload(path)))
            with self.assertRaisesRegex(ValueError,'确认'):self.request('csv_apply',{'token':preview['token'],'confirmed':False})
            applied = self.request('csv_apply',{'token':preview['token'],'confirmed':True})
            self.assertEqual(applied,self.request('csv_apply',{'token':preview['token'],'confirmed':True}))
            self.assertEqual(business.get_record('customers',applied['rows'][0]['id'])['credit_limit_minor'],8999999999999993)
            with self.assertRaisesRegex(ValueError,'已存在'):self.request('csv_preview',dict(module='customers',**self.upload(path)))
            path.write_text('legal_name,credit_limit\n合成CSV失败不能留存,1.01\n合成CSV坏金额,1.001\n',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'第3行'):self.request('csv_preview',dict(module='customers',**self.upload(path)))
            self.assertFalse(any(row['legal_name']=='合成CSV失败不能留存' for row in business.rows('customers')))
            path.write_text('legal_name\n合成CSV旧预览失效\n',encoding='utf-8')
            preview = self.request('csv_preview',dict(module='customers',**self.upload(path)))
            business.save_record('customers',{'legal_name':'合成CSV其他写入'})
            with self.assertRaisesRegex(ValueError,'变化'):self.request('csv_apply',{'token':preview['token'],'confirmed':True})

    def test_retained_attachment_restore_and_photo_classification(self):
        from services import web_workbench_service as business
        from services.attachment_service import add_attachment, attachment_path
        from PIL import Image
        project=business.save_record('projects',{'name':'合成照片测试','project_code':'PHOTO-EXTENDED'})
        record=business.save_record('construction',{'project_id':project,'work_details':'合成资料','start_date':'2026-10-03','end_date':'2026-10-03'})
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.png';Image.new('RGB',(40,30),'blue').save(path)
            attachment=add_attachment('construction',record,path)
            values={'id':record,'entity':'construction','attachment_id':attachment,'reason':'合成移出验证'}
            for action,expected in [('attachment_void','void'),('attachment_restore','active')]:
                preview=self.request('evidence_preview',dict(values,action=action))
                self.request('evidence_apply',{'token':preview['token'],'confirmed':True})
                rows=self.request(f'attachments?entity=construction&id={record}&history=1')
                row=next(row for row in rows if row['id']==attachment)
                self.assertEqual(row['status'],expected);self.assertTrue(attachment_path(row['file_path']).is_file())
            current=business.get_record('construction',record)
            ids=self.request('photos_upload',{'id':record,'expected':current['_version'],'category':'施工现场','files':[self.upload(path)],'confirmed':True})
            rows=self.request(f'photos?id={record}');self.assertEqual(len(rows),1)
            preview=self.request('evidence_preview',{'action':'photo_category','id':record,'photo_id':ids[0],'category':'验收照片','reason':'合成分类核对'})
            self.request('evidence_apply',{'token':preview['token'],'confirmed':True})
            self.assertEqual(self.request(f'photos?id={record}')[0]['photo_type'],'验收照片')
            # Removing evidence never calls the original irreversible photo delete.
            preview=self.request('evidence_preview',dict(values,attachment_id=rows[0]['attachment_id'],action='attachment_void'))
            self.request('evidence_apply',{'token':preview['token'],'confirmed':True})
            self.assertEqual(len(self.request(f'photos?id={record}')),1)

    def test_real_local_text_pdf_create_duplicate_conflict_and_cancel(self):
        from services import web_workbench_service as business
        project=business.save_record('projects',{'name':'合成PDF项目','project_code':'PDF-EXTENDED','customer_name':'合成建设单位'})
        contract=business.save_record('contracts',{'name':'合成PDF合同','contract_no':'PDF-EXTENDED-HT','customer_name':'合成建设单位','amount':'113','sign_date':'2026-10-03'})
        business.save_record('allocations',{'project_id':project,'contract_id':contract,'amount':'113'})
        settlement=business.save_record('settlements',{'project_id':project,'contract_id':contract,'amount':'113','basis':'合成票面验收','settlement_date':'2026-10-03'})
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic-invoice.pdf';synthetic_invoice_pdf(path)
            result=self.request('pdf_preview',dict(module='invoices',**self.upload(path)))
            recognized=result['recognition']
            self.assertEqual(recognized['amount'],'113.00');self.assertEqual(recognized['invoice_no'],'12345678901234567890')
            record=dict(recognized,project_id=project,contract_id=contract,settlement_id=settlement,tax_rate_percent=recognized['tax_rate'])
            with self.assertRaisesRegex(ValueError,'税率.*冲突'):self.request('pdf_apply',{'token':result['token'],'record':dict(record,tax_rate_percent='9'),'confirmed':True})
            with self.assertRaisesRegex(ValueError,'冲突'):self.request('pdf_apply',{'token':result['token'],'record':dict(record,amount='114'),'confirmed':True})
            created=self.request('pdf_apply',{'token':result['token'],'record':record,'confirmed':True})
            self.assertEqual(business.get_record('invoices',created)['amount_minor'],11300)
            self.assertEqual(created,self.request('pdf_apply',{'token':result['token'],'record':record,'confirmed':True}))
            duplicate=self.request('pdf_preview',dict(module='invoices',**self.upload(path)))
            self.assertTrue(duplicate['match'])
            with self.assertRaisesRegex(ValueError,'已有|已关联'):self.request('pdf_apply',{'token':duplicate['token'],'confirmed':True})
            self.request('pdf_discard',{'token':duplicate['token']})
            with self.assertRaisesRegex(ValueError,'取消'):self.request('pdf_apply',{'token':duplicate['token'],'confirmed':True})

    def test_input_pdf_uses_local_recognition_and_original_entity_guards(self):
        from services import web_workbench_service as business
        entity=business.entities.list_entities()[0]
        supplier=business.rows('suppliers')[0]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic-input.pdf'
            synthetic_invoice_pdf(path,number='87654321012345678901',buyer=entity['name'],seller=supplier['name'])
            result=self.request('pdf_preview',dict(module='input_invoices',**self.upload(path)))
            record=dict(result['recognition'],entity_id=entity['id'],supplier_id=supplier['id'],tax_rate_label='13%',deduction_status='pending')
            with self.assertRaisesRegex(ValueError,'税率.*冲突'):self.request('pdf_apply',{'token':result['token'],'record':dict(record,tax_rate_label='9%'),'confirmed':True})
            created=self.request('pdf_apply',{'token':result['token'],'record':record,'confirmed':True})
            invoice=business.get_record('input_invoices',created)
            self.assertEqual(invoice['gross_minor'],11300);self.assertTrue(invoice['file_sha256'])
            self.assertEqual(invoice['deduction_status'],'pending')

    def test_csv_over_100_rows_and_month_matrix_original_drilldown(self):
        from services import web_workbench_service as business
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic-full.csv'
            path.write_text('legal_name\n'+'\n'.join(f'合成CSV完整第{index}条' for index in range(101))+'\n',encoding='utf-8')
            preview=self.request('csv_preview',dict(module='customers',**self.upload(path)))
            self.assertEqual(preview['review']['count'],101)
            applied=self.request('csv_apply',{'token':preview['token'],'confirmed':True})
            self.assertEqual(applied['count'],101)
            path.write_text('supplier_id,name,specification,unit,price,tax_rate_percent\n2,合成整批报价,H1,吨,10.01,13\n',encoding='utf-8')
            quote=self.request('csv_preview',dict(module='offers',**self.upload(path)))
            self.request('csv_apply',{'token':quote['token'],'confirmed':True})
            with self.assertRaisesRegex(ValueError,'已存在'):self.request('csv_preview',dict(module='offers',**self.upload(path)))
        matrix=self.request('monthly_workdays?start=2026-10-01&end=2026-10-31')
        worker=matrix['workers'][0]
        detail=self.request(f'operation_read?name=worker_month&id={worker["worker_id"]}&month=2026-10&start=2026-10-01&end=2026-10-31')
        self.assertEqual(worker['months']['2026-10'],detail['work_days'])
        self.assertEqual(len(detail['details']),detail['record_count'])


if __name__ == '__main__':
    unittest.main()
