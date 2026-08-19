"""Purchase-payment settlement facts and supporting governance indexes."""


def migration_300(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS payment_purchase_allocations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            payment_entry_id INTEGER NOT NULL,
            purchase_order_id INTEGER NOT NULL,
            allocated_amount_minor INTEGER NOT NULL CHECK(allocated_amount_minor > 0),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'void')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            voided_at TEXT,
            FOREIGN KEY(payment_entry_id) REFERENCES payment_entries(id),
            FOREIGN KEY(purchase_order_id) REFERENCES purchase_orders(id)
        );

        CREATE INDEX IF NOT EXISTS idx_payment_purchase_payment
            ON payment_purchase_allocations(payment_entry_id, status);
        CREATE INDEX IF NOT EXISTS idx_payment_purchase_order
            ON payment_purchase_allocations(purchase_order_id, status);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_payment_purchase_active_pair
            ON payment_purchase_allocations(payment_entry_id, purchase_order_id)
            WHERE status='active';
        CREATE INDEX IF NOT EXISTS idx_work_logs_governance
            ON work_logs(project_id, status, work_date);
        CREATE INDEX IF NOT EXISTS idx_purchase_orders_governance
            ON purchase_orders(project_id, status, purchase_date);
        CREATE INDEX IF NOT EXISTS idx_projects_customer_status
            ON projects(customer_partner_id, status);
        """
    )


MIGRATIONS = [
    (300, "采购付款事实与数据治理索引", migration_300),
]
