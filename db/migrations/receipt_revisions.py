"""Preserve receipt changes and void operations for financial audit."""


def create_receipt_revisions(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS receipt_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_id INTEGER NOT NULL,
            action TEXT NOT NULL CHECK(action IN ('update', 'void')),
            previous_receipt_no TEXT NOT NULL,
            previous_receipt_date TEXT NOT NULL,
            previous_payer_name_snapshot TEXT NOT NULL DEFAULT '',
            previous_amount_minor INTEGER NOT NULL,
            previous_payment_method TEXT NOT NULL,
            previous_notes TEXT,
            previous_status TEXT NOT NULL,
            previous_project_id INTEGER NOT NULL,
            previous_contract_id INTEGER,
            previous_invoice_id INTEGER,
            previous_settlement_id INTEGER,
            previous_allocated_amount_minor INTEGER NOT NULL,
            changed_at TEXT NOT NULL,
            FOREIGN KEY (receipt_id) REFERENCES receipts(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (previous_project_id) REFERENCES projects(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (previous_contract_id) REFERENCES contracts(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (previous_invoice_id) REFERENCES sales_invoices(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (previous_settlement_id) REFERENCES settlements(id)
                ON DELETE RESTRICT
        );

        CREATE INDEX IF NOT EXISTS idx_receipt_revisions_receipt
        ON receipt_revisions(receipt_id, changed_at DESC, id DESC);
        """
    )


MIGRATIONS = [
    (350, "回款修改与作废审计历史", create_receipt_revisions),
]
