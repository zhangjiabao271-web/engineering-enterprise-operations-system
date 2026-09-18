"""Read and maintain the collection follow-up workbench.

Financial balances remain derived from settlements and receipt allocations.
This module stores only collection-management facts and immutable follow-up
notes; it never creates or edits settlements, invoices, or receipts.
"""

from datetime import date, datetime
from uuid import uuid4

from db.connection import db_read, db_transaction


CASE_STATUSES = {
    "pending": "待跟进",
    "following": "跟进中",
    "promised": "承诺付款",
    "paused": "暂缓",
    "closed": "已完成",
}


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _optional_date(value, label):
    value = str(value or "").strip()
    if not value:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as error:
        raise ValueError(f"{label}必须使用 YYYY-MM-DD 格式") from error
    return value


def _days_since(value, today):
    if not value:
        return None
    return (today - datetime.strptime(value, "%Y-%m-%d").date()).days


def _aging_bucket(days):
    if days is None:
        return "未确定"
    if days < 0:
        return "未到期"
    if days <= 30:
        return "0—30天"
    if days <= 60:
        return "31—60天"
    if days <= 90:
        return "61—90天"
    return "90天以上"


def _attention_status(row, today):
    if int(row.get("receivable_minor") or 0) <= 0:
        return "已结清"
    if row.get("promised_date") and row["promised_date"] < today:
        return "承诺逾期"
    if row.get("next_followup_date") and row["next_followup_date"] <= today:
        return "今日跟进" if row["next_followup_date"] == today else "跟进逾期"
    if row.get("due_date") and row["due_date"] < today:
        return "已逾期"
    return CASE_STATUSES.get(row.get("case_status"), "待跟进")


def list_project_cases(project_id=None, *, include_settled=False):
    """Return one collection row per project without merging projects."""
    with db_read() as conn:
        sql = """
            WITH settlement_balance AS (
                SELECT s.id, s.project_id, s.settlement_date, s.amount_minor,
                       COALESCE((
                           SELECT SUM(ra.allocated_amount_minor)
                           FROM receipt_allocations ra
                           JOIN receipts r ON r.id=ra.receipt_id
                           WHERE ra.settlement_id=s.id
                             AND ra.status='active' AND r.status='active'
                       ), 0) AS received_minor
                FROM settlements s
                WHERE s.status='active'
            ),
            project_balance AS (
                SELECT p.id AS project_id,
                       COALESCE(SUM(sb.amount_minor), 0) AS settlement_minor,
                       COALESCE(SUM(sb.received_minor), 0) AS receipt_minor,
                       MIN(CASE WHEN sb.amount_minor>sb.received_minor
                                THEN sb.settlement_date END)
                           AS oldest_unpaid_date
                FROM projects p
                LEFT JOIN settlement_balance sb ON sb.project_id=p.id
                GROUP BY p.id
            )
            SELECT p.id AS project_id, p.project_code, p.name AS project_name,
                   p.status AS project_status, p.business_mode,
                   p.customer_partner_id,
                   COALESCE(bp.legal_name, p.customer_name, '未确认客户')
                       AS customer_name,
                   pb.settlement_minor, pb.receipt_minor,
                   MAX(pb.settlement_minor-pb.receipt_minor, 0)
                       AS receivable_minor,
                   pb.oldest_unpaid_date,
                   cc.id AS case_id, cc.due_date, cc.promised_date,
                   cc.next_followup_date, COALESCE(cc.owner_name, '') owner_name,
                   COALESCE(cc.status, 'pending') AS case_status,
                   COALESCE(cc.overdue_reason, '') AS overdue_reason,
                   COALESCE(cc.next_action, '') AS next_action,
                   COALESCE(cc.notes, '') AS notes,
                   cc.updated_at AS case_updated_at,
                   COALESCE((
                       SELECT MAX(cfl.followup_date)
                       FROM collection_followup_logs cfl
                       WHERE cfl.case_id=cc.id
                   ), '') AS last_followup_date,
                   COALESCE((
                       SELECT cfl.content
                       FROM collection_followup_logs cfl
                       WHERE cfl.case_id=cc.id
                       ORDER BY cfl.followup_date DESC, cfl.id DESC LIMIT 1
                   ), '') AS last_followup_content
            FROM projects p
            JOIN project_balance pb ON pb.project_id=p.id
            LEFT JOIN business_partners bp ON bp.id=p.customer_partner_id
            LEFT JOIN collection_cases cc ON cc.project_id=p.id
            WHERE pb.settlement_minor>0
        """
        params = []
        if project_id:
            sql += " AND p.id=?"
            params.append(int(project_id))
        if not include_settled:
            sql += " AND pb.settlement_minor>pb.receipt_minor"
        sql += """
            ORDER BY MAX(pb.settlement_minor-pb.receipt_minor, 0) DESC,
                     pb.oldest_unpaid_date, p.name
        """
        today = date.today()
        today_text = today.isoformat()
        rows = []
        for source in conn.execute(sql, params).fetchall():
            row = dict(source)
            row["customer_key"] = str(
                row["customer_partner_id"] or f"project:{row['project_id']}"
            )
            aging_start = row["due_date"] or row["oldest_unpaid_date"]
            row["aging_days"] = _days_since(aging_start, today)
            row["aging_bucket"] = _aging_bucket(row["aging_days"])
            row["case_status_label"] = CASE_STATUSES.get(
                row["case_status"], "待跟进"
            )
            row["attention_status"] = _attention_status(row, today_text)
            row["needs_action_today"] = row["attention_status"] in {
                "承诺逾期", "今日跟进", "跟进逾期"
            }
            rows.append(row)
        return rows


