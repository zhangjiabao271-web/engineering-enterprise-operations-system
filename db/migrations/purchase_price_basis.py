def migrate(conn):
    conn.execute("ALTER TABLE purchase_order_items ADD COLUMN price_basis TEXT NOT NULL DEFAULT 'exclusive' CHECK(price_basis IN ('exclusive','inclusive'))")
    conn.execute("ALTER TABLE supplier_offers ADD COLUMN price_basis TEXT NOT NULL DEFAULT 'exclusive' CHECK(price_basis IN ('exclusive','inclusive'))")
    conn.execute("ALTER TABLE supplier_offers ADD COLUMN quoted_price_minor INTEGER CHECK(quoted_price_minor >= 0)")
    conn.execute("""CREATE TABLE purchase_price_revisions (
        id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL,
        entity_id INTEGER NOT NULL, before_json TEXT NOT NULL,
        after_json TEXT NOT NULL, reason TEXT NOT NULL, changed_at TEXT NOT NULL
    )""")


MIGRATIONS = [(480, "采购及报价支持含税计价并保留价格修订历史", migrate)]
