"""Fresh synthetic catalog for supplier-dependent purchase-form acceptance."""
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
        raise ValueError('只允许qa下全新合成验收目录')
    output.mkdir(parents=True)
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(output/'synthetic.db'),
        SUPPLY_CHAIN_ATTACHMENTS_PATH=str(output/'attachments'),SUPPLY_CHAIN_BACKUP_DIR=str(output/'backups'))
    sys.path.insert(0,str(root))
    logging.disable(logging.CRITICAL)
    from db.schema import init_db
    from db.connection import db_read,db_transaction
    from services import web_workbench_service as business
    from services.web_workbench_operations import database_fingerprint
    from web_workbench.server import Handler,WorkbenchServer
    from web_workbench.synthetic import seed
    init_db()
    seed()
    second=business.save_record('suppliers',{'name':'合成演示 · 另一家五金供应商'})
    empty=business.save_record('suppliers',{'name':'合成演示 · 暂无报价供应商'})
    first_offer=business.save_record('offers',{'supplier_id':2,'name':'合成螺杆','specification':'M24',
        'unit':'支','price':'9.70','price_basis':'exclusive','tax_rate_percent':'10'})
    second_offer=business.save_record('offers',{'supplier_id':second,'name':'合成螺杆','specification':'M24',
        'unit':'支','price':'17.00','price_basis':'inclusive','tax_rate_percent':'13'})
    historical_offer=business.save_record('offers',{'supplier_id':2,'name':'合成停用材料','unit':'支',
        'price':'10.00','price_basis':'inclusive','tax_rate_percent':'13'})
    historical=business.save_record('purchases',{'project_id':1,'supplier_id':2,'purchase_date':'2026-10-01',
        'items':[{'product_id':historical_offer,'material_name_snapshot':'合成停用材料',
                  'unit_snapshot':'支','quantity':'2','unit_price':'8.88','price_basis':'inclusive','tax_percent':'13'}]})
    with db_transaction() as conn:
        conn.execute("UPDATE supplier_offers SET status='inactive' WHERE id=?",(historical_offer,))
    class AcceptanceHandler(Handler):
        def get_api(self,path,query):
            if path=='/api/qa_state':
                with db_read() as conn:
                    self.send_json({'digest':database_fingerprint(conn)})
                return
            super().get_api(path,query)
    server=WorkbenchServer(0,read_only=False,demo=True,staging=output/'staging')
    server.RequestHandlerClass=AcceptanceHandler
    print(json.dumps({'url':server.origin,'synthetic':True,'supplier_a':2,'supplier_b':second,
        'supplier_empty':empty,'offer_a':first_offer,'offer_b':second_offer,'historical_purchase':historical,
        'historical_offer':historical_offer},ensure_ascii=False),flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=='__main__':
    main()
