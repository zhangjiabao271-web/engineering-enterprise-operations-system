"""Create and serve a fresh, writable synthetic ledger for entity UI acceptance."""
import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    output=args.output.resolve()
    if output.exists() or not output.is_relative_to(root/'qa'):
        raise ValueError('只允许qa下全新的合成验收目录')
    output.mkdir(parents=True)
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(output/'synthetic.db'),
        SUPPLY_CHAIN_ATTACHMENTS_PATH=str(output/'attachments'),SUPPLY_CHAIN_BACKUP_DIR=str(output/'backups'))
    sys.path.insert(0,str(root))
    logging.disable(logging.CRITICAL)
    from db.schema import init_db
    from services import web_workbench_service as business, operating_entity_service as ownership
    from services import input_invoice_service
    from web_workbench.synthetic import seed
    from web_workbench.server import Handler, WorkbenchServer
    from db.connection import db_read
    from services.web_workbench_operations import database_fingerprint
    init_db()
    seed()
    business.save_record('receipts',{'project_id':1,'contract_id':1,'settlement_id':1,
        'amount':'25000.01','receipt_date':'2026-10-10','payment_method':'银行转账'})
    cash=business.save_record('projects',{'name':'合成演示 · 已完工现金维修工程','status':'已完工',
        'business_mode':'cash','invoice_policy':'not_required'})
    ownership.assign_records('projects',[cash],2,'合成零星工程主体')
    business.save_record('settlements',{'project_id':cash,'amount':'75000',
        'settlement_date':'2026-09-20','basis':'合成最终结算'})
    business.save_record('receipts',{'project_id':cash,'amount':'60000','receipt_date':'2026-09-21',
        'payment_method':'现金'})
    loose=business.save_record('projects',{'name':'合成演示 · 无合同待确认项目','status':'进行中',
        'business_mode':'cash','invoice_policy':'not_required'})
    second=business.save_record('projects',{'name':'合成演示 · 示例安装厂区工程','status':'进行中','customer_partner_id':1})
    contract=business.save_record('contracts',{'name':'合成演示 · 示例安装年度框架合同',
        'contract_no':'ENTITY-HT-002','customer_partner_id':1,'contract_type':'annual','pricing_mode':'actual','amount':'0',
        'sign_date':'2026-10-01','status':'active','entity_id':2})
    business.save_record('allocations',{'contract_id':contract,'project_id':second,'amount':'0'})
    business.save_record('settlements',{'project_id':second,'contract_id':contract,'amount':'15000',
        'settlement_date':'2026-10-02','basis':'合成确认'})
    business.save_record('costs',{'project_ids':[1,cash],'allocation_method':'equal','amount':'1000.03',
        'category':'管理费','cost_date':'2026-09-20'})
    ownership.assign_records('purchase_orders',[1],1,'合成票据实际购买主体')
    input_invoice_service.create_invoice({'invoice_no':'12345678901234567890','invoice_date':'2026-10-03',
        'entity_id':1,'supplier_id':2,'buyer_name':'示例钢结构有限公司',
        'seller_name':'合成演示 · 钢材供应商','net_amount':'100','tax_amount':'13','amount':'113','tax_rate_label':'13%'},
        [{'kind':'purchase_orders','record_id':1,'amount':'113'}])
    for index in range(54):
        business.save_record('contracts',{'name':f'合成演示 · 分页验收合同{index+1:02d}',
            'customer_partner_id':1,'amount':'100','sign_date':'2026-10-01','status':'active'})
    class AcceptanceHandler(Handler):
        def get_api(self,path,query):
            if path=='/api/qa_state':
                with db_read() as conn:
                    self.send_json({'digest':database_fingerprint(conn)})
                return
            super().get_api(path,query)

    server=WorkbenchServer(0,read_only=False,demo=True,staging=output/'staging')
    server.RequestHandlerClass=AcceptanceHandler
    print(json.dumps({'url':server.origin,'output':str(output),'synthetic':True,
        'pending_contract':1,'cash_project':cash,'loose_project':loose},ensure_ascii=False),flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=='__main__':
    main()
