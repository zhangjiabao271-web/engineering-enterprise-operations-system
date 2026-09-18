def upgrade(conn):
    conn.execute("""ALTER TABLE receipts ADD COLUMN automatic_income_allocation
                    INTEGER NOT NULL DEFAULT 0 CHECK(automatic_income_allocation IN (0,1))""")


MIGRATIONS = [(560, '项目预收款与自动抵扣收入', upgrade)]
