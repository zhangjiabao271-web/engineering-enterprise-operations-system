"""HTTP preview, cancellation, refresh and exact money on isolated ledgers."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch


class OperatingEntityHTTPTests(unittest.TestCase):
    def setUp(self):
        from db import connection, migration_runner
        from db.schema import init_db
        from web_workbench.server import WorkbenchServer
        from web_workbench.synthetic import seed
        self.temp=tempfile.TemporaryDirectory(prefix='entity_http_')
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'synthetic.db'
        for module in (connection,migration_runner):
            database=patch.object(module,'DB_PATH',self.path)
            database.start()
            self.addCleanup(database.stop)
        init_db()
        seed()
        self.server=WorkbenchServer(0,read_only=False,demo=True,staging=Path(self.temp.name)/'staging')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self,path,data=None):
        headers={'Origin':self.server.origin,'X-Workbench-Token':self.server.token,'Content-Type':'application/json'}
        request=urllib.request.Request(self.server.origin+'/api/'+path,
            data=json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            response=urllib.request.urlopen(request)
        except urllib.error.HTTPError as error:
            response=error
        with response:
            return response.status,json.load(response)

    def ledger(self):
        from db.connection import db_read
        with db_read() as conn:
            return list(conn.iterdump())

    def preview(self):
        status,data=self.request('operation_preview',{'name':'entity_assign',
            'record':{'kind':'contracts','ids':[1],'entity_id':1,'reason':'合成归属确认'}})
        self.assertEqual(status,200,data)
        return data['result']

    def test_cancel_and_confirm_refresh_status_and_dashboard(self):
        before=self.ledger()
        preview=self.preview()
        self.assertEqual(preview['review']['ownership_projects'][0]['ownership_label'],'已自动归属')
        self.assertEqual(self.ledger(),before)
        self.assertEqual(self.request('operation_discard',{'token':preview['token']})[0],200)
        self.assertEqual(self.ledger(),before)
        self.assertEqual(self.request('operation_apply',{'token':preview['token'],'confirmed':True})[0],400)
        preview=self.preview()
        status,applied=self.request('operation_apply',{'token':preview['token'],'confirmed':True})
        self.assertEqual(status,200,applied)
        self.assertEqual(applied['result']['updated_count'],1)
        status,projects=self.request('operation_read?name=entity_records&kind=projects')
        self.assertEqual(status,200,projects)
        self.assertEqual(projects[0]['ownership_status'],'inherited')
        self.assertFalse(projects[0]['assignable'])
        self.assertEqual(self.request('operation_read?name=entity_records&kind=contracts&pending=1')[1],[])
        overview=self.request('operation_read?name=entity_overview')[1]
        self.assertEqual(overview['entities'][0]['metrics']['income_minor'],'10000000')
        self.assertEqual(overview['pending_metrics']['income_minor'],'0')
        detail=self.request('operation_read?name=entity_detail&metric=income&entity_id=1')[1]
        self.assertEqual(detail['total_minor'],'10000000')
        self.assertEqual(sum(int(row['amount_minor']) for row in detail['rows']),int(detail['total_minor']))

    def test_read_only_routes_refuse_ownership_writes(self):
        self.server.read_only=True
        before=self.ledger()
        self.assertEqual(self.request('operation_read?name=entity_overview')[0],200)
        self.assertEqual(self.request('operation_read?name=entity_detail&metric=income&entity_id=all')[0],200)
        self.assertEqual(self.request('operation_preview',{'name':'entity_assign',
            'record':{'kind':'contracts','ids':[1],'entity_id':1,'reason':'不应写入'}})[0],403)
        self.assertEqual(self.ledger(),before)

    def test_large_minor_amounts_are_transported_as_exact_strings(self):
        from db.connection import db_transaction
        amount=9_007_199_254_740_993
        with db_transaction() as conn:
            conn.execute('UPDATE settlements SET amount_minor=? WHERE id=1',(amount,))
        overview=self.request('operation_read?name=entity_overview')[1]
        detail=self.request('operation_read?name=entity_detail&metric=income&entity_id=all')[1]
        self.assertEqual(overview['total_metrics']['income_minor'],str(amount))
        self.assertEqual(detail['rows'][0]['amount_minor'],str(amount))
        self.assertEqual(detail['total_minor'],str(amount))

    def test_contract_project_reassignment_is_rejected_without_any_partial_write(self):
        before=self.ledger()
        status,result=self.request('operation_preview',{'name':'entity_assign',
            'record':{'kind':'projects','ids':[1],'entity_id':1,'reason':'不能重复归集'}})
        self.assertEqual(status,400,result)
        self.assertIn('项目已关联合同',result['error'])
        self.assertEqual(self.ledger(),before)

    def test_entity_page_assets_are_available_to_the_browser(self):
        with urllib.request.urlopen(self.server.origin+'/') as response:
            html=response.read().decode('utf-8')
        for filename in ('entities.js','entities.css'):
            self.assertIn('/'+filename,html)
            with urllib.request.urlopen(self.server.origin+'/'+filename) as response:
                self.assertEqual(response.status,200)
                self.assertTrue(response.read())


if __name__=='__main__':
    unittest.main()
