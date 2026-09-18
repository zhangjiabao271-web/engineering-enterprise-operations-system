def upgrade(conn):
    conn.execute("""ALTER TABLE contracts ADD COLUMN income_mode TEXT NOT NULL
                    DEFAULT 'manual' CHECK(income_mode IN ('manual', 'invoice'))""")
    conn.execute("""CREATE TABLE invoice_income_links (
        invoice_id INTEGER PRIMARY KEY REFERENCES sales_invoices(id),
        settlement_id INTEGER NOT NULL UNIQUE REFERENCES settlements(id),
        created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE invoice_income_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        contract_id INTEGER NOT NULL REFERENCES contracts(id),
        invoice_id INTEGER REFERENCES sales_invoices(id),
        action TEXT NOT NULL, before_json TEXT NOT NULL,
        after_json TEXT NOT NULL, created_at TEXT NOT NULL)""")


MIGRATIONS = [(550, '合同随开票确认收入', upgrade)]
