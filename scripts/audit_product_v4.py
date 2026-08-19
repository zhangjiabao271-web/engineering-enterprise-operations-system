import ast
import sqlite3
import sys
from pathlib import Path


sys.stdout.reconfigure(encoding="utf-8")
root = Path(__file__).resolve().parent.parent
database_path = Path(sys.argv[1]) if len(sys.argv) > 1 else root / "supplier_data.db"


def scalar(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def table_exists(conn, table):
    return bool(
        scalar(
            conn,
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        )
    )


conn = sqlite3.connect(database_path)
conn.row_factory = sqlite3.Row
print("MIGRATIONS")
print([tuple(row) for row in conn.execute(
    "SELECT version, description FROM schema_migrations ORDER BY version"
)])

tables = [
    "business_partners",
    "customer_profiles",
    "materials",
    "supplier_offers",
    "projects",
    "project_sites",
    "purchase_orders",
    "purchase_order_items",
    "construction_records",
    "workers",
    "work_logs",
    "project_operating_entries",
    "contracts",
    "contract_project_allocations",
    "settlements",
    "sales_invoices",
    "receipts",
    "receipt_allocations",
    "cost_entries",
    "cost_allocation_lines",
    "business_attachments",
]
print("COUNTS")
for table in tables:
    print(table, scalar(conn, f"SELECT COUNT(*) FROM {table}") if table_exists(conn, table) else None)

print("PROJECT_COVERAGE")
coverage = conn.execute(
    """SELECT p.id, p.project_code, p.name, p.status,
              COUNT(DISTINCT ps.id) AS sites,
              COUNT(DISTINCT po.id) AS purchases,
              COUNT(DISTINCT cr.id) AS construction_records,
              COUNT(DISTINCT cpa.id) AS contract_entries,
              COUNT(DISTINCT st.id) AS settlement_entries,
              COUNT(DISTINCT CASE WHEN r.status='active'
                                  THEN ra.receipt_id END) AS receipt_entries
       FROM projects p
       LEFT JOIN project_sites ps ON ps.project_id=p.id AND ps.is_active=1
       LEFT JOIN purchase_orders po ON po.project_id=p.id AND po.status='有效'
       LEFT JOIN construction_sites cs ON cs.project_id=p.id
       LEFT JOIN construction_records cr
         ON cr.site_id=cs.id AND cr.record_status='有效'
       LEFT JOIN contract_project_allocations cpa
         ON cpa.project_id=p.id AND cpa.status='active'
       LEFT JOIN settlements st
         ON st.project_id=p.id AND st.status='active'
       LEFT JOIN receipt_allocations ra ON ra.project_id=p.id
       LEFT JOIN receipts r ON r.id=ra.receipt_id
       GROUP BY p.id
       ORDER BY p.id"""
).fetchall()
for row in coverage:
    print(dict(row))

print("UNASSIGNED")
print(
    {
        "purchase_orders": scalar(
            conn,
            "SELECT COUNT(*) FROM purchase_orders WHERE project_id IS NULL AND status='有效'",
        ),
        "purchase_amount_cents": scalar(
            conn,
            """SELECT COALESCE(SUM(total_amount_cents), 0)
               FROM purchase_orders WHERE project_id IS NULL AND status='有效'""",
        ),
    }
)
print("INTEGRITY")
print(
    {
        "foreign_key_violations": len(conn.execute("PRAGMA foreign_key_check").fetchall()),
        "unmapped_sites": scalar(
            conn,
            """SELECT COUNT(*) FROM construction_sites cs
               LEFT JOIN project_sites ps ON ps.legacy_construction_site_id=cs.id
               WHERE ps.id IS NULL""",
        ),
    }
)
conn.close()

print("CODE_DEPENDENCIES")
for path in sorted((root / "pages").glob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports_database = False
    imported_services = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports_database |= any(alias.name == "database" for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module == "services":
                imported_services.update(alias.name for alias in node.names)
    print(
        path.name,
        {
            "lines": len(path.read_text(encoding="utf-8").splitlines()),
            "database": imports_database,
            "services": sorted(imported_services),
        },
    )
