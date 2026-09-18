def upgrade(conn):
    for name in ('net_amount_minor', 'tax_amount_minor'):
        conn.execute(f'ALTER TABLE sales_invoices ADD COLUMN {name} INTEGER CHECK({name}>=0)')
        conn.execute(f'ALTER TABLE sales_invoice_revisions ADD COLUMN previous_{name} INTEGER')
    conn.execute("ALTER TABLE sales_invoices ADD COLUMN tax_rate_label TEXT NOT NULL DEFAULT ''")
    conn.execute("ALTER TABLE sales_invoice_revisions ADD COLUMN previous_tax_rate_label TEXT NOT NULL DEFAULT ''")


MIGRATIONS = [(590, '保存发票票面未税金额税额和多税率信息', upgrade)]
