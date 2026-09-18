"""Deterministic, read-only operating queries for the AI assistant.

The model may explain these results, but it must never calculate the underlying
financial facts.  Every matched result therefore contains a ready-to-display
answer and one or more traceable sources.
"""

from datetime import date

from services import (
    business_knowledge_service,
    collection_service,
    cost_service,
    data_governance_service,
    finance_service,
    funds_service,
    master_data_service,
    operations_service,
    procurement_service,
    project_profit_service,
    project_service,
)


def _money(value):
    return f"¥{int(value or 0) / 100:,.2f}"


def _percent(value):
    return "--" if value is None else f"{float(value):.1f}%"


def _normalized(value):
    return business_knowledge_service.normalize_text(value)


def _contains_any(text, words):
    return any(word in text for word in words)


def _detail_columns(*columns):
    return [
        {"key": key, "label": label, "kind": kind}
        for key, label, kind in columns
    ]


PROJECT_COLUMNS = _detail_columns(
    ("project_name", "项目", "text"),
    ("settlement_minor", "确认收入", "money"),
    ("invoice_minor", "已开票", "money"),
    ("receipt_minor", "已回款", "money"),
    ("receivable_minor", "未回款", "money"),
    ("cash_balance_minor", "回款减已付采购（非余额）", "money"),
)


def _source(module, page_key, label, scope_label, details, *, kpis=None, columns=None):
    return {
        "module": module,
        "page_key": page_key,
        "view_type": "operating",
        "label": label,
        "scope_label": scope_label,
        "record_count": len(details or []),
        "kpis": list(kpis or []),
        "columns": list(columns or []),
        "details": list(details or []),
    }


def _project_scope(question, project_id=None, conversation_context=None):
    projects = project_service.list_projects(active_only=False)
    mentioned = business_knowledge_service._mentioned_projects(question, projects)
    if not mentioned:
        residual = _normalized(question)
        for word in (
            "项目", "目前", "确认", "收入", "开票", "发票", "回款", "收款",
            "毛利", "利润", "经营", "现金", "余额", "总成本", "成本", "多少",
            "哪些", "数据", "缺口", "为负", "最低", "最高", "还有", "今年",
            "本月", "主要", "花在", "哪里", "情况", "怎么样", "了", "的",
        ):
            residual = residual.replace(_normalized(word), "")
        if len(residual) >= 2:
            mentioned = [
                row for row in projects
                if residual in _normalized(row.get("name"))
            ]
    if len(mentioned) > 1:
        return None, mentioned
    if mentioned:
        return mentioned[0], []
    inherited = project_id or (conversation_context or {}).get("project_id")
    if inherited:
        resolved = next(
            (row for row in projects if int(row["id"]) == int(inherited)), None
        )
        return resolved, []
    return None, []


def _customer_scope(question, year):
    customers = master_data_service.list_customers(year=year)
    normalized_question = _normalized(question)
    matches = []
    for customer in customers:
        names = {
            _normalized(customer.get("name")),
            _normalized(customer.get("legal_name")),
            _normalized(customer.get("short_name")),
        }
        names.discard("")
        matched_length = max(
            (len(name) for name in names if name in normalized_question),
            default=0,
        )
        if matched_length:
            matches.append((matched_length, customer))
    if not matches:
        return None, customers
    longest = max(length for length, _row in matches)
    best = [row for length, row in matches if length == longest]
    return (best[0], customers) if len(best) == 1 else (None, customers)


def _context_updates(project=None, modules=None, year=None):
    updates = {"data_modules": list(modules or [])}
    if project:
        updates["project_id"] = int(project["id"])
    if year:
        today = date.today()
        updates["time"] = {
            "code": "current_year" if year == today.year else "previous_year",
            "label": f"{year}年",
            "start_date": f"{year}-01-01",
            "end_date": f"{year}-12-31",
        }
    return updates


def _matched(intent, answer, sources, *, project=None, modules=None, year=None):
    return {
        "status": "matched",
        "intent": intent,
        "answer_style": "direct_fact",
        "answer": answer,
        "sources": sources,
        "context_updates": _context_updates(project, modules, year),
    }


