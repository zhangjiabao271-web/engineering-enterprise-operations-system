"""Serve a new synthetic ledger for cockpit UI acceptance."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.exists() or not output.is_relative_to(root / "qa"):
        raise ValueError("只允许qa下全新的合成验收目录")
    output.mkdir(parents=True)
    os.environ.update(SUPPLY_CHAIN_DB_PATH=str(output / "synthetic.db"),
                      SUPPLY_CHAIN_ATTACHMENTS_PATH=str(output / "attachments"),
                      SUPPLY_CHAIN_BACKUP_DIR=str(output / "backups"))
    sys.path.insert(0, str(root))
    logging.disable(logging.CRITICAL)
    from db.schema import init_db
    from services import web_workbench_service as business
    from web_workbench.synthetic import seed
    from web_workbench.server import WorkbenchServer

    init_db()
    seed()
    business.save_record("receipts", {"project_id": 1, "contract_id": 1, "settlement_id": 1,
        "amount": "25000.01", "receipt_date": "2026-10-10", "payment_method": "银行转账"})
    project = business.save_record("projects", {"name": "合成演示 · 已完工园区维修现金工程",
        "customer_name": "合成演示 · 科技有限公司", "status": "已完工",
        "business_mode": "cash", "invoice_policy": "not_required"})
    business.save_record("settlements", {"project_id": project, "amount": "75000",
        "settlement_date": "2026-09-20", "basis": "合成演示最终结算"})
    business.save_record("costs", {"project_ids": [1, project], "allocation_method": "equal",
        "amount": "1000.01", "category": "管理费", "cost_date": "2026-09-20"})
    business.save_record("costs", {"amount": "1572.00", "category": "管理费", "cost_date": "2026-10-11"})
    server = WorkbenchServer(0, read_only=True, demo=True, staging=output / "staging")
    print(json.dumps({"url": server.origin, "output": str(output), "synthetic": True}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
