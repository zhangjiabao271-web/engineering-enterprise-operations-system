"""Target frequent worker/date and company/date lookups; keep existing indexes."""


def upgrade(conn):
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_work_logs_active_worker_date
        ON work_logs(worker_id,work_date) WHERE COALESCE(status,'active')='active'""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_receipts_active_date
        ON receipts(receipt_date) WHERE status='active'""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_purchase_orders_active_date
        ON purchase_orders(purchase_date) WHERE status='active'""")


MIGRATIONS = [(600, '工天与业务日期查询索引优化', upgrade)]
