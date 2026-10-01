"""One shared financial fact row per project; integer cents, no inferred offsets."""

COST_FACTS_SQL = """
        SELECT 'purchase-'||x.id AS id, x.purchase_date AS date, x.order_no AS number,
          pc.project_id AS project_id, p.name AS project, '采购成本' AS category, x.merchant_name_snapshot AS counterparty,
          pc.cost_minor AS amount_minor
        FROM purchase_project_costs pc JOIN purchase_orders x ON x.id=pc.purchase_order_id
        LEFT JOIN projects p ON p.id=pc.project_id WHERE x.status='active'
        UNION ALL
        SELECT 'purchase-'||x.id,x.purchase_date,x.order_no,NULL,NULL,'采购成本',
          x.merchant_name_snapshot,x.total_amount_cents FROM purchase_orders x
        WHERE x.status='active' AND NOT EXISTS
          (SELECT 1 FROM purchase_project_costs pc WHERE pc.purchase_order_id=x.id)
        UNION ALL
        SELECT 'labor-'||x.id,x.work_date,'GT-'||x.id,x.project_id,p.name,'人工成本',w.name,
          COALESCE(x.amount_minor,CAST(ROUND(x.amount*100) AS INTEGER))
        FROM work_logs x JOIN workers w ON w.id=x.worker_id
        LEFT JOIN projects p ON p.id=x.project_id WHERE COALESCE(x.status,'active')='active'
        UNION ALL
        SELECT 'expense-'||x.id,x.cost_date,x.cost_no,a.project_id,p.name,x.category,
          x.counterparty_name_snapshot,a.amount_minor FROM cost_entries x
        JOIN cost_allocation_lines a ON a.cost_entry_id=x.id AND a.status='active'
        LEFT JOIN projects p ON p.id=a.project_id WHERE x.status='active'
        UNION ALL
        SELECT 'expense-'||x.id,x.cost_date,x.cost_no,x.project_id,p.name,x.category,
          x.counterparty_name_snapshot,x.amount_minor FROM cost_entries x
        LEFT JOIN projects p ON p.id=x.project_id WHERE x.status='active' AND NOT EXISTS
          (SELECT 1 FROM cost_allocation_lines a WHERE a.cost_entry_id=x.id AND a.status='active')
    """

PROJECT_FINANCE_SQL = """
WITH contract_totals AS (
 SELECT project_id,SUM(allocated_amount_minor) AS allocated_minor
 FROM contract_project_allocations WHERE status='active' GROUP BY project_id
), income_totals AS (
 SELECT project_id,SUM(amount_minor) AS settlement_minor
 FROM settlements WHERE status='active' GROUP BY project_id
), invoice_totals AS (
 SELECT project_id,SUM(amount_minor) AS invoice_minor
 FROM sales_invoices WHERE status='active' GROUP BY project_id
), receipt_totals AS (
 SELECT a.project_id,SUM(a.allocated_amount_minor) AS receipt_minor,
 SUM(CASE WHEN a.settlement_id IS NULL THEN a.allocated_amount_minor ELSE 0 END) AS pending_receipt_minor,
 SUM(CASE WHEN a.settlement_id IS NULL AND r.automatic_income_allocation=1
     THEN a.allocated_amount_minor ELSE 0 END) AS advance_minor
 FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
 WHERE a.status='active' AND r.status='active' GROUP BY a.project_id
)
SELECT p.id AS project_id,p.project_code,p.name AS project_name,
 p.customer_partner_id,p.status AS project_status,p.business_mode,p.invoice_policy,
 COALESCE(c.allocated_minor,0) AS allocated_minor,COALESCE(s.settlement_minor,0) AS settlement_minor,
 COALESCE(i.invoice_minor,0) AS invoice_minor,COALESCE(r.receipt_minor,0) AS receipt_minor,
 COALESCE(r.pending_receipt_minor,0) AS pending_receipt_minor,COALESCE(r.advance_minor,0) AS advance_minor,
 MAX(COALESCE(s.settlement_minor,0)-COALESCE(r.receipt_minor,0)
     +COALESCE(r.pending_receipt_minor,0),0) AS receivable_minor
FROM projects p
LEFT JOIN contract_totals c ON c.project_id=p.id
LEFT JOIN income_totals s ON s.project_id=p.id
LEFT JOIN invoice_totals i ON i.project_id=p.id
LEFT JOIN receipt_totals r ON r.project_id=p.id
"""


def project_finance_rows(conn, project_id=None):
    sql = f'SELECT * FROM ({PROJECT_FINANCE_SQL})'
    params = []
    if project_id is not None:
        sql += ' WHERE project_id=?'
        params.append(int(project_id))
    sql += " ORDER BY CASE project_status WHEN '进行中' THEN 1 WHEN '筹备中' THEN 2 ELSE 3 END,project_id DESC"
    return [dict(row) for row in conn.execute(sql, params)]


def customer_receivable_rows(conn):
    """Explicit customer attribution, with no cross-project advance offset."""
    return [dict(row) for row in conn.execute('''
        SELECT customer_id,project_id,MAX(SUM(amount_minor),0) AS receivable_minor FROM (
          SELECT COALESCE(p.customer_partner_id,c.customer_partner_id) AS customer_id,
            s.project_id,s.amount_minor FROM settlements s
          JOIN projects p ON p.id=s.project_id LEFT JOIN contracts c ON c.id=s.contract_id
          WHERE s.status='active'
          UNION ALL
          SELECT COALESCE(p.customer_partner_id,c.customer_partner_id),a.project_id,-a.allocated_amount_minor
          FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
          JOIN projects p ON p.id=a.project_id LEFT JOIN contracts c ON c.id=a.contract_id
          WHERE a.status='active' AND r.status='active' AND a.settlement_id IS NOT NULL
        ) GROUP BY customer_id,project_id''')]
