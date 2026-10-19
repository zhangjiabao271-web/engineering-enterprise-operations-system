"""Isolated synthetic data for consolidated navigation and partner summaries."""
import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    output = parser.parse_args().output.resolve()
    root = Path(__file__).resolve().parents[1]
    if output.exists() or not output.is_relative_to(root / 'qa'):
        raise ValueError('仅使用qa下全新合成目录')
    output.mkdir(parents=True)
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(output/'synthetic.db'),
        SUPPLY_CHAIN_ATTACHMENTS_PATH=str(output/'attachments'),
        SUPPLY_CHAIN_BACKUP_DIR=str(output/'backups'))
    sys.path.insert(0, str(root))
    logging.disable(logging.CRITICAL)
    from db.schema import init_db
    from db.connection import db_read, db_transaction
    from services import web_workbench_service as business
    from services.web_workbench_operations import database_fingerprint
    from web_workbench.server import Handler, WorkbenchServer
    from web_workbench.synthetic import seed
    init_db()
    seed()
    second = business.save_record('customers', {'legal_name':'合成演示 · 零星工程客户'})
    pending = business.save_record('projects', {'name':'合成演示 · 尚未确认收入的项目',
        'customer_partner_id':second, 'status':'进行中'})
    business.save_record('costs', {'project_id':pending, 'category':'工程施工 / 材料辅料',
        'amount':'111.01', 'cost_date':'2026-10-02', 'notes':'合成待完善成本'})
    cash = business.save_record('projects', {'name':'合成演示 · 无合同现金工程',
        'customer_partner_id':second, 'status':'已完工', 'business_mode':'cash',
        'invoice_policy':'not_required', 'cash_agreed_amount':'1000'})
    business.save_record('settlements', {'project_id':cash, 'amount':'1000',
        'settlement_date':'2026-10-03', 'basis':'合成客户确认'})
    receipt = business.save_record('receipts', {'project_id':1, 'contract_id':1,
        'settlement_id':1, 'amount':'20000', 'receipt_date':'2026-10-04',
        'payment_method':'银行转账'})
    store = business.save_record('suppliers', {'name':'合成演示 · 五金经销门店',
        'supplier_kind':'distributor', 'category':'耗材'})
    offer = business.save_record('offers', {'supplier_id':store,'name':'合成演示 · 焊条',
        'unit':'箱','price':'100','price_basis':'inclusive','tax_rate_percent':'13'})
    purchase = business.save_record('purchases', {'project_id':1,'supplier_id':store,
        'purchase_date':'2026-10-05','payment_status':'已付款',
        'items':[{'product_id':offer,'material_name_snapshot':'合成演示 · 焊条',
        'unit_snapshot':'箱','quantity':'2','unit_price':'100','price_basis':'inclusive','tax_percent':'13'}]})
    business.partners.update_supplier(2, {'name':'合成演示 · 钢材供应商',
        'category':'钢材', 'supplier_kind':'manufacturer'})
    with db_transaction() as conn:
        conn.execute("UPDATE contracts SET sign_date='2025-12-01' WHERE id=1")

    class AcceptanceHandler(Handler):
        def get_api(self, path, query):
            if path == '/api/qa_state':
                with db_read() as conn:
                    self.send_json({'digest':database_fingerprint(conn)})
                return
            super().get_api(path, query)

    server = WorkbenchServer(0, read_only=False, demo=True, staging=output/'staging')
    server.RequestHandlerClass = AcceptanceHandler
    metadata = {'url':server.origin, 'synthetic':True, 'customer':second,
        'pending':pending, 'cash':cash, 'supplier':store, 'offer':offer,
        'purchase':purchase, 'receipt':receipt}
    (output/'fixture.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(metadata,ensure_ascii=False),flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