def summarize_project_cases(rows):
    rows = list(rows or [])
    return {
        "project_count": len(rows),
        "receivable_minor": sum(int(row["receivable_minor"]) for row in rows),
        "action_count": sum(bool(row["needs_action_today"]) for row in rows),
        "action_minor": sum(
            int(row["receivable_minor"])
            for row in rows if row["needs_action_today"]
        ),
        "promised_overdue_count": sum(
            row["attention_status"] == "承诺逾期" for row in rows
        ),
    }


def list_customer_cases(project_rows=None):
    """Aggregate project cases for prioritization while preserving drill-down."""
    rows = list_project_cases() if project_rows is None else list(project_rows)
    groups = {}
    for row in rows:
        key = row["customer_key"]
        group = groups.setdefault(
            key,
            {
                "customer_key": key,
                "customer_partner_id": row["customer_partner_id"],
                "customer_name": row["customer_name"],
                "project_count": 0,
                "receivable_minor": 0,
                "oldest_aging_days": None,
                "action_count": 0,
                "promised_overdue_count": 0,
                "owner_names": set(),
                "next_followup_date": None,
                "next_action": "",
            },
        )
        group["project_count"] += 1
        group["receivable_minor"] += int(row["receivable_minor"])
        days = row["aging_days"]
        if days is not None:
            current_oldest = group["oldest_aging_days"]
            group["oldest_aging_days"] = (
                days if current_oldest is None else max(current_oldest, days)
            )
        group["action_count"] += int(row["needs_action_today"])
        group["promised_overdue_count"] += int(
            row["attention_status"] == "承诺逾期"
        )
        if row["owner_name"]:
            group["owner_names"].add(row["owner_name"])
        followup_date = row["next_followup_date"]
        if followup_date and (
            not group["next_followup_date"]
            or followup_date < group["next_followup_date"]
        ):
            group["next_followup_date"] = followup_date
            group["next_action"] = row["next_action"]
    result = []
    for group in groups.values():
        group["owner_name"] = "、".join(sorted(group.pop("owner_names")))
        group["aging_bucket"] = _aging_bucket(group["oldest_aging_days"])
        if group["promised_overdue_count"]:
            group["attention_status"] = "承诺逾期"
        elif group["action_count"]:
            group["attention_status"] = "需要跟进"
        else:
            group["attention_status"] = "按计划跟进"
        result.append(group)
    return sorted(
        result,
        key=lambda row: (
            -row["promised_overdue_count"],
            -row["action_count"],
            -row["receivable_minor"],
            row["customer_name"],
        ),
    )


def get_project_case(project_id):
    rows = list_project_cases(project_id, include_settled=True)
    return rows[0] if rows else None


