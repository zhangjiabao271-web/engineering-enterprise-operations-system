"""Read-only project ledger with financial facts and reconcilable drill-downs."""

from db.business_facts import project_finance_rows
from db.connection import db_read, use_read_snapshot
from services import contract_service, finance_service, project_service


MONEY_KEYS = ("settlement_minor", "receipt_minor", "invoice_minor", "receivable_minor")


def _rows(conn, project_id=None, keyword=""):
    projects = project_service.list_projects(keyword=keyword)
    finances = {row["project_id"]: row for row in project_finance_rows(conn, project_id)}
    counts = {row["project_id"]: dict(row) for row in conn.execute("""
        SELECT project_id,
               SUM(kind='settlement') AS settlement_count,
               SUM(kind='invoice') AS invoice_count,
               SUM(kind='receipt') AS receipt_count
        FROM (
            SELECT project_id, 'settlement' AS kind, id FROM settlements WHERE status='active'
            UNION ALL
            SELECT project_id, 'invoice', id FROM sales_invoices WHERE status='active'
            UNION ALL
            SELECT a.project_id, 'receipt', r.id
            FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
            WHERE a.status='active' AND r.status='active' GROUP BY a.project_id,r.id
        ) GROUP BY project_id
    """)}
    result = []
    for project in projects:
        facts = finances.get(project["id"])
        if facts is None:
            continue
        count = counts.get(project["id"], {})
        result.append({**project, **{key: facts[key] for key in MONEY_KEYS},
                       "pending_receipt_minor": facts["pending_receipt_minor"],
                       "advance_minor": facts["advance_minor"],
                       **{key: int(count.get(key, 0)) for key in
                          ("settlement_count", "receipt_count", "invoice_count")}})
    return result


def list_projects(project_id=None, keyword=""):
    with db_read() as conn, use_read_snapshot(conn):
        return _rows(conn, project_id, keyword)


def get_details(project_id):
    with db_read() as conn, use_read_snapshot(conn):
        projects = _rows(conn, int(project_id))
        if not projects:
            raise ValueError("项目不存在，请刷新台账后核对")
        project = projects[0]
        project_id = project["id"]
        income = contract_service.list_settlements(project_id=project_id)
        invoices = finance_service.list_invoices(project_id=project_id)
        receipts = [dict(row) for row in conn.execute("""
            SELECT r.*, SUM(a.allocated_amount_minor) AS project_amount_minor,
                   SUM(CASE WHEN a.settlement_id IS NULL THEN a.allocated_amount_minor
                            ELSE 0 END) AS pending_allocation_minor,
                   REPLACE(GROUP_CONCAT(DISTINCT c.contract_no), ',', '、') AS contract_no,
                   REPLACE(GROUP_CONCAT(DISTINCT s.settlement_no), ',', '、') AS settlement_no
            FROM receipts r JOIN receipt_allocations a ON a.receipt_id=r.id
            LEFT JOIN contracts c ON c.id=a.contract_id
            LEFT JOIN settlements s ON s.id=a.settlement_id
            WHERE r.status='active' AND a.status='active' AND a.project_id=?
            GROUP BY r.id ORDER BY r.receipt_date DESC,r.id DESC
        """, (project_id,))]
        received = {row["settlement_id"]: row["amount_minor"] for row in conn.execute("""
            SELECT a.settlement_id,SUM(a.allocated_amount_minor) AS amount_minor
            FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
            WHERE a.project_id=? AND a.settlement_id IS NOT NULL
              AND a.status='active' AND r.status='active' GROUP BY a.settlement_id
        """, (project_id,))}
        for row in income:
            row["received_minor"] = received.get(row["id"], 0)
            row["unreceived_minor"] = max(row["amount_minor"] - row["received_minor"], 0)
        details = {
            "settlements": {"rows": income, "amount_key": "amount_minor",
                            "total_minor": project["settlement_minor"]},
            "receipts": {"rows": receipts, "amount_key": "project_amount_minor",
                         "total_minor": project["receipt_minor"]},
            "invoices": {"rows": invoices, "amount_key": "amount_minor",
                         "total_minor": project["invoice_minor"]},
            "outstanding": {"rows": [row for row in income if row["unreceived_minor"] > 0],
                            "amount_key": "unreceived_minor",
                            "total_minor": project["receivable_minor"]},
        }
        for detail in details.values():
            detail["detail_total_minor"] = sum(row[detail["amount_key"]] for row in detail["rows"])
            detail["reconciled"] = detail["detail_total_minor"] == detail["total_minor"]
        return {"project": project, "summary": {key: project[key] for key in MONEY_KEYS},
                "details": details}
