from datetime import date
from pathlib import Path
import sys


def main():
    project_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(project_root))

    from db.connection import get_connection
    from services import business_knowledge_service

    today = date.today()
    question = "今年买材料花了多少钱"
    knowledge = business_knowledge_service.retrieve_business_knowledge(question)
    procurement = knowledge["procurement"]
    if procurement.get("status") != "aggregate":
        raise RuntimeError("采购总额问题没有进入 aggregate 意图")
    calculated = procurement["candidates"][0]

    start_date = f"{today.year}-01-01"
    end_date = today.isoformat()
    conn = get_connection()
    material = conn.execute(
        """SELECT COALESCE(SUM(poi.line_amount_cents), 0)
           FROM purchase_orders po
           JOIN purchase_order_items poi ON poi.purchase_order_id=po.id
           WHERE po.status='有效' AND po.purchase_date BETWEEN ? AND ?""",
        (start_date, end_date),
    ).fetchone()[0]
    freight = conn.execute(
        """SELECT COALESCE(SUM(po.freight_amount_cents), 0)
           FROM purchase_orders po
           WHERE po.status='有效' AND po.purchase_date BETWEEN ? AND ?""",
        (start_date, end_date),
    ).fetchone()[0]
    order_count = conn.execute(
        """SELECT COUNT(*) FROM purchase_orders po
           WHERE po.status='有效' AND po.purchase_date BETWEEN ? AND ?""",
        (start_date, end_date),
    ).fetchone()[0]
    conn.close()

    expected_total = int(material) + int(freight)
    if calculated["tax_inclusive_material_amount_cents"] != int(material):
        raise RuntimeError("含税材料金额与独立 SQL 汇总不一致")
    if calculated["freight_amount_cents"] != int(freight):
        raise RuntimeError("运费与独立 SQL 汇总不一致")
    if calculated["procurement_total_cents"] != expected_total:
        raise RuntimeError("采购总额与独立 SQL 汇总不一致")
    if calculated["order_count"] != int(order_count):
        raise RuntimeError("采购单数量与独立 SQL 汇总不一致")
    print(
        "Procurement aggregate validation passed:",
        {
            "material_cents": int(material),
            "freight_cents": int(freight),
            "total_cents": expected_total,
            "order_count": int(order_count),
        },
    )


if __name__ == "__main__":
    main()
