"""Preserve invoice changes and allow audited recovery of voided invoices."""


def create_invoice_revisions(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sales_invoice_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('update', 'void', 'restore')),
            previous_invoice_no TEXT NOT NULL,
            previous_project_id INTEGER NOT NULL,
            previous_contract_id INTEGER,
            previous_invoice_date TEXT NOT NULL,
            previous_amount_minor INTEGER NOT NULL,
            previous_tax_rate_bps INTEGER NOT NULL,
            previous_buyer_name_snapshot TEXT NOT NULL DEFAULT '',
            previous_notes TEXT,
            previous_status TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            FOREIGN KEY (invoice_id) REFERENCES sales_invoices(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (previous_project_id) REFERENCES projects(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (previous_contract_id) REFERENCES contracts(id)
                ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_sales_invoice_revisions_invoice
        ON sales_invoice_revisions(invoice_id, changed_at DESC, id DESC);
        """
    )


MIGRATIONS = [
    (250, "销项发票修改、作废与恢复历史", create_invoice_revisions),
]
