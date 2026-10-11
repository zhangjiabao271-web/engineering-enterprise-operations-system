"""Test-only fixture server; production server has no QA endpoints.

Always builds a new synthetic DB, and exposes the unchanged production writes
through its real HTTP handler. The browser drives ordinary visible controls.
"""
import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    output=Path(args.output).resolve()
    root=Path(__file__).resolve().parents[1]
    allowed=((root/'qa').resolve(),Path(os.environ['TEMP']).resolve())
    if output.exists() or not any(output.is_relative_to(root) for root in allowed):
        raise ValueError('验收必须使用工作区或临时目录下新的空目录，不能选择已有数据库')
    output.mkdir(parents=True)
    database=output/'synthetic.db'
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(database),SUPPLY_CHAIN_ATTACHMENTS_PATH=str(output/'attachments'),
                      SUPPLY_CHAIN_BACKUP_DIR=str(output/'backups'),PYTHONIOENCODING='utf-8')
    sys.path.insert(0,str(root))
    from db.schema import init_db
    from db.connection import db_read
    from web_workbench.synthetic import seed
    from web_workbench.server import Handler, WorkbenchServer
    from services import web_workbench_service as business
    from services.web_workbench_operations import OPERATIONS, database_fingerprint
    from tests.web_write_cases import operation_fixture, operation_facts, form_fixture, form_facts
    init_db();seed()
    template=output/'template.db'
    with closing(sqlite3.connect(database)) as source,closing(sqlite3.connect(template)) as target:
        source.backup(target)
    cases=[{'name':name,'kind':'operation','module':definition['module']} for name,definition in OPERATIONS.items() if not definition['read']]
    for module,definition in business.schema().items():
        for edit,capability in ((False,'can_create'),(True,'can_update')):
            if definition[capability]:cases.append({'name':('edit_' if edit else 'create_')+module,'kind':'edit' if edit else 'create','module':module})
    cases.extend([{'name':'inspection','kind':'action','module':'construction'},
        {'name':'plan_cancel','kind':'action','module':'plans'}, {'name':'project_site','kind':'action','module':'projects'}])

    class FixtureHandler(Handler):
        def get_api(self,path,query):
            if path=='/api/qa_cases':
                self.send_json(cases);return
            if path=='/api/qa_state':
                with db_read() as conn:
                    self.send_json({'digest':database_fingerprint(conn)});return
            if path=='/api/qa_facts':
                fixture=self.server.fixture
                if fixture['kind']=='operation':
                    with db_read() as conn:
                        actual,expected=operation_facts(fixture['name'],business,fixture['record'],conn)
                elif fixture['kind']=='action':
                    with db_read() as conn:
                        if fixture['name']=='inspection':
                            actual=list(conn.execute('SELECT inspection_status,inspection_date,inspector FROM construction_records WHERE id=1').fetchone())
                            expected=['已验收','2026-10-03','合成验收人']
                        elif fixture['name']=='plan_cancel':
                            actual=conn.execute('SELECT status FROM fund_plans WHERE id=?',(fixture['id'],)).fetchone()[0]
                            expected='cancelled'
                        else:
                            actual=conn.execute("SELECT COUNT(*) FROM project_sites WHERE project_id=1 AND name='合成新增地点'").fetchone()[0]
                            expected=1
                else:
                    actual,expected=form_facts(fixture['module'],business,self.server.last_saved_id)
                self.send_json({'actual':actual,'expected':expected});return
            super().get_api(path,query)

        def post_api(self,path,data):
            if path=='/api/qa_fixture':
                case=next(case for case in cases if case['name']==data['name'])
                with closing(sqlite3.connect(template)) as source,closing(sqlite3.connect(database)) as target:
                    source.backup(target)
                self.server.previews.clear()
                self.server.last_saved_id=None
                if case['kind']=='operation':fixture={**case,'record':operation_fixture(case['name'],business)}
                elif case['kind']=='action':
                    record_id=1
                    if case['name']=='inspection':
                        values={'inspection_status':'已验收','inspection_date':'2026-10-03','inspector':'合成验收人','inspection_notes':'合成验收依据'}
                    elif case['name']=='plan_cancel':
                        values={'reason':'合成取消计划依据'}
                        record_id=business.save_record('plans',{'direction':'out','title':'合成网页取消计划','amount':'100.01'})
                    else:
                        values={'site_name':'合成新增地点','address':'合成地址'}
                    fixture={**case,'id':record_id,'record':values}
                else:fixture={**case,**form_fixture(case['module'],business,edit=case['kind']=='edit')}
                self.server.fixture=fixture
                return fixture
            result=super().post_api(path,data)
            if path=='/api/save':self.server.last_saved_id=result
            return result

    server=WorkbenchServer(0,read_only=False,demo=True,staging=output/'staging')
    server.RequestHandlerClass=FixtureHandler
    print(json.dumps({'url':server.origin,'synthetic':True,'database':str(database),'cases':len(cases)},ensure_ascii=False),flush=True)
    try:server.serve_forever()
    finally:server.server_close()


if __name__=='__main__':main()
