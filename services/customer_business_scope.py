"""Explicit customer/project links for navigation, never name-based attribution."""
from db.connection import db_read


def project_ids_for_customer(customer_id):
    """Match the overview's fallback only where the project has no own customer.

    This selects original project ledgers, not customer portions of their costs.
    A project with several legacy contract customers can appear for each; its
    full-cycle facts stay unchanged and are labelled as project totals in the UI.
    """
    with db_read() as conn:
        return {row[0] for row in conn.execute('''
            WITH links AS (
                SELECT id AS project_id,customer_partner_id AS customer_id FROM projects
                UNION
                SELECT a.project_id,c.customer_partner_id
                FROM contract_project_allocations a JOIN contracts c ON c.id=a.contract_id
                JOIN projects p ON p.id=a.project_id
                WHERE p.customer_partner_id IS NULL AND a.status='active' AND c.status<>'void'
                UNION
                SELECT s.project_id,c.customer_partner_id FROM settlements s
                JOIN contracts c ON c.id=s.contract_id JOIN projects p ON p.id=s.project_id
                WHERE p.customer_partner_id IS NULL AND s.status='active'
                UNION
                SELECT i.project_id,c.customer_partner_id FROM sales_invoices i
                JOIN contracts c ON c.id=i.contract_id JOIN projects p ON p.id=i.project_id
                WHERE p.customer_partner_id IS NULL AND i.status='active'
                UNION
                SELECT a.project_id,COALESCE(sc.customer_partner_id,c.customer_partner_id)
                FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
                JOIN projects p ON p.id=a.project_id LEFT JOIN settlements s ON s.id=a.settlement_id
                LEFT JOIN contracts sc ON sc.id=s.contract_id LEFT JOIN contracts c ON c.id=a.contract_id
                WHERE p.customer_partner_id IS NULL AND a.status='active' AND r.status='active'
                    AND (a.settlement_id IS NULL OR (s.status='active' AND s.project_id=a.project_id))
            ) SELECT DISTINCT project_id FROM links WHERE customer_id=?
        ''', (int(customer_id),))}
