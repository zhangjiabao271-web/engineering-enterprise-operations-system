"""Contract and cash business totals, on a fresh synthetic ledger only."""
import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True,type=Path)
    output=parser.parse_args().output.resolve()
    root=Path(__file__).resolve().parents[1]
    if output.exists() or not output.is_relative_to(root/'qa'):
        raise ValueError('仅允许qa下全新合成目录')
    output.mkdir(parents=True)
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(output/'synthetic.db'),
        SUPPLY_CHAIN_ATTACHMENTS_PATH=str(output/'attachments'),SUPPLY_CHAIN_BACKUP_DIR=str(output/'backups'))
    sys.path.insert(0,str(root))
    logging.disable(logging.CRITICAL)
    from db.schema import init_db
    from db.connection import db_read
    from services import web_workbench_service as business
    from services.operating_entity_service import assign_records
    from services.web_workbench_operations import database_fingerprint
    from web_workbench.server import Handler,WorkbenchServer
    from web_workbench.synthetic import seed
    init_db()
    seed()
    items=[]
    for suffix,agreed,income,receipts in [('甲','78000','78000',['50000','28000']),
        ('乙','98683','97100',['60000','37100']),('预收','86000',None,['40000'])]:
        customer=business.save_record('customers',{'legal_name':'合成演示 · 现金客户'+suffix})
        project=business.save_record('projects',{'name':'合成演示 · 现金项目'+suffix,
            'customer_partner_id':customer,'business_mode':'cash','invoice_policy':'not_required',
            'cash_agreed_amount':agreed,'status':'已完工' if income else '进行中'})
        assign_records('projects',[project],2,'合成现金业务归属')
        contract_values={'name':'合成演示 · 留存协议'+suffix,'contract_no':'DEMO-CASH-'+suffix,
            'customer_partner_id':customer,'amount':agreed,'sign_date':'2026-08-01',
            'status':'active','entity_id':2}
        contract=business.save_record('contracts',contract_values)
        if income:
            business.contracts.update_contract(contract,{**contract_values,'status':'completed'})
        settlement=None
        if income:
            settlement=business.save_record('settlements',{'project_id':project,'amount':income,
                'settlement_date':'2026-09-28','basis':'合成最终结算'})
        for amount in receipts:
            record={'project_id':project,'amount':amount,'receipt_date':'2026-09-30','payment_method':'现金'}
            if settlement:
                record['settlement_id']=settlement
            business.save_record('receipts',record)
        items.append(dict(customer=customer,project=project,contract=contract,settlement=settlement,
                          expected_income=income or '0',expected_receipt=str(sum(int(x) for x in receipts))))

    class AcceptanceHandler(Handler):
        def get_api(self,path,query):
            if path=='/api/qa_state':
                with db_read() as conn:
                    self.send_json({'digest':database_fingerprint(conn)})
                return
            super().get_api(path,query)

    server=WorkbenchServer(0,read_only=False,demo=True,staging=output/'staging')
    server.RequestHandlerClass=AcceptanceHandler
    metadata={'url':server.origin,'synthetic':True,'cash_projects':items}
    (output/'fixture.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(metadata,ensure_ascii=False),flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=='__main__':
    main()
