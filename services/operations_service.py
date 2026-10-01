from datetime import datetime

from db.connection import db_read
from services import project_profit_service


ACTIVE_PROJECT_STATUSES = {"筹备中", "进行中"}


def _percent(numerator, denominator):
    return numerator / denominator * 100 if denominator else None


def _entry_facts(conn):
    project_ids = [
        row["id"] for row in conn.execute("SELECT id FROM projects").fetchall()
    ]
    facts = {
        project_id: {
            "contract_count": 0,
            "settlement_count": 0,
            "invoice_count": 0,
            "receipt_count": 0,
        }
        for project_id in project_ids
    }
    sources = (
        (
            "contract_count",
            """SELECT project_id, COUNT(*) AS fact_count
               FROM contract_project_allocations
               WHERE status='active' GROUP BY project_id""",
        ),
        (
            "settlement_count",
            """SELECT project_id, COUNT(*) AS fact_count
               FROM settlements WHERE status='active' GROUP BY project_id""",
        ),
        (
            "invoice_count",
            """SELECT project_id, COUNT(*) AS fact_count
               FROM sales_invoices WHERE status='active' GROUP BY project_id""",
        ),
        (
            "receipt_count",
            """SELECT ra.project_id, COUNT(DISTINCT ra.receipt_id) AS fact_count
               FROM receipt_allocations ra
               JOIN receipts r ON r.id=ra.receipt_id
               WHERE r.status='active' GROUP BY ra.project_id""",
        ),
    )
    for key, sql in sources:
        for row in conn.execute(sql).fetchall():
            facts[row["project_id"]][key] = row["fact_count"]
    return facts


def _project_stage(summary, facts, project):
    if facts["receipt_count"] and facts["settlement_count"]:
        return "receipt", "回款跟踪"
    if facts["invoice_count"] and facts["settlement_count"]:
        return "invoice", "开票跟踪"
    if facts["settlement_count"] and (
        project["business_mode"] == "cash" or facts["contract_count"]
    ):
        return "accountable", "已可核算"
    if facts["settlement_count"]:
        return "contract_missing", "待补合同"
    if summary["accepted_minor"]:
        return "settlement_pending", "待结算"
    if (
        summary["construction_record_count"]
        or summary["purchase_order_count"]
        or summary["labor_record_count"]
    ):
        return "execution", "执行归集"
    if facts["contract_count"]:
        return "contract", "合同履约"
    return "setup", "待补资料"


def _project_gaps(summary, facts, project):
    gaps = []
    is_cash = project["business_mode"] == "cash"
    invoice_required = project["invoice_policy"] != "not_required"
    if not is_cash and not facts["contract_count"]:
        gaps.append("缺合同分配")
    if not facts["settlement_count"]:
        gaps.append("缺结算确认")
    elif invoice_required and not facts["invoice_count"]:
        gaps.append("缺开票记录")
    if facts["settlement_count"] and not facts["receipt_count"]:
        gaps.append("缺回款记录")
    if summary["invoice_minor"] > summary["settlement_minor"]:
        gaps.append("开票超过结算")
    if summary["receipt_minor"] > summary["settlement_minor"]:
        gaps.append("部分回款待确认收入")
    if (
        summary["construction_record_count"]
        and not (
            summary["purchase_order_count"]
            or summary["labor_record_count"]
            or summary["other_cost_minor"]
        )
    ):
        gaps.append("缺成本归集")
    return gaps


def _gap_text(gaps):
    return "、".join(gaps[:2]) + (f" 等{len(gaps)}项" if len(gaps) > 2 else "")


def get_project_guidance(project_id, summary=None):
    """Return the same stage and data gaps as the dashboard for one project."""
    summary = summary or project_profit_service.get_project_summary(project_id)
    project = summary["project"]
    if project["id"] != project_id:
        raise ValueError("项目与经营汇总不匹配")
    with db_read() as conn:
        facts = dict(conn.execute(
            """SELECT
                 (SELECT COUNT(*) FROM contract_project_allocations
                  WHERE project_id=? AND status='active') AS contract_count,
                 (SELECT COUNT(*) FROM settlements
                  WHERE project_id=? AND status='active') AS settlement_count,
                 (SELECT COUNT(*) FROM sales_invoices
                  WHERE project_id=? AND status='active') AS invoice_count,
                 (SELECT COUNT(DISTINCT ra.receipt_id)
                  FROM receipt_allocations ra
                  JOIN receipts r ON r.id=ra.receipt_id
                  WHERE ra.project_id=? AND r.status='active') AS receipt_count""",
            (project_id,) * 4,
        ).fetchone())
    stage_code, stage_label = _project_stage(summary, facts, project)
    gaps = _project_gaps(summary, facts, project)
    return {
        "stage_code": stage_code,
        "stage_label": stage_label,
        "gaps": gaps,
        "gap_text": _gap_text(gaps),
    }


