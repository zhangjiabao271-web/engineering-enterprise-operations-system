import argparse
import sqlite3
import sys
from pathlib import Path


REQUIRED_TABLES = {
    "organizations",
    "employees",
    "business_partners",
    "partner_roles",
    "partner_contacts",
    "units_of_measure",
    "material_categories",
    "materials",
    "supplier_offers",
    "supplier_profiles",
    "customer_profiles",
    "project_sites",
    "wbs_nodes",
    "cost_codes",
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
    "worker_rate_versions",
    "labor_rate_adjustments",
    "labor_rate_adjustment_items",
    "labor_rate_lock_events",
}


def scalar(conn, sql):
    return conn.execute(sql).fetchone()[0]


def validate(path):
    conn = sqlite3.connect(path)
    try:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = sorted(REQUIRED_TABLES - tables)
        checks = {
            "missing_tables": missing,
            "foreign_key_violations": conn.execute("PRAGMA foreign_key_check").fetchall(),
            "organizations": scalar(conn, "SELECT COUNT(*) FROM organizations"),
            "partners_without_roles": scalar(
                conn,
                """SELECT COUNT(*) FROM business_partners bp
                   WHERE NOT EXISTS (
                       SELECT 1 FROM partner_roles pr WHERE pr.partner_id=bp.id
                   )""",
            ),
            "customers_without_profiles": scalar(
                conn,
                """SELECT COUNT(*) FROM partner_roles pr
                   LEFT JOIN customer_profiles cp ON cp.partner_id=pr.partner_id
                   WHERE pr.role_code='customer' AND cp.partner_id IS NULL""",
            ),
            "suppliers_without_profiles": scalar(
                conn,
                """SELECT COUNT(*) FROM partner_roles pr
                   LEFT JOIN supplier_profiles sp ON sp.partner_id=pr.partner_id
                   WHERE pr.role_code='supplier' AND sp.partner_id IS NULL""",
            ),
            "unmapped_projects": scalar(
                conn,
                "SELECT COUNT(*) FROM projects WHERE organization_id IS NULL OR public_id IS NULL",
            ),
            "supplier_count_difference": scalar(conn, "SELECT COUNT(*) FROM suppliers")
            - scalar(conn, "SELECT COUNT(*) FROM business_partners WHERE legacy_supplier_id IS NOT NULL"),
            "product_offer_count_difference": scalar(conn, "SELECT COUNT(*) FROM products")
            - scalar(conn, "SELECT COUNT(*) FROM supplier_offers WHERE legacy_product_id IS NOT NULL"),
            "site_count_difference": scalar(conn, "SELECT COUNT(*) FROM construction_sites")
            - scalar(conn, "SELECT COUNT(*) FROM project_sites WHERE legacy_construction_site_id IS NOT NULL"),
            "projects_without_root_wbs": scalar(
                conn,
                """SELECT COUNT(*) FROM projects p
                   WHERE NOT EXISTS (
                       SELECT 1 FROM wbs_nodes w WHERE w.project_id=p.id AND w.wbs_code='ROOT'
                   )""",
            ),
            "unmapped_formal_purchase_orders": scalar(
                conn,
                """SELECT COUNT(*) FROM purchase_orders
                   WHERE purchase_type='正式采购' AND status='有效'
                     AND supplier_partner_id IS NULL""",
            ),
            "unmapped_legacy_purchase_items": scalar(
                conn,
                """SELECT COUNT(*) FROM purchase_order_items
                   WHERE product_id IS NOT NULL
                     AND (material_id IS NULL OR supplier_offer_id IS NULL)""",
            ),
            "unextended_work_logs": scalar(
                conn,
                """SELECT COUNT(*) FROM work_logs
                   WHERE public_id IS NULL OR organization_id IS NULL
                     OR amount_minor IS NULL OR daily_rate_minor IS NULL""",
            ),
            "workers_without_rate_version": scalar(
                conn,
                """SELECT COUNT(*) FROM workers w
                   WHERE NOT EXISTS (
                       SELECT 1 FROM worker_rate_versions wrv
                       WHERE wrv.worker_id=w.id AND wrv.status='active'
                   )""",
            ),
            "invalid_labor_adjustments": scalar(
                conn,
                """SELECT COUNT(*) FROM labor_rate_adjustments
                   WHERE delta_minor <> new_amount_minor - old_amount_minor
                      OR affected_count < 0 OR skipped_locked_count < 0""",
            ),
            "locked_labor_without_timestamp": scalar(
                conn,
                """SELECT COUNT(*) FROM work_logs
                   WHERE COALESCE(rate_locked, 0)=1 AND rate_locked_at IS NULL""",
            ),
            "active_legacy_operating_entries": scalar(
                conn,
                """SELECT COUNT(*) FROM project_operating_entries
                   WHERE status='active'""",
            ),
            "overallocated_contracts": scalar(
                conn,
                """SELECT COUNT(*) FROM (
                       SELECT c.id
                       FROM contracts c
                       JOIN contract_project_allocations a
                         ON a.contract_id=c.id AND a.status='active'
                       WHERE c.status<>'void'
                       GROUP BY c.id, c.tax_inclusive_amount_minor
                       HAVING SUM(a.allocated_amount_minor)
                              > c.tax_inclusive_amount_minor
                   )""",
            ),
            "unbalanced_receipts": scalar(
                conn,
                """SELECT COUNT(*) FROM (
                       SELECT r.id
                       FROM receipts r
                       LEFT JOIN receipt_allocations ra ON ra.receipt_id=r.id
                       WHERE r.status='active'
                       GROUP BY r.id, r.amount_minor
                       HAVING COALESCE(SUM(ra.allocated_amount_minor), 0)
                              <> r.amount_minor
                   )""",
            ),
            "oversettled_allocations": scalar(
                conn,
                """SELECT COUNT(*) FROM (
                       SELECT a.contract_id, a.project_id
                       FROM contract_project_allocations a
                       LEFT JOIN settlements s
                         ON s.contract_id=a.contract_id
                        AND s.project_id=a.project_id
                        AND s.status='active'
                       WHERE a.status='active'
                       GROUP BY a.contract_id, a.project_id,
                                a.allocated_amount_minor
                       HAVING COALESCE(SUM(s.amount_minor), 0)
                              > a.allocated_amount_minor
                   )""",
            ),
            "overinvoiced_projects": scalar(
                conn,
                """SELECT COUNT(*) FROM (
                       SELECT i.contract_id, i.project_id
                       FROM sales_invoices i
                       WHERE i.status='active'
                       GROUP BY i.contract_id, i.project_id
                       HAVING SUM(i.amount_minor) > COALESCE((
                           SELECT SUM(s.amount_minor) FROM settlements s
                           WHERE s.contract_id=i.contract_id
                             AND s.project_id=i.project_id
                             AND s.status='active'
                       ), 0)
                   )""",
            ),
            "overreceived_projects": scalar(
                conn,
                """SELECT COUNT(*) FROM (
                       SELECT ra.contract_id, ra.project_id
                       FROM receipt_allocations ra
                       JOIN receipts r ON r.id=ra.receipt_id
                       WHERE r.status='active'
                       GROUP BY ra.contract_id, ra.project_id
                       HAVING SUM(ra.allocated_amount_minor) > COALESCE((
                           SELECT SUM(s.amount_minor) FROM settlements s
                           WHERE s.contract_id=ra.contract_id
                             AND s.project_id=ra.project_id
                             AND s.status='active'
                       ), 0)
                   )""",
            ),
        }
        failed = (
            bool(checks["missing_tables"])
            or bool(checks["foreign_key_violations"])
            or checks["organizations"] < 1
            or any(
                checks[name] != 0
                for name in (
                    "unmapped_projects",
                    "supplier_count_difference",
                    "product_offer_count_difference",
                    "site_count_difference",
                    "projects_without_root_wbs",
                    "unmapped_formal_purchase_orders",
                    "unmapped_legacy_purchase_items",
                    "unextended_work_logs",
                    "workers_without_rate_version",
                    "invalid_labor_adjustments",
                    "locked_labor_without_timestamp",
                    "active_legacy_operating_entries",
                    "overallocated_contracts",
                    "unbalanced_receipts",
                    "oversettled_allocations",
                    "overinvoiced_projects",
                    "overreceived_projects",
                )
            )
        )
        return checks, failed
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Validate a migrated V3 database")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()
    checks, failed = validate(args.database)
    for name, value in checks.items():
        print(f"{name}: {value}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
