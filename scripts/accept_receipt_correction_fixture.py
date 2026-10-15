"""Disposable receipt-correction and initial-setup browser fixture."""
import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    folder = args.output.resolve()
    if folder.exists() or not folder.is_relative_to(root / 'qa'):
        raise ValueError('请选择qa下全新的合成验收目录')
    folder.mkdir(parents=True)
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(folder/'synthetic.db'), SUPPLY_CHAIN_ATTACHMENTS_PATH=str(folder/'attachments'))
    sys.path.insert(0,str(root))
    logging.disable(logging.CRITICAL)
    from db.schema import init_db
    from db.connection import db_read
    from services import web_workbench_service as b
    from services.web_workbench_operations import database_fingerprint
    from web_workbench.server import Handler, WorkbenchServer
    from web_workbench.synthetic import seed
    init_db()
    seed()

    def case(label):
        project = b.save_record('projects', {'name':label,'status':'进行中','customer_partner_id':1})
        contract = b.save_record('contracts', {'name':label+'年度合同','contract_type':'annual','pricing_mode':'actual',
            'income_mode':'receipt','amount':'0','sign_date':'2026-10-14','status':'active','customer_partner_id':1})
        b.save_record('allocations',{'project_id':project,'contract_id':contract,'amount':'0'})
        receipt = b.save_record('receipts',{'project_id':project,'contract_id':contract,'amount':'200','receipt_date':'2026-10-14'})
        return project, contract, receipt
    first = case('合成可更正项目')
    blocked = case('合成有开票项目')
    b.save_record('invoices',{'project_id':blocked[0],'contract_id':blocked[1], 'invoice_no':'1234567820261014',
        'invoice_date':'2026-10-14','amount':'200'})

    class FixtureHandler(Handler):
        def get_api(self,path,query):
            if path == '/api/qa_state':
                with db_read() as conn:
                    result={'synthetic':True,'fingerprint':database_fingerprint(conn),'first_id':first[2],'blocked_id':blocked[2],
                        'receipts':[dict(row) for row in conn.execute('SELECT id,amount_minor,status FROM receipts ORDER BY id')],
                        'settled':{label:b.contracts.get_contract(case[1])['settled_minor'] for label,case in [('first',first),('blocked',blocked)]},
                        'entities':b.entities.list_entities()}
                self.send_json(result)
                return
            super().get_api(path,query)
    server=WorkbenchServer(0,read_only=False,demo=True,staging=folder/'staging')
    server.RequestHandlerClass=FixtureHandler
    print(json.dumps({'url':server.origin,'synthetic':True},ensure_ascii=False),flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
