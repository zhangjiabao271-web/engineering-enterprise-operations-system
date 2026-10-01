from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from db.connection import db_read
from db.business_facts import project_finance_rows


ENTRY_TYPES = {
    "contract_value": "合同分配额",
    "settlement": "结算确认",
    "invoice": "销项开票",
    "receipt": "收到回款",
    "other_cost": "其他成本",
    "other_payment": "其他付款",
}

def _legacy_minor_units(value):
    """Convert an existing legacy amount without applying form validation."""
    try:
        amount = Decimal(str(value or 0))
    except (InvalidOperation, TypeError):
        return 0
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def list_entries(project_id=None):
    with db_read() as conn:
        sql = """
            SELECT poe.*, p.name AS project_name
            FROM project_operating_entries poe
            JOIN projects p ON p.id=poe.project_id
            WHERE poe.status='active'
        """
        params = []
        if project_id:
            sql += " AND poe.project_id=?"
            params.append(project_id)
        sql += " ORDER BY poe.entry_date DESC, poe.id DESC"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _labor_allocation(conn):
    """Aggregate active labor entries per project.

    Attribution is explicit: only work_logs.project_id counts. Entries
    without a project stay in the unassigned (待归集) bucket — the runtime
    never guesses attribution from free-text site names.
    """
    by_project = {}
    unassigned_count = 0
    unassigned_minor = 0
    for row in conn.execute(
        """SELECT project_id, amount, amount_minor
           FROM work_logs WHERE COALESCE(status, 'active')='active'"""
    ).fetchall():
        amount_minor = (
            row["amount_minor"]
            if row["amount_minor"] is not None
            else _legacy_minor_units(row["amount"] or 0)
        )
        project_id = row["project_id"]
        if project_id:
            bucket = by_project.setdefault(
                project_id, {"amount_minor": 0, "record_count": 0}
            )
            bucket["amount_minor"] += amount_minor
            bucket["record_count"] += 1
        else:
            unassigned_count += 1
            unassigned_minor += amount_minor
    return by_project, {
        "record_count": unassigned_count,
        "amount_minor": unassigned_minor,
    }


def _manual_totals(conn, project_id):
    finance = project_finance_rows(conn, project_id)[0]
    totals = {**dict.fromkeys(ENTRY_TYPES, 0),
              'contract_value': finance['allocated_minor'], 'settlement': finance['settlement_minor'],
              'invoice': finance['invoice_minor'], 'receipt': finance['receipt_minor']}
    totals["other_cost"] = conn.execute(
        """SELECT COALESCE(SUM(amount_minor), 0)
           FROM (
               SELECT cal.amount_minor
               FROM cost_allocation_lines cal
               JOIN cost_entries ce ON ce.id=cal.cost_entry_id
               WHERE cal.project_id=? AND cal.status='active'
                 AND ce.status='active'
               UNION ALL
               SELECT ce.amount_minor
               FROM cost_entries ce
               WHERE ce.project_id=? AND ce.status='active'
                 AND NOT EXISTS (
                   SELECT 1 FROM cost_allocation_lines cal
                   WHERE cal.cost_entry_id=ce.id AND cal.status='active'
                 )
           )""",
        (project_id, project_id),
    ).fetchone()[0]
    return totals


def _purchase_totals(conn, project_id):
    row = conn.execute(
        """SELECT COALESCE(SUM(ppc.material_minor), 0) AS material_minor,
                  COALESCE(SUM(tax_minor), 0) AS tax_minor,
                  COALESCE(SUM(tax_inclusive_material_minor), 0)
                      AS tax_inclusive_material_minor,
                  COALESCE(SUM(freight_minor), 0) AS freight_minor,
                  COALESCE(SUM(cost_minor), 0) AS cost_minor,
                  COUNT(DISTINCT ppc.purchase_order_id) AS order_count
           FROM purchase_project_costs ppc
           JOIN purchase_orders po ON po.id=ppc.purchase_order_id
           WHERE ppc.project_id=? AND po.status='active'""",
        (project_id,),
    ).fetchone()
    paid = conn.execute(
        """SELECT COALESCE(SUM(ppc.cost_minor), 0)
           FROM purchase_project_costs ppc
           JOIN purchase_orders po ON po.id=ppc.purchase_order_id
           WHERE ppc.project_id=? AND po.status='active'
             AND po.payment_status='已付款'""",
        (project_id,),
    ).fetchone()[0]
    category_rows = _purchase_item_breakdown(conn, project_id, "cost_category")
    if row["freight_minor"]:
        category_rows.append(
            {"label": "采购运费", "amount_minor": row["freight_minor"]}
        )
    return {
        "cost_minor": row["cost_minor"],
        "material_minor": row["material_minor"],
        "tax_minor": row["tax_minor"],
        "tax_inclusive_material_minor": row["tax_inclusive_material_minor"],
        "freight_minor": row["freight_minor"],
        "paid_minor": paid,
        "order_count": row["order_count"],
        "categories": category_rows,
    }