def _project_finance(project):
    dashboard = finance_service.get_finance_dashboard(project["id"])
    row = (dashboard.get("projects") or [{}])[0]
    invoices = finance_service.list_invoices(project_id=project["id"])
    receipts = finance_service.list_receipts(project_id=project["id"])
    answer = (
        f"“{project['name']}”目前确认收入 {_money(row.get('settlement_minor'))}，"
        f"已开票 {_money(row.get('invoice_minor'))}，已回款 {_money(row.get('receipt_minor'))}，"
        f"尚未回款 {_money(row.get('receivable_minor'))}。"
    )
    if row.get("invoice_policy") == "not_required":
        answer += "该项目为无需开票业务，开票进度不参与判断。"
    else:
        answer += (
            f"尚未开票 {_money(row.get('uninvoiced_minor'))}，"
            f"开票进度 {_percent(row.get('invoice_rate_percent'))}，"
            f"回款进度 {_percent(row.get('receipt_rate_percent'))}。"
        )
    details = [dict(row)]
    source = _source(
        "开票与回款",
        "finance",
        "查看项目开票与回款",
        project["name"],
        details,
        kpis=[
            ("确认收入", _money(row.get("settlement_minor"))),
            ("已开票", _money(row.get("invoice_minor"))),
            ("已回款", _money(row.get("receipt_minor"))),
            ("未回款", _money(row.get("receivable_minor"))),
        ],
        columns=_detail_columns(
            ("project_name", "项目", "text"),
            ("settlement_minor", "确认收入", "money"),
            ("invoice_minor", "已开票", "money"),
            ("receipt_minor", "已回款", "money"),
            ("receivable_minor", "未回款", "money"),
            ("uninvoiced_minor", "未开票", "money"),
        ),
    )
    source["invoice_count"] = len(invoices)
    source["receipt_count"] = len(receipts)
    return _matched(
        "project_finance",
        answer,
        [source],
        project=project,
        modules=["开票与回款"],
    )


def _project_profit(project):
    summary = project_profit_service.get_project_summary(project["id"])
    answer = (
        f"“{project['name']}”已确认收入 {_money(summary['settlement_minor'])}，"
        f"总成本 {_money(summary['total_cost_minor'])}，毛利 {_money(summary['gross_profit_minor'])}，"
        f"毛利率 {_percent(summary['gross_margin_percent'])}。"
        f"已回款 {_money(summary['receipt_minor'])}，回款减已付采购标记金额 "
        f"{_money(summary['cash_balance_minor'])}。此项未扣工资和费用，也不含期初，并非账户余额；真实资金见资金管理。"
    )
    detail = {
        "project_name": project["name"],
        **{key: summary.get(key) for key in (
            "settlement_minor", "invoice_minor", "receipt_minor",
            "receivable_minor", "cash_balance_minor",
        )},
        "total_cost_minor": summary["total_cost_minor"],
        "gross_profit_minor": summary["gross_profit_minor"],
        "gross_margin_percent": summary["gross_margin_percent"],
        "purchase_minor": summary["purchase_cost_minor"],
        "labor_minor": summary["labor_cost_minor"],
        "other_cost_minor": summary["other_cost_minor"],
    }
    columns = _detail_columns(
        ("project_name", "项目", "text"),
        ("settlement_minor", "确认收入", "money"),
        ("total_cost_minor", "总成本", "money"),
        ("gross_profit_minor", "毛利", "money"),
        ("gross_margin_percent", "毛利率", "percent"),
        ("cash_balance_minor", "回款减已付采购（非余额）", "money"),
    )
    source = _source(
        "项目经营核算",
        "profit",
        "查看项目经营核算",
        project["name"],
        [detail],
        kpis=[
            ("确认收入", _money(summary["settlement_minor"])),
            ("总成本", _money(summary["total_cost_minor"])),
            ("毛利", _money(summary["gross_profit_minor"])),
            ("回款减已付采购（非余额）", _money(summary["cash_balance_minor"])),
        ],
        columns=columns,
    )
    return _matched(
        "project_profit",
        answer,
        [source],
        project=project,
        modules=["项目经营核算"],
    )


