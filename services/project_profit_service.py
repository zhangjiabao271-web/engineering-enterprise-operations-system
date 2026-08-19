from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from db.connection import get_connection


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
    conn = get_connection()
    try:
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
    finally:
        conn.close()


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
    totals = {key: 0 for key in ENTRY_TYPES}
    totals["contract_value"] = conn.execute(
        """SELECT COALESCE(SUM(allocated_amount_minor), 0)
           FROM contract_project_allocations
           WHERE project_id=? AND status='active'""",
        (project_id,),
    ).fetchone()[0]
    totals["settlement"] = conn.execute(
        """SELECT COALESCE(SUM(amount_minor), 0) FROM settlements
           WHERE project_id=? AND status='active'""",
        (project_id,),
    ).fetchone()[0]
    totals["invoice"] = conn.execute(
        """SELECT COALESCE(SUM(amount_minor), 0) FROM sales_invoices
           WHERE project_id=? AND status='active'""",
        (project_id,),
    ).fetchone()[0]
    totals["receipt"] = conn.execute(
        """SELECT COALESCE(SUM(ra.allocated_amount_minor), 0)
           FROM receipt_allocations ra
           JOIN receipts r ON r.id=ra.receipt_id
           WHERE ra.project_id=? AND r.status='active'""",
        (project_id,),
    ).fetchone()[0]
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
           WHERE ppc.project_id=? AND po.status='有效'""",
        (project_id,),
    ).fetchone()
    paid = conn.execute(
        """SELECT COALESCE(SUM(ppc.cost_minor), 0)
           FROM purchase_project_costs ppc
           JOIN purchase_orders po ON po.id=ppc.purchase_order_id
           WHERE ppc.project_id=? AND po.status='有效'
             AND po.payment_status='已付款'""",
        (project_id,),
    ).fetchone()[0]
    categories = conn.execute(
        """SELECT poi.cost_category AS label,
                  COALESCE(SUM(ppc.tax_inclusive_material_minor), 0)
                      AS amount_minor
           FROM purchase_project_costs ppc
           JOIN purchase_orders po ON po.id=ppc.purchase_order_id
           JOIN purchase_order_items poi ON poi.purchase_order_id=po.id
           WHERE ppc.project_id=? AND po.status='有效'
           GROUP BY poi.cost_category
           ORDER BY amount_minor DESC""",
        (project_id,),
    ).fetchall()
    category_rows = [dict(item) for item in categories]
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


def _purchase_material_breakdown(conn, project_id):
    """Return tax-inclusive material spend grouped by recorded material name."""
    rows = conn.execute(
        """SELECT
                   COALESCE(
                       NULLIF(TRIM(poi.material_name_snapshot), ''),
                       '未命名材料'
                   ) AS label,
                   COALESCE(SUM(ppc.tax_inclusive_material_minor), 0)
                       AS amount_minor,
                   COUNT(DISTINCT po.id) AS order_count
           FROM purchase_project_costs ppc
           JOIN purchase_orders po ON po.id=ppc.purchase_order_id
           JOIN purchase_order_items poi ON poi.purchase_order_id=po.id
           WHERE ppc.project_id=? AND po.status='有效'
           GROUP BY COALESCE(
               NULLIF(TRIM(poi.material_name_snapshot), ''),
               '未命名材料'
           )
           ORDER BY amount_minor DESC, label""",
        (project_id,),
    ).fetchall()
    return [dict(row) for row in rows]


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


def _project_summary_from_connection(conn, project_id, labor_by_project):
    project = conn.execute(
        """SELECT p.id, p.project_code, p.name, p.status,
                  COALESCE(bp.legal_name, p.customer_name, '') AS customer_name
           FROM projects p
           LEFT JOIN business_partners bp ON bp.id=p.customer_partner_id
           WHERE p.id=?""",
        (project_id,),
    ).fetchone()
    if not project:
        raise ValueError("项目不存在")

    manual = _manual_totals(conn, project_id)
    purchase = _purchase_totals(conn, project_id)
    construction = _construction_totals(conn, project_id)
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
    receivable_minor = manual["settlement"] - manual["receipt"]
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
        "purchase_material_breakdown": _purchase_material_breakdown(
            conn, project_id
        ),
        "other_cost_categories": _other_cost_categories(conn, project_id),
    }


def get_project_summary(project_id):
    conn = get_connection()
    try:
        labor_by_project, unassigned_labor = _labor_allocation(conn)
        result = _project_summary_from_connection(
            conn, int(project_id), labor_by_project
        )
        result["unassigned_labor"] = unassigned_labor
        unassigned_purchase = conn.execute(
            """SELECT COUNT(*) AS order_count,
                      COALESCE(SUM(total_amount_cents), 0) AS amount_minor
               FROM purchase_orders
               WHERE project_id IS NULL AND status='有效'
                 AND NOT EXISTS (
                   SELECT 1 FROM purchase_cost_allocation_lines pal
                   WHERE pal.purchase_order_id=purchase_orders.id
                     AND pal.status='active'
                 )"""
        ).fetchone()
        result["unassigned_purchase"] = dict(unassigned_purchase)
        return result
    finally:
        conn.close()


def get_portfolio_summary():
    conn = get_connection()
    try:
        labor_by_project, unassigned_labor = _labor_allocation(conn)
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
            _project_summary_from_connection(conn, project_id, labor_by_project)
            for project_id in project_ids
        ]
        return {
            "projects": rows,
            "unassigned_labor": unassigned_labor,
        }
    finally:
        conn.close()
