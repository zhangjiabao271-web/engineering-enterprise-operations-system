"""Flag allocations of voided receipts and backfill missing void audits."""


def add_receipt_allocation_status(conn):
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(receipt_allocations)")
    }
    if "status" not in columns:
        conn.execute(
            "ALTER TABLE receipt_allocations "
            "ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"
        )
    # Allocations of a voided receipt must no longer look effective to any
    # query that forgets to join the receipts table.
    conn.execute(
        """UPDATE receipt_allocations
           SET status='void'
           WHERE receipt_id IN (SELECT id FROM receipts WHERE status='void')"""
    )
    # Receipts voided before the revision trail existed have no audit row;
    # record one from their current state so the void is replayable.
    conn.execute(
        """INSERT INTO receipt_revisions (
               receipt_id, action, previous_receipt_no, previous_receipt_date,
               previous_payer_name_snapshot, previous_amount_minor,
               previous_payment_method, previous_notes, previous_status,
               previous_project_id, previous_contract_id, previous_invoice_id,
               previous_settlement_id, previous_allocated_amount_minor,
               changed_at
           )
           SELECT r.id, 'void', r.receipt_no, r.receipt_date,
                  r.payer_name_snapshot, r.amount_minor, r.payment_method,
                  r.notes, 'active',
                  agg.project_id, agg.contract_id, agg.invoice_id,
                  agg.settlement_id, agg.allocated_minor, r.updated_at
           FROM receipts r
           JOIN (
               SELECT receipt_id,
                      MIN(project_id) AS project_id,
                      MIN(contract_id) AS contract_id,
                      CASE WHEN COUNT(DISTINCT invoice_id)=1
                           THEN MIN(invoice_id) END AS invoice_id,
                      CASE WHEN COUNT(DISTINCT settlement_id)=1
                           THEN MIN(settlement_id) END AS settlement_id,
                      SUM(allocated_amount_minor) AS allocated_minor
               FROM receipt_allocations
               GROUP BY receipt_id
           ) agg ON agg.receipt_id=r.id
           WHERE r.status='void'
             AND NOT EXISTS (
                 SELECT 1 FROM receipt_revisions rr
                 WHERE rr.receipt_id=r.id AND rr.action='void'
             )"""
    )


MIGRATIONS = [
    (410, "作废回款的分配标记失效并补记作废审计", add_receipt_allocation_status),
]
