"""Isolated browser ledger for the project/contract/receivable workflow."""
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
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(root / 'qa'):
        raise ValueError('仅使用qa下全新的合成验收目录')
    output.mkdir(parents=True)
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(output / 'synthetic.db'),
        SUPPLY_CHAIN_ATTACHMENTS_PATH=str(output / 'attachments'), SUPPLY_CHAIN_BACKUP_DIR=str(output / 'backups'))
    sys.path.insert(0, str(root))
    logging.disable(logging.CRITICAL)
    from db.schema import init_db
    from services import web_workbench_service as b
    from web_workbench.server import WorkbenchServer
    init_db()
    customer = b.save_record('customers', {'legal_name': '合成验收建设单位'})
    project = b.save_record('projects', {'name': '合成验收钢平台工程', 'customer_partner_id': customer, 'status': '进行中'})
    first = b.save_record('contracts', {'name': '合成早期合同', 'customer_partner_id': customer, 'project_id': project,
        'amount': '100', 'sign_date': '2025-01-01', 'status': 'active', 'entity_id': 1})
    second = b.save_record('contracts', {'name': '合成后续合同', 'customer_partner_id': customer, 'project_id': project,
        'amount': '60', 'sign_date': '2026-02-01', 'status': 'active', 'entity_id': 1})
    for contract, amount in ((first, '100'), (second, '60')):
        b.save_record('settlements', {'project_id': project, 'contract_id': contract, 'amount': amount,
            'settlement_date': '2026-10-01', 'basis': '合成确认，收入日期晚于合同'})
    unsigned = b.save_record('settlements', {'project_id': project, 'amount': '40', 'settlement_date': '2026-03-01',
        'basis': '合成已认可价款，合同尚未签订'})
    b.save_record('costs', {'project_id': project, 'amount': '25', 'category': '工程施工 / 材料辅料', 'cost_date': '2026-10-01'})
    b.save_record('receipts', {'project_id': project, 'amount': '90', 'receipt_date': '2026-04-01',
        'allocation_scope': 'project', 'entity_id': 1})
    server = WorkbenchServer(0, read_only=False, demo=True, staging=output / 'staging')
    fixture = {'url': server.origin, 'synthetic': True, 'project': project, 'customer': customer,
               'first': first, 'second': second, 'unsigned': unsigned}
    (output / 'fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
    print(json.dumps(fixture), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
