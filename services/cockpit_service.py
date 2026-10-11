"""Read-only cockpit: every card and drill-down uses one ledger snapshot."""

import re
from datetime import datetime

from db.business_facts import COST_FACTS_SQL
from db.connection import db_read, use_read_snapshot
from services import finance_service, operations_service, project_profit_service


METRICS = {
    "income": ("已确认收入", "有效收入确认；含已完工项目，不以合同金额代替。"),
    "receipts": ("项目已回款", "按工程项目分配的回款；不含独立历史旧账回收。"),
    "outstanding": ("应收未收", "各项目确认收入尚未收回的余额；预收款不跨项目抵扣。"),
    "invoices": ("项目已开票", "有效销项发票价税合计；开票不等于到账。"),
    "costs": ("已归集项目成本", "采购、人工及费用按项目分摊后的合计；不是实际资金支出。"),
    "profit": ("在营可核算项目毛利", "仅在营且已具备核算事实的项目：确认收入减已登记成本；不是净利润。"),
    "uninvoiced": ("待开票金额", "需开票项目的确认收入减已开票金额；不开票的现金工程不计入。"),
    "unassigned": ("待归集成本", "尚无明确项目归属的采购、人工和费用；单项目范围不混入全公司待归集。"),
}


def _month(value=None):
    value = value or datetime.now().astimezone().strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", value):
        raise ValueError("月份格式应为 YYYY-MM")
    return value


def _cost_rows(conn, project_id=None, unassigned=False):
    condition = "project_id IS NULL" if unassigned else "project_id IS NOT NULL"
    params = []
    if project_id is not None:
        condition += " AND project_id=?"
        params.append(int(project_id))
    return [dict(row) for row in conn.execute(
        f"SELECT * FROM ({COST_FACTS_SQL}) WHERE {condition} ORDER BY date DESC,id,project_id", params
    )]


def _financial_rows(conn, metric, project_id=None):
    sources = {
        "income": """SELECT s.id,s.project_id,s.settlement_date AS date,
            s.settlement_no AS number,s.amount_minor,s.basis AS description
            FROM settlements s WHERE s.status='active'""",
        "invoices": """SELECT i.id,i.project_id,i.invoice_date AS date,
            i.invoice_no AS number,i.amount_minor,i.notes AS description
            FROM sales_invoices i WHERE i.status='active'""",
        "receipts": """SELECT r.id,a.project_id,r.receipt_date AS date,
            r.receipt_no AS number,SUM(a.allocated_amount_minor) AS amount_minor,
            r.payment_method AS description
            FROM receipts r JOIN receipt_allocations a ON a.receipt_id=r.id
            WHERE r.status='active' AND a.status='active' GROUP BY r.id,a.project_id""",
    }
    sql = f"""SELECT x.*,p.name AS project FROM ({sources[metric]}) x
              JOIN projects p ON p.id=x.project_id"""
    params = []
    if project_id is not None:
        sql += " WHERE x.project_id=?"
        params.append(int(project_id))
    return [dict(row) for row in conn.execute(sql + " ORDER BY date DESC,id DESC,project_id", params)]


def _facts(project_id):
    finance = finance_service.get_finance_dashboard(project_id)
    portfolio = project_profit_service.get_portfolio_summary()
    summaries = {row["project"]["id"]: row for row in portfolio["projects"]}
    projects = []
    for row in finance["projects"]:
        summary = summaries[row["project_id"]]
        projects.append({**row, "customer_name": summary["project"]["customer_name"],
                         "total_cost_minor": summary["total_cost_minor"]})
    if project_id is not None and not projects:
        raise ValueError("项目不存在，请重新选择项目范围")
    return finance, projects


