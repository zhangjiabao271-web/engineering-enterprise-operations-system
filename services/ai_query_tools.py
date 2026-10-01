"""Allowlisted business queries. The model supplies parameters, never SQL.

All statements run in one read-only snapshot; aggregates precede display limits.
"""

import sqlite3
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

from db import connection
from db.business_facts import PROJECT_FINANCE_SQL, COST_FACTS_SQL
from services.expense_categories import NON_COST


class QueryValidationError(ValueError):
    pass


PROJECT_JOIN = "LEFT JOIN projects p ON p.id=x.project_id"
CUSTOMER_JOIN = """LEFT JOIN contracts c ON c.id=x.contract_id
    LEFT JOIN business_partners bp ON bp.id=COALESCE(p.customer_partner_id,c.customer_partner_id)"""


def _dataset(label, page, sql, dimensions, metrics, note, dated=True):
    return dict(label=label, page=page, sql=sql, dimensions=dimensions,
                metrics=metrics, note=note, dated=dated)


DATASETS = {
    "labor": _dataset("人工工天", "labor", """
        SELECT x.id, x.work_date AS date, p.name AS project,
          w.name || '（编号' || w.id || '）' AS worker,
          x.construction_site AS site, x.work_days AS days,
          CASE WHEN x.is_overtime=1 THEN x.work_days ELSE 0 END AS overtime_days,
          COALESCE(x.amount_minor, CAST(ROUND(x.amount*100) AS INTEGER)) AS amount_minor
        FROM work_logs x JOIN workers w ON w.id=x.worker_id
        LEFT JOIN projects p ON p.id=x.project_id
        WHERE COALESCE(x.status,'active')='active'
    """, ["project", "worker", "site"], ["amount_minor", "days", "overtime_days"],
        "工天按记录相加，加班已包含在工天总数内；金额为应计人工成本，不是已发工资。"),
    "purchases": _dataset("采购整单", "purchase", """
        SELECT x.id, x.purchase_date AS date, x.order_no AS number,
          COALESCE(bp.legal_name,x.merchant_name_snapshot) AS supplier,
          x.payment_status AS payment_status, x.total_amount_cents AS amount_minor
        FROM purchase_orders x LEFT JOIN business_partners bp ON bp.id=x.supplier_partner_id
        WHERE x.status='active'
    """, ["supplier", "payment_status"], ["amount_minor"],
        "一张采购单只计一次，含整单运费；不是项目分摊成本。按项目采购成本请查costs。"),
    "income": _dataset("确认收入", "finance", f"""
        SELECT x.id, x.settlement_date AS date, x.settlement_no AS number,
          p.name AS project, bp.legal_name AS customer, x.amount_minor
        FROM settlements x {PROJECT_JOIN} {CUSTOMER_JOIN} WHERE x.status='active'
    """, ["project", "customer"], ["amount_minor"], "按收入确认日期统计，不用合同金额、开票或回款代替收入。"),
    "invoices": _dataset("销项发票", "finance", f"""
        SELECT x.id, x.invoice_date AS date, x.invoice_no AS number,
          p.name AS project, bp.legal_name AS customer, x.amount_minor, x.tax_amount_minor
        FROM sales_invoices x {PROJECT_JOIN} {CUSTOMER_JOIN} WHERE x.status='active'
    """, ["project", "customer"], ["amount_minor", "tax_amount_minor"],
        "金额为价税合计；未知税额保留未知，只汇总已知税额。此工具不提供发票抵扣余额。"),
    "receipts": _dataset("回款分配", "finance", f"""
        SELECT x.id, r.receipt_date AS date, r.receipt_no AS number,
          p.name AS project, bp.legal_name AS customer, x.allocated_amount_minor AS amount_minor
        FROM receipt_allocations x JOIN receipts r ON r.id=x.receipt_id
        {PROJECT_JOIN} {CUSTOMER_JOIN}
        WHERE r.status='active' AND x.status='active'
    """, ["project", "customer"], ["amount_minor"],
        "按实际回款日期与有效项目分配统计；记录数是分配条数，包含预收款，不等于确认收入。"),
    "costs": _dataset("项目成本台账", "cost", COST_FACTS_SQL,
        ["project", "category", "counterparty"], ["amount_minor"],
        "采购、人工与其他费用按项目分配归集，含待归集项；按业务日期，不代表实际付款或利润。含生活/还本等登记项，判断经营成本须明确分类。"),
    "receivables": _dataset("当前项目应收", "finance", f"""
        SELECT p.project_id AS id,p.project_name AS project,bp.legal_name AS customer,
          p.receivable_minor AS amount_minor
        FROM ({PROJECT_FINANCE_SQL}) p
        LEFT JOIN business_partners bp ON bp.id=p.customer_partner_id
    """, ["project", "customer"], ["amount_minor"],
        "当前累计应收=项目有效确认收入减已分配至收入确认的回款，逐项目不小于零；预收款不擅自抵减。不是某年度新增应收；无客户绑定的项目单列待归集。", False),
}