def _invoice_balance(project=None):
    invoices = finance_service.list_invoices(
        project_id=project["id"] if project else None
    )
    open_invoices = [row for row in invoices if int(row.get("unreceived_minor") or 0) > 0]
    total = sum(int(row["unreceived_minor"]) for row in open_invoices)
    scope = project["name"] if project else "全公司"
    if open_invoices:
        answer = f"{scope}共有 {len(open_invoices)} 张发票尚未结清，未回款余额合计 {_money(total)}。"
        top = sorted(open_invoices, key=lambda row: (-row["unreceived_minor"], row["invoice_date"]))[:3]
        answer += "余额较大的包括：" + "、".join(
            f"{row['project_name']} {row['invoice_no']} {_money(row['unreceived_minor'])}"
            for row in top
        ) + "。"
    else:
        answer = f"{scope}当前没有尚未结清的有效发票，发票余额为 ¥0.00。"
    details = [
        {
            "invoice_date": row.get("invoice_date"),
            "invoice_no": row.get("invoice_no"),
            "project_name": row.get("project_name"),
            "amount_minor": row.get("amount_minor"),
            "received_minor": row.get("received_minor"),
            "unreceived_minor": row.get("unreceived_minor"),
            "collection_status": row.get("collection_status"),
        }
        for row in open_invoices
    ]
    source = _source(
        "发票余额",
        "finance",
        f"查看 {len(details)} 张未结清发票",
        scope,
        details,
        kpis=[
            ("未结清发票", f"{len(details)} 张"),
            ("发票总额", _money(sum(row["amount_minor"] for row in details))),
            ("已抵回款", _money(sum(row["received_minor"] for row in details))),
            ("剩余余额", _money(total)),
        ],
        columns=_detail_columns(
            ("invoice_date", "开票日期", "text"),
            ("invoice_no", "发票号码", "text"),
            ("project_name", "项目", "text"),
            ("amount_minor", "发票金额", "money"),
            ("received_minor", "已抵回款", "money"),
            ("unreceived_minor", "剩余余额", "money"),
            ("collection_status", "状态", "text"),
        ),
    )
    return _matched(
        "invoice_balance", answer, [source], project=project,
        modules=["发票余额"],
    )


def _receipt_matching(project=None):
    receipts = finance_service.list_receipts(
        project_id=project["id"] if project else None
    )
    pending = [row for row in receipts if int(row.get("invoice_unmatched_minor") or 0) > 0]
    total = sum(int(row["invoice_unmatched_minor"]) for row in pending)
    scope = project["name"] if project else "全公司"
    answer = (
        f"{scope}共有 {len(pending)} 笔回款还在等待现有或后续发票抵扣，"
        f"待匹配金额合计 {_money(total)}。"
        if pending else
        f"{scope}当前没有等待发票抵扣的回款，待匹配金额为 ¥0.00。"
    )
    details = [
        {
            "receipt_date": row.get("receipt_date"),
            "receipt_no": row.get("receipt_no"),
            "project_name": row.get("project_name"),
            "amount_minor": row.get("amount_minor"),
            "invoice_matched_minor": row.get("invoice_matched_minor"),
            "invoice_unmatched_minor": row.get("invoice_unmatched_minor"),
            "invoice_no": row.get("invoice_no"),
        }
        for row in pending
    ]
    source = _source(
        "回款核销",
        "finance",
        f"查看 {len(details)} 笔待匹配回款",
        scope,
        details,
        kpis=[
            ("待匹配回款", f"{len(details)} 笔"),
            ("回款金额", _money(sum(row["amount_minor"] for row in details))),
            ("已抵发票", _money(sum(row["invoice_matched_minor"] for row in details))),
            ("待抵发票", _money(total)),
        ],
        columns=_detail_columns(
            ("receipt_date", "回款日期", "text"),
            ("receipt_no", "回款单号", "text"),
            ("project_name", "项目", "text"),
            ("amount_minor", "回款金额", "money"),
            ("invoice_matched_minor", "已抵发票", "money"),
            ("invoice_unmatched_minor", "待抵发票", "money"),
            ("invoice_no", "已匹配发票", "text"),
        ),
    )
    return _matched(
        "receipt_matching", answer, [source], project=project,
        modules=["回款核销"],
    )


