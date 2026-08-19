"""Link sales invoices to the settlement facts they cover."""

from datetime import datetime
from uuid import uuid4


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def create_invoice_settlement_links(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS invoice_settlement_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            invoice_id INTEGER NOT NULL,
            settlement_id INTEGER NOT NULL,
            allocated_amount_minor INTEGER NOT NULL
                CHECK(allocated_amount_minor > 0),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (invoice_id) REFERENCES sales_invoices(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (settlement_id) REFERENCES settlements(id)
                ON DELETE RESTRICT,
            UNIQUE (invoice_id, settlement_id)
        );

        CREATE INDEX IF NOT EXISTS idx_invoice_settlement_by_settlement
        ON invoice_settlement_allocations(settlement_id, invoice_id);

        CREATE TABLE IF NOT EXISTS invoice_settlement_allocation_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_revision_id INTEGER NOT NULL,
            settlement_id INTEGER NOT NULL,
            allocated_amount_minor INTEGER NOT NULL
                CHECK(allocated_amount_minor > 0),
            FOREIGN KEY (invoice_revision_id)
                REFERENCES sales_invoice_revisions(id) ON DELETE RESTRICT,
            FOREIGN KEY (settlement_id) REFERENCES settlements(id)
                ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_invoice_settlement_revision
        ON invoice_settlement_allocation_revisions(invoice_revision_id);
        """
    )

    # Historical links are filled only when the project/contract has exactly
    # one active settlement. Multiple possible settlements stay unlinked so
    # the migration never guesses which settlement an invoice belongs to.
    candidates = conn.execute(
        """SELECT i.id AS invoice_id,
                  i.amount_minor AS allocated_amount_minor,
                  MIN(s.id) AS settlement_id
           FROM sales_invoices i
           JOIN settlements s
             ON s.project_id=i.project_id
            AND COALESCE(s.contract_id, 0)=COALESCE(i.contract_id, 0)
            AND s.status='active'
           WHERE NOT EXISTS (
               SELECT 1 FROM invoice_settlement_allocations a
               WHERE a.invoice_id=i.id
           )
           GROUP BY i.id
           HAVING COUNT(s.id)=1"""
    ).fetchall()
    now = _now()
    for row in candidates:
        conn.execute(
            """INSERT INTO invoice_settlement_allocations (
                   public_id, invoice_id, settlement_id,
                   allocated_amount_minor, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                str(uuid4()),
                row["invoice_id"],
                row["settlement_id"],
                row["allocated_amount_minor"],
                now,
                now,
            ),
        )


MIGRATIONS = [
    (280, "结算与销项发票分次开票关联", create_invoice_settlement_links),
]
