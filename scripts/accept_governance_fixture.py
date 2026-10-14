"""Disposable source-identification fixture; never reads a formal ledger."""
import argparse
import json
import os
import sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--read-only',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    folder=args.output.resolve()
    if folder.exists() or not folder.is_relative_to(root/'qa'):
        raise ValueError('请选择qa下全新的合成验收目录')
    folder.mkdir(parents=True)
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(folder/'synthetic.db'),
        SUPPLY_CHAIN_ATTACHMENTS_PATH=str(folder/'attachments'),SUPPLY_CHAIN_BACKUP_DIR=str(folder/'backups'))
    sys.path.insert(0,str(root))
    from db.schema import init_db
    from db.connection import db_read
    from services import web_workbench_service as business
    from services.web_workbench_operations import database_fingerprint
    from web_workbench.server import Handler,WorkbenchServer
    from web_workbench.synthetic import seed
    init_db()
    seed()
    supplier=business.save_record('suppliers',{'name':'合成五金店'})
    pending=business.procurement.add_purchase_order({
        'purchase_type':'零星采购','purchase_date':'2026-10-14','supplier_id':supplier,
        'merchant_name_snapshot':'合成五金店','freight_amount_cents':1000,'payment_status':'已付款',
    },business.prepare_purchase_items([
        {'material_name_snapshot':'合成螺杆','specification_snapshot':'M24','quantity':'12',
         'unit_snapshot':'支','unit_price':'5.50','tax_percent':'13','price_basis':'exclusive'},
        {'material_name_snapshot':'合成螺帽','specification_snapshot':'M24','quantity':'3',
         'unit_snapshot':'只','unit_price':'2','tax_percent':'1','price_basis':'inclusive'},
    ]))
    second=business.save_record('projects',{'name':'合成第二项目','status':'进行中'})
    business.procurement.add_purchase_order({
        'purchase_type':'零星采购','purchase_date':'2026-10-14','merchant_name_snapshot':'合成工具店',
        'allocation_method':'equal','project_ids':[1,second],
    },business.prepare_purchase_items([{'material_name_snapshot':'合成已均摊电焊机','quantity':'1','unit_snapshot':'台',
        'unit_price':'100.01','price_basis':'inclusive','tax_percent':'0',
        'cost_category':business.procurement.TOOL_EQUIPMENT_CATEGORY}]))
    worklog=business.labor.add_work_log({'worker_id':1,'work_date':'2026-10-14','work_days':'0.5',
        'construction_site':'合成历史工地','work_type':'合成安装任务','allow_unassigned':True})
    business.entities.assign_records('purchase_orders',[pending],1,'合成主体确认')
    entity=business.entities.list_entities()[0]
    invoice=business.inputs.create_invoice({'invoice_no':'1234567820261014','invoice_date':'2026-10-14',
        'entity_id':1,'supplier_id':supplier,'buyer_name':entity['name'],'seller_name':'合成五金店',
        'net_amount':'80','tax_amount':'10.58','amount':'90.58','tax_rate_label':'混合税率'})

    class FixtureHandler(Handler):
        def get_api(self,path,query):
            if path=='/api/qa_state':
                with db_read() as conn:
                    result={'synthetic':True,'pending_id':pending,'worklog_id':worklog,'invoice_id':invoice,
                        'fingerprint':database_fingerprint(conn),
                        'assigned_project':conn.execute('SELECT project_id FROM purchase_orders WHERE id=?',(pending,)).fetchone()[0]}
                self.send_json(result)
                return
            super().get_api(path,query)

    server=WorkbenchServer(0,read_only=args.read_only,demo=True,staging=folder/'staging')
    server.RequestHandlerClass=FixtureHandler
    print(json.dumps({'url':server.origin,'synthetic':True,'read_only':args.read_only},ensure_ascii=False),flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__=='__main__':
    main()