def _customer_business(question, year):
    customer, customers = _customer_scope(question, year)
    if customer:
        detail = master_data_service.get_customer_business_detail(customer["id"], year=year)
        summary = detail["summary"]
        answer = (
            f"{year}年“{customer['name']}”已确认业务 {_money(summary['yearly_business_minor'])}，"
            f"本年回款 {_money(summary['yearly_receipt_minor'])}；"
            f"按全部历史确认收入与回款计算，当前未回款 {_money(summary['current_receivable_minor'])}。"
        )
        details = detail["projects"]
        scope = customer["name"]
    else:
        summary = master_data_service.summarize_customer_business(customers)
        asks_debt = _contains_any(question, ("欠款", "未回款", "应收"))
        rows = summary["receivable_rows"] if asks_debt else summary["income_rows"]
        metric = "未回款" if asks_debt else f"{year}年业务"
        if rows:
            leader = rows[0]
            answer = (
                f"{metric}最多的客户是“{leader['label']}”，金额 {_money(leader['amount_minor'])}，"
                f"占有金额客户合计的 {leader['share_percent']:.1f}%。"
            )
        else:
            answer = f"当前客户档案中没有可用于计算{metric}排名的数据。"
        details = [
            {
                "name": row["label"],
                (
                    "current_receivable_minor"
                    if asks_debt else "yearly_business_minor"
                ): row["amount_minor"],
                "share_percent": row["share_percent"],
            }
            for row in rows
        ]
        scope = f"{year}年客户排名"
    columns = _detail_columns(
        ("name", "客户", "text"),
        ("project_name", "项目", "text"),
        ("yearly_business_minor", "本年业务", "money"),
        ("yearly_receipt_minor", "本年回款", "money"),
        ("current_receivable_minor", "当前未回款", "money"),
        ("share_percent", "占比", "percent"),
    )
    source = _source(
        "客户经营档案",
        "customer",
        "查看客户业务与未回款",
        scope,
        details,
        kpis=[
            (
                f"{year}年业务",
                _money(
                    summary.get("yearly_business_minor")
                    if customer else summary.get("total_income_minor")
                ),
            ),
            (
                "当前未回款",
                _money(
                    summary.get("current_receivable_minor")
                    if customer else summary.get("total_receivable_minor")
                ),
            ),
            ("相关记录", f"{len(details)} 条"),
        ],
        columns=columns,
    )
    return _matched(
        "customer_business", answer, [source], modules=["客户经营档案"], year=year
    )


def _cost_breakdown(project=None):
    dashboard = cost_service.get_cost_dashboard(
        project_id=project["id"] if project else None
    )
    summary = dashboard["summary"]
    categories = sorted(dashboard["by_category"], key=lambda item: item[1], reverse=True)
    scope = project["name"] if project else "全公司"
    answer = (
        f"{dashboard['month']}，{scope}总成本 {_money(summary['total_minor'])}。"
        f"其中采购 {_money(summary['purchase_minor'])}、人工 {_money(summary['labor_minor'])}、"
        f"其他成本 {_money(summary['manual_minor'])}。"
    )
    if categories:
        answer += "主要分类是：" + "、".join(
            f"{label} {_money(amount)}" for label, amount in categories[:4]
        ) + "。"
    if summary.get("unassigned_minor"):
        answer += f"另有待归集成本 {_money(summary['unassigned_minor'])}。"
    details = [
        {"category": label, "amount_minor": amount}
        for label, amount in categories
    ]
    source = _source(
        "成本看板", "cost", "查看成本分类明细", f"{scope} · {dashboard['month']}",
        details,
        kpis=[
            ("总成本", _money(summary["total_minor"])),
            ("采购", _money(summary["purchase_minor"])),
            ("人工", _money(summary["labor_minor"])),
            ("其他", _money(summary["manual_minor"])),
        ],
        columns=_detail_columns(
            ("category", "成本分类", "text"),
            ("amount_minor", "金额", "money"),
        ),
    )
    return _matched(
        "cost_breakdown", answer, [source], project=project, modules=["成本看板"]
    )


