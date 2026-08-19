def add_purchase_cost_allocation_lines(conn):
    """Allow one tool/equipment purchase to be shared across projects."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS purchase_cost_allocation_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            public_id TEXT NOT NULL UNIQUE,
            purchase_order_id INTEGER NOT NULL,
            project_id INTEGER NOT NULL,
            amount_minor INTEGER NOT NULL CHECK(amount_minor >= 0),
            allocation_method TEXT NOT NULL DEFAULT 'equal'
                CHECK(allocation_method='equal'),
            allocation_version INTEGER NOT NULL CHECK(allocation_version > 0),
            status TEXT NOT NULL DEFAULT 'active'
                CHECK(status IN ('active', 'void')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            voided_at TEXT,
            FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(id)
                ON DELETE RESTRICT,
            FOREIGN KEY (project_id) REFERENCES projects(id)
                ON DELETE RESTRICT,
            UNIQUE (purchase_order_id, allocation_version, project_id)
        );

        CREATE INDEX IF NOT EXISTS idx_purchase_cost_alloc_order
        ON purchase_cost_allocation_lines(
            purchase_order_id, status, allocation_version
        );

        CREATE INDEX IF NOT EXISTS idx_purchase_cost_alloc_project
        ON purchase_cost_allocation_lines(project_id, status, purchase_order_id);

        DROP VIEW IF EXISTS purchase_project_costs;
        CREATE VIEW purchase_project_costs AS
        WITH item_totals AS (
            SELECT purchase_order_id,
                   COALESCE(SUM(material_amount_cents), 0) AS material_minor,
                   COALESCE(SUM(tax_amount_cents), 0) AS tax_minor,
                   COALESCE(SUM(line_amount_cents), 0)
                       AS tax_inclusive_material_minor
            FROM purchase_order_items
            GROUP BY purchase_order_id
        ),
        active_allocations AS (
            SELECT pal.purchase_order_id, pal.project_id, pal.amount_minor,
                   SUM(pal.amount_minor) OVER (
                       PARTITION BY pal.purchase_order_id
                       ORDER BY pal.project_id, pal.id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                   ) AS cumulative_minor,
                   COALESCE(SUM(pal.amount_minor) OVER (
                       PARTITION BY pal.purchase_order_id
                       ORDER BY pal.project_id, pal.id
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                   ), 0) AS previous_minor,
                   SUM(pal.amount_minor) OVER (
                       PARTITION BY pal.purchase_order_id
                   ) AS allocated_total_minor
            FROM purchase_cost_allocation_lines pal
            WHERE pal.status='active'
        ),
        allocated_costs AS (
            SELECT po.id AS purchase_order_id,
                   aa.project_id,
                   aa.amount_minor AS cost_minor,
                   CASE WHEN aa.allocated_total_minor > 0 THEN
                       CAST(it.tax_minor * aa.cumulative_minor
                            / aa.allocated_total_minor AS INTEGER)
                       - CAST(it.tax_minor * aa.previous_minor
                              / aa.allocated_total_minor AS INTEGER)
                   ELSE 0 END AS tax_minor,
                   CASE WHEN aa.allocated_total_minor > 0 THEN
                       CAST(po.freight_amount_cents * aa.cumulative_minor
                            / aa.allocated_total_minor AS INTEGER)
                       - CAST(po.freight_amount_cents * aa.previous_minor
                              / aa.allocated_total_minor AS INTEGER)
                   ELSE 0 END AS freight_minor
            FROM purchase_orders po
            JOIN item_totals it ON it.purchase_order_id=po.id
            JOIN active_allocations aa ON aa.purchase_order_id=po.id
            WHERE po.project_id IS NULL
        )
        SELECT po.id AS purchase_order_id,
               po.project_id,
               po.total_amount_cents AS cost_minor,
               it.material_minor,
               it.tax_minor,
               it.tax_inclusive_material_minor,
               po.freight_amount_cents AS freight_minor,
               'direct' AS allocation_method
        FROM purchase_orders po
        JOIN item_totals it ON it.purchase_order_id=po.id
        WHERE po.project_id IS NOT NULL
        UNION ALL
        SELECT ac.purchase_order_id,
               ac.project_id,
               ac.cost_minor,
               ac.cost_minor - ac.tax_minor - ac.freight_minor
                   AS material_minor,
               ac.tax_minor,
               ac.cost_minor - ac.freight_minor
                   AS tax_inclusive_material_minor,
               ac.freight_minor,
               'equal' AS allocation_method
        FROM allocated_costs ac;
        """
    )


MIGRATIONS = [
    (360, "工具和设备采购多项目平均分摊", add_purchase_cost_allocation_lines),
]
