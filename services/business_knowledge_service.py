"""只读业务知识检索：把自然说法映射到可追溯的本地经营事实。"""

import re
import unicodedata
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from datetime import date, timedelta

from services import labor_service, procurement_service, project_service


_QUESTION_NOISE = (
    "请帮我分析一下",
    "请分析一下",
    "帮我分析一下",
    "请帮我分析",
    "请分析",
    "帮我分析",
    "分析一下",
    "采购情况",
    "购买情况",
    "和风险",
    "到目前为止",
    "截止到现在",
    "截止目前",
    "这个月",
    "上个月",
    "今年",
    "本年",
    "去年",
    "本月",
    "当月",
    "分别是多少",
    "一共是多少",
    "总共是多少",
    "有多少了",
    "买了多少",
    "采购了多少",
    "购买了多少",
    "目前",
    "现在",
    "已经",
    "累计",
    "一共",
    "总共",
    "采购",
    "购买",
    "买入",
    "买了",
    "买",
    "数量",
    "用量",
    "金额",
    "多少钱",
    "多少",
    "情况",
    "查询",
    "查一下",
    "帮我查",
    "分析",
    "风险",
    "供应商",
    "这家",
    "那里",
    "那边",
    "目前有",
    "有了",
    "有",
    "的",
    "吗",
    "呢",
    "了",
)

_PURCHASE_INTENT_WORDS = (
    "采购",
    "购买",
    "买",
    "进货",
    "材料",
    "供应商",
    "单价",
    "数量",
    "用量",
    "多少钱",
)

_DIRECT_FACT_WORDS = (
    "多少",
    "数量",
    "用量",
    "金额",
    "多少钱",
    "单价",
    "哪些",
    "什么",
    "合计",
    "总额",
)

_PROCUREMENT_AGGREGATE_WORDS = (
    "花了多少钱",
    "花多少钱",
    "花费多少",
    "采购总额",
    "采购金额",
    "采购成本",
    "材料成本",
    "材料总额",
    "材料金额",
    "买了多少材料",
    "买了多少东西",
    "一共花",
    "总共花",
    "合计",
    "总额",
    "多少钱",
)

_PROCUREMENT_AGGREGATE_NOISE = (
    "全公司",
    "整个公司",
    "公司一共",
    "公司总共",
    "公司",
    "所有材料",
    "全部材料",
    "材料",
    "物料",
    "所有东西",
    "全部东西",
    "东西",
    "采购成本",
    "材料成本",
    "采购费用",
    "材料费用",
    "货款",
    "花费",
    "花了",
    "花",
    "费用",
    "钱",
)

_SUPPLIER_QUERY_NOISE = _QUESTION_NOISE + (
    "供应商那里",
    "供应商那边",
    "供应商",
    "这家店",
    "这家公司",
    "这家",
    "那里",
    "那边",
    "所有的东西",
    "所有东西",
    "全部东西",
    "多少东西",
    "东西",
    "所有的材料",
    "所有材料",
    "全部材料",
    "多少材料",
    "材料",
    "总和价格",
    "总价格",
    "合计价格",
    "总额",
    "合计",
    "我在",
    "我从",
    "我跟",
    "我向",
    "在",
    "从",
    "跟",
    "向",
    "我",
)

_LABOR_COST_WORDS = (
    "人工成本",
    "人工费用",
    "人工费",
    "人工工资",
    "工资成本",
    "工资费用",
    "工钱",
)

_PROJECT_ALIASES = {
    "澄湖": "澄湖药业",
    "屹峰": "屹峰药业",
    "朗润": "朗润药业",
}


