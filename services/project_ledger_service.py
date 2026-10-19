"""Read-only project ledger with financial facts and reconcilable drill-downs."""

from db.business_facts import project_finance_rows, COST_FACTS_SQL
from db.connection import db_read, use_read_snapshot
from services import contract_service, finance_service, project_service
from services import project_accounting_service as accounting


MONEY_KEYS = ("allocated_minor", "settlement_minor", "receipt_minor", "invoice_minor", "receivable_minor",
              "cost_minor", "gross_profit_minor")


def _rows(conn, project_id=None, keyword=""):
    projects = project_service.list_projects(keyword=keyword)
    finances = {row["project_id"]: row for row in project_finance_rows(conn, project_id)}
    cost_totals = {row['project_id']: row['total'] for row in conn.execute(
        'SELECT project_id,SUM(amount_minor) AS total FROM (' + COST_FACTS_SQL + ') GROUP BY project_id')}
    unsigned = {row['project_id']: dict(row) for row in conn.execute('''SELECT project_id,COUNT(*) AS count,
        SUM(amount_minor) AS amount FROM settlements WHERE status='active' AND source_type='contract'
        AND contract_id IS NULL GROUP BY project_id''')}
    result = []
    for project in projects:
        facts = finances.get(project["id"])
        if facts is None:
            continue
        facts['cost_minor'] = cost_totals.get(project['id'], 0)
        facts['gross_profit_minor'] = facts['settlement_minor'] - facts['cost_minor']
        result.append({**project, **{key: facts[key] for key in MONEY_KEYS},
                       'unsigned_count': unsigned.get(project['id'], {}).get('count', 0),
                       'unsigned_minor': unsigned.get(project['id'], {}).get('amount', 0),
                       "pending_receipt_minor": facts["pending_receipt_minor"],
                       "advance_minor": facts["advance_minor"]})
    return accounting.decorate_projects(result)


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
        contracts = [dict(row) for row in conn.execute('''SELECT c.*,a.allocated_amount_minor,
            c.tax_inclusive_amount_minor AS contract_amount_minor FROM contract_project_allocations a
            JOIN contracts c ON c.id=a.contract_id WHERE a.project_id=? AND a.status='active'
            AND c.status<>'void' ORDER BY c.sign_date,c.id''', (project_id,))]
        costs = [dict(row) for row in conn.execute('SELECT * FROM (' + COST_FACTS_SQL +
            ') WHERE project_id=? ORDER BY date DESC,id', (project_id,))]
        accounting_fields = {key: project[key] for key in accounting.ACCOUNTING_KEYS}
        details = {
            'contracts': {'rows': contracts, 'amount_key': 'allocated_amount_minor',
                          'total_minor': project['allocated_minor']},
            'costs': {'rows': costs, 'amount_key': 'amount_minor', 'total_minor': project['cost_minor']},
            'profit': {'rows': [row for row in [
                               {'label': '已确认收入', 'amount_minor': project['settlement_minor']},
                               {'label': '减：项目累计成本', 'amount_minor': -project['cost_minor']}]
                               if row['amount_minor']],
                       'amount_key': 'amount_minor', 'total_minor': project['gross_profit_minor'],
                       'label': '账面毛利（含待完善）', 'note': project['accounting_note'],
                       **accounting_fields},
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
        summary = {key: project[key] for key in MONEY_KEYS}
        summary.update(accounting.summarize_projects([project]))
        summary.update(accounting_fields)
        return {"project": project, "summary": summary,
                "details": details}