def _purchase_item_breakdown(conn, project_id, field):
    rows = conn.execute(
        """SELECT poi.*, ppc.tax_inclusive_material_minor AS project_material_minor
           FROM purchase_project_costs ppc
           JOIN purchase_orders po ON po.id=ppc.purchase_order_id
           JOIN purchase_order_items poi ON poi.purchase_order_id=po.id
           WHERE ppc.project_id=? AND po.status='active'
           ORDER BY po.id, poi.id""", (project_id,)
    ).fetchall()
    return _group_purchase_items(rows, field)


def _group_purchase_items(rows, field):
    orders = {}
    for row in rows:
        orders.setdefault(row["purchase_order_id"], []).append(row)
    grouped = {}
    for order_id, items in orders.items():
        total = sum(item["line_amount_cents"] for item in items)
        cumulative = previous = 0
        for item in items:
            cumulative += item["line_amount_cents"]
            allocated = item["project_material_minor"] * cumulative // total if total else 0
            label = str(item[field] or "未分类").strip() or "未分类"
            record = grouped.setdefault(label, {"label": label, "amount_minor": 0, "orders": set()})
            record["amount_minor"] += allocated - previous
            record["orders"].add(order_id)
            previous = allocated
    result = [{"label": row["label"], "amount_minor": row["amount_minor"], "order_count": len(row["orders"])} for row in grouped.values()]
    return sorted(result, key=lambda row: (-row["amount_minor"], row["label"]))


def _purchase_material_breakdown(conn, project_id):
    return _purchase_item_breakdown(conn, project_id, "material_name_snapshot")


def _construction_totals(conn, project_id):
    return dict(
        conn.execute(
            """SELECT COUNT(*) AS record_count,
                      COALESCE(SUM(cr.work_amount_cents), 0) AS recorded_minor,
                      COALESCE(SUM(CASE WHEN cr.inspection_status='已验收'
                                        THEN cr.work_amount_cents ELSE 0 END), 0)
                          AS accepted_minor
               FROM construction_records cr
               JOIN construction_sites cs ON cs.id=cr.site_id
               WHERE cs.project_id=? AND cr.record_status='有效'""",
            (project_id,),
        ).fetchone()
    )


def _other_cost_categories(conn, project_id):
    rows = conn.execute(
        """SELECT COALESCE(category, '其他成本') AS label,
                  COALESCE(SUM(amount_minor), 0) AS amount_minor
           FROM (
               SELECT ce.category, cal.amount_minor
               FROM cost_allocation_lines cal
               JOIN cost_entries ce ON ce.id=cal.cost_entry_id
               WHERE cal.project_id=? AND cal.status='active'
                 AND ce.status='active'
               UNION ALL
               SELECT ce.category, ce.amount_minor
               FROM cost_entries ce
               WHERE ce.project_id=? AND ce.status='active'
                 AND NOT EXISTS (
                   SELECT 1 FROM cost_allocation_lines cal
                   WHERE cal.cost_entry_id=ce.id AND cal.status='active'
                 )
           )
           GROUP BY category ORDER BY amount_minor DESC""",
        (project_id, project_id),
    ).fetchall()
    return [dict(row) for row in rows]


