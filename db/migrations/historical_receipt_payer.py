def upgrade(conn):
    # Existing payments retain an unknown payer rather than assuming who paid.
    conn.execute("ALTER TABLE historical_receipts ADD COLUMN payer_name_snapshot TEXT NOT NULL DEFAULT ''")


MIGRATIONS = [(540, '历史回款付款人快照', upgrade)]