def get_executive_overview(month=None):
    """Return the owner-facing operating view and North Star proxy.

    Until formal contract/settlement modules replace manual operating entries,
    a project is considered accountable when it has at least one active
    settlement confirmation. This proxy is intentionally explicit so the
    dashboard does not present incomplete project profit as trustworthy.
    """
    month = month or datetime.now().strftime("%Y-%m")
    portfolio = project_profit_service.get_portfolio_summary()
    with db_read() as conn:
        facts_by_project = _entry_facts(conn)
        unassigned_purchase = dict(
            conn.execute(
                """SELECT COUNT(*) AS order_count,
                          COALESCE(SUM(total_amount_cents), 0) AS amount_minor
                   FROM purchase_orders po
                   WHERE po.project_id IS NULL AND po.status='active'
                     AND NOT EXISTS (
                       SELECT 1 FROM purchase_cost_allocation_lines pal
                       WHERE pal.purchase_order_id=po.id AND pal.status='active'
                     )"""
            ).fetchone()
        )
        pending_inspection_count = conn.execute(
            """SELECT COUNT(*)
               FROM construction_records
               WHERE record_status='有效'
                 AND inspection_status IN ('待验收', '需整改')"""
        ).fetchone()[0]
        current_month_purchase_minor = conn.execute(
            """SELECT COALESCE(SUM(total_amount_cents), 0)
               FROM purchase_orders
               WHERE status='active' AND substr(purchase_date, 1, 7)=?""",
            (month,),
        ).fetchone()[0]

    projects = []
    for summary in portfolio["projects"]:
        project = summary["project"]
        facts = facts_by_project.get(
            project["id"],
            {
                "contract_count": 0,
                "settlement_count": 0,
                "invoice_count": 0,
                "receipt_count": 0,
            },
        )
        stage_code, stage_label = _project_stage(summary, facts, project)
        gaps = _project_gaps(summary, facts, project)
        is_active = project["status"] in ACTIVE_PROJECT_STATUSES
        is_accountable = bool(
            facts["settlement_count"]
            and (
                project["business_mode"] == "cash"
                or facts["contract_count"]
            )
        )
        projects.append(
            {
                "project_id": project["id"],
                "project_code": project["project_code"],
                "project_name": project["name"],
                "customer_name": project["customer_name"],
                "status": project["status"],
                "is_active": is_active,
                "is_accountable": is_accountable,
                "stage_code": stage_code,
                "stage_label": stage_label,
                "gaps": gaps,
                "gap_text": _gap_text(gaps),
                "contract_minor": summary["contract_minor"],
                "settlement_minor": summary["settlement_minor"],
                "invoice_minor": summary["invoice_minor"],
                "receipt_minor": summary["receipt_minor"],
                "total_cost_minor": summary["total_cost_minor"],
                "gross_profit_minor": summary["gross_profit_minor"],
                "cash_balance_minor": summary["cash_balance_minor"],
                "receivable_minor": summary["receivable_minor"],
                "purchase_order_count": summary["purchase_order_count"],
                "labor_record_count": summary["labor_record_count"],
                "construction_record_count": summary[
                    "construction_record_count"
                ],
            }
        )

    active_projects = [row for row in projects if row["is_active"]]
    accountable_projects = [
        row for row in active_projects if row["is_accountable"]
    ]
    contracted_projects = [
        row
        for row in active_projects
        if facts_by_project[row["project_id"]]["contract_count"]
    ]
    settled_projects = [
        row
        for row in active_projects
        if facts_by_project[row["project_id"]]["settlement_count"]
    ]
    assigned_purchase_minor = sum(
        row["purchase_cost_minor"]
        for row in portfolio["projects"]
    )
    unassigned_labor = portfolio["unassigned_labor"]
    assigned_labor_minor = sum(
        row["labor_cost_minor"] for row in portfolio["projects"]
    )
    total_purchase_minor = (
        assigned_purchase_minor + unassigned_purchase["amount_minor"]
    )
    total_labor_minor = assigned_labor_minor + unassigned_labor["amount_minor"]
    unassigned_cost_minor = (
        unassigned_purchase["amount_minor"] + unassigned_labor["amount_minor"]
    )

    projects.sort(
        key=lambda row: (
            not row["is_active"],
            row["is_accountable"],
            -len(row["gaps"]),
            row["project_code"],
        )
    )
    active_projects = [row for row in projects if row["is_active"]]
    return {
        "month": month,
        "north_star": {
            "name": "项目经营可核算率",
            "accountable_project_count": len(accountable_projects),
            "active_project_count": len(active_projects),
            "percent": _percent(
                len(accountable_projects), len(active_projects)
            ),
            "is_proxy": False,
            "definition": (
                "合同工程已有合同分配和结算确认，或零星现金工程已有完工金额确认"
                "的在营项目 ÷ 全部在营项目"
            ),
        },
        "summary": {
            "confirmed_gross_profit_minor": sum(
                row["gross_profit_minor"] for row in accountable_projects
            ),
            "receivable_minor": sum(
                max(row["receivable_minor"], 0) for row in projects
            ),
            "cash_balance_minor": sum(
                row["cash_balance_minor"] for row in accountable_projects
            ),
            "unassigned_cost_minor": unassigned_cost_minor,
            "current_month_purchase_minor": current_month_purchase_minor,
            "pending_inspection_count": pending_inspection_count,
        },
        "drivers": {
            "contract_coverage_percent": _percent(
                len(contracted_projects), len(active_projects)
            ),
            "settlement_coverage_percent": _percent(
                len(settled_projects), len(active_projects)
            ),
            "purchase_attribution_percent": _percent(
                assigned_purchase_minor, total_purchase_minor
            ),
            "labor_attribution_percent": _percent(
                assigned_labor_minor, total_labor_minor
            ),
            "unassigned_purchase_count": unassigned_purchase["order_count"],
            "unassigned_purchase_minor": unassigned_purchase["amount_minor"],
            "unassigned_labor_count": unassigned_labor["record_count"],
            "unassigned_labor_minor": unassigned_labor["amount_minor"],
            "pending_inspection_count": pending_inspection_count,
        },
        "projects": active_projects,
    }