def _project_summary_from_connection(conn, project_id, labor_by_project, bulk=None):
    project = bulk['projects'].get(project_id) if bulk else conn.execute(
        """SELECT p.id, p.project_code, p.name, p.status,
                  p.business_mode, p.invoice_policy,
                  COALESCE(bp.legal_name, p.customer_name, '') AS customer_name
           FROM projects p
           LEFT JOIN business_partners bp ON bp.id=p.customer_partner_id
           WHERE p.id=?""",
        (project_id,),
    ).fetchone()
    if not project:
        raise ValueError("项目不存在")

    if bulk:
        manual = bulk['manual'][project_id]
        purchase = bulk['purchase'][project_id]
        construction = bulk['construction'].get(
            project_id, {'record_count': 0, 'recorded_minor': 0, 'accepted_minor': 0}
        )
        materials = bulk['materials'].get(project_id, [])
        other_categories = bulk['other_categories'].get(project_id, [])
    else:
        manual = _manual_totals(conn, project_id)
        purchase = _purchase_totals(conn, project_id)
        construction = _construction_totals(conn, project_id)
        materials = _purchase_material_breakdown(conn, project_id)
        other_categories = _other_cost_categories(conn, project_id)
    labor = labor_by_project.get(
        project_id, {"amount_minor": 0, "record_count": 0}
    )
    other_cost_minor = manual["other_cost"]
    total_cost_minor = (
        purchase["cost_minor"] + labor["amount_minor"] + other_cost_minor
    )
    gross_profit_minor = manual["settlement"] - total_cost_minor
    margin = (
        gross_profit_minor / manual["settlement"] * 100
        if manual["settlement"] else None
    )
    finance = bulk['finance'][project_id] if bulk else project_finance_rows(conn, project_id)[0]
    receivable_minor = finance['receivable_minor']
    # Legacy comparison only: excludes opening funds, wages and expense payments.
    # Real account balances are computed exclusively by funds_service.
    cash_out_minor = purchase["paid_minor"]
    cash_balance_minor = manual["receipt"] - cash_out_minor
    settlement_progress = (
        manual["settlement"] / manual["contract_value"] * 100
        if manual["contract_value"] else None
    )

    return {
        "project": dict(project),
        "contract_minor": manual["contract_value"],
        "recorded_minor": construction["recorded_minor"],
        "accepted_minor": construction["accepted_minor"],
        "settlement_minor": manual["settlement"],
        "invoice_minor": manual["invoice"],
        "receipt_minor": manual["receipt"],
        "purchase_cost_minor": purchase["cost_minor"],
        "purchase_material_minor": purchase["material_minor"],
        "purchase_tax_minor": purchase["tax_minor"],
        "purchase_tax_inclusive_material_minor": purchase[
            "tax_inclusive_material_minor"
        ],
        "purchase_freight_minor": purchase["freight_minor"],
        "purchase_paid_minor": purchase["paid_minor"],
        "labor_cost_minor": labor["amount_minor"],
        "other_cost_minor": other_cost_minor,
        "total_cost_minor": total_cost_minor,
        "gross_profit_minor": gross_profit_minor,
        "gross_margin_percent": margin,
        "receivable_minor": receivable_minor,
        "cash_out_minor": cash_out_minor,
        "cash_balance_minor": cash_balance_minor,
        "settlement_progress_percent": settlement_progress,
        "purchase_order_count": purchase["order_count"],
        "labor_record_count": labor["record_count"],
        "construction_record_count": construction["record_count"],
        "purchase_categories": purchase["categories"],
        "purchase_material_breakdown": materials,
        "other_cost_categories": other_categories,
    }


def get_project_summary(project_id):
    with db_read() as conn:
        labor_by_project, unassigned_labor = _labor_allocation(conn)
        result = _project_summary_from_connection(
            conn, int(project_id), labor_by_project
        )
        result["unassigned_labor"] = unassigned_labor
        unassigned_purchase = conn.execute(
            """SELECT COUNT(*) AS order_count,
                      COALESCE(SUM(total_amount_cents), 0) AS amount_minor
               FROM purchase_orders
               WHERE project_id IS NULL AND status='active'
                 AND NOT EXISTS (
                   SELECT 1 FROM purchase_cost_allocation_lines pal
                   WHERE pal.purchase_order_id=purchase_orders.id
                     AND pal.status='active'
                 )"""
        ).fetchone()
        result["unassigned_purchase"] = dict(unassigned_purchase)
        return result


def get_portfolio_summary():
    with db_read() as conn:
        labor_by_project, unassigned_labor = _labor_allocation(conn)
        bulk = _portfolio_facts(conn)
        project_ids = [
            row["id"]
            for row in conn.execute(
                """SELECT id FROM projects
                   ORDER BY CASE status
                       WHEN '进行中' THEN 1 WHEN '筹备中' THEN 2 ELSE 3 END,
                       id DESC"""
            ).fetchall()
        ]
        rows = [
            _project_summary_from_connection(conn, project_id, labor_by_project, bulk)
            for project_id in project_ids
        ]
        return {
            "projects": rows,
            "unassigned_labor": unassigned_labor,
        }