def _business_risks(question):
    overview = operations_service.get_executive_overview()
    projects = overview["projects"]
    if _contains_any(question, ("现金余额为负", "负现金", "现金风险")):
        rows = [row for row in projects if row["cash_balance_minor"] < 0]
        rows.sort(key=lambda row: row["cash_balance_minor"])
        label, intent, page_key = "回款少于已付采购标记金额（非账户现金风险）", "cash_risk", "profit"
        amount_key = "cash_balance_minor"
    elif _contains_any(question, ("负毛利", "毛利最低", "利润最低", "亏损")):
        rows = [row for row in projects if row["gross_profit_minor"] < 0]
        rows.sort(key=lambda row: row["gross_profit_minor"])
        label, intent, page_key = "毛利为负", "profit_risk", "profit"
        amount_key = "gross_profit_minor"
    elif _contains_any(question, ("欠款最多", "应收最多", "未回款最多")):
        rows = [row for row in projects if row["receivable_minor"] > 0]
        rows.sort(key=lambda row: row["receivable_minor"], reverse=True)
        label, intent, page_key = "未回款", "receivable_risk", "finance"
        amount_key = "receivable_minor"
    else:
        rows = [row for row in projects if row.get("gaps")]
        rows.sort(key=lambda row: (-len(row["gaps"]), row["project_name"]))
        label, intent, page_key = "存在数据缺口", "business_gaps", "governance"
        amount_key = None
    if rows:
        answer = f"当前共有 {len(rows)} 个项目{label}。"
        answer += "优先关注：" + "、".join(
            (
                f"{row['project_name']} {_money(row[amount_key])}"
                if amount_key else
                f"{row['project_name']}（{'、'.join(row['gaps'])}）"
            )
            for row in rows[:5]
        ) + "。"
    else:
        answer = f"当前没有项目{label}。"
    risk_columns = (
        _detail_columns(
            ("project_name", "项目", "text"),
            ("status", "项目状态", "text"),
            ("gap_text", "数据缺口", "text"),
        )
        if intent == "business_gaps" else PROJECT_COLUMNS
    )
    source = _source(
        "项目经营总览", page_key, f"查看 {len(rows)} 个相关项目", label,
        rows,
        kpis=[("相关项目", f"{len(rows)} 个")],
        columns=risk_columns,
    )
    return _matched(intent, answer, [source], modules=["项目经营总览"])


def _collection_actions(question):
    rows = collection_service.list_project_cases()
    actionable = [row for row in rows if row["needs_action_today"]]
    asks_promised_overdue = "承诺" in question and _contains_any(
        question, ("逾期", "没回", "未回")
    )
    if asks_promised_overdue:
        selected = [
            row for row in rows if row["attention_status"] == "承诺逾期"
        ]
        scope_label = "客户承诺已逾期"
    elif actionable:
        selected = actionable
        scope_label = "今天到期或已经逾期"
    else:
        selected = rows[:5]
        scope_label = "尚未安排今日跟进，按应收金额建议优先"

    if not rows:
        answer = "当前没有待回款项目，不需要安排催款。"
    elif asks_promised_overdue and not selected:
        answer = "当前没有客户承诺付款日已经逾期的项目。"
    elif selected and (asks_promised_overdue or actionable):
        total = sum(int(row["receivable_minor"]) for row in selected)
        answer = (
            f"今天有 {len(selected)} 个项目需要催款或复查，"
            f"涉及未回款 {_money(total)}。优先处理："
            + "、".join(
                f"{row['project_name']}（{row['attention_status']}，"
                f"{_money(row['receivable_minor'])}）"
                for row in selected[:5]
            )
            + "。"
        )
    else:
        answer = (
            "当前还没有设置今天到期的催款计划。建议先按应收金额安排："
            + "、".join(
                f"{row['project_name']} {_money(row['receivable_minor'])}"
                for row in selected
            )
            + "。请在“开票与回款—回款跟进”中补充责任人、承诺日和下次跟进日。"
        )

    source = _source(
        "回款作战台",
        "finance",
        f"查看 {len(selected)} 个催款项目",
        scope_label,
        selected,
        kpis=[
            ("待回款项目", f"{len(rows)} 个"),
            ("今日需处理", f"{len(actionable)} 个"),
            (
                "今日涉及金额",
                _money(sum(
                    int(row["receivable_minor"]) for row in actionable
                )),
            ),
        ],
        columns=_detail_columns(
            ("customer_name", "客户", "text"),
            ("project_name", "项目", "text"),
            ("receivable_minor", "未回款", "money"),
            ("aging_bucket", "账龄", "text"),
            ("promised_date", "承诺付款日", "date"),
            ("next_followup_date", "下次跟进", "date"),
            ("owner_name", "责任人", "text"),
            ("attention_status", "当前状态", "text"),
            ("next_action", "下一步动作", "text"),
        ),
    )
    return _matched(
        "collection_actions",
        answer,
        [source],
        modules=["回款作战台"],
    )


