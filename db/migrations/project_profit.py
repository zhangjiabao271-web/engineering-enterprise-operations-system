from datetime import datetime


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def create_project_profit_center(conn):
    """Create manual operating facts used by the V1 project profit center.

    Existing procurement, labor and construction facts remain in their source
    modules. This table stores only operating events that do not yet have a
    dedicated contract, settlement, invoice or cash module.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS project_operating_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            organization_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            entry_type TEXT NOT NULL CHECK(entry_type IN (
                'contract_value', 'settlement', 'invoice', 'receipt',
                'other_cost', 'other_payment'
            )),
            category TEXT,
            entry_date TEXT NOT NULL,
            amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
            reference_no TEXT,
            counterparty_name TEXT,
            notes TEXT,
            source_type TEXT NOT NULL DEFAULT 'manual',
            source_id TEXT,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'void')),
            created_at TEXT NOT NULL,
            created_by INTEGER,
            updated_at TEXT NOT NULL,
            updated_by INTEGER,
            FOREIGN KEY (organization_id) REFERENCES organizations(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
                ON DELETE RESTRICT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_project_operating_entries_project
        ON project_operating_entries(project_id, entry_date)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_project_operating_entries_type
        ON project_operating_entries(entry_type, status)
    """)

    # Record the implementation date without treating it as a business fact.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS project_profit_metadata (
               key TEXT PRIMARY KEY,
               value TEXT NOT NULL
           )"""
    )
    conn.execute(
        """INSERT OR IGNORE INTO project_profit_metadata(key, value)
           VALUES ('v1_created_at', ?)""",
        (_now(),),
    )


MIGRATIONS = [
    (130, "V3 项目利润中心手工经营事项", create_project_profit_center),
]
