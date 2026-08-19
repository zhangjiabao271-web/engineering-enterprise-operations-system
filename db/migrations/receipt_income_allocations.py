"""Link receipts to income confirmations and preserve allocation history."""


def add_receipt_income_allocations(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS receipt_allocation_revisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receipt_revision_id INTEGER NOT NULL,
            previous_project_id INTEGER NOT NULL,
            previous_contract_id INTEGER,
            previous_invoice_id INTEGER,
            previous_settlement_id INTEGER,
            previous_allocated_amount_minor INTEGER NOT NULL,
            previous_notes TEXT,
            FOREIGN KEY (receipt_revision_id) REFERENCES receipt_revisions(id)
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

        CREATE INDEX IF NOT EXISTS idx_receipt_allocation_revisions_revision
        ON receipt_allocation_revisions(receipt_revision_id, id);

        CREATE INDEX IF NOT EXISTS idx_receipt_allocations_receipt_settlement
        ON receipt_allocations(receipt_id, settlement_id);
        """
    )

    # An invoice already points to one income confirmation in the current
    # workflow, so its receipts can be backfilled without guessing.
    conn.execute(
        """UPDATE receipt_allocations
           SET settlement_id=(
               SELECT MIN(a.settlement_id)
               FROM invoice_settlement_allocations a
               JOIN settlements s ON s.id=a.settlement_id
               WHERE a.invoice_id=receipt_allocations.invoice_id
                 AND s.status='active'
           )
           WHERE settlement_id IS NULL
             AND invoice_id IS NOT NULL
             AND 1=(
                 SELECT COUNT(DISTINCT a.settlement_id)
                 FROM invoice_settlement_allocations a
                 JOIN settlements s ON s.id=a.settlement_id
                 WHERE a.invoice_id=receipt_allocations.invoice_id
                   AND s.status='active'
             )"""
    )

    # A contract/project pair with exactly one active confirmation is equally
    # unambiguous. Pairs with multiple confirmations remain visibly pending.
    conn.execute(
        """UPDATE receipt_allocations
           SET settlement_id=(
               SELECT MIN(s.id)
               FROM settlements s
               WHERE s.project_id=receipt_allocations.project_id
                 AND s.contract_id=receipt_allocations.contract_id
                 AND s.status='active'
           )
           WHERE settlement_id IS NULL
             AND invoice_id IS NULL
             AND contract_id IS NOT NULL
             AND 1=(
                 SELECT COUNT(*)
                 FROM settlements s
                 WHERE s.project_id=receipt_allocations.project_id
                   AND s.contract_id=receipt_allocations.contract_id
                   AND s.status='active'
             )"""
    )

    # Preserve the allocation snapshot carried by older revision rows.
    conn.execute(
        """INSERT INTO receipt_allocation_revisions (
               receipt_revision_id, previous_project_id,
               previous_contract_id, previous_invoice_id,
               previous_settlement_id, previous_allocated_amount_minor,
               previous_notes
           )
           SELECT rr.id, rr.previous_project_id, rr.previous_contract_id,
                  rr.previous_invoice_id, rr.previous_settlement_id,
                  rr.previous_allocated_amount_minor, rr.previous_notes
           FROM receipt_revisions rr
           WHERE NOT EXISTS (
               SELECT 1 FROM receipt_allocation_revisions rar
               WHERE rar.receipt_revision_id=rr.id
           )"""
    )


MIGRATIONS = [
    (370, "回款与收入确认精确关联及分配历史", add_receipt_income_allocations),
]