def _business_action_plan():
    collection_rows = collection_service.get_actionable_cases()
    fulfillment_rows = data_governance_service.list_fulfillment_gaps()
    if collection_rows or fulfillment_rows:
        parts = []
        if collection_rows:
            parts.append(f"催款或复查 {len(collection_rows)} 项")
        if fulfillment_rows:
            parts.append(f"履约与资料待办 {len(fulfillment_rows)} 项")
        answer = "今天建议优先处理：" + "，".join(parts) + "。"
        priorities = []
        priorities.extend(
            f"催款：{row['project_name']}（{row['attention_status']}）"
            for row in collection_rows[:3]
        )
        priorities.extend(
            f"{row['issue_type']}：{row['project_name'] or row['subject']}"
            for row in fulfillment_rows[: max(0, 5 - len(priorities))]
        )
        if priorities:
            answer += "优先顺序：" + "；".join(priorities) + "。"
    else:
        answer = "当前没有到期催款或履约资料待办。"

    sources = []
    if collection_rows:
        sources.append(
            _source(
                "回款作战台", "finance", "查看今日催款", "今天需处理",
                collection_rows,
                kpis=[("今日催款", f"{len(collection_rows)} 项")],
                columns=_detail_columns(
                    ("project_name", "项目", "text"),
                    ("receivable_minor", "未回款", "money"),
                    ("attention_status", "状态", "text"),
                    ("next_action", "下一步动作", "text"),
                ),
            )
        )
    if fulfillment_rows:
        sources.append(
            _source(
                "数据治理中心", "governance", "查看履约与资料待办",
                "当前未办结", fulfillment_rows,
                kpis=[("待办", f"{len(fulfillment_rows)} 项")],
                columns=_detail_columns(
                    ("issue_type", "问题", "text"),
                    ("project_name", "项目", "text"),
                    ("subject", "事项", "text"),
                    ("action", "建议动作", "text"),
                ),
            )
        )
    return _matched(
        "business_action_plan",
        answer,
        sources,
        modules=["回款作战台", "数据治理中心"],
    )


def _purchase_year_comparison():
    rows = procurement_service.list_purchase_orders()
    current_year = date.today().year
    totals = {current_year: 0, current_year - 1: 0}
    orders = {current_year: set(), current_year - 1: set()}
    freight_seen = set()
    for row in rows:
        try:
            year = int(str(row.get("purchase_date") or "")[:4])
        except ValueError:
            continue
        if year not in totals:
            continue
        order_id = row.get("id") or row.get("order_no")
        totals[year] += int(row.get("line_amount_cents") or 0)
        orders[year].add(order_id)
        if order_id not in freight_seen:
            totals[year] += int(row.get("freight_amount_cents") or 0)
            freight_seen.add(order_id)
    change = totals[current_year] - totals[current_year - 1]
    if totals[current_year - 1]:
        rate = change / totals[current_year - 1] * 100
        trend = "增加" if change >= 0 else "减少"
        answer = (
            f"{current_year}年采购 {_money(totals[current_year])}，"
            f"{current_year - 1}年采购 {_money(totals[current_year - 1])}，"
            f"同比{trend} {_money(abs(change))}（{abs(rate):.1f}%）。"
        )
    else:
        answer = (
            f"{current_year}年采购 {_money(totals[current_year])}；"
            f"{current_year - 1}年没有有效采购基数，不能计算同比百分比。"
        )
    details = [
        {"year": year, "amount_minor": totals[year], "order_count": len(orders[year])}
        for year in (current_year - 1, current_year)
    ]
    source = _source(
        "采购台账", "purchase", "查看年度采购对比", "全公司",
        details,
        kpis=[
            (f"{current_year}年", _money(totals[current_year])),
            (f"{current_year - 1}年", _money(totals[current_year - 1])),
            ("增减额", _money(change)),
        ],
        columns=_detail_columns(
            ("year", "年度", "text"),
            ("order_count", "采购单数", "integer"),
            ("amount_minor", "采购总额", "money"),
        ),
    )
    return _matched("purchase_year_comparison", answer, [source], modules=["采购台账"])