def list_followup_logs(project_id):
    with db_read() as conn:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT cfl.*, COALESCE(cc.owner_name, '') AS owner_name
                   FROM collection_followup_logs cfl
                   JOIN collection_cases cc ON cc.id=cfl.case_id
                   WHERE cfl.project_id=?
                   ORDER BY cfl.followup_date DESC, cfl.id DESC""",
                (int(project_id),),
            ).fetchall()
        ]


def save_project_case(project_id, data):
    project_id = int(project_id or 0)
    if not project_id:
        raise ValueError("请选择要跟进的项目")
    status = str(data.get("status") or "pending").strip()
    if status not in CASE_STATUSES:
        raise ValueError("请选择有效的跟进状态")
    values = {
        "due_date": _optional_date(data.get("due_date"), "应收到期日"),
        "promised_date": _optional_date(data.get("promised_date"), "客户承诺日"),
        "next_followup_date": _optional_date(
            data.get("next_followup_date"), "下次跟进日"
        ),
        "owner_name": str(data.get("owner_name") or "").strip(),
        "status": status,
        "overdue_reason": str(data.get("overdue_reason") or "").strip(),
        "next_action": str(data.get("next_action") or "").strip(),
        "notes": str(data.get("notes") or "").strip(),
        "followup_date": _optional_date(data.get("followup_date"), "跟进日期"),
        "followup_content": str(data.get("followup_content") or "").strip(),
    }
    if values["followup_content"] and not values["followup_date"]:
        raise ValueError("填写跟进内容时必须选择跟进日期")
    if not values["followup_content"]:
        values["followup_date"] = None
    with db_transaction(immediate=True) as conn:
        project = conn.execute(
            """SELECT id, organization_id FROM projects WHERE id=?""",
            (project_id,),
        ).fetchone()
        if not project:
            raise ValueError("项目不存在")
        has_settlement = conn.execute(
            """SELECT 1 FROM settlements
               WHERE project_id=? AND status='active' LIMIT 1""",
            (project_id,),
        ).fetchone()
        if not has_settlement:
            raise ValueError("该项目尚无有效收入确认，不能建立回款跟进")
        balance = conn.execute(
            """SELECT
                   COALESCE((SELECT SUM(amount_minor) FROM settlements
                             WHERE project_id=? AND status='active'), 0)
                   - COALESCE((
                       SELECT SUM(ra.allocated_amount_minor)
                       FROM receipt_allocations ra
                       JOIN receipts r ON r.id=ra.receipt_id
                       WHERE ra.project_id=? AND ra.status='active'
                         AND r.status='active'
                   ), 0)""",
            (project_id, project_id),
        ).fetchone()[0]
        if status == "closed" and int(balance or 0) > 0:
            raise ValueError("项目仍有未回款余额，不能标记为已完成")
        changed_at = _now()
        existing = conn.execute(
            "SELECT id FROM collection_cases WHERE project_id=?",
            (project_id,),
        ).fetchone()
        if existing:
            case_id = int(existing["id"])
            conn.execute(
                """UPDATE collection_cases
                   SET due_date=?, promised_date=?, next_followup_date=?,
                       owner_name=?, status=?, overdue_reason=?, next_action=?,
                       notes=?, updated_at=?
                   WHERE id=?""",
                (
                    values["due_date"], values["promised_date"],
                    values["next_followup_date"], values["owner_name"],
                    values["status"], values["overdue_reason"],
                    values["next_action"], values["notes"], changed_at,
                    case_id,
                ),
            )
        else:
            cursor = conn.execute(
                """INSERT INTO collection_cases (
                       public_id, organization_id, project_id, due_date,
                       promised_date, next_followup_date, owner_name, status,
                       overdue_reason, next_action, notes, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()), int(project["organization_id"]), project_id,
                    values["due_date"], values["promised_date"],
                    values["next_followup_date"], values["owner_name"],
                    values["status"], values["overdue_reason"],
                    values["next_action"], values["notes"], changed_at,
                    changed_at,
                ),
            )
            case_id = int(cursor.lastrowid)
        if values["followup_content"]:
            conn.execute(
                """INSERT INTO collection_followup_logs (
                       public_id, case_id, project_id, followup_date, content,
                       promised_date, next_followup_date, next_action, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid4()), case_id, project_id,
                    values["followup_date"], values["followup_content"],
                    values["promised_date"], values["next_followup_date"],
                    values["next_action"], changed_at,
                ),
            )
        return case_id


def get_actionable_cases(limit=None):
    rows = [row for row in list_project_cases() if row["needs_action_today"]]
    rows.sort(
        key=lambda row: (
            row["promised_date"] or row["next_followup_date"] or "9999-12-31",
            -int(row["receivable_minor"]),
            row["project_name"],
        )
    )
    return rows[:limit] if limit else rows
