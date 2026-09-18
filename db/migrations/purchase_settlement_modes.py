def migrate(conn):
    fields = {
        "settlement_mode": "TEXT NOT NULL DEFAULT 'quantity' CHECK(settlement_mode IN ('quantity','weight','total'))",
        "net_weight": "TEXT",
        "weight_unit": "TEXT CHECK(weight_unit IN ('吨','公斤'))",
        "weight_unit_price": "TEXT",
        "settlement_total_cents": "INTEGER CHECK(settlement_total_cents>=0)",
        "weigh_ticket_no": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in fields.items():
        conn.execute(f"ALTER TABLE purchase_order_items ADD COLUMN {name} {definition}")
    conn.execute("ALTER TABLE business_attachments ADD COLUMN purchase_order_id INTEGER REFERENCES purchase_orders(id)")
    # Extend the old six-entity check while preserving legacy construction links.
    schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='business_attachments'").fetchone()[0]
    indexes = [row[0] for row in conn.execute("SELECT sql FROM sqlite_master WHERE tbl_name='business_attachments' AND type='index' AND sql IS NOT NULL")]
    old_entities = " + ".join(f"({name} IS NOT NULL)" for name in ("contract_id", "settlement_id", "invoice_id", "receipt_id", "cost_entry_id", "payment_entry_id"))
    check = f"CHECK (({old_entities}) <= 1 AND (({old_entities})=1 OR construction_record_id IS NOT NULL OR purchase_order_id IS NOT NULL) AND (purchase_order_id IS NULL OR (({old_entities})=0 AND construction_record_id IS NULL))))"
    schema = schema[:schema.rindex("CHECK (")] + check
    conn.execute(schema.replace("CREATE TABLE business_attachments", "CREATE TABLE business_attachments_new", 1))
    columns = ",".join(row[1] for row in conn.execute("PRAGMA table_info(business_attachments)"))
    conn.execute(f"INSERT INTO business_attachments_new ({columns}) SELECT {columns} FROM business_attachments")
    conn.execute("DROP TABLE business_attachments")
    conn.execute("ALTER TABLE business_attachments_new RENAME TO business_attachments")
    for sql in indexes:
        conn.execute(sql)
    conn.execute("CREATE INDEX idx_business_attachments_purchase ON business_attachments(purchase_order_id,status)")


MIGRATIONS = [(500, "采购支持过磅重量与整批结算及磅单附件", migrate)]