def _funds_overview():
    overview = funds_service.get_overview()
    if not overview['accounts']:
        answer = '资金账尚未启用，请先在资金管理中建立真实账户和期初余额。不能用回款减采购推算实际账户余额。'
    else:
        answer = (f"已登记账户账面余额 {_money(overview['balance_minor'])}；"
                  f"本月实际到账 {_money(overview['income_minor'])}，实际付出 {_money(overview['expense_minor'])}。"
                  '内部转账不计收支；到账含老板投入和借款，不等于工程收入。余额取决于录入完整性，请与银行或现金盘点核对。')
    source = _source('资金管理','funds','账户资金账','全部已登记账户',overview['accounts'],
                     columns=_detail_columns(('name','账户','text'),('opening_date','启用日','text'),
                                             ('balance_minor','账面余额','money')))
    return _matched('funds_overview',answer,[source],modules=['资金管理'])


def retrieve_operating_query(question, project_id=None, conversation_context=None):
    """Return a deterministic operating answer, or ``not_applicable``."""
    text = str(question or "").strip()
    if _contains_any(text, ('账户余额', '资金余额', '银行余额', '资金管理', '现金流')):
        project, _ambiguous = _project_scope(
            text, project_id=project_id if '项目' in text else None,
            conversation_context=conversation_context if '项目' in text else None,
        )
        if project:
            result = _project_profit(project)
            result['answer'] = '账户资金属于公司层面，目前不按项目计算独立银行余额。' + result['answer']
            return result
        return _funds_overview()
    year = date.today().year - 1 if "去年" in text else date.today().year
    operating_terms = (
        "确认收入", "收入", "结算", "开票", "开了多少票", "发票",
        "回款", "回了多少", "收款", "打钱",
        "应收", "未回款", "毛利", "利润", "经营现金", "现金余额",
        "总成本", "成本多少", "本月成本", "成本主要", "成本分类",
        "花在哪里", "费用构成", "客户", "业务最多", "贡献", "欠款最多",
        "数据缺口", "负现金", "现金风险", "亏损", "同比", "相比",
        "催款", "催谁", "跟进", "待办", "行动清单", "今天做什么", "今日重点",
        "本周重点", "经营重点",
    )
    if not _contains_any(text, operating_terms):
        return {
            "status": "not_applicable",
            "intent": None,
            "answer_style": None,
            "answer": None,
            "sources": [],
            "context_updates": {},
        }
    project, ambiguous_projects = _project_scope(
        text, project_id=project_id, conversation_context=conversation_context
    )
    if ambiguous_projects:
        names = "、".join(f"“{row['name']}”" for row in ambiguous_projects)
        return _matched(
            "project_ambiguous",
            f"我找到了多个同名或相近项目：{names}。请说一下完整项目名称。",
            [],
            modules=[],
        )

    if "去年" in text and _contains_any(text, ("采购", "材料")) and _contains_any(text, ("相比", "比较", "同比", "增加", "减少")):
        return _purchase_year_comparison()
    if _contains_any(text, ("催款", "催谁", "回款跟进", "承诺逾期")):
        return _collection_actions(text)
    if _contains_any(text, ("待办", "行动清单", "今天做什么", "今日重点", "本周重点", "经营重点")):
        return _business_action_plan()
    if _contains_any(text, ("发票", "开票")) and _contains_any(text, ("余额", "未结清", "还有多少", "剩余")):
        return _invoice_balance(project)
    if _contains_any(text, ("回款", "打钱", "收款")) and _contains_any(text, ("后续开票", "等发票", "待匹配", "未匹配", "抵发票", "核销")):
        return _receipt_matching(project)
    if "客户" in text or _contains_any(text, ("客户贡献", "客户业务", "业务最多")):
        return _customer_business(text, year)
    if _contains_any(text, ("数据缺口", "负现金", "现金风险", "现金余额为负", "负毛利", "毛利最低", "利润最低", "亏损", "应收最多", "未回款最多", "欠款最多")):
        return _business_risks(text)
    if _contains_any(text, ("本月成本", "成本主要", "成本分类", "花在哪里", "费用构成")):
        return _cost_breakdown(project)
    if project and _contains_any(text, ("确认收入", "收入", "结算", "开票", "开了多少票", "回款", "回了多少", "应收", "未回款")):
        return _project_finance(project)
    if project and _contains_any(text, ("毛利", "利润", "经营现金", "现金余额", "总成本", "成本多少")):
        return _project_profit(project)
    return {
        "status": "not_applicable",
        "intent": None,
        "answer_style": None,
        "answer": None,
        "sources": [],
        "context_updates": {},
    }


