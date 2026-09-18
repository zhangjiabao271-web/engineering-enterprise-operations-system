def migrate(conn):
    conn.execute("ALTER TABLE supplier_profiles ADD COLUMN supplier_kind TEXT NOT NULL DEFAULT 'unclassified' CHECK(supplier_kind IN ('unclassified','manufacturer','distributor'))")


MIGRATIONS = [(490, "供应商区分生产厂家与经销门店", migrate)]
