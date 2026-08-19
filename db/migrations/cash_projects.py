"""Support non-contract, non-invoiced cash engineering projects."""


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn, table, name, definition):
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def migration_340(conn):
    _add_column(
        conn,
        "projects",
        "business_mode",
        "TEXT NOT NULL DEFAULT 'contract' "
        "CHECK(business_mode IN ('contract', 'cash'))",
    )
    _add_column(
        conn,
        "projects",
        "invoice_policy",
        "TEXT NOT NULL DEFAULT 'required' "
        "CHECK(invoice_policy IN ('required', 'not_required', 'pending'))",
    )
    _add_column(
        conn,
        "business_partners",
        "entity_type",
        "TEXT NOT NULL DEFAULT 'enterprise' "
        "CHECK(entity_type IN ('enterprise', 'individual_business', 'individual'))",
    )
    _add_column(
        conn,
        "settlements",
        "source_type",
        "TEXT NOT NULL DEFAULT 'contract' "
        "CHECK(source_type IN ('contract', 'cash_job'))",
    )
    _add_column(
        conn,
        "receipt_allocations",
        "settlement_id",
        "INTEGER REFERENCES settlements(id) ON DELETE RESTRICT",
    )

    conn.execute(
        """UPDATE settlements
           SET source_type=CASE WHEN contract_id IS NULL
                                THEN 'cash_job' ELSE 'contract' END"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_projects_business_mode
           ON projects(business_mode, status)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_settlements_source_type
           ON settlements(source_type, project_id, status)"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS idx_receipt_allocations_settlement
           ON receipt_allocations(settlement_id, receipt_id)"""
    )


MIGRATIONS = [(340, "零星现金工程、完工金额确认与现金回款核销", migration_340)]