def normalize_text(value):
    """Normalize human-entered text while preserving Chinese and alphanumerics."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", text)


def _is_subsequence(needle, haystack):
    iterator = iter(haystack)
    return all(character in iterator for character in needle)


def _bigrams(value):
    if len(value) < 2:
        return {value} if value else set()
    return {value[index : index + 2] for index in range(len(value) - 1)}


def _similarity(query, candidate):
    query = normalize_text(query)
    candidate = normalize_text(candidate)
    if not query or not candidate:
        return 0.0
    if query == candidate:
        return 1.0
    if query in candidate:
        return max(0.9, 0.98 - (len(candidate) - len(query)) * 0.012)
    if candidate in query:
        return max(0.88, 0.96 - (len(query) - len(candidate)) * 0.012)
    if len(query) >= 2 and _is_subsequence(query, candidate):
        return 0.82 + 0.12 * len(query) / len(candidate)

    query_chars = set(query)
    candidate_chars = set(candidate)
    char_union = query_chars | candidate_chars
    char_score = len(query_chars & candidate_chars) / len(char_union)
    query_bigrams = _bigrams(query)
    candidate_bigrams = _bigrams(candidate)
    bigram_union = query_bigrams | candidate_bigrams
    bigram_score = (
        len(query_bigrams & candidate_bigrams) / len(bigram_union)
        if bigram_union
        else 0.0
    )
    sequence_score = SequenceMatcher(None, query, candidate).ratio()
    return 0.55 * sequence_score + 0.3 * char_score + 0.15 * bigram_score


def _material_similarity(query, candidate):
    """Material-aware similarity with a small, auditable set of oral suffix rules."""
    query = normalize_text(query)
    candidate = normalize_text(candidate)
    score = _similarity(query, candidate)
    # “板” is often said as a category word but omitted in a ledger snapshot,
    # e.g. “岩棉板” -> “岩棉瓦楞”. Keep a penalty so exact names still win.
    if len(query) >= 3 and query.endswith("板"):
        score = max(score, _similarity(query[:-1], candidate) - 0.07)
    return max(0.0, min(score, 1.0))


def _supplier_mention_score(question, candidate):
    """Score an explicit supplier mention and return the text that matched."""
    question = normalize_text(question)
    candidate = normalize_text(candidate)
    if not question or len(candidate) < 2:
        return 0.0, ""
    if candidate in question:
        return 1.0, candidate
    max_length = min(len(candidate), len(question))
    for length in range(max_length, 1, -1):
        matches = []
        for start in range(len(candidate) - length + 1):
            segment = candidate[start : start + length]
            if segment in question:
                score = 0.72 + 0.23 * length / len(candidate)
                if start == 0:
                    score += 0.1
                matches.append((min(score, 0.99), segment, start))
        if matches:
            score, segment, _start = max(matches)
            return score, segment
    return 0.0, ""


def _supplier_residual(question, matched_text):
    residual = normalize_text(question)
    if matched_text:
        residual = residual.replace(normalize_text(matched_text), "", 1)
    for phrase in sorted(set(_SUPPLIER_QUERY_NOISE), key=len, reverse=True):
        residual = residual.replace(normalize_text(phrase), "")
    return residual


def _remove_known_scope_names(query, rows):
    """Remove a mentioned project/supplier so it does not dilute material matching."""
    result = query
    known_names = {
        normalize_text(row.get(field))
        for row in rows
        for field in ("project_name", "supplier_name", "merchant_name_snapshot")
        if row.get(field)
    }
    for name in sorted(known_names, key=len, reverse=True):
        if name and name in result:
            result = result.replace(name, "")
            continue
        # Users often omit legal suffixes such as “药业” or “有限公司”.
        prefix_length = 0
        for index, character in enumerate(name):
            if index < len(result) and result[index] == character:
                prefix_length += 1
            else:
                break
        if prefix_length >= 2 and len(name) >= 4:
            result = result[prefix_length:]
    return result


def _material_query(question, rows):
    query = normalize_text(question)
    query = _remove_known_scope_names(query, rows)
    for phrase in sorted(_QUESTION_NOISE, key=len, reverse=True):
        query = query.replace(normalize_text(phrase), "")
    return query


def _is_procurement_aggregate_question(question, material_query):
    """Separate a total-spend question from a named-material lookup."""
    normalized = normalize_text(question)
    has_purchase_intent = any(
        normalize_text(word) in normalized for word in _PURCHASE_INTENT_WORDS
    )
    has_aggregate_intent = any(
        normalize_text(word) in normalized
        for word in _PROCUREMENT_AGGREGATE_WORDS
    )
    residual = normalize_text(material_query)
    for phrase in sorted(_PROCUREMENT_AGGREGATE_NOISE, key=len, reverse=True):
        residual = residual.replace(normalize_text(phrase), "")
    return has_purchase_intent and has_aggregate_intent and len(residual) < 2


def _decimal(value):
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def _display_decimal(value):
    value = Decimal(value)
    if value == value.to_integral():
        return str(int(value))
    return format(value.normalize(), "f")


def _confidence(score):
    if score >= 0.995:
        return "exact"
    if score >= 0.84:
        return "high"
    return "medium"


def _candidate_summary(name, score, rows):
    quantities = defaultdict(Decimal)
    project_quantities = defaultdict(Decimal)
    amount_cents = 0
    freight_cents = 0
    freight_orders = set()
    details = []
    for row in rows:
        unit = str(row.get("unit_snapshot") or "").strip() or "未注明单位"
        quantity = _decimal(row.get("quantity"))
        project = row.get("project_name") or "未归集项目"
        quantities[unit] += quantity
        project_quantities[(project, unit)] += quantity
        amount_cents += int(row.get("line_amount_cents") or 0)
        order_key = row.get("id")
        if order_key is None or order_key not in freight_orders:
            freight_cents += int(row.get("freight_amount_cents") or 0)
            if order_key is not None:
                freight_orders.add(order_key)
        details.append(
            {
                "purchase_date": row.get("purchase_date"),
                "project": project,
                "supplier": row.get("supplier_name")
                or row.get("merchant_name_snapshot")
                or "未记录供应商",
                "standard_name": row.get("material_name_snapshot"),
                "specification": row.get("specification_snapshot") or "",
                "quantity": _display_decimal(_decimal(row.get("quantity"))),
                "unit": unit,
                "tax_inclusive_amount_cents": int(
                    row.get("line_amount_cents") or 0
                ),
            }
        )
    return {
        "standard_name": name,
        "match_score": round(score, 3),
        "confidence": _confidence(score),
        "record_count": len(rows),
        "quantity_by_unit": [
            {"unit": unit, "quantity": _display_decimal(quantity)}
            for unit, quantity in sorted(quantities.items())
        ],
        "quantity_by_project_unit": [
            {
                "project": project,
                "unit": unit,
                "quantity": _display_decimal(quantity),
            }
            for (project, unit), quantity in sorted(project_quantities.items())
        ],
        "tax_inclusive_amount_cents": amount_cents,
        "freight_amount_cents": freight_cents,
        "details": details,
    }


def _supplier_candidate_summary(name, score, matched_text, rows):
    order_ids = set()
    freight_order_ids = set()
    material_amount_cents = 0
    tax_amount_cents = 0
    tax_inclusive_material_amount_cents = 0
    freight_amount_cents = 0
    material_quantities = defaultdict(Decimal)
    material_amounts = defaultdict(int)
    project_amounts = defaultdict(int)
    details = []
    for row in rows:
        order_id = row.get("id")
        if order_id is not None:
            order_ids.add(order_id)
        material = str(row.get("material_name_snapshot") or "").strip() or "未命名材料"
        unit = str(row.get("unit_snapshot") or "").strip() or "未注明单位"
        project = row.get("project_name") or "未归集项目"
        quantity = _decimal(row.get("quantity"))
        line_amount = int(row.get("line_amount_cents") or 0)
        material_quantities[(material, unit)] += quantity
        material_amounts[(material, unit)] += line_amount
        material_amount_cents += int(row.get("material_amount_cents") or line_amount)
        tax_amount_cents += int(row.get("tax_amount_cents") or 0)
        tax_inclusive_material_amount_cents += line_amount
        project_amounts[project] += line_amount
        if order_id is None or order_id not in freight_order_ids:
            freight = int(row.get("freight_amount_cents") or 0)
            freight_amount_cents += freight
            project_amounts[project] += freight
            if order_id is not None:
                freight_order_ids.add(order_id)
        details.append(
            {
                "purchase_date": row.get("purchase_date"),
                "order_no": row.get("order_no"),
                "project": project,
                "supplier": name,
                "material": material,
                "specification": row.get("specification_snapshot") or "",
                "quantity": _display_decimal(quantity),
                "unit": unit,
                "tax_inclusive_amount_cents": line_amount,
            }
        )
    return {
        "supplier_name": name,
        "matched_text": matched_text,
        "match_score": round(score, 3),
        "confidence": _confidence(score),
        "order_count": len(order_ids) if order_ids else len(rows),
        "line_count": len(rows),
        "material_type_count": len(material_quantities),
        "material_amount_cents": material_amount_cents,
        "tax_amount_cents": tax_amount_cents,
        "tax_inclusive_material_amount_cents": tax_inclusive_material_amount_cents,
        "freight_amount_cents": freight_amount_cents,
        "procurement_total_cents": (
            tax_inclusive_material_amount_cents + freight_amount_cents
        ),
        "materials": [
            {
                "material": material,
                "unit": unit,
                "quantity": _display_decimal(material_quantities[(material, unit)]),
                "tax_inclusive_amount_cents": material_amounts[(material, unit)],
            }
            for material, unit in sorted(material_quantities)
        ],
        "amount_by_project": [
            {"project": project, "amount_cents": amount}
            for project, amount in sorted(project_amounts.items())
        ],
        "details": details,
    }


def _procurement_aggregate_summary(rows):
    order_ids = set()
    freight_order_ids = set()
    supplier_names = set()
    material_names = set()
    material_amount_cents = 0
    tax_amount_cents = 0
    tax_inclusive_material_amount_cents = 0
    freight_amount_cents = 0
    project_amounts = defaultdict(int)
    details = []
    for row in rows or []:
        order_id = row.get("id")
        if order_id is not None:
            order_ids.add(order_id)
        supplier = str(
            row.get("supplier_name")
            or row.get("merchant_name_snapshot")
            or "未记录供应商"
        ).strip()
        material = str(row.get("material_name_snapshot") or "未命名材料").strip()
        project = row.get("project_name") or "未归集项目"
        unit = str(row.get("unit_snapshot") or "").strip()
        line_amount = int(row.get("line_amount_cents") or 0)
        supplier_names.add(supplier)
        material_names.add(material)
        material_amount_cents += int(row.get("material_amount_cents") or line_amount)
        tax_amount_cents += int(row.get("tax_amount_cents") or 0)
        tax_inclusive_material_amount_cents += line_amount
        project_amounts[project] += line_amount
        if order_id is None or order_id not in freight_order_ids:
            freight = int(row.get("freight_amount_cents") or 0)
            freight_amount_cents += freight
            project_amounts[project] += freight
            if order_id is not None:
                freight_order_ids.add(order_id)
        details.append(
            {
                "purchase_date": row.get("purchase_date"),
                "order_no": row.get("order_no"),
                "project": project,
                "supplier": supplier,
                "material": material,
                "specification": row.get("specification_snapshot") or "",
                "quantity": _display_decimal(_decimal(row.get("quantity"))),
                "unit": unit,
                "tax_inclusive_amount_cents": line_amount,
            }
        )
    return {
        "order_count": len(order_ids) if order_ids else len(rows or []),
        "line_count": len(rows or []),
        "supplier_count": len(supplier_names),
        "material_type_count": len(material_names),
        "material_amount_cents": material_amount_cents,
        "tax_amount_cents": tax_amount_cents,
        "tax_inclusive_material_amount_cents": tax_inclusive_material_amount_cents,
        "freight_amount_cents": freight_amount_cents,
        "procurement_total_cents": (
            tax_inclusive_material_amount_cents + freight_amount_cents
        ),
        "amount_by_project": [
            {"project": project, "amount_cents": amount}
            for project, amount in sorted(
                project_amounts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        "details": details,
    }


def _is_direct_fact_question(question):
    normalized = normalize_text(question)
    return len(normalized) <= 36 and any(
        normalize_text(word) in normalized for word in _DIRECT_FACT_WORDS
    )


def _resolve_time_scope(question, today=None):
    """Turn common oral time phrases into an explicit, auditable date range."""
    today = today or date.today()
    normalized = normalize_text(question)
    if "上个月" in normalized:
        first_this_month = today.replace(day=1)
        end = first_this_month - timedelta(days=1)
        start = end.replace(day=1)
        code = "previous_month"
        label = f"{start.year}年{start.month}月"
    elif any(word in normalized for word in ("这个月", "本月", "当月")):
        start = today.replace(day=1)
        end = today
        code = "current_month"
        label = f"{today.year}年{today.month}月"
    elif "去年" in normalized:
        year = today.year - 1
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        code = "previous_year"
        label = f"{year}年"
    elif any(word in normalized for word in ("今年", "本年")):
        start = date(today.year, 1, 1)
        end = today
        code = "current_year"
        label = f"{today.year}年"
    else:
        return None
    return {
        "code": code,
        "label": label,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }


def _filter_rows_by_time(rows, time_scope):
    if not time_scope:
        return list(rows or [])
    start = time_scope["start_date"]
    end = time_scope["end_date"]
    return [
        row
        for row in (rows or [])
        if start <= str(row.get("purchase_date") or "")[:10] <= end
    ]


def _scope(project_id, time_scope=None, supplier_name=None):
    scope = (
        {"type": "project", "project_id": int(project_id)}
        if project_id
        else {"type": "company"}
    )
    if time_scope:
        scope["time"] = time_scope
    if supplier_name:
        scope["supplier"] = supplier_name
    return scope


def _mentioned_projects(question, projects):
    normalized = normalize_text(question)
    matches = []
    for project in projects:
        tokens = {
            normalize_text(project.get("name")),
            normalize_text(project.get("project_code")),
        }
        matched_length = max(
            (len(token) for token in tokens if token and token in normalized),
            default=0,
        )
        if matched_length:
            matches.append((matched_length, project))
    if matches:
        longest = max(length for length, _project in matches)
        return [project for length, project in matches if length == longest]

    projects_by_name = {
        normalize_text(project.get("name")): project for project in projects
    }
    alias_matches = []
    for alias, canonical_name in _PROJECT_ALIASES.items():
        if normalize_text(alias) not in normalized:
            continue
        project = projects_by_name.get(normalize_text(canonical_name))
        if project:
            alias_matches.append(project)
    return alias_matches


def _is_labor_cost_question(question):
    normalized = normalize_text(question)
    if any(normalize_text(word) in normalized for word in _LABOR_COST_WORDS):
        return True
    return "人工" in normalized and any(
        word in normalized
        for word in ("成本", "费用", "花了多少钱", "多少钱", "多少")
    )


def retrieve_labor_cost_knowledge(
    question,
    project_id=None,
    conversation_context=None,
):
    """Resolve project, period and labor-cost metric from an oral question."""
    conversation_context = dict(conversation_context or {})
    normalized = normalize_text(question)
    result = {
        "domain": "labor_cost",
        "user_query": question,
        "answer_style": (
            "direct_fact" if _is_direct_fact_question(question) else "analysis"
        ),
        "intent": "none",
        "status": "not_applicable",
        "requires_confirmation": False,
        "scope": {"type": "company"},
        "project": None,
        "summary": None,
        "candidates": [],
    }
    if not _is_labor_cost_question(question):
        return result

    result["intent"] = "labor_cost"
    projects = project_service.list_projects(active_only=False)
    mentioned = _mentioned_projects(question, projects)
    if len(mentioned) > 1:
        result.update(
            {
                "status": "ambiguous",
                "requires_confirmation": True,
                "candidates": [
                    {
                        "id": project["id"],
                        "name": project["name"],
                        "project_code": project["project_code"],
                    }
                    for project in mentioned
                ],
            }
        )
        return result

    explicit_company_scope = any(
        phrase in normalized
        for phrase in ("全公司", "整个公司", "公司一共", "公司总共")
    )
    resolved_project = mentioned[0] if mentioned else None
    if not resolved_project and not explicit_company_scope:
        inherited_project_id = project_id or conversation_context.get("project_id")
        if inherited_project_id:
            resolved_project = next(
                (
                    project
                    for project in projects
                    if project["id"] == int(inherited_project_id)
                ),
                None,
            )

    time_scope = _resolve_time_scope(question) or conversation_context.get("time")
    scope = _scope(
        resolved_project["id"] if resolved_project else None,
        time_scope,
    )
    scope["explicit_company"] = explicit_company_scope
    summary = labor_service.get_labor_cost_summary(
        start_date=(time_scope or {}).get("start_date"),
        end_date=(time_scope or {}).get("end_date"),
        project_id=resolved_project["id"] if resolved_project else None,
    )
    result.update(
        {
            "status": "matched",
            "scope": scope,
            "project": (
                {
                    "id": resolved_project["id"],
                    "name": resolved_project["name"],
                    "project_code": resolved_project["project_code"],
                }
                if resolved_project
                else None
            ),
            "summary": summary,
        }
    )
    return result


def retrieve_supplier_procurement_knowledge(
    question,
    project_id=None,
    conversation_context=None,
):
    """Recognize a supplier mention and aggregate every purchase under it."""
    conversation_context = dict(conversation_context or {})
    company_rows = procurement_service.list_purchase_orders()
    scoped_rows = (
        procurement_service.list_purchase_orders(project_id=project_id)
        if project_id
        else company_rows
    )
    time_scope = _resolve_time_scope(question) or conversation_context.get("time")
    confirmed_supplier = normalize_text(
        conversation_context.get("supplier_name")
    )
    result = {
        "domain": "supplier_procurement",
        "scope": _scope(project_id, time_scope),
        "user_query": question,
        "answer_style": (
            "direct_fact" if _is_direct_fact_question(question) else "analysis"
        ),
        "intent": "none",
        "status": "not_applicable",
        "requires_confirmation": False,
        "residual_query": "",
        "candidates": [],
    }
    grouped_company = defaultdict(list)
    display_names = {}
    for row in company_rows:
        display_name = str(
            row.get("supplier_name")
            or row.get("merchant_name_snapshot")
            or ""
        ).strip()
        normalized_name = normalize_text(display_name)
        if not normalized_name:
            continue
        grouped_company[normalized_name].append(row)
        display_names.setdefault(normalized_name, display_name)
    ranked = sorted(
        (
            (*_supplier_mention_score(question, name), name)
            for name in grouped_company
        ),
        reverse=True,
    )
    ranked = [item for item in ranked if item[0] >= 0.82]
    normalized_question = normalize_text(question)
    explicit_supplier_intent = any(
        word in normalized_question
        for word in ("供应商", "那里", "那边", "这家", "从哪家", "哪家")
    )
    if not ranked:
        if explicit_supplier_intent:
            result["status"] = "not_found"
            result["intent"] = "supplier_aggregate"
        return result

    top_score, top_text, _top_name = ranked[0]
    exact_match = top_score >= 0.995
    close_matches = [
        (score, matched_text, name)
        for score, matched_text, name in ranked
        if score >= 0.82
        and (top_score - score <= 0.06 or matched_text == top_text)
    ]
    confirmed_match = (
        bool(confirmed_supplier)
        and len(close_matches) == 1
        and close_matches[0][2] == confirmed_supplier
    )
    ambiguous = len(close_matches) > 1 and not exact_match
    needs_single_confirmation = not exact_match and not confirmed_match
    selected = close_matches if ambiguous else [ranked[0]]
    residual = _supplier_residual(question, top_text)
    result["residual_query"] = residual
    result["requires_confirmation"] = ambiguous or needs_single_confirmation
    result["intent"] = "supplier_material" if residual else "supplier_aggregate"

    scoped_by_supplier = defaultdict(list)
    for row in _filter_rows_by_time(scoped_rows, time_scope):
        display_name = str(
            row.get("supplier_name")
            or row.get("merchant_name_snapshot")
            or ""
        ).strip()
        scoped_by_supplier[normalize_text(display_name)].append(row)

    result["candidates"] = [
        _supplier_candidate_summary(
            display_names[name],
            score,
            matched_text,
            scoped_by_supplier.get(name, []),
        )
        for score, matched_text, name in selected
    ]
    if ambiguous or needs_single_confirmation:
        result["status"] = "ambiguous"
    elif residual:
        result["status"] = "context_only"
    else:
        result["status"] = "matched"
    return result


def retrieve_procurement_knowledge(
    question,
    project_id=None,
    supplier_name=None,
    inherited_time_scope=None,
    confirmed_material_name=None,
):
    """Retrieve auditable procurement facts relevant to a natural-language query."""
    all_rows = procurement_service.list_purchase_orders(project_id=project_id)
    if supplier_name:
        normalized_supplier = normalize_text(supplier_name)
        all_rows = [
            row
            for row in all_rows
            if normalize_text(
                row.get("supplier_name") or row.get("merchant_name_snapshot")
            )
            == normalized_supplier
        ]
    time_scope = _resolve_time_scope(question) or inherited_time_scope
    rows = _filter_rows_by_time(all_rows, time_scope)
    material_query = _material_query(question, all_rows)
    normalized_question = normalize_text(question)
    has_purchase_intent = any(
        normalize_text(word) in normalized_question for word in _PURCHASE_INTENT_WORDS
    )
    result = {
        "domain": "procurement",
        "scope": _scope(project_id, time_scope, supplier_name),
        "user_query": question,
        "material_query": material_query,
        "intent": "material_lookup",
        "answer_style": (
            "direct_fact"
            if _is_direct_fact_question(question)
            or (supplier_name and len(normalized_question) <= 12)
            else "analysis"
        ),
        "status": "not_applicable",
        "requires_confirmation": False,
        "candidates": [],
    }
    if _is_procurement_aggregate_question(question, material_query):
        result["intent"] = "procurement_aggregate"
        result["answer_style"] = "direct_fact"
        result["status"] = "aggregate"
        result["candidates"] = [_procurement_aggregate_summary(rows)]
        return result
    if not rows or len(material_query) < 2:
        if has_purchase_intent:
            result["status"] = "not_found"
        return result

    grouped = defaultdict(list)
    display_names = {}
    for row in rows:
        display_name = str(row.get("material_name_snapshot") or "").strip()
        normalized_name = normalize_text(display_name)
        if not normalized_name:
            continue
        grouped[normalized_name].append(row)
        display_names.setdefault(normalized_name, display_name)

    ranked = sorted(
        (
            (_material_similarity(material_query, normalized_name), normalized_name)
            for normalized_name in grouped
        ),
        reverse=True,
    )
    if not ranked:
        result["status"] = "not_found" if has_purchase_intent else "not_applicable"
        return result

    top_score = ranked[0][0]
    if top_score < 0.64:
        result["status"] = "not_found" if has_purchase_intent else "not_applicable"
        result["candidates"] = [
            {
                "standard_name": display_names[name],
                "match_score": round(score, 3),
            }
            for score, name in ranked[:3]
            if score >= 0.45
        ]
        return result

    close_matches = [
        (score, name)
        for score, name in ranked
        if score >= 0.64 and top_score - score <= 0.055
    ]
    confirmed_material = normalize_text(confirmed_material_name)
    confirmed_option = next(
        (
            (score, name)
            for score, name in close_matches
            if name == confirmed_material
        ),
        None,
    )
    exact_match = top_score >= 0.995
    ambiguous = (
        len(close_matches) > 1
        and not exact_match
        and confirmed_option is None
    )
    if confirmed_option is not None:
        selected_matches = [confirmed_option]
    else:
        selected_matches = close_matches if ambiguous else [ranked[0]]
    result["status"] = "ambiguous" if ambiguous else "matched"
    result["requires_confirmation"] = ambiguous
    result["candidates"] = [
        _candidate_summary(display_names[name], score, grouped[name])
        for score, name in selected_matches
    ]
    return result


def retrieve_business_knowledge(
    question,
    project_id=None,
    conversation_context=None,
):
    """Entry point kept domain-neutral so more business modules can be added later."""
    conversation_context = dict(conversation_context or {})
    normalized_question = normalize_text(question)
    explicit_company_scope = any(
        phrase in normalized_question
        for phrase in ("全公司", "公司一共", "公司总共", "整个公司")
    )
    if project_id is None:
        project_id = conversation_context.get("project_id")
    labor_cost = retrieve_labor_cost_knowledge(
        question,
        project_id=project_id,
        conversation_context=conversation_context,
    )
    if labor_cost.get("status") != "not_applicable":
        return {
            "labor_cost": labor_cost,
            "supplier_procurement": {"status": "not_applicable"},
            "procurement": {"status": "not_applicable"},
        }

    resolved_project = None
    if explicit_company_scope:
        project_id = None
    else:
        projects = project_service.list_projects(active_only=False)
        mentioned_projects = _mentioned_projects(question, projects)
        if len(mentioned_projects) == 1:
            resolved_project = mentioned_projects[0]
            project_id = resolved_project["id"]
        elif project_id:
            resolved_project = next(
                (
                    project
                    for project in projects
                    if project["id"] == int(project_id)
                ),
                None,
            )
    supplier = retrieve_supplier_procurement_knowledge(
        question,
        project_id=project_id,
        conversation_context=conversation_context,
    )
    supplier_name = (
        None
        if explicit_company_scope
        else conversation_context.get("supplier_name")
    )
    if supplier["status"] == "context_only" and len(supplier["candidates"]) == 1:
        supplier_name = supplier["candidates"][0]["supplier_name"]
    if supplier["status"] in ("matched", "ambiguous"):
        procurement = {
            "domain": "procurement",
            "scope": supplier["scope"],
            "user_query": question,
            "status": "not_applicable",
            "reason": "supplier_aggregate_query",
            "requires_confirmation": False,
            "candidates": [],
        }
    else:
        procurement = retrieve_procurement_knowledge(
            question,
            project_id=project_id,
            supplier_name=supplier_name,
            inherited_time_scope=conversation_context.get("time"),
            confirmed_material_name=conversation_context.get("material_name"),
        )
        if explicit_company_scope and procurement.get("status") == "aggregate":
            procurement["context_clears"] = ["supplier_name", "material_name"]
    for item in (supplier, procurement):
        scope = item.get("scope") or {}
        scope["explicit_company"] = explicit_company_scope
        if resolved_project:
            scope["project_name"] = resolved_project["name"]
        item["scope"] = scope
    return {
        "labor_cost": labor_cost,
        "supplier_procurement": supplier,
        "procurement": procurement,
    }
