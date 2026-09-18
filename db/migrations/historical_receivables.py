"""Historical collections are independent of construction revenue."""


def upgrade(conn):
    conn.execute("""CREATE TABLE historical_receivable_projects (
        id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES business_partners(id),
        name TEXT NOT NULL, opening_minor INTEGER CHECK(opening_minor>=0),
        notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        UNIQUE(customer_id,name))""")
    conn.execute("""CREATE TABLE historical_receipts (
        id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES historical_receivable_projects(id),
        request_key TEXT NOT NULL UNIQUE, receipt_date TEXT NOT NULL,
        amount_minor INTEGER NOT NULL CHECK(amount_minor>0),
        payment_method TEXT NOT NULL DEFAULT '待确认', notes TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','void')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    conn.execute("CREATE INDEX historical_receipts_project ON historical_receipts(project_id,status)")
    conn.execute("""CREATE TABLE historical_receivable_audit (
        id INTEGER PRIMARY KEY, entity TEXT NOT NULL, entity_id INTEGER NOT NULL,
        before_json TEXT NOT NULL, after_json TEXT NOT NULL, reason TEXT NOT NULL,
        created_at TEXT NOT NULL)""")


MIGRATIONS = [(530, '独立历史旧账项目与回款', upgrade)]
