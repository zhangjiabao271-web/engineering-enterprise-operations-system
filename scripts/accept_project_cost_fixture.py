"""New synthetic project costs for browser acceptance; never opens the formal ledger."""

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
    from services import procurement_service, project_service
    from web_workbench.synthetic import seed
    from web_workbench.server import WorkbenchServer

    init_db()
    seed()
    other = project_service.create_project({"name": "合成演示 · 工具均摊项目", "status": "进行中"})
    procurement_service.add_purchase_order({
        "purchase_type": "零星采购", "project_ids": [1, other], "allocation_method": "equal",
        "merchant_name_snapshot": "合成演示 · 五金店", "purchase_date": "2026-10-10",
        "payment_status": "已付款", "freight_amount_cents": 711,
    }, [{"material_name_snapshot": "电焊机接头", "unit_snapshot": "个", "quantity": 3,
         "price_basis": "inclusive", "tax_inclusive_unit_price_cents": 1200,
         "cost_category": "工具和设备"}])
    procurement_service.add_purchase_order({
        "purchase_type": "零星采购", "project_id": 1,
        "merchant_name_snapshot": "合成演示 · 材料经营部", "purchase_date": "2026-10-10",
        "payment_status": "已付款", "freight_amount_cents": 7711,
    }, [
        {"material_name_snapshot": "彩钢瓦", "specification_snapshot": "840型 · 厚度0.426毫米",
         "unit_snapshot": "米", "quantity": 996, "price_basis": "inclusive",
         "tax_inclusive_unit_price_cents": 189, "tax_rate_bps": 900},
        {"material_name_snapshot": "H型钢", "specification_snapshot": "H300 · 过磅结算",
         "settlement_mode": "weight", "net_weight": "1.257", "weight_unit": "吨",
         "weight_unit_price": "3568.1234", "price_basis": "exclusive", "tax_rate_bps": 1300,
         "weigh_ticket_no": "DEMO-B-001", "purpose": "合成验收 · 钢平台"},
        {"material_name_snapshot": "整批钢板", "settlement_mode": "total",
         "settlement_total_cents": 988888, "price_basis": "inclusive", "tax_rate_bps": 1300},
    ])
    server = WorkbenchServer(0, read_only=True, demo=True, staging=output / "staging")
    print(json.dumps({"url": server.origin, "output": str(output), "project_id": 1,
                      "synthetic": True}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