LABELS = {"project": "项目", "worker": "工人", "site": "工地", "supplier": "供应商",
          "customer": "客户", "category": "分类", "counterparty": "往来单位/人员",
          "payment_status": "付款状态", "date": "日期", "month": "月份", "number": "单号",
          "amount_minor": "金额", "tax_amount_minor": "已知税额", "days": "工天",
          "overtime_days": "其中加班工天", "count": "记录数", "share_percent": "占比(%)", "id": "来源编号"}

# 固定 SQL 由程序维护，模型只能选择指标与范围，不能执行自定义 SQL。
_NON_COST_SQL = ",".join("'" + value.replace("'", "''") + "'" for value in sorted(NON_COST))
DATASETS["profit"] = _dataset("已登记收支毛利分析", "profit", f"""
    SELECT 'income-'||x.id AS id,x.settlement_date AS date,p.name AS project,
      bp.legal_name AS customer,x.amount_minor AS income_minor,0 AS cost_minor,
      x.amount_minor AS gross_profit_minor,1 AS income_records,0 AS cost_records,x.project_id
    FROM settlements x {PROJECT_JOIN} {CUSTOMER_JOIN} WHERE x.status='active'
    UNION ALL
    SELECT 'cost-'||x.id,x.date,x.project,bp.legal_name,0,x.amount_minor,
      -x.amount_minor,0,1,x.project_id FROM ({COST_FACTS_SQL}) x
    LEFT JOIN projects p ON p.id=x.project_id
    LEFT JOIN business_partners bp ON bp.id=p.customer_partner_id
    WHERE COALESCE(x.category,'') NOT IN ({_NON_COST_SQL})
""", ["project", "customer"],
    ["income_minor", "cost_minor", "gross_profit_minor", "income_records", "cost_records"],
    "毛利=有效确认收入－已登记经营成本；按各自业务日期统计，期间收支差额不等于最终项目利润。"
    "已排除明确的生活、还款本金与资产购置；未核实分类仍可能影响结果。"
    "没有进项抵扣净额调整，不是税后利润；待归集记录单列，未登记成本不会自动补齐。"
    "按项目分组且指定起止日期时，同时显示每个项目的期间月均毛利。")
LABELS.update(income_minor="确认收入", cost_minor="已登记经营成本",
              gross_profit_minor="已登记毛利", income_records="收入记录数", cost_records="成本记录数",
              project_id="项目编号")


def catalog():
    return {key: {k: v for k, v in spec.items() if k not in ("sql", "page")}
            for key, spec in DATASETS.items()}


def validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) - {"queries", "calculations", "clarification", "unsupported"}:
        raise QueryValidationError("查询计划格式无效，不能包含SQL或未授权操作。")
    queries = plan.get("queries", [])
    if not isinstance(queries, list) or len(queries) > 6:
        raise QueryValidationError("每次最多组合六项查询。")
    if not queries:
        if plan.get("calculations"):
            raise QueryValidationError("没有来源查询时不能计算。")
        message = plan.get("clarification") or plan.get("unsupported")
        if not isinstance(message, str) or not message.strip() or len(message) > 500:
            raise QueryValidationError("需要明确查询条件或说明不支持的范围。")
        return plan
    if plan.get("clarification") or plan.get("unsupported"):
        raise QueryValidationError("条件未明确时不能同时执行查询。")
    for query in queries:
        allowed = {"dataset", "filters", "start_date", "end_date", "group_by", "metrics", "order_by", "descending", "limit"}
        if not isinstance(query, dict) or set(query) - allowed:
            raise QueryValidationError("查询包含未知参数。")
        dataset = query.get("dataset")
        spec = DATASETS.get(dataset) if isinstance(dataset, str) else None
        if spec is None:
            raise QueryValidationError("没有这个查账工具。")
        for key in ("group_by", "metrics"):
            values = query.get(key, [])
            if key == "group_by":
                permitted = spec["dimensions"] + (["month", "date"] if spec["dated"] else [])
            else:
                permitted = spec["metrics"] + ["count"]
            if not isinstance(values, list) or not all(isinstance(v, str) and v in permitted for v in values) or len(values) != len(set(values)):
                raise QueryValidationError(f"{spec['label']}不支持这些{key}字段。")
        if len(query.get("group_by", [])) > 2 or not query.get("metrics"):
            raise QueryValidationError("请选择指标，最多同时按两个维度分组。")
        filters = query.get("filters", {})
        if not isinstance(filters, dict) or any(k not in spec["dimensions"] for k in filters):
            raise QueryValidationError("工具不支持该筛选条件，不能忽略条件计算。")
        if any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in filters.values()):
            raise QueryValidationError("筛选条件须为不超过100字的名称。")
        if type(query.get("limit", 20)) is not int or not 1 <= query.get("limit", 20) <= 50:
            raise QueryValidationError("结果展示数量应为1至50。")
        if type(query.get("descending", True)) is not bool:
            raise QueryValidationError("排序方向无效。")
        if query.get("order_by") and query["order_by"] not in query["metrics"] + query.get("group_by", []):
            raise QueryValidationError("排序字段必须属于所选指标或分组。")
        start, end = query.get("start_date"), query.get("end_date")
        if any(value is not None and not isinstance(value, str) for value in (start, end)):
            raise QueryValidationError("日期须为YYYY-MM-DD文本或空值。")
        if not spec["dated"] and (start or end):
            raise QueryValidationError("当前应收是累计快照，不能套用年度或月份条件。")
        if spec["dated"]:
            if not end:
                raise QueryValidationError("须明确截止日期，防止混入未来记录。")
            try:
                end_day = date.fromisoformat(end)
                start_day = date.fromisoformat(start) if start else None
            except (TypeError, ValueError) as error:
                raise QueryValidationError("日期必须为YYYY-MM-DD。") from error
            if end_day.isoformat() != end or (start_day and start_day.isoformat() != start):
                raise QueryValidationError("日期必须为带连字符的YYYY-MM-DD，避免范围比较错误。")
            if end_day > date.today() or (start_day and start_day > end_day):
                raise QueryValidationError("时间范围无效，不能把未来记录算作已发生。")
    calculations = plan.get("calculations", [])
    if not isinstance(calculations, list) or len(calculations) > 12:
        raise QueryValidationError("最多十二项衍生计算。")
    for calculation in calculations:
        if not isinstance(calculation, dict) or set(calculation) != {"label", "operation", "inputs"}:
            raise QueryValidationError("计算只能指定名称、操作和查询指标引用。")
        if not isinstance(calculation["label"], str) or not 1 <= len(calculation["label"]) <= 60:
            raise QueryValidationError("计算名称无效。")
        operation = calculation["operation"]
        if operation not in ("difference", "ratio_percent", "growth_percent", "average_record", "average_month", "average_project"):
            raise QueryValidationError("不支持该计算操作。")
        inputs = calculation["inputs"]
        if not isinstance(inputs, list) or len(inputs) != (1 if operation.startswith("average_") else 2):
            raise QueryValidationError("计算引用数量无效。")
        for ref in inputs:
            if not isinstance(ref, dict) or set(ref) != {"query", "metric"}:
                raise QueryValidationError("计算必须引用查询合计，不能编造数值。")
            index = ref["query"]
            if type(index) is not int or not 0 <= index < len(queries) or ref["metric"] not in queries[index]["metrics"]:
                raise QueryValidationError("计算引用的查询指标不存在。")
        if len(inputs) == 2 and inputs[0]["metric"].endswith("_minor") != inputs[1]["metric"].endswith("_minor"):
            raise QueryValidationError("不能把金额与工天等不同单位混算。")
        if operation == "average_month" and not queries[inputs[0]["query"]].get("start_date"):
            raise QueryValidationError("月平均须指定起止日期，包含没有记录的月份。")
        if operation == "average_project" and queries[inputs[0]["query"]]["dataset"] != "profit":
            raise QueryValidationError("每项目平均须使用按项目编号去重的profit数据。")
        if operation in ("difference", "growth_percent") and inputs[0]["metric"] != inputs[1]["metric"]:
            raise QueryValidationError("差额比较须使用同一个指标，毛利请使用profit的gross_profit_minor。")
    return plan