def _portfolio_facts(conn):
    """Fetch each fact set once; query count is independent of project count."""
    finance = {r['project_id']: r for r in project_finance_rows(conn)}
    projects = {r['id']: dict(r) for r in conn.execute('''
        SELECT p.id,p.project_code,p.name,p.status,p.business_mode,p.invoice_policy,
          COALESCE(bp.legal_name,p.customer_name,'') AS customer_name
        FROM projects p LEFT JOIN business_partners bp ON bp.id=p.customer_partner_id''')}
    other_categories = {}
    for row in conn.execute('''SELECT project_id,category AS label,SUM(amount_minor) AS amount_minor FROM (
        SELECT a.project_id,c.category,a.amount_minor FROM cost_allocation_lines a
        JOIN cost_entries c ON c.id=a.cost_entry_id WHERE a.status='active' AND c.status='active'
        UNION ALL SELECT c.project_id,c.category,c.amount_minor FROM cost_entries c WHERE c.status='active'
        AND NOT EXISTS(SELECT 1 FROM cost_allocation_lines a WHERE a.cost_entry_id=c.id AND a.status='active')
        ) GROUP BY project_id,category ORDER BY amount_minor DESC'''):
        other_categories.setdefault(row['project_id'], []).append({'label': row['label'], 'amount_minor': row['amount_minor']})
    manual = {pid: {**dict.fromkeys(ENTRY_TYPES, 0), 'contract_value': f['allocated_minor'],
                   'settlement': f['settlement_minor'], 'invoice': f['invoice_minor'], 'receipt': f['receipt_minor'],
                   'other_cost': sum(r['amount_minor'] for r in other_categories.get(pid, []))}
              for pid, f in finance.items()}
    purchases = {pid: dict.fromkeys(('cost_minor','material_minor','tax_minor','tax_inclusive_material_minor',
                                    'freight_minor','paid_minor','order_count'), 0) for pid in projects}
    for row in conn.execute('''SELECT pc.project_id,SUM(pc.cost_minor) AS cost_minor,
        SUM(pc.material_minor) AS material_minor,SUM(pc.tax_minor) AS tax_minor,
        SUM(pc.tax_inclusive_material_minor) AS tax_inclusive_material_minor,SUM(pc.freight_minor) AS freight_minor,
        SUM(CASE WHEN po.payment_status='已付款' THEN pc.cost_minor ELSE 0 END) AS paid_minor,
        COUNT(DISTINCT pc.purchase_order_id) AS order_count
        FROM purchase_project_costs pc JOIN purchase_orders po ON po.id=pc.purchase_order_id
        WHERE po.status='active' GROUP BY pc.project_id'''):
        purchases[row['project_id']] = {k: row[k] for k in purchases[row['project_id']]}
    items = {}
    for row in conn.execute('''SELECT poi.*,pc.project_id,pc.tax_inclusive_material_minor AS project_material_minor
        FROM purchase_project_costs pc JOIN purchase_orders po ON po.id=pc.purchase_order_id
        JOIN purchase_order_items poi ON poi.purchase_order_id=po.id WHERE po.status='active'
        ORDER BY pc.project_id,po.id,poi.id'''):
        items.setdefault(row['project_id'], []).append(row)
    materials = {}
    for pid, purchase in purchases.items():
        purchase['categories'] = _group_purchase_items(items.get(pid, []), 'cost_category')
        if purchase['freight_minor']:
            purchase['categories'].append({'label': '采购运费', 'amount_minor': purchase['freight_minor']})
        materials[pid] = _group_purchase_items(items.get(pid, []), 'material_name_snapshot')
    construction = {r['project_id']: dict(r) for r in conn.execute('''
        SELECT s.project_id,COUNT(*) AS record_count,COALESCE(SUM(c.work_amount_cents),0) AS recorded_minor,
          SUM(CASE WHEN c.inspection_status='已验收' THEN c.work_amount_cents ELSE 0 END) AS accepted_minor
        FROM construction_records c JOIN construction_sites s ON s.id=c.site_id
        WHERE c.record_status='有效' GROUP BY s.project_id''')}
    return {'projects': projects, 'finance': finance, 'manual': manual, 'purchase': purchases,
            'materials': materials, 'construction': construction, 'other_categories': other_categories}
