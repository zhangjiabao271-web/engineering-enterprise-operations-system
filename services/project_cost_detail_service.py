"""Read-only, full-lifecycle cost drill-down for one project."""

from db.connection import db_read
from services import project_profit_service


def _distribute(total, weights):
    """Allocate integer cents by cumulative weight, keeping the final cent."""
    if not weights:
        return []
    positive = [max(0, int(weight or 0)) for weight in weights]
    if not sum(positive):
        positive = [1] * len(weights)
    denominator = sum(positive)
    cumulative = previous = 0
    shares = []
    for weight in positive:
        cumulative += weight
        current = total * cumulative // denominator
        shares.append(current - previous)
        previous = current
    return shares


def _purchase_rows(conn, project_id):
    orders = conn.execute(
        """SELECT ppc.*, po.purchase_date, po.order_no,
                  po.merchant_name_snapshot, po.payment_status
           FROM purchase_project_costs ppc
           JOIN purchase_orders po ON po.id=ppc.purchase_order_id
           WHERE ppc.project_id=? AND po.status='active'
           ORDER BY po.purchase_date DESC, po.id DESC""",
        (project_id,),
    ).fetchall()
    items_by_order = {}
    for item in conn.execute(
        """SELECT ppc.purchase_order_id, poi.material_name_snapshot,
                  poi.specification_snapshot, poi.cost_category, poi.quantity,
                  poi.unit_snapshot, poi.purpose, poi.material_amount_cents,
                  poi.tax_amount_cents
           FROM purchase_project_costs ppc
           JOIN purchase_orders po ON po.id=ppc.purchase_order_id
           JOIN purchase_order_items poi ON poi.purchase_order_id=po.id
           WHERE ppc.project_id=? AND po.status='active'
           ORDER BY ppc.purchase_order_id, poi.id""",
        (project_id,),
    ):
        items_by_order.setdefault(item["purchase_order_id"], []).append(item)
    result = []
    for order in orders:
        items = items_by_order.get(order["purchase_order_id"], [])
        material_shares = _distribute(
            order["material_minor"], [item["material_amount_cents"] for item in items]
        )
        tax_shares = _distribute(
            order["tax_minor"], [item["tax_amount_cents"] for item in items]
        )
        for item, material, tax in zip(items, material_shares, tax_shares):
            result.append({
                "date": order["purchase_date"],
                "source_no": order["order_no"],
                "counterparty": order["merchant_name_snapshot"],
                "category": item["cost_category"] or "材料费",
                "description": item["material_name_snapshot"],
                "specification": item["specification_snapshot"] or "",
                "quantity": item["quantity"],
                "unit": item["unit_snapshot"] or "",
                "purpose": item["purpose"] or "",
                "material_minor": material,
                "tax_minor": tax,
                "freight_minor": 0,
                "amount_minor": material + tax,
                "payment_status": order["payment_status"],
                "allocation_method": order["allocation_method"],
            })
        if order["freight_minor"]:
            result.append({
                "date": order["purchase_date"],
                "source_no": order["order_no"],
                "counterparty": order["merchant_name_snapshot"],
                "category": "采购运费",
                "description": "运费",
                "specification": "",
                "quantity": None,
                "unit": "",
                "purpose": "",
                "material_minor": 0,
                "tax_minor": 0,
                "freight_minor": order["freight_minor"],
                "amount_minor": order["freight_minor"],
                "payment_status": order["payment_status"],
                "allocation_method": order["allocation_method"],
            })
    return result


def _labor_rows(conn, project_id):
    rows = conn.execute(
        """SELECT wl.id, wl.work_date, wl.work_days, wl.is_overtime,
                  wl.work_type, wl.construction_site, wl.notes,
                  wl.amount, wl.amount_minor, w.name AS worker_name
           FROM work_logs wl JOIN workers w ON w.id=wl.worker_id
           WHERE wl.project_id=? AND COALESCE(wl.status, 'active')='active'
           ORDER BY wl.work_date DESC, wl.id DESC""",
        (project_id,),
    ).fetchall()
    return [{
        "date": row["work_date"],
        "source_no": f"GT-{row['id']:06d}",
        "worker": row["worker_name"],
        "days": row["work_days"],
        "is_overtime": bool(row["is_overtime"]),
        "work_type": row["work_type"] or "",
        "site": row["construction_site"] or "",
        "notes": row["notes"] or "",
        "amount_minor": (
            row["amount_minor"] if row["amount_minor"] is not None
            else project_profit_service._legacy_minor_units(row["amount"])
        ),
    } for row in rows]


def _other_rows(conn, project_id):
    rows = conn.execute(
        """SELECT ce.id, ce.cost_date, ce.cost_no, ce.category,
                  ce.counterparty_name_snapshot, ce.vehicle_no, ce.notes,
                  cal.amount_minor, cal.allocation_method
           FROM cost_entries ce
           JOIN cost_allocation_lines cal ON cal.cost_entry_id=ce.id
           WHERE cal.project_id=? AND cal.status='active' AND ce.status='active'
           UNION ALL
           SELECT ce.id, ce.cost_date, ce.cost_no, ce.category,
                  ce.counterparty_name_snapshot, ce.vehicle_no, ce.notes,
                  ce.amount_minor, 'direct' AS allocation_method
           FROM cost_entries ce
           WHERE ce.project_id=? AND ce.status='active'
             AND NOT EXISTS (
                 SELECT 1 FROM cost_allocation_lines cal
                 WHERE cal.cost_entry_id=ce.id AND cal.status='active'
             )
           ORDER BY cost_date DESC, id DESC""",
        (project_id, project_id),
    ).fetchall()
    return [{
        "date": row["cost_date"],
        "source_no": row["cost_no"],
        "category": row["category"],
        "counterparty": row["counterparty_name_snapshot"] or "",
        "vehicle_no": row["vehicle_no"] or "",
        "notes": row["notes"] or "",
        "amount_minor": row["amount_minor"],
        "allocation_method": row["allocation_method"],
    } for row in rows]


def get_project_cost_details(project_id):
    """Return current project shares; no payment or cash fact is inferred."""
    with db_read() as conn:
        project = conn.execute(
            "SELECT id, name, project_code FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if not project:
            raise ValueError("项目不存在")
        purchase = _purchase_rows(conn, project_id)
        labor = _labor_rows(conn, project_id)
        other = _other_rows(conn, project_id)
        summary = project_profit_service._project_summary_from_connection(
            conn, project_id, project_profit_service._labor_allocation(conn)[0]
        )
        totals = {
            "purchase": sum(row["amount_minor"] for row in purchase),
            "labor": sum(row["amount_minor"] for row in labor),
            "other": sum(row["amount_minor"] for row in other),
        }
        expected = {
            "purchase": summary["purchase_cost_minor"],
            "labor": summary["labor_cost_minor"],
            "other": summary["other_cost_minor"],
        }
        if totals != expected:
            raise ValueError("项目成本明细与汇总不一致，请先核对原始记录")
        return {
            "project": dict(project),
            "purchase": purchase,
            "labor": labor,
            "other": other,
            "totals": totals,
            "total_cost_minor": summary["total_cost_minor"],
        }