def _calculate(calculation, queries, results):
    refs = calculation["inputs"]
    values = []
    for ref in refs:
        totals = results[ref["query"]]["totals"]
        value = totals.get(ref["metric"])
        if value is None or totals.get("unknown_" + ref["metric"]):
            return f"{calculation['label']}：无法可靠计算，来源缺失或含未知值。"
        values.append(Decimal(str(value)))
    operation = calculation["operation"]
    note = ""
    if operation == "difference":
        value = values[0] - values[1]
    else:
        if operation in ("ratio_percent", "growth_percent"):
            divisor = values[1]
        elif operation == "average_project":
            totals = results[refs[0]["query"]]["totals"]
            if totals["unassigned_records"]:
                return f"{calculation['label']}：有待归集记录，不能可靠计算每项目平均。"
            divisor = Decimal(totals["project_count"])
            note = f"（按期间有收入或成本记录的{divisor}个独立项目）"
        elif operation == "average_record":
            divisor = Decimal(results[refs[0]["query"]]["totals"]["count"])
            note = f"（按全部{divisor}条来源记录，不按展示条数）"
        else:
            query = queries[refs[0]["query"]]
            start, end = date.fromisoformat(query["start_date"]), date.fromisoformat(query["end_date"])
            divisor = Decimal((end.year-start.year)*12 + end.month-start.month + 1)
            note = f"（按范围覆盖的{divisor}个自然月，含无记录月份及未结束月份）"
        if divisor == 0:
            return f"{calculation['label']}：分母为零，无法计算。"
        if operation == "growth_percent" and divisor < 0:
            return f"{calculation['label']}：基期为负值，请比较金额差额，避免误导性增长率。"
        numerator = values[0]
        if operation == "growth_percent":
            numerator -= divisor
        value = numerator / divisor
        if operation in ("ratio_percent", "growth_percent"):
            value *= 100
    display = f"{value:.2f}%" if operation in ("ratio_percent", "growth_percent") else _format(refs[0]["metric"], value)
    evidence = "、".join(f"查询{ref['query']+1}/{LABELS.get(ref['metric'], ref['metric'])}" for ref in refs)
    return f"{calculation['label']}：{display}{note}；来源：{evidence}"


def _format(key, value):
    if value is None:
        return "未知"
    if key.endswith("_minor"):
        return f"¥{Decimal(value) / 100:,.2f}"
    return str(value)


