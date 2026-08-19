"""Non-destructive date normalization and foreign-key lookup indexes."""

import re


def _normalize_iso_date(value):
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", (value or "").strip())
    if not match:
        return value
    year, month, day = map(int, match.groups())
    try:
        from datetime import date
        return date(year, month, day).isoformat()
    except ValueError:
        return value


def migration_310(conn):
    for column in ("record_date", "start_date", "end_date", "inspection_date"):
        rows = conn.execute(
            f"SELECT id, {column} FROM construction_records WHERE {column} IS NOT NULL"
        ).fetchall()
        for row in rows:
            normalized = _normalize_iso_date(row[column])
            if normalized != row[column]:
                conn.execute(
                    f"UPDATE construction_records SET {column}=? WHERE id=?",
                    (normalized, row["id"]),
                )

    indexes = {
        "idx_products_supplier": ("products", "supplier_id"),
        "idx_purchases_product": ("purchases", "product_id"),
        "idx_purchases_supplier": ("purchases", "supplier_id"),
        "idx_work_logs_project_site": ("work_logs", "project_site_id"),
        "idx_work_logs_organization": ("work_logs", "organization_id"),
        "idx_projects_manager_employee": ("projects", "manager_employee_id"),
        "idx_purchase_orders_supplier_legacy": ("purchase_orders", "supplier_id"),
        "idx_purchase_orders_organization": ("purchase_orders", "organization_id"),
        "idx_purchase_items_product": ("purchase_order_items", "product_id"),
        "idx_construction_sites_project": ("construction_sites", "project_id"),
        "idx_material_categories_parent": ("material_categories", "parent_id"),
        "idx_materials_category": ("materials", "category_id"),
        "idx_materials_unit": ("materials", "unit_id"),
        "idx_supplier_offers_organization": ("supplier_offers", "organization_id"),
        "idx_wbs_nodes_parent": ("wbs_nodes", "parent_id"),
        "idx_cost_codes_parent": ("cost_codes", "parent_id"),
        "idx_supplier_profiles_partner": ("supplier_profiles", "partner_id"),
        "idx_operating_entries_organization": ("project_operating_entries", "organization_id"),
        "idx_contracts_customer": ("contracts", "customer_partner_id"),
        "idx_settlements_contract": ("settlements", "contract_id"),
        "idx_sales_invoices_contract": ("sales_invoices", "contract_id"),
        "idx_receipt_allocations_invoice": ("receipt_allocations", "invoice_id"),
        "idx_receipt_allocations_contract": ("receipt_allocations", "contract_id"),
        "idx_receipt_allocations_receipt": ("receipt_allocations", "receipt_id"),
        "idx_payment_entries_contract": ("payment_entries", "contract_id"),
        "idx_business_attachments_organization": ("business_attachments", "organization_id"),
        "idx_labor_adjustments_project": ("labor_rate_adjustments", "project_id"),
        "idx_labor_adjustment_items_project": ("labor_rate_adjustment_items", "project_id"),
        "idx_ai_conversations_project": ("ai_conversations", "project_id"),
        "idx_invoice_revisions_previous_contract": ("sales_invoice_revisions", "previous_contract_id"),
        "idx_invoice_revisions_previous_project": ("sales_invoice_revisions", "previous_project_id"),
        "idx_invoice_settlement_revision_settlement": (
            "invoice_settlement_allocation_revisions", "settlement_id"
        ),
    }
    existing_tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    for name, (table, column) in indexes.items():
        if table in existing_tables:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {table}({column})")


MIGRATIONS = [(310, "经营日期规范化与关键外键索引", migration_310)]
