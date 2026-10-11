"""Fresh synthetic ledger for project-table and read-only drill-down UI checks."""

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
    cash = business.save_record("projects", {"name": "合成验收 · 长项目名称的零星厂区雨棚及围护维修工程",
        "project_code": "DEMO-CASH", "status": "进行中", "business_mode": "cash"})
    business.save_record("settlements", {"project_id": cash, "amount": "20237.70",
        "settlement_date": "2026-10-09", "basis": "合成结算，非真实业务"})
    business.save_record("receipts", {"project_id": cash, "amount": "5000.00",
        "receipt_date": "2026-10-10", "payment_method": "现金"})
    history = business.save_record("projects", {"name": "合成验收 · 已完工项目", "status": "已完工"})
    for index in range(42):
        business.save_record("projects", {"name": f"合成验收 · 待施工项目{index + 1:02d}",
            "project_code": f"EMPTY-{index + 1:02d}", "status": "筹备中"})
    server = WorkbenchServer(0, read_only=True, demo=True, staging=output / "staging")
    print(json.dumps({"url": server.origin, "output": str(output), "cash_project": cash,
                      "historical_project": history, "synthetic": True}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
