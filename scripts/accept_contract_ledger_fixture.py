"""Fresh synthetic customer/year contract data for browser acceptance."""
import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(root/'qa'):
        raise ValueError('验收仅使用qa下全新的合成账本')
    output.mkdir(parents=True)
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(output/'synthetic.db'),
        SUPPLY_CHAIN_ATTACHMENTS_PATH=str(output/'attachments'),SUPPLY_CHAIN_BACKUP_DIR=str(output/'backups'))
    sys.path.insert(0,str(root))
    logging.disable(logging.CRITICAL)
    from db.schema import init_db
    from services import web_workbench_service as business, attachment_service
    from web_workbench.synthetic import seed
    from web_workbench.server import WorkbenchServer
    from pypdf import PdfWriter
    init_db()
    seed()
    business.save_record('receipts',{'project_id':1,'contract_id':1,'settlement_id':1,
        'amount':'25000.01','receipt_date':'2026-10-10','payment_method':'银行转账'})
    contract = business.save_record('contracts',{'name':'合成演示 · 跨年据实年度合同',
        'customer_partner_id':1,'pricing_mode':'actual','contract_type':'annual','amount':'0',
        'sign_date':'2025-12-20','status':'active','entity_id':2})
    business.save_record('allocations',{'contract_id':contract,'project_id':1,'amount':'0'})
    settlement = business.save_record('settlements',{'contract_id':contract,'project_id':1,
        'amount':'17000.03','settlement_date':'2026-10-03','basis':'合成跨年业务'})
    business.save_record('receipts',{'project_id':1,'contract_id':contract,'settlement_id':settlement,
        'amount':'1200.02','receipt_date':'2026-10-10','payment_method':'银行转账'})
    pdf = output/'合成合同预览.pdf'
    writer = PdfWriter()
    writer.add_blank_page(595,842)
    with pdf.open('wb') as target:
        writer.write(target)
    attachment_service.add_attachment('contract',contract,pdf,category='合同附件')
    for index in range(46):
        customer = business.save_record('customers',{'legal_name':f'合成演示 · 客户{index+1:02d}钢结构建设有限公司'})
        values = {'name':f'合成演示 · 客户{index+1:02d}厂房维修合同','contract_no':f'CL-{index+1:03d}',
            'customer_partner_id':customer,'amount':'12345.67','sign_date':'2026-09-01',
            'status':'active'}
        identity = business.save_record('contracts',values)
        if index==0:
            business.contracts.update_contract(identity,{**values,'status':'completed'})
    server = WorkbenchServer(0,read_only=False,demo=True,staging=output/'staging')
    metadata = {'url':server.origin,'synthetic':True,'contract':contract,'project':1,
                'customer':'customer:1','pdf':str(pdf)}
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
