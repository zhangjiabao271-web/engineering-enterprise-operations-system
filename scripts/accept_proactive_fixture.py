"""Disposable proactive-workbench fixture. Never imports model credentials."""
import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from datetime import timedelta
from pathlib import Path


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    folder=Path(parser.parse_args().output).resolve();root=Path(__file__).resolve().parents[1]
    if folder.exists() or not folder.is_relative_to(root/'qa'):raise ValueError('请选择qa下新的验收目录')
    folder.mkdir(parents=True)
    database=folder/'synthetic.db'
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(database),SUPPLY_CHAIN_ATTACHMENTS_PATH=str(folder/'attachments'),
                      SUPPLY_CHAIN_BACKUP_DIR=str(folder/'backups'))
    sys.path.insert(0,str(root))
    from db.schema import init_db
    from db.connection import db_read
    from web_workbench.synthetic import seed
    from web_workbench.server import Handler,WorkbenchServer
    from services import assistant_entry_service as entries,operating_attention_service as attention
    from services import web_workbench_service as business
    from services.web_workbench_operations import database_fingerprint
    init_db();seed()
    template=folder/'template.db'
    with closing(sqlite3.connect(database)) as source,closing(sqlite3.connect(template)) as target:source.backup(target)
    original_extract=entries.extract

    class FakeClient:
        def business_query_plan(self,messages):
            question=json.loads(messages[-1]['content'])['question']
            if '工天和回款' in question:
                return {'records':[
                    {'module':'receipts','data':{'project':'厂房钢结构','date':attention.today(),
                        'amount':'120000','payment_method':'银行转账'}},
                    {'module':'worklogs','data':{'project':'厂房钢结构','date':attention.today(),
                        'workers':['安装工'],'days':'0.5','site':'合成批量工地','is_overtime':False}}],
                    'questions':['另外一笔均摊费用请使用费用入口核对'] if '另有均摊' in question else []}
            records=[{'module':'costs','data':{'project':'厂房钢结构','date':attention.today(),
                'amount':'470','category':'管理与财务 / 业务招待','description':'合成买烟','paid':True}},
                {'module':'purchases','data':{'project':'厂房钢结构','supplier':'钢材供应商','date':attention.today(),
                 'payment_method':'现金','payment_status':'已付款','invoice_status':'有发票',
                 'items':[{'name':'合成油漆','total_amount':'3650','price_basis':'inclusive',
                           'tax_percent':None if '缺税率' in question else '1'}]}}]
            return {'records':records,'questions':[]}
    entries.extract=lambda question,**kwargs:original_extract(question,client=FakeClient(),previous=kwargs.get('previous'))

    class FixtureHandler(Handler):
        def get_api(self,path,query):
            if path=='/api/qa_drafts':
                with db_read() as conn:
                    self.send_json([dict(row) for row in conn.execute('SELECT state,request_key FROM assistant_entry_drafts')])
                return
            if path=='/api/qa_state':
                with db_read() as conn:
                    self.send_json({'fingerprint':database_fingerprint(conn),
                        'costs':conn.execute('SELECT COUNT(*) FROM cost_entries').fetchone()[0],
                        'purchases':conn.execute('SELECT COUNT(*) FROM purchase_orders').fetchone()[0],
                        'funds':conn.execute('SELECT COUNT(*) FROM fund_transactions').fetchone()[0]})
                return
            super().get_api(path,query)

        def post_api(self,path,data):
            if path=='/api/qa_prior_checks':
                day=(attention.clock().date()-timedelta(days=1)).isoformat()
                with db_read() as conn:
                    bases={kind:attention._daily_basis(conn,kind,day)[0] for kind in attention.DAILY}
                for kind,basis in bases.items():
                    attention.set_check(kind,day,'later',basis)
                return day
            if path=='/api/qa_reset':
                with closing(sqlite3.connect(template)) as source,closing(sqlite3.connect(database)) as target:source.backup(target)
                self.server.assistant.close()
                from web_workbench.assistant import WebAssistant
                self.server.assistant=WebAssistant()
                return True
            if path=='/api/qa_resolve_contract':
                from services.attachment_service import add_attachment
                return add_attachment('contract',1,root/'WORKFLOW.md',category='合成附件')
            if path=='/api/qa_add_worklog':
                return business.save_record('worklogs',{'worker_id':1,'project_id':1,
                    'work_date':attention.today(),'work_days':'1','construction_site':'合成自检工地'})
            return super().post_api(path,data)

    server=WorkbenchServer(0,read_only=False,demo=True,staging=folder/'staging')
    server.RequestHandlerClass=FixtureHandler
    print(json.dumps({'url':server.origin,'synthetic':True,'mock_model':True,'database':str(database)},ensure_ascii=False),flush=True)
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':main()
