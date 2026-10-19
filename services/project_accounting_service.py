"""Read-only accounting guardrails; book profit never excludes pending costs."""

from db.connection import db_read, use_read_snapshot
from services import project_profit_service


ACCOUNTING_KEYS = ("is_accountable", "accounting_status", "accounting_note")
BOOK_PROFIT_NOTE = "账面毛利（含待完善）＝已确认收入－项目累计成本。"


def is_accountable(project, facts):
    """Confirmed income plus a traceable revenue boundary, not a profit proxy."""
    return bool(facts['settlement_count'] and
                (project['business_mode'] == 'cash' or facts['contract_count']))


def _accounting_fields(project, facts):
    accountable = is_accountable(project, facts)
    missing = []
    if project["business_mode"] != "cash" and not facts["contract_count"]:
        missing.append("有效合同分配")
    if not facts["settlement_count"]:
        missing.append("收入确认")
    if accountable:
        status = "已可核算"
        if project["business_mode"] == "cash":
            basis = "现金工程已有收入确认，无需合同分配"
        else:
            basis = "已有有效合同分配和收入确认"
        note = f"{basis}，计入已确认毛利。"
    else:
        if len(missing) == 2:
            status = "待补合同及确认收入"
        elif not facts["settlement_count"]:
            status = "待确认收入"
        else:
            status = "待补合同"
        note = f"缺{'、'.join(missing)}，暂不计入已确认毛利；收入和成本仍完整计入账面合计。"
    return {
        "is_accountable": accountable,
        "accounting_status": status,
        "accounting_note": BOOK_PROFIT_NOTE + note,
    }


def get_accounting_map():
    """Batch effective entry facts and readable guardrails for every project.

    Active allocations of void contracts and void receipt allocations are not
    effective facts. A completed contract remains a valid revenue boundary.
    Nested callers reuse their existing read snapshot through ``db_read``.
    """
    with db_read() as conn:
        rows = conn.execute("""
            WITH contract_counts AS (
                SELECT a.project_id, COUNT(DISTINCT c.id) AS contract_count,
                       COUNT(DISTINCT CASE WHEN c.pricing_mode='actual' THEN c.id END)
                           AS actual_contract_count
                FROM contract_project_allocations a JOIN contracts c ON c.id=a.contract_id
                WHERE a.status='active' AND c.status<>'void' GROUP BY a.project_id
            ), settlement_counts AS (
                SELECT project_id, COUNT(*) AS settlement_count
                FROM settlements WHERE status='active' GROUP BY project_id
            ), invoice_counts AS (
                SELECT project_id, COUNT(*) AS invoice_count
                FROM sales_invoices WHERE status='active' GROUP BY project_id
            ), receipt_counts AS (
                SELECT a.project_id, COUNT(DISTINCT r.id) AS receipt_count
                FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
                WHERE a.status='active' AND r.status='active' GROUP BY a.project_id
            )
            SELECT p.id, p.business_mode,
                   COALESCE(c.contract_count,0) AS contract_count,
                   COALESCE(c.actual_contract_count,0) AS actual_contract_count,
                   COALESCE(s.settlement_count,0) AS settlement_count,
                   COALESCE(i.invoice_count,0) AS invoice_count,
                   COALESCE(r.receipt_count,0) AS receipt_count
            FROM projects p
            LEFT JOIN contract_counts c ON c.project_id=p.id
            LEFT JOIN settlement_counts s ON s.project_id=p.id
            LEFT JOIN invoice_counts i ON i.project_id=p.id
            LEFT JOIN receipt_counts r ON r.project_id=p.id
        """).fetchall()
    result = {}
    for row in rows:
        facts = {key: row[key] for key in (
            "contract_count", "actual_contract_count", "settlement_count",
            "invoice_count", "receipt_count",
        )}
        result[row["id"]] = {**facts, **_accounting_fields(row, facts)}
    return result


def decorate_projects(rows, *, accounting_map=None):
    """Copy ledger/profit rows and attach one batch of authoritative guardrails.

    Supports ``id``, ``project_id`` and nested ``project.id``, plus ledger's
    ``cost_minor`` alias. Existing book profit is preserved verbatim. Call while
    the amount query's ``use_read_snapshot`` is active, or use
    ``get_profit_dashboard`` to obtain amounts and flags in one snapshot.
    """
    rows = [dict(row) for row in rows]
    if not rows:
        return rows
    if accounting_map is None:
        accounting_map = get_accounting_map()
    for row in rows:
        project = row.get("project", {})
        project_id = row.get("project_id", row.get("id", project.get("id")))
        if project_id is None or int(project_id) not in accounting_map:
            raise ValueError("项目不存在，请刷新后核对核算状态")
        row.update(accounting_map[int(project_id)])
        row.setdefault("total_cost_minor", row.get("cost_minor", 0))
        row.setdefault("gross_profit_minor", row["settlement_minor"] - row["total_cost_minor"])
    return rows


def summarize_projects(rows):
    """Sum every book amount; split profit, never discard pending-project cost.

    Decorated rows are aggregated without further database reads. Undecorated
    rows receive guardrails in one batch; their caller must own the snapshot.
    ``gross_profit_minor`` retains its historical book-difference meaning.
    """
    rows = list(rows)
    if any("is_accountable" not in row for row in rows):
        rows = decorate_projects(rows)
    summary = dict.fromkeys((
        "settlement_minor", "receivable_minor", "total_cost_minor", "gross_profit_minor",
        "confirmed_gross_profit_minor", "pending_gross_profit_minor",
        "accountable_count", "pending_count", "project_count",
    ), 0)
    for row in rows:
        summary["settlement_minor"] += row["settlement_minor"]
        summary["receivable_minor"] += row.get("receivable_minor", 0)
        summary["total_cost_minor"] += row.get("total_cost_minor", row.get("cost_minor", 0))
        summary["gross_profit_minor"] += row["gross_profit_minor"]
        if row["is_accountable"]:
            summary["confirmed_gross_profit_minor"] += row["gross_profit_minor"]
            summary["accountable_count"] += 1
        else:
            summary["pending_gross_profit_minor"] += row["gross_profit_minor"]
            summary["pending_count"] += 1
        summary["project_count"] += 1
    return summary


def get_profit_dashboard(project_id=None, *, customer_id=None):
    """Profit-board payload with amounts, flags and totals in one read snapshot.

    ``accounting_summary`` is scoped to the returned rows, including receivables.
    Existing portfolio fields (including any legacy ``summary``) are preserved.
    Customer scope uses real project ownership, never customer display names.
    This is the integration entry point for ``dashboards('profit', ...)``.
    """
    with db_read() as conn, use_read_snapshot(conn):
        if project_id is not None:
            details = project_profit_service.get_project_summary(project_id)
            result = {"projects": [details], "details": details}
        else:
            result = dict(project_profit_service.get_portfolio_summary())
        rows = result["projects"]
        if customer_id is not None:
            from services.customer_business_scope import project_ids_for_customer
            project_ids = project_ids_for_customer(customer_id)
            rows = [row for row in rows if row["project"]["id"] in project_ids]
        rows = decorate_projects(rows)
        result["projects"] = [
            {**row, "id": row["project"]["id"], "project_name": row["project"]["name"]}
            for row in rows
        ]
        result["accounting_summary"] = summarize_projects(result["projects"])
        if project_id is not None:
            result["summary"] = dict(result["accounting_summary"])
            if rows:
                result["details"] = rows[0]
            else:
                result.pop("details", None)
        return result