def get_daily_briefing():
    """Build current, read-only reminders and suggested questions."""
    overview = operations_service.get_executive_overview()
    invoices = finance_service.list_invoices()
    receipts = finance_service.list_receipts()
    collection_rows = collection_service.list_project_cases()
    fulfillment_rows = data_governance_service.list_fulfillment_gaps()
    actionable_rows = [
        row for row in collection_rows if row["needs_action_today"]
    ]
    unplanned_rows = [
        row for row in collection_rows
        if not row["next_followup_date"] and not row["promised_date"]
    ]
    projects = overview["projects"]
    reminders = []

    def add(title, value, question, page_key, severity="info"):
        reminders.append({
            "title": title,
            "value": value,
            "question": question,
            "page_key": page_key,
            "severity": severity,
        })

    add(
        "今日催款",
        f"{len(actionable_rows)} 个到期 · {len(unplanned_rows)} 个未安排",
        "今天该催谁？",
        "finance",
        "danger" if actionable_rows else "warning" if unplanned_rows else "info",
    )
    add(
        "经营待办",
        f"{len(fulfillment_rows)} 项待处理",
        "今天做什么？",
        "governance",
        "warning" if fulfillment_rows else "info",
    )
    receivable_rows = [row for row in projects if row["receivable_minor"] > 0]
    receivable_total = sum(row["receivable_minor"] for row in receivable_rows)
    add("待回款", f"{len(receivable_rows)} 个项目 · {_money(receivable_total)}", "哪些项目未回款最多？", "finance", "warning")
    open_invoices = [row for row in invoices if row["unreceived_minor"] > 0]
    add("未结清发票", f"{len(open_invoices)} 张 · {_money(sum(row['unreceived_minor'] for row in open_invoices))}", "哪些发票还有余额？", "finance", "warning")
    unmatched = [row for row in receipts if row["invoice_unmatched_minor"] > 0]
    add("等待发票抵扣", f"{len(unmatched)} 笔 · {_money(sum(row['invoice_unmatched_minor'] for row in unmatched))}", "哪些回款还在等后续开票？", "finance")
    negative_cash = [row for row in projects if row["cash_balance_minor"] < 0]
    add("回款不足已付采购", f"{len(negative_cash)} 个", "哪些项目现金余额为负？", "profit", "danger" if negative_cash else "info")
    gaps = [row for row in projects if row.get("gaps")]
    add("数据缺口", f"{len(gaps)} 个项目", "哪些项目还有数据缺口？", "governance", "warning" if gaps else "info")
    suggestions = [{"label": "今日行动", "question": "今天做什么？"}] + [
        {"label": item["title"], "question": item["question"]}
        for item in reminders
        if item["value"] and not item["value"].startswith("0")
    ][:4]
    return {"reminders": reminders, "suggestions": suggestions}
