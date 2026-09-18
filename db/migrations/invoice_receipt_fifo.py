"""Customer-level FIFO reconciliation between invoices and receipts."""

from datetime import datetime
from uuid import uuid4


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _customer_plan(conn, customer_id):
    invoices = conn.execute(
        """SELECT i.id, i.invoice_date, i.amount_minor
           FROM sales_invoices i
           JOIN projects p ON p.id=i.project_id
           LEFT JOIN contracts c ON c.id=i.contract_id
           WHERE i.status='active'
             AND COALESCE(p.customer_partner_id, c.customer_partner_id)=?
           ORDER BY i.invoice_date, i.id""",
        (int(customer_id),),
    ).fetchall()
    invoice_remaining = {
        int(row["id"]): int(row["amount_minor"]) for row in invoices
    }
    receipts = conn.execute(
        """SELECT r.id, r.receipt_date, r.amount_minor,
                  CASE
                    WHEN COUNT(DISTINCT ra.invoice_id)=1
                     AND SUM(CASE WHEN ra.invoice_id IS NULL THEN 1 ELSE 0 END)=0
                    THEN MIN(ra.invoice_id)
                  END AS manual_invoice_id
           FROM receipts r
           JOIN receipt_allocations ra
             ON ra.receipt_id=r.id AND ra.status='active'
           JOIN projects p ON p.id=ra.project_id
           LEFT JOIN contracts c ON c.id=ra.contract_id
           WHERE r.status='active' AND p.business_mode='contract'
           GROUP BY r.id
           HAVING COUNT(DISTINCT COALESCE(
                      p.customer_partner_id, c.customer_partner_id
                  ))=1
              AND MIN(COALESCE(
                      p.customer_partner_id, c.customer_partner_id
                  ))=?
           ORDER BY r.receipt_date, r.id""",
        (int(customer_id),),
    ).fetchall()

    plan = []
    automatic_receipts = []
    for receipt in receipts:
        receipt_id = int(receipt["id"])
        remaining = int(receipt["amount_minor"])
        manual_invoice_id = receipt["manual_invoice_id"]
        if manual_invoice_id in invoice_remaining:
            allocated = min(
                remaining, invoice_remaining[int(manual_invoice_id)]
            )
            if allocated:
                plan.append(
                    (
                        int(manual_invoice_id),
                        receipt_id,
                        allocated,
                        "manual",
                    )
                )
                invoice_remaining[int(manual_invoice_id)] -= allocated
                remaining -= allocated
        if remaining:
            automatic_receipts.append((receipt_id, remaining))

    invoice_index = 0
    for receipt_id, receipt_remaining in automatic_receipts:
        while receipt_remaining and invoice_index < len(invoices):
            invoice_id = int(invoices[invoice_index]["id"])
            available = invoice_remaining[invoice_id]
            if not available:
                invoice_index += 1
                continue
            allocated = min(receipt_remaining, available)
            plan.append((invoice_id, receipt_id, allocated, "fifo"))
            invoice_remaining[invoice_id] -= allocated
            receipt_remaining -= allocated
            if not invoice_remaining[invoice_id]:
                invoice_index += 1
    return plan


def add_invoice_receipt_fifo(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS invoice_receipt_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            batch_id TEXT NOT NULL,
            customer_partner_id INTEGER NOT NULL,
            invoice_id INTEGER NOT NULL,
            receipt_id INTEGER NOT NULL,
            allocated_amount_minor INTEGER NOT NULL
                CHECK(allocated_amount_minor > 0),
            allocation_method TEXT NOT NULL
                CHECK(allocation_method IN ('fifo', 'manual')),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'void')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            voided_at TEXT,
            FOREIGN KEY (customer_partner_id) REFERENCES business_partners(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (invoice_id) REFERENCES sales_invoices(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (receipt_id) REFERENCES receipts(id)
                ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_invoice_receipt_customer
        ON invoice_receipt_allocations(customer_partner_id, status, id);

        CREATE INDEX IF NOT EXISTS idx_invoice_receipt_invoice
        ON invoice_receipt_allocations(invoice_id, status, id);

        CREATE INDEX IF NOT EXISTS idx_invoice_receipt_receipt
        ON invoice_receipt_allocations(receipt_id, status, id);

        CREATE UNIQUE INDEX IF NOT EXISTS idx_invoice_receipt_active_pair
        ON invoice_receipt_allocations(invoice_id, receipt_id)
        WHERE status='active';
        """
    )

    customer_ids = {
        int(row[0])
        for row in conn.execute(
            """SELECT customer_id FROM (
                   SELECT COALESCE(p.customer_partner_id, c.customer_partner_id)
                              AS customer_id
                   FROM sales_invoices i
                   JOIN projects p ON p.id=i.project_id
                   LEFT JOIN contracts c ON c.id=i.contract_id
                   WHERE i.status='active'
                   UNION
                   SELECT COALESCE(p.customer_partner_id, c.customer_partner_id)
                   FROM receipt_allocations ra
                   JOIN receipts r ON r.id=ra.receipt_id
                   JOIN projects p ON p.id=ra.project_id
                   LEFT JOIN contracts c ON c.id=ra.contract_id
                   WHERE r.status='active' AND ra.status='active'
                     AND p.business_mode='contract'
               ) WHERE customer_id IS NOT NULL"""
        ).fetchall()
    }
    now = _now()
    for customer_id in sorted(customer_ids):
        plan = _customer_plan(conn, customer_id)
        if not plan:
            continue
        batch_id = str(uuid4())
        for invoice_id, receipt_id, amount_minor, method in plan:
            conn.execute(
                """INSERT INTO invoice_receipt_allocations (
                       public_id, batch_id, customer_partner_id,
                       invoice_id, receipt_id, allocated_amount_minor,
                       allocation_method, status, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
                (
                    str(uuid4()),
                    batch_id,
                    customer_id,
                    invoice_id,
                    receipt_id,
                    amount_minor,
                    method,
                    now,
                    now,
                ),
            )


MIGRATIONS = [
    (440, "客户发票与回款按时间顺序自动核销", add_invoice_receipt_fifo),
]