def _columns(keys):
    # The existing table renderer turns missing money into zero. Provide formatted
    # display fields while retaining the original nullable values in the evidence.
    return [{"key": key + "_display" if key.endswith("_minor") else key,
             "label": LABELS.get(key, key), "kind": "text"} for key in keys]


def _execute(conn, query):
    spec = DATASETS[query["dataset"]]
    base = f"WITH facts AS ({spec['sql']})"
    params, conditions, resolved = [], [], {}
    for field, term in query.get("filters", {}).items():
        exact = conn.execute(f"{base} SELECT 1 FROM facts WHERE {field}=? LIMIT 1", (term,)).fetchone()
        candidates = [term] if exact else [r[0] for r in conn.execute(
            f"{base} SELECT DISTINCT {field} FROM facts WHERE instr({field},?)>0 ORDER BY {field} LIMIT 21", (term,))]
        if len(candidates) != 1:
            names = "、".join(candidates[:10])
            raise QueryValidationError(f"{LABELS[field]}“{term}”" + (f"匹配多项，请指定完整名称：{names}" if candidates else "没有匹配记录，请核对名称；不能按零处理。"))
        resolved[field] = candidates[0]
        conditions.append(f"{field}=?")
        params.append(candidates[0])
    if spec["dated"]:
        if query.get("start_date"):
            conditions.append("date>=?")
            params.append(query["start_date"])
        conditions.append("date<=?")
        params.append(query["end_date"])
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    metrics = query["metrics"]
    expressions = ["COUNT(*) AS count"] + [f"SUM({key}) AS {key}" for key in metrics if key != "count"]
    unknowns = [f"SUM(CASE WHEN {key} IS NULL THEN 1 ELSE 0 END) AS unknown_{key}" for key in metrics if key != "count"]
    if query["dataset"] == "profit":
        unknowns += ["COUNT(DISTINCT project_id) AS project_count",
                     "SUM(CASE WHEN project_id IS NULL THEN 1 ELSE 0 END) AS unassigned_records",
                     "SUM(income_records) AS income_record_count", "SUM(cost_records) AS cost_record_count"]
    total = dict(conn.execute(f"{base} SELECT {','.join(expressions + unknowns)} FROM facts{where}", params).fetchone())
    groups = query.get("group_by", [])
    rows = []
    truncated = False
    if groups:
        fields = ["substr(date,1,7) AS month" if key == "month" else key for key in groups]
        sort = query.get("order_by") or metrics[0]
        direction = "DESC" if query.get("descending", True) else "ASC"
        group_keys = ["project_id" if query["dataset"] == "profit" and key == "project" else key
                      for key in groups]
        if query["dataset"] == "profit" and "project" in groups:
            fields.append("project_id")
        rows = [dict(r) for r in conn.execute(
            f"{base} SELECT {','.join(fields + expressions + unknowns)} FROM facts{where} GROUP BY {','.join(group_keys)} "
            f"ORDER BY {sort} {direction}, {','.join(groups)} LIMIT ?", params + [query.get("limit", 20) + 1])]
        truncated = len(rows) > query.get("limit", 20)
        rows = rows[:query.get("limit", 20)]
        primary = metrics[0]
        for row in rows:
            row["share_percent"] = round(row[primary] / total[primary] * 100, 2) if row.get(primary) is not None and total.get(primary) else None
    detail_fields = ["id"] + (["date"] if spec["dated"] else []) + spec["dimensions"] + spec["metrics"]
    if query["dataset"] == "profit":
        detail_fields.insert(1, "project_id")
    if query["dataset"] in {"purchases", "income", "invoices", "receipts", "costs"}:
        detail_fields.insert(1, "number")
    details = [dict(r) for r in conn.execute(
        f"{base} SELECT {','.join(detail_fields)} FROM facts{where} ORDER BY "
        + ("date DESC," if spec["dated"] else "") + "id LIMIT 201", params)]
    details_truncated = len(details) > 200
    for row in details:
        for key in spec["metrics"]:
            if key.endswith("_minor"):
                row[key + "_display"] = _format(key, row.get(key))
    scope = "、".join(f"{LABELS[k]}={v}" for k, v in resolved.items()) or "全公司"
    period = f"{query.get('start_date') or '最早记录'}至{query.get('end_date')}" if spec["dated"] else f"当前快照（{date.today()}）"
    answer = f"{spec['label']}｜{scope}｜{period}\n"
    if not total["count"]:
        answer += "该范围没有有效记录，不能据此认定实际业务为零。"
    else:
        answer += "；".join(f"{LABELS[k]}：{_format(k, total[k])}" for k in metrics)
        for key in metrics:
            if total.get(f"unknown_{key}"):
                answer += f"；{LABELS[key]}有{total[f'unknown_{key}']}条未知，合计仅含已知值"
    for row in rows:
        name = " / ".join(str(row.get(k) or "待归集/未填") for k in groups)
        if query["dataset"] == "profit" and "project" in groups and row.get("project_id") is not None:
            name += f"（项目编号{row['project_id']}）"
        answer += "\n" + name + "：" + "；".join(f"{LABELS[k]} {_format(k,row[k])}" for k in metrics)
        if (query["dataset"] == "profit" and "project" in groups
                and query.get("start_date") and "gross_profit_minor" in metrics):
            start_day = date.fromisoformat(query["start_date"])
            end_day = date.fromisoformat(query["end_date"])
            months = (end_day.year - start_day.year) * 12 + end_day.month - start_day.month + 1
            if row["gross_profit_minor"] is not None and not row.get("unknown_gross_profit_minor"):
                monthly = Decimal(row["gross_profit_minor"]) / months
                answer += f"；月均毛利 {_format('gross_profit_minor', monthly)}（{months}个自然月）"
            else:
                answer += "；月均毛利无法可靠计算（存在未知金额）"
        if row["share_percent"] is not None:
            answer += f"（{LABELS[metrics[0]]}占比{row['share_percent']}%）"
    if truncated:
        answer += "\n分组仅展示前列，合计和占比分母包含全部匹配记录。"
    answer += "\n口径：" + spec["note"]
    if query["dataset"] == "profit":
        if not total.get("income_record_count") or not total.get("cost_record_count"):
            answer += "\n注意：该范围缺少收入或成本记录；数字仅为已登记差额，不能认定收入成本已经完整。"
        if total.get("unassigned_records"):
            answer += f"\n注意：包含{total['unassigned_records']}条待归集记录，不能分摊到具体项目。"
    source = {"module": spec["label"], "page_key": spec["page"], "view_type": "operating",
              "label": f"{spec['label']}原始明细", "scope_label": f"{scope} · {period}" + (" · 明细仅展示前200条，汇总未截断" if details_truncated else ""),
              "record_count": total["count"], "details": details[:200], "columns": _columns(detail_fields),
              "kpis": [(LABELS[k], _format(k, total[k])) for k in metrics]}
    return {"answer": answer, "source": source, "totals": total, "groups": rows,
            "resolved_filters": resolved, "groups_truncated": truncated}


def execute_plan(plan, *, db_path=None, cancel_event=None):
    validate_plan(plan)
    if not plan.get("queries"):
        return {"answer": plan.get("clarification") or plan["unsupported"], "sources": [], "results": []}
    path = Path(db_path or connection.DB_PATH).resolve()
    if cancel_event is not None and cancel_event.is_set():
        raise QueryValidationError("查询已取消，未执行统计。")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
    conn.row_factory = sqlite3.Row
    deadline = time.monotonic() + 8
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline or bool(cancel_event and cancel_event.is_set())), 1000)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        results = [_execute(conn, query) for query in plan["queries"]]
    finally:
        conn.close()
    calculations = [_calculate(item, plan["queries"], results) for item in plan.get("calculations", [])]
    return {"answer": "\n\n".join(calculations + [r["answer"] for r in results]),
            "sources": [r["source"] for r in results], "results": results}
