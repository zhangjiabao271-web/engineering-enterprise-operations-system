"""Project-based receivables; existing receipt instructions remain explicit."""


def migrate(conn):
    conn.execute("""ALTER TABLE receipts ADD COLUMN allocation_scope TEXT NOT NULL
        DEFAULT 'contract' CHECK(allocation_scope IN ('contract','project'))""")
    conn.execute('ALTER TABLE settlements ADD COLUMN business_date TEXT')
    conn.execute('''CREATE TABLE settlement_contract_revisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        settlement_id INTEGER NOT NULL REFERENCES settlements(id),
        previous_json TEXT NOT NULL,
        current_json TEXT NOT NULL,
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
    )''')
    conn.execute('CREATE INDEX idx_receipts_project_fifo ON receipts(allocation_scope,status,receipt_date,id)')


MIGRATIONS = [(650, 'Project receivable order and traceable unsigned business links', migrate)]
