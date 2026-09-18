"""Allow zero-value project links for actual-price contracts."""


def allow_zero_value_contract_project_links(conn):
    table_sql = conn.execute(
        """SELECT sql FROM sqlite_master
           WHERE type='table' AND name='contract_project_allocations'"""
    ).fetchone()
    if not table_sql:
        return
    normalized_sql = "".join(table_sql[0].lower().split())
    if "check(allocated_amount_minor>0)" not in normalized_sql:
        return

    conn.execute(
        """CREATE TABLE contract_project_allocations_new (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               public_id TEXT NOT NULL UNIQUE,
               contract_id INTEGER NOT NULL,
               project_id INTEGER NOT NULL,
               allocated_amount_minor INTEGER NOT NULL
                   CHECK(allocated_amount_minor >= 0),
               notes TEXT,
               status TEXT NOT NULL DEFAULT 'active'
                   CHECK(status IN ('active', 'void')),
               source_legacy_entry_id INTEGER UNIQUE,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               FOREIGN KEY (contract_id) REFERENCES contracts(id)
                   ON DELETE RESTRICT,
               FOREIGN KEY (project_id) REFERENCES projects(id)
                   ON DELETE RESTRICT
           )"""
    )
    conn.execute(
        """INSERT INTO contract_project_allocations_new (
               id, public_id, contract_id, project_id, allocated_amount_minor,
               notes, status, source_legacy_entry_id, created_at, updated_at
           )
           SELECT id, public_id, contract_id, project_id, allocated_amount_minor,
                  notes, status, source_legacy_entry_id, created_at, updated_at
           FROM contract_project_allocations"""
    )
    conn.execute("DROP TABLE contract_project_allocations")
    conn.execute(
        "ALTER TABLE contract_project_allocations_new RENAME TO contract_project_allocations"
    )
    conn.execute(
        """CREATE UNIQUE INDEX idx_contract_allocations_active_project
           ON contract_project_allocations(contract_id, project_id)
           WHERE status='active'"""
    )
    conn.execute(
        """CREATE INDEX idx_contract_allocations_project
           ON contract_project_allocations(project_id, status)"""
    )


MIGRATIONS = [
    (390, "单价据实合同使用零金额项目关联", allow_zero_value_contract_project_links),
]
