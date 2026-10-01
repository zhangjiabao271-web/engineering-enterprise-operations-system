"""Add entity and input-invoice ledgers without rewriting existing business rows."""


def add_operating_entities(conn):
    statements = [
        '''CREATE TABLE operating_entities (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            tax_identity TEXT NOT NULL CHECK(tax_identity IN ('general','small')),
            tax_number TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1)''',
        '''CREATE TABLE record_entities (
            record_type TEXT NOT NULL, record_id INTEGER NOT NULL,
            entity_id INTEGER NOT NULL REFERENCES operating_entities(id),
            source TEXT NOT NULL, updated_at TEXT NOT NULL,
            PRIMARY KEY(record_type,record_id))''',
        '''CREATE TABLE operating_entity_audit (
            id INTEGER PRIMARY KEY, record_type TEXT NOT NULL, record_id INTEGER NOT NULL,
            old_entity_id INTEGER, new_entity_id INTEGER, reason TEXT NOT NULL,
            created_at TEXT NOT NULL)''',
        '''CREATE TABLE input_invoices (
            id INTEGER PRIMARY KEY, invoice_no TEXT NOT NULL UNIQUE,
            invoice_date TEXT NOT NULL, entity_id INTEGER NOT NULL REFERENCES operating_entities(id),
            supplier_id INTEGER NOT NULL REFERENCES business_partners(id),
            buyer_name TEXT NOT NULL, seller_name TEXT NOT NULL,
            buyer_tax_number TEXT NOT NULL DEFAULT '', seller_tax_number TEXT NOT NULL DEFAULT '',
            net_minor INTEGER NOT NULL CHECK(net_minor>=0), tax_minor INTEGER NOT NULL CHECK(tax_minor>=0),
            gross_minor INTEGER NOT NULL CHECK(gross_minor>0 AND gross_minor=net_minor+tax_minor),
            tax_rate_label TEXT NOT NULL, deduction_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(deduction_status IN ('pending','eligible','deducted','ineligible')),
            deduction_period TEXT, status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','void')),
            notes TEXT NOT NULL DEFAULT '', file_path TEXT, file_sha256 TEXT UNIQUE,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''',
        '''CREATE TABLE input_invoice_links (
            id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES input_invoices(id),
            purchase_order_id INTEGER REFERENCES purchase_orders(id),
            cost_entry_id INTEGER REFERENCES cost_entries(id),
            amount_minor INTEGER NOT NULL CHECK(amount_minor>0),
            CHECK((purchase_order_id IS NOT NULL)+(cost_entry_id IS NOT NULL)=1),
            UNIQUE(invoice_id,purchase_order_id), UNIQUE(invoice_id,cost_entry_id))''',
        '''CREATE TABLE input_invoice_audit (
            id INTEGER PRIMARY KEY, invoice_id INTEGER NOT NULL REFERENCES input_invoices(id),
            action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
            created_at TEXT NOT NULL)''',
        'CREATE INDEX idx_record_entities_entity ON record_entities(entity_id,record_type)',
        'CREATE INDEX idx_input_links_purchase ON input_invoice_links(purchase_order_id)',
        'CREATE INDEX idx_input_links_cost ON input_invoice_links(cost_entry_id)',
    ]
    for sql in statements:
        conn.execute(sql)
    conn.executemany('INSERT INTO operating_entities(name,tax_identity) VALUES (?,?)', [
        ('示例钢结构有限公司', 'general'),
        ('示例建筑安装队', 'small'),
    ])


MIGRATIONS = [(610, '独立经营主体归属与进项发票台账', add_operating_entities)]