def get_overview(project_id=None, month=None):
    month = _month(month)
    with db_read() as conn, use_read_snapshot(conn):
        legacy = operations_service.get_executive_overview(month)
        finance, projects = _facts(project_id)
        active = [row for row in legacy["projects"]
                  if project_id is None or row["project_id"] == int(project_id)]
        accountable = [row for row in active if row["is_accountable"]]
        costs = _cost_rows(conn, project_id)
        unassigned = _cost_rows(conn, project_id, unassigned=True)
        values = {
            "income": finance["summary"]["settlement_minor"],
            "receipts": finance["summary"]["receipt_minor"],
            "outstanding": finance["summary"]["receivable_minor"],
            "invoices": finance["summary"]["invoice_minor"],
            "costs": sum(row["amount_minor"] for row in costs),
            "profit": sum(row["gross_profit_minor"] for row in accountable),
            "uninvoiced": finance["summary"]["uninvoiced_minor"],
            "unassigned": sum(row["amount_minor"] for row in unassigned),
        }
        if values["costs"] != sum(row["total_cost_minor"] for row in projects):
            raise ValueError("成本明细与项目核算不一致，请先核对账本")
        def progress(key, label):
            value = finance["summary"][key]
            return f"{label} {value:.1f}%" if value is not None else "尚无适用的确认收入"

        notes = {
            "income": "有效收入确认累计，含已完工项目",
            "receipts": progress("receipt_rate_percent", "已确认收入回款进度"),
            "outstanding": f"{sum(row['receivable_minor'] > 0 for row in projects)} 个项目仍未收清",
            "invoices": progress("invoice_rate_percent", "需开票收入的开票进度"),
            "costs": "采购 + 人工 + 费用，含历史项目",
            "profit": "仅在营可核算项目，不是净利润",
            "uninvoiced": "不开票的现金工程不计入",
            "unassigned": f"{len(unassigned)} 条记录尚无明确项目归属",
        }
        metrics = [{"key": key, "label": label, "definition": definition, "note": notes[key],
                    "amount_minor": values[key]} for key, (label, definition) in METRICS.items()]
        year, number = map(int, month.split("-"))
        end = year * 12 + number - 1
        months = [f"{index // 12:04d}-{index % 12 + 1:02d}" for index in range(end - 5, end + 1)]
        trend = {value: {"month": value, "income_minor": 0, "receipt_minor": 0, "cost_minor": 0}
                 for value in months}
        for metric, key in (("income", "income_minor"), ("receipts", "receipt_minor")):
            for row in _financial_rows(conn, metric, project_id):
                period = row["date"][:7]
                if period in trend:
                    trend[period][key] += row["amount_minor"]
        composition = {"采购成本": 0, "人工成本": 0, "费用成本": 0}
        for row in costs:
            period = row["date"][:7]
            if period in trend:
                trend[period]["cost_minor"] += row["amount_minor"]
            kind = row["id"].split("-", 1)[0]
            category = {"purchase": "采购成本", "labor": "人工成本", "expense": "费用成本"}[kind]
            composition[category] += row["amount_minor"]
        customers = {}
        for row in projects:
            name = row["customer_name"] or "客户待完善"
            # Identity is the customer ID, not a fuzzy or duplicate display name.
            identity = row["customer_partner_id"] or f"project-{row['project_id']}"
            bucket = customers.setdefault(identity, {"name": name, "amount_minor": 0})
            bucket["amount_minor"] += row["receivable_minor"]
        pending = sorted((row for row in projects if row["receivable_minor"] > 0),
                         key=lambda row: (-row["receivable_minor"], row["project_id"]))
        return {**legacy, "cockpit": {
            "scope": projects[0]["project_name"] if project_id is not None else "全部工程项目",
            "month": month, "metrics": metrics, "trend": list(trend.values()),
            "composition": [{"name": key, "amount_minor": value} for key, value in composition.items()],
            "customers": sorted((row for row in customers.values() if row["amount_minor"] > 0),
                                key=lambda row: (-row["amount_minor"], row["name"])),
            "active_count": len(active), "accountable_count": len(accountable),
            "outstanding_count": len(pending), "projects": active,
            "priorities": pending[:5],
        }}


def get_detail(metric, project_id=None, month=None):
    if metric not in METRICS:
        raise ValueError("不支持的驾驶舱明细")
    if month:
        month = _month(month)
        if metric not in ("income", "receipts", "costs"):
            raise ValueError("此余额指标不按发生月份筛选")
    with db_read() as conn, use_read_snapshot(conn):
        finance, projects = _facts(project_id)
        if metric in ("income", "receipts", "invoices"):
            rows = _financial_rows(conn, metric, project_id)
        elif metric in ("costs", "unassigned"):
            rows = _cost_rows(conn, project_id, unassigned=metric == "unassigned")
        elif metric == "profit":
            overview = operations_service.get_executive_overview()
            rows = [{**row, "amount_minor": row["gross_profit_minor"]} for row in overview["projects"]
                    if row["is_accountable"] and (project_id is None or row["project_id"] == int(project_id))]
        else:
            key = "receivable_minor" if metric == "outstanding" else "uninvoiced_minor"
            rows = [{**row, "amount_minor": row[key]} for row in projects if row[key] > 0]
            rows.sort(key=lambda row: (-row["amount_minor"], row["project_id"]))
        if month:
            rows = [row for row in rows if row["date"].startswith(month + "-")]
        label, definition = METRICS[metric]
        return {"metric": metric, "label": label, "definition": definition, "month": month,
                "scope": projects[0]["project_name"] if project_id is not None else "全部工程项目",
                "rows": rows, "total_minor": sum(row["amount_minor"] for row in rows)}
