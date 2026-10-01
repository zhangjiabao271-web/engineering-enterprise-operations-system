"""Read-only, source-backed workday answers, without model arithmetic."""

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal

from services import business_knowledge_service as knowledge
from services import labor_service, project_service


def _period(question, today):
    if "昨天" in question or "今天" in question:
        day = today - timedelta(days=int("昨天" in question))
        return {"label": day.isoformat(), "start_date": day.isoformat(), "end_date": day.isoformat()}
    match = re.search(r"(?:(20\d{2})年)?(1[0-2]|0?[1-9])月", question)
    if match:
        year, month = int(match[1] or today.year), int(match[2])
        start = date(year, month, 1)
        end = date(year, month, calendar.monthrange(year, month)[1])
    else:
        match = re.search(r"(20\d{2})年", question)
        if not match:
            return knowledge._resolve_time_scope(question, today=today)
        start, end = date(int(match[1]), 1, 1), date(int(match[1]), 12, 31)
    return {"label": f"{start}至{end}", "start_date": start.isoformat(), "end_date": min(end, today).isoformat()}


def retrieve_labor_query(question, project_id=None, conversation_context=None, today=None):
    context = conversation_context or {}
    today = today or date.today()
    text = knowledge.normalize_text(question)
    days_question = any(word in text for word in (
        "几工", "工天", "多少工", "几天班", "多少天班", "出勤", "做了几天", "做了多少天",
    ))
    if any(word in text for word in ("工资", "成本", "多少钱", "为什么", "建议")):
        return None
    # Inherit a person only for a short, unambiguous time-only follow-up.
    followup = bool(context.get("labor_query")) and bool(re.fullmatch(
        r"(?:那|那么)?(?:今年|去年|本月|这个月|上个月|今天|昨天|(?:20\d{2}年)?\d{1,2}月|20\d{2}年)(?:呢|的呢)?[？?。!！]*", text
    ))
    if not days_question and not followup:
        return None

    def reply(answer, updates=None, sources=None):
        return {"response_type": "answer", "message_type": "answer", "answer": answer,
                "question": question, "answer_mode": "local", "intent": "labor_days",
                "context_updates": {"labor_query": None, **(updates or {})}, "sources": sources or []}

    workers = labor_service.get_workers(active_only=False)
    matches = [w for w in workers if knowledge.normalize_text(w["name"]) and knowledge.normalize_text(w["name"]) in text]
    if len(matches) > 1:
        identifier = re.search(r"编号(\d+)", text)
        if identifier:
            matches = [w for w in matches if w["id"] == int(identifier[1])]
        else:
            by_trade = [w for w in matches if w.get("trade") and knowledge.normalize_text(w["trade"]) in text]
            if by_trade:
                matches = by_trade
    if not matches and followup:
        matches = [w for w in workers if w["id"] == context["labor_query"].get("worker_id")]
    company = any(word in text for word in ("全公司", "所有工人", "全部工人"))
    if followup and context["labor_query"].get("worker_id") is None:
        company = True
    if len(matches) > 1:
        return reply("匹配到多名工人，请补充姓名或工种，暂不合并计算：" + "、".join(f"{w['name']}（{w.get('trade') or '未填工种'}，编号{w['id']}）" for w in matches))
    if not matches and not company:
        return reply("请提供工人档案中的完整姓名，或问“全公司今年一共多少工天”。没有匹配到姓名不代表工天为零。")
    worker = matches[0] if matches else None
    projects = project_service.list_projects(active_only=False)
    mentioned = knowledge._mentioned_projects(question, projects)
    if len(mentioned) > 1:
        return reply("请明确要查询的项目：" + "、".join(p["name"] for p in mentioned))
    selected_id = project_id or context.get("project_id")
    if company:
        selected_id = None
    project = mentioned[0] if mentioned else next((p for p in projects if p["id"] == selected_id), None)
    if "项目" in text and not mentioned and not selected_id:
        return reply("请补充档案中的完整项目名称，避免把全公司工天当成某个项目的工天。")
    if any(word in text for word in ("周", "季度", "最近", "到", "至", "-", "/")) or len(re.findall(r"\d+月", text)) > 1:
        return reply("目前工天查询支持今天、昨天、本月、上个月、今年、去年及明确年月（例如2026年8月）。请先指定其中一个时间范围。")
    period = _period(text, today)
    if not period and followup:
        period = context["labor_query"].get("time")
    period = period or {"label": "全部历史至今天", "start_date": None, "end_date": today.isoformat()}
    if period["start_date"] and period["start_date"] > period["end_date"]:
        return reply("这个时间范围在未来，不能作为已发生的工天统计。")
    summary = labor_service.get_labor_cost_summary(
        start_date=period["start_date"], end_date=period["end_date"],
        project_id=project["id"] if project else None, worker_id=worker["id"] if worker else None,
    )
    details = summary["details"]
    overtime = sum((Decimal(str(r["work_days"])) for r in details if r["is_overtime"]), Decimal(0))
    subject = worker["name"] if worker else "全体工人"
    scope = project["name"] if project else "全公司（含待归集记录）"
    label = f"{subject} · {scope} · {period['start_date'] or '最早记录'}至{period['end_date']}"
    answer = (f"{label}\n共 {summary['work_days']:g} 工，其中加班 {overtime:g} 工（已包含在总工天中），"
              f"共 {summary['record_count']} 条有效记录。\n按记录中的工天数相加，半天按0.5工；不计作废记录。")
    if not details:
        answer += "\n该范围内没有有效工天记录，不代表实际没有施工。"
    if summary.get("by_month"):
        answer += "\n按月：" + "；".join(f"{r['month']}：{r['work_days']:g}工" for r in summary["by_month"])
    source = {"module": "人工工天", "page_key": "labor", "view_type": "operating",
              "label": "工天原始记录", "scope_label": label, "record_count": len(details), "details": details,
              "columns": [{"key": key, "label": title, "kind": "text"} for key, title in (
                  ("work_date", "日期"), ("worker_name", "工人"), ("project_name", "项目"),
                  ("construction_site", "工地"), ("work_days", "工天"), ("is_overtime", "加班（1=是）"))]}
    return reply(answer, {"labor_query": {"worker_id": worker["id"] if worker else None, "time": period},
                         "project_id": project["id"] if project else None, "data_modules": ["人工工天"],
                         "pending_confirmation": None}, [source])
