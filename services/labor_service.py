from services._common import now as _now, organization_id as _organization_id
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from db.connection import get_connection


PROJECT_SITE_ALIASES = {
    "澄湖": "澄湖药业",
    "澄湖药业": "澄湖药业",
    "屹峰": "屹峰药业",
    "屹峰药业": "屹峰药业",
}

RATE_ADJUSTMENT_MODES = {
    "future_only": "只影响以后新录入的工天",
    "through_today": "同步调整生效日至今天的已有工天",
    "custom": "自定义日期和项目范围",
}


def _minor(value):
    return int(
        (Decimal(str(value or 0)) * 100).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def _amount_minor(work_days, rate_minor):
    return int(
        (Decimal(str(work_days or 0)) * Decimal(int(rate_minor))).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


def _iso_date(value, label):
    try:
        return datetime.strptime(str(value or "").strip(), "%Y-%m-%d").date()
    except ValueError as error:
        raise ValueError(f"{label}必须是 YYYY-MM-DD") from error


def _normalize_work_log_data(data):
    normalized = dict(data)
    raw_site = (normalized.get("construction_site") or "").strip()
    canonical = PROJECT_SITE_ALIASES.get(raw_site.casefold())
    normalized["construction_site"] = canonical or raw_site
    normalized["is_overtime"] = 1 if normalized.get("is_overtime") else 0
    return normalized


def _resolve_project(conn, data):
    key = (data.get("construction_site") or "").strip().casefold()
    canonical = PROJECT_SITE_ALIASES.get(key)
    if canonical:
        row = conn.execute(
            """SELECT p.id AS project_id, ps.id AS site_id
               FROM projects p
               LEFT JOIN project_sites ps
                 ON ps.project_id=p.id AND ps.name=p.name AND ps.is_active=1
               WHERE p.name=?
               ORDER BY ps.id
               LIMIT 1""",
            (canonical,),
        ).fetchone()
        if not row:
            raise ValueError(f"项目“{canonical}”不存在")
        return row["project_id"], row["site_id"]
    project_id = int(data.get("project_id") or 0) or None
    site_id = int(data.get("project_site_id") or 0) or None
    if project_id:
        return project_id, site_id
    key = (data.get("construction_site") or "").strip().casefold()
    if not key:
        return None, None
    matches = set()
    for row in conn.execute(
        """SELECT p.id AS project_id, NULL AS site_id, p.project_code AS value
           FROM projects p
           UNION ALL
           SELECT p.id, NULL, p.name FROM projects p
           UNION ALL
           SELECT ps.project_id, ps.id, ps.name FROM project_sites ps
           WHERE ps.is_active=1
           UNION ALL
           SELECT cs.project_id, ps.id, cs.site_name
           FROM construction_sites cs
           LEFT JOIN project_sites ps
             ON ps.legacy_construction_site_id=cs.id
           WHERE cs.is_active=1"""
    ).fetchall():
        if (row["value"] or "").strip().casefold() == key:
            matches.add((row["project_id"], row["site_id"]))
    project_ids = {item[0] for item in matches}
    project_id = next(iter(project_ids)) if len(project_ids) == 1 else None
    site_ids = {item[1] for item in matches if item[1] is not None}
    site_id = next(iter(site_ids)) if len(site_ids) == 1 else None
    return project_id, site_id


def _resolve_attribution(conn, data):
    """Explicit project attribution for work-log writes.

    Priority: explicit project_id > explicit unassigned flag > exact
    site-text resolution. Anything else is rejected so silent orphan
    rows can no longer be created.
    """
    site_name_key = (data.get("construction_site") or "").strip().casefold()
    project_id = int(data.get("project_id") or 0) or None
    if project_id:
        exists = conn.execute(
            "SELECT 1 FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if not exists:
            raise ValueError("所选项目不存在")
        site_id = int(data.get("project_site_id") or 0) or None
        if site_id:
            site = conn.execute(
                """SELECT name FROM project_sites
                   WHERE id=? AND project_id=? AND is_active=1""",
                (site_id, project_id),
            ).fetchone()
            if not site:
                raise ValueError("所选施工地点不属于当前项目或已停用")
            if site["name"].strip().casefold() != site_name_key:
                raise ValueError("施工地点名称与所选项目地点不一致")
        return project_id, site_id
    if data.get("allow_unassigned"):
        return None, None
    project_id, site_id = _resolve_project(conn, data)
    if not project_id:
        raise ValueError(
            "无法根据工地名称确定所属项目；"
            "请选择所属项目，或明确选择“待归集”后再保存"
        )
    return project_id, site_id


def suggest_project_for_site(site_text):
    """Best-effort project suggestion for a site name (exact match only)."""
    conn = get_connection()
    try:
        project_id, site_id = _resolve_project(
            conn, {"construction_site": site_text}
        )
        if not project_id:
            return None
        row = conn.execute(
            "SELECT id, name, project_code FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        result = dict(row) if row else None
        if result:
            result["project_site_id"] = site_id
        return result
    finally:
        conn.close()


def add_worker(data):
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = _now()
        rate_minor = _minor(data.get("daily_rate", 0))
        if rate_minor < 0:
            raise ValueError("默认日工资不能为负数")
        cursor = conn.execute(
            """INSERT INTO workers (
                   name, trade, phone, daily_rate, status, notes, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                data["name"],
                data.get("trade", ""),
                data.get("phone", ""),
                rate_minor / 100,
                data.get("status", "在职"),
                data.get("notes", ""),
                now,
            ),
        )
        worker_id = cursor.lastrowid
        conn.execute(
            """INSERT INTO worker_rate_versions (
                   public_id, worker_id, rate_minor, effective_from,
                   reason, source, status, created_at
               ) VALUES (?, ?, ?, ?, '新增工人默认日工资',
                         'worker_creation', 'active', ?)""",
            (str(uuid4()), worker_id, rate_minor, date.today().isoformat(), now),
        )
        conn.commit()
        return worker_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_worker(worker_id, data):
    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT daily_rate FROM workers WHERE id=?", (worker_id,)
        ).fetchone()
        if not existing:
            raise ValueError("工人不存在")
        conn.execute(
            """UPDATE workers
               SET name=?, trade=?, phone=?, status=?, notes=?
               WHERE id=?""",
            (
                data["name"],
                data.get("trade", ""),
                data.get("phone", ""),
                data.get("status", "在职"),
                data.get("notes", ""),
                worker_id,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_workers(worker_ids):
    if not worker_ids:
        return
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(worker_ids))
        worker_ids = [int(value) for value in worker_ids]
        count = conn.execute(
            f"SELECT COUNT(*) FROM work_logs WHERE worker_id IN ({placeholders})",
            tuple(worker_ids),
        ).fetchone()[0]
        if count:
            raise ValueError("所选工人已有工天记录，不能删除；可以将状态改为“离职”。")
        conn.execute(
            f"DELETE FROM workers WHERE id IN ({placeholders})",
            tuple(worker_ids),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_workers(keyword="", active_only=False):
    conn = get_connection()
    try:
        sql = "SELECT * FROM workers WHERE 1=1"
        params = []
        if keyword:
            sql += " AND (name LIKE ? OR trade LIKE ? OR phone LIKE ? OR notes LIKE ?)"
            params.extend([f"%{keyword}%"] * 4)
        if active_only:
            sql += " AND status='在职'"
        sql += " ORDER BY CASE status WHEN '在职' THEN 1 ELSE 2 END, name"
        rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def get_worker_by_id(worker_id):
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM workers WHERE id=?", (worker_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _effective_rate_minor(conn, worker_id, work_date):
    row = conn.execute(
        """SELECT rate_minor FROM worker_rate_versions
           WHERE worker_id=? AND status='active' AND effective_from<=?
             AND (effective_to IS NULL OR effective_to>=?)
           ORDER BY effective_from DESC, id DESC LIMIT 1""",
        (worker_id, work_date, work_date),
    ).fetchone()
    if row:
        return int(row["rate_minor"])
    worker = conn.execute(
        "SELECT daily_rate FROM workers WHERE id=?", (worker_id,)
    ).fetchone()
    if not worker:
        raise ValueError("工人不存在")
    return _minor(worker["daily_rate"])


def get_effective_worker_rate(worker_id, work_date):
    work_date = _iso_date(work_date, "工天日期").isoformat()
    conn = get_connection()
    try:
        return _effective_rate_minor(conn, int(worker_id), work_date) / 100
    finally:
        conn.close()


def get_effective_worker_rates(worker_ids, work_date):
    if not worker_ids:
        return {}
    work_date = _iso_date(work_date, "工天日期").isoformat()
    conn = get_connection()
    try:
        return {
            int(worker_id): _effective_rate_minor(
                conn, int(worker_id), work_date
            ) / 100
            for worker_id in worker_ids
        }
    finally:
        conn.close()


def _work_log_amounts(conn, data):
    try:
        work_days = Decimal(str(data.get("work_days", 1)))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("工天必须是有效数字") from error
    if not work_days.is_finite() or work_days <= 0:
        raise ValueError("工天必须大于 0")
    raw_rate = data.get("daily_rate")
    if raw_rate in (None, ""):
        rate_minor = _effective_rate_minor(
            conn, int(data["worker_id"]), data["work_date"]
        )
    else:
        rate_minor = _minor(raw_rate)
    if rate_minor < 0:
        raise ValueError("日工资不能为负数")
    amount_minor = _amount_minor(work_days, rate_minor)
    return {
        "work_days": float(work_days),
        "daily_rate": rate_minor / 100,
        "amount": amount_minor / 100,
        "daily_rate_minor": rate_minor,
        "amount_minor": amount_minor,
    }


def _validate_daily_work_limit(conn, entries, exclude_log_id=None):
    """Ensure each worker's active daily total stays at or below one day.

    Multiple records on the same day are intentional: a worker may spend the
    morning at one site and the afternoon at another. Only the daily sum is a
    hard boundary. Batch entries are aggregated before any row is written so
    the whole operation remains atomic.
    """
    requested = {}
    for data, amounts in entries:
        key = (int(data["worker_id"]), data["work_date"])
        requested[key] = requested.get(key, Decimal("0")) + Decimal(
            str(amounts["work_days"])
        )

    for (worker_id, work_date), requested_days in requested.items():
        sql = """SELECT work_days
                 FROM work_logs
                 WHERE worker_id=? AND work_date=?
                   AND COALESCE(status, 'active')='active'"""
        params = [worker_id, work_date]
        if exclude_log_id is not None:
            sql += " AND id<>?"
            params.append(int(exclude_log_id))
        existing_days = sum(
            (
                Decimal(str(row["work_days"] or 0))
                for row in conn.execute(sql, params).fetchall()
            ),
            Decimal("0"),
        )
        total_days = existing_days + requested_days
        if total_days > Decimal("1"):
            worker = conn.execute(
                "SELECT name FROM workers WHERE id=?", (worker_id,)
            ).fetchone()
            worker_name = worker["name"] if worker else f"工人 {worker_id}"
            raise ValueError(
                f"{worker_name} 在 {work_date} 的有效工天合计不能超过 1；"
                f"已有 {existing_days:g}，本次 {requested_days:g}，合计 {total_days:g}"
            )


def add_work_log(data):
    data = _normalize_work_log_data(data)
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        project_id, site_id = _resolve_attribution(conn, data)
        amounts = _work_log_amounts(conn, data)
        _validate_daily_work_limit(conn, [(data, amounts)])
        now = _now()
        cursor = conn.execute(
            """INSERT INTO work_logs (
                   worker_id, work_date, construction_site, work_type,
                   work_days, is_overtime, daily_rate, amount, notes, created_at,
                   public_id, organization_id, project_id, project_site_id,
                   daily_rate_minor, amount_minor, status, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'active', ?)""",
            (
                data["worker_id"],
                data["work_date"],
                data["construction_site"],
                data.get("work_type", ""),
                amounts["work_days"],
                data["is_overtime"],
                amounts["daily_rate"],
                amounts["amount"],
                data.get("notes", ""),
                now,
                str(uuid4()),
                _organization_id(conn),
                project_id,
                site_id,
                amounts["daily_rate_minor"],
                amounts["amount_minor"],
                now,
            ),
        )
        conn.commit()
        return cursor.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def add_work_logs_batch(entries):
    if not entries:
        return 0
    entries = [_normalize_work_log_data(data) for data in entries]
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = _now()
        organization_id = _organization_id(conn)
        prepared = []
        for data in entries:
            project_id, site_id = _resolve_attribution(conn, data)
            amounts = _work_log_amounts(conn, data)
            prepared.append((data, amounts, project_id, site_id))
        _validate_daily_work_limit(
            conn, [(data, amounts) for data, amounts, _project, _site in prepared]
        )
        for data, amounts, project_id, site_id in prepared:
            conn.execute(
                """INSERT INTO work_logs (
                       worker_id, work_date, construction_site, work_type,
                       work_days, is_overtime, daily_rate, amount, notes, created_at,
                       public_id, organization_id, project_id, project_site_id,
                       daily_rate_minor, amount_minor, status, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              'active', ?)""",
                (
                    data["worker_id"],
                    data["work_date"],
                    data["construction_site"],
                    data.get("work_type", ""),
                    amounts["work_days"],
                    data["is_overtime"],
                    amounts["daily_rate"],
                    amounts["amount"],
                    data.get("notes", ""),
                    now,
                    str(uuid4()),
                    organization_id,
                    project_id,
                    site_id,
                    amounts["daily_rate_minor"],
                    amounts["amount_minor"],
                    now,
                ),
            )
        conn.commit()
        return len(entries)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_work_log(log_id, data):
    data = _normalize_work_log_data(data)
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        project_id, site_id = _resolve_attribution(conn, data)
        amounts = _work_log_amounts(conn, data)
        _validate_daily_work_limit(conn, [(data, amounts)], exclude_log_id=log_id)
        result = conn.execute(
            """UPDATE work_logs
               SET worker_id=?, work_date=?, construction_site=?,
                   work_type=?, work_days=?, is_overtime=?, daily_rate=?,
                   amount=?, notes=?,
                   project_id=?, project_site_id=?, daily_rate_minor=?,
                   amount_minor=?, updated_at=?
               WHERE id=? AND COALESCE(status, 'active')='active'
                 AND COALESCE(rate_locked, 0)=0""",
            (
                data["worker_id"],
                data["work_date"],
                data["construction_site"],
                data.get("work_type", ""),
                amounts["work_days"],
                data["is_overtime"],
                amounts["daily_rate"],
                amounts["amount"],
                data.get("notes", ""),
                project_id,
                site_id,
                amounts["daily_rate_minor"],
                amounts["amount_minor"],
                _now(),
                log_id,
            ),
        )
        if not result.rowcount:
            locked = conn.execute(
                "SELECT rate_locked FROM work_logs WHERE id=?", (log_id,)
            ).fetchone()
            if locked and locked["rate_locked"]:
                raise ValueError("工天记录的工资已锁定，请先解除锁定")
            raise ValueError("工天记录不存在或已作废")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_work_logs_overtime(log_ids, is_overtime):
    if not log_ids:
        return 0
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(log_ids))
        result = conn.execute(
            f"""UPDATE work_logs
                SET is_overtime=?, updated_at=?
                WHERE id IN ({placeholders})
                  AND COALESCE(status, 'active')='active'""",
            (1 if is_overtime else 0, _now(), *log_ids),
        )
        conn.commit()
        return result.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_work_log_project_options(include_project_id=None):
    """Return projects that can be selected when recording work days.

    Closed projects stay out of new-entry choices.  An existing work log may
    still retain its closed project while being edited, so callers can include
    that one project explicitly.
    """
    conn = get_connection()
    try:
        where_clause = "status<>'已关闭'"
        params = []
        if include_project_id:
            where_clause = "(status<>'已关闭' OR id=?)"
            params.append(int(include_project_id))
        sql = """
            SELECT id, project_code, name, status
            FROM projects
            WHERE {where_clause}
        """.format(where_clause=where_clause)
        sql += """
            ORDER BY CASE status
                         WHEN '进行中' THEN 1
                         WHEN '筹备中' THEN 2
                         WHEN '已完工' THEN 3
                         ELSE 4
                     END,
                     id DESC
        """
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def list_work_log_site_options(project_id):
    """Return only active V3 sites belonging to the selected project.

    Historical free-text work locations and legacy sites are intentionally not
    candidates.  They remain untouched on existing records.
    """
    if not project_id:
        return []
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, project_id, site_code, name
               FROM project_sites
               WHERE project_id=? AND is_active=1
               ORDER BY site_code, id""",
            (int(project_id),),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def delete_work_logs(log_ids):
    if not log_ids:
        return
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(log_ids))
        locked = conn.execute(
            f"""SELECT COUNT(*) FROM work_logs
                WHERE id IN ({placeholders}) AND COALESCE(rate_locked, 0)=1""",
            log_ids,
        ).fetchone()[0]
        if locked:
            raise ValueError(f"所选记录中有 {locked} 条工资已锁定，请先解除锁定")
        conn.execute(
            f"""UPDATE work_logs SET status='void', updated_at=?
                WHERE id IN ({placeholders})
                  AND COALESCE(status, 'active')='active'""",
            (_now(), *log_ids),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_work_log_by_id(log_id):
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT * FROM work_logs
               WHERE id=? AND COALESCE(status, 'active')='active'""",
            (log_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_work_logs(month="", keyword=""):
    conn = get_connection()
    try:
        sql = """
            SELECT wl.*, w.name AS worker_name, w.trade,
                   COALESCE(p.name, '') AS project_name
            FROM work_logs wl
            JOIN workers w ON w.id=wl.worker_id
            LEFT JOIN projects p ON p.id=wl.project_id
            WHERE COALESCE(wl.status, 'active')='active'
        """
        params = []
        if month:
            sql += " AND substr(wl.work_date, 1, 7)=?"
            params.append(month)
        if keyword:
            sql += """
                AND (w.name LIKE ? OR w.trade LIKE ?
                     OR wl.construction_site LIKE ? OR wl.work_type LIKE ?
                     OR wl.notes LIKE ? OR p.name LIKE ?)
            """
            params.extend([f"%{keyword}%"] * 6)
        sql += " ORDER BY wl.work_date DESC, wl.id DESC"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_labor_cost_summary(start_date=None, end_date=None, project_id=None):
    """Aggregate labor cost with chart-ready breakdowns and traceable rows."""
    conn = get_connection()
    try:
        conditions = ["COALESCE(wl.status, 'active')='active'"]
        params = []
        if start_date:
            conditions.append("wl.work_date>=?")
            params.append(str(start_date)[:10])
        if end_date:
            conditions.append("wl.work_date<=?")
            params.append(str(end_date)[:10])
        if project_id:
            conditions.append("wl.project_id=?")
            params.append(int(project_id))
        rows = conn.execute(
            f"""SELECT wl.id, wl.work_date, wl.construction_site,
                       COALESCE(wl.work_type, '') AS work_type,
                       wl.work_days, COALESCE(wl.is_overtime, 0) AS is_overtime,
                       COALESCE(
                           wl.daily_rate_minor,
                           CAST(ROUND(COALESCE(wl.daily_rate, 0) * 100) AS INTEGER)
                       ) AS daily_rate_minor,
                       COALESCE(
                           wl.amount_minor,
                           CAST(ROUND(COALESCE(wl.amount, 0) * 100) AS INTEGER)
                       ) AS amount_minor,
                       w.id AS worker_id, w.name AS worker_name,
                       wl.project_id, COALESCE(p.name, '待归集') AS project_name
                FROM work_logs wl
                JOIN workers w ON w.id=wl.worker_id
                LEFT JOIN projects p ON p.id=wl.project_id
                WHERE {' AND '.join(conditions)}
                ORDER BY wl.work_date DESC, wl.id DESC""",
            params,
        ).fetchall()
        details = [dict(row) for row in rows]
        by_month = {}
        by_worker = {}
        for row in details:
            amount_minor = int(row["amount_minor"] or 0)
            work_days = Decimal(str(row["work_days"] or 0))
            month = str(row["work_date"] or "")[:7]
            month_item = by_month.setdefault(
                month,
                {"month": month, "amount_minor": 0, "work_days": Decimal("0"), "record_count": 0},
            )
            month_item["amount_minor"] += amount_minor
            month_item["work_days"] += work_days
            month_item["record_count"] += 1

            worker_id = int(row["worker_id"])
            worker_item = by_worker.setdefault(
                worker_id,
                {
                    "worker_id": worker_id,
                    "worker_name": row["worker_name"],
                    "amount_minor": 0,
                    "work_days": Decimal("0"),
                    "record_count": 0,
                },
            )
            worker_item["amount_minor"] += amount_minor
            worker_item["work_days"] += work_days
            worker_item["record_count"] += 1

        def display_rows(items, sort_key):
            result = []
            for item in sorted(items, key=sort_key):
                normalized = dict(item)
                normalized["work_days"] = float(normalized["work_days"])
                result.append(normalized)
            return result

        amount_minor = sum(int(row["amount_minor"] or 0) for row in details)
        return {
            "record_count": len(details),
            "worker_count": len(by_worker),
            "work_days": float(
                sum((Decimal(str(row["work_days"] or 0)) for row in details), Decimal("0"))
            ),
            "amount_minor": amount_minor,
            "by_month": display_rows(
                by_month.values(), lambda item: item["month"]
            ),
            "by_worker": display_rows(
                by_worker.values(),
                lambda item: (-item["amount_minor"], item["worker_name"]),
            ),
            "details": details,
        }
    finally:
        conn.close()


def get_work_months():
    conn = get_connection()
    try:
        return [
            row["month"]
            for row in conn.execute(
                """SELECT DISTINCT substr(work_date, 1, 7) AS month
                   FROM work_logs
                   WHERE length(work_date)>=7
                     AND COALESCE(status, 'active')='active'
                   ORDER BY month DESC"""
            ).fetchall()
            if row["month"]
        ]
    finally:
        conn.close()


def _adjustment_parameters(data):
    worker_id = int(data.get("worker_id") or 0)
    if not worker_id:
        raise ValueError("请选择工人")
    raw_new_rate = data.get("new_daily_rate")
    if raw_new_rate in (None, ""):
        raise ValueError("请填写新日工资")
    try:
        new_rate_minor = _minor(raw_new_rate)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("新日工资必须是有效数字") from error
    if new_rate_minor < 0:
        raise ValueError("新日工资不能为负数")
    effective = _iso_date(data.get("effective_from"), "生效日期")
    today = date.today()
    if effective > today:
        raise ValueError("当前版本暂不允许未来生效日期")
    mode = data.get("scope_mode") or "future_only"
    if mode not in RATE_ADJUSTMENT_MODES:
        raise ValueError("调薪影响范围无效")
    project_id = int(data.get("project_id") or 0) or None
    if mode == "future_only":
        range_end = None
        project_id = None
    elif mode == "through_today":
        range_end = today
        project_id = None
    else:
        range_end = _iso_date(data.get("range_end"), "截止日期")
        if range_end < effective:
            raise ValueError("截止日期不能早于生效日期")
        if range_end > today:
            raise ValueError("截止日期不能晚于今天")
    reason = (data.get("reason") or "").strip()
    if not reason:
        raise ValueError("请填写调薪原因")
    return {
        "worker_id": worker_id,
        "new_rate_minor": new_rate_minor,
        "effective_from": effective,
        "range_end": range_end,
        "scope_mode": mode,
        "project_id": project_id,
        "reason": reason,
    }


def _preview_rate_adjustment(conn, data):
    params = _adjustment_parameters(data)
    worker = conn.execute(
        "SELECT id, name, daily_rate FROM workers WHERE id=?",
        (params["worker_id"],),
    ).fetchone()
    if not worker:
        raise ValueError("工人不存在")
    latest_version = conn.execute(
        """SELECT id, effective_from, rate_minor, source
           FROM worker_rate_versions
           WHERE worker_id=? AND status='active'
           ORDER BY effective_from DESC, id DESC LIMIT 1""",
        (params["worker_id"],),
    ).fetchone()
    if (
        latest_version
        and params["effective_from"].isoformat() < latest_version["effective_from"]
    ):
        raise ValueError(
            f"已有 {latest_version['effective_from']} 生效的较新工资版本，"
            "不能再插入更早版本；请调整生效日期"
        )

    rows = []
    if params["scope_mode"] != "future_only":
        sql = """SELECT wl.id, wl.worker_id, wl.work_date, wl.work_days,
                        wl.daily_rate, wl.amount, wl.daily_rate_minor,
                        wl.amount_minor, wl.project_id, wl.rate_locked,
                        COALESCE(p.name, wl.construction_site, '未归集项目')
                            AS project_name
                 FROM work_logs wl
                 LEFT JOIN projects p ON p.id=wl.project_id
                 WHERE wl.worker_id=?
                   AND COALESCE(wl.status, 'active')='active'
                   AND wl.work_date>=? AND wl.work_date<=?"""
        query_params = [
            params["worker_id"],
            params["effective_from"].isoformat(),
            params["range_end"].isoformat(),
        ]
        if params["project_id"]:
            sql += " AND wl.project_id=?"
            query_params.append(params["project_id"])
        sql += " ORDER BY wl.work_date, wl.id"
        rows = conn.execute(sql, query_params).fetchall()

    locked_count = 0
    unchanged_count = 0
    changed_rows = []
    project_impacts = {}
    total_days = Decimal("0")
    old_total = 0
    new_total = 0
    for row in rows:
        if row["rate_locked"]:
            locked_count += 1
            continue
        old_rate_minor = (
            int(row["daily_rate_minor"])
            if row["daily_rate_minor"] is not None
            else _minor(row["daily_rate"] or 0)
        )
        old_amount_minor = (
            int(row["amount_minor"])
            if row["amount_minor"] is not None
            else _minor(row["amount"] or 0)
        )
        new_amount_minor = _amount_minor(
            row["work_days"], params["new_rate_minor"]
        )
        if (
            old_rate_minor == params["new_rate_minor"]
            and old_amount_minor == new_amount_minor
        ):
            unchanged_count += 1
            continue
        item = {
            "work_log_id": row["id"],
            "work_date": row["work_date"],
            "project_id": row["project_id"],
            "project_name": row["project_name"],
            "work_days": float(row["work_days"] or 0),
            "old_daily_rate_minor": old_rate_minor,
            "new_daily_rate_minor": params["new_rate_minor"],
            "old_amount_minor": old_amount_minor,
            "new_amount_minor": new_amount_minor,
            "delta_minor": new_amount_minor - old_amount_minor,
        }
        changed_rows.append(item)
        days = Decimal(str(row["work_days"] or 0))
        total_days += days
        old_total += old_amount_minor
        new_total += new_amount_minor
        project = project_impacts.setdefault(
            (row["project_id"], row["project_name"]),
            {
                "project_id": row["project_id"],
                "project_name": row["project_name"],
                "record_count": 0,
                "work_days": Decimal("0"),
                "old_amount_minor": 0,
                "new_amount_minor": 0,
                "delta_minor": 0,
            },
        )
        project["record_count"] += 1
        project["work_days"] += days
        project["old_amount_minor"] += old_amount_minor
        project["new_amount_minor"] += new_amount_minor
        project["delta_minor"] += new_amount_minor - old_amount_minor

    impacts = []
    for project in project_impacts.values():
        project = dict(project)
        project["work_days"] = float(project["work_days"])
        impacts.append(project)
    impacts.sort(key=lambda item: (item["project_name"], item["project_id"] or 0))
    return {
        "worker": dict(worker),
        "current_rate_minor": _minor(worker["daily_rate"] or 0),
        "new_rate_minor": params["new_rate_minor"],
        "effective_from": params["effective_from"].isoformat(),
        "range_end": (
            params["range_end"].isoformat() if params["range_end"] else None
        ),
        "scope_mode": params["scope_mode"],
        "scope_label": RATE_ADJUSTMENT_MODES[params["scope_mode"]],
        "project_id": params["project_id"],
        "reason": params["reason"],
        "candidate_count": len(rows),
        "affected_count": len(changed_rows),
        "skipped_locked_count": locked_count,
        "unchanged_count": unchanged_count,
        "total_days": float(total_days),
        "old_amount_minor": old_total,
        "new_amount_minor": new_total,
        "delta_minor": new_total - old_total,
        "project_impacts": impacts,
        "_rows": changed_rows,
    }


def preview_rate_adjustment(data):
    conn = get_connection()
    try:
        preview = _preview_rate_adjustment(conn, data)
        preview.pop("_rows", None)
        return preview
    finally:
        conn.close()


def apply_rate_adjustment(data):
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        preview = _preview_rate_adjustment(conn, data)
        now = _now()
        effective_from = preview["effective_from"]
        latest = conn.execute(
            """SELECT id, effective_from FROM worker_rate_versions
               WHERE worker_id=? AND status='active'
               ORDER BY effective_from DESC, id DESC LIMIT 1""",
            (preview["worker"]["id"],),
        ).fetchone()
        if latest:
            if latest["effective_from"] == effective_from:
                conn.execute(
                    """UPDATE worker_rate_versions
                       SET status='superseded', effective_to=? WHERE id=?""",
                    (effective_from, latest["id"]),
                )
            else:
                prior_end = (
                    datetime.strptime(effective_from, "%Y-%m-%d").date()
                    - timedelta(days=1)
                ).isoformat()
                conn.execute(
                    "UPDATE worker_rate_versions SET effective_to=? WHERE id=?",
                    (prior_end, latest["id"]),
                )
        conn.execute(
            """INSERT INTO worker_rate_versions (
                   public_id, worker_id, rate_minor, effective_from,
                   reason, source, status, created_at
               ) VALUES (?, ?, ?, ?, ?, 'adjustment', 'active', ?)""",
            (
                str(uuid4()),
                preview["worker"]["id"],
                preview["new_rate_minor"],
                effective_from,
                preview["reason"],
                now,
            ),
        )
        conn.execute(
            "UPDATE workers SET daily_rate=? WHERE id=?",
            (preview["new_rate_minor"] / 100, preview["worker"]["id"]),
        )
        cursor = conn.execute(
            """INSERT INTO labor_rate_adjustments (
                   public_id, worker_id, old_rate_minor, new_rate_minor,
                   effective_from, range_end, scope_mode, project_id, reason,
                   affected_count, skipped_locked_count, total_days,
                   old_amount_minor, new_amount_minor, delta_minor,
                   status, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                         'applied', ?)""",
            (
                str(uuid4()),
                preview["worker"]["id"],
                preview["current_rate_minor"],
                preview["new_rate_minor"],
                effective_from,
                preview["range_end"],
                preview["scope_mode"],
                preview["project_id"],
                preview["reason"],
                preview["affected_count"],
                preview["skipped_locked_count"],
                preview["total_days"],
                preview["old_amount_minor"],
                preview["new_amount_minor"],
                preview["delta_minor"],
                now,
            ),
        )
        adjustment_id = cursor.lastrowid
        for row in preview["_rows"]:
            conn.execute(
                """INSERT INTO labor_rate_adjustment_items (
                       adjustment_id, work_log_id, project_id,
                       old_daily_rate_minor, new_daily_rate_minor,
                       old_amount_minor, new_amount_minor, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    adjustment_id,
                    row["work_log_id"],
                    row["project_id"],
                    row["old_daily_rate_minor"],
                    row["new_daily_rate_minor"],
                    row["old_amount_minor"],
                    row["new_amount_minor"],
                    now,
                ),
            )
            conn.execute(
                """UPDATE work_logs
                   SET daily_rate=?, amount=?, daily_rate_minor=?, amount_minor=?,
                       updated_at=?
                   WHERE id=? AND COALESCE(rate_locked, 0)=0""",
                (
                    row["new_daily_rate_minor"] / 100,
                    row["new_amount_minor"] / 100,
                    row["new_daily_rate_minor"],
                    row["new_amount_minor"],
                    now,
                    row["work_log_id"],
                ),
            )
        conn.commit()
        result = dict(preview)
        result.pop("_rows", None)
        result["adjustment_id"] = adjustment_id
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_rate_adjustments(worker_id, limit=20):
    conn = get_connection()
    try:
        return [
            dict(row)
            for row in conn.execute(
                """SELECT lra.*, p.name AS project_name
                   FROM labor_rate_adjustments lra
                   LEFT JOIN projects p ON p.id=lra.project_id
                   WHERE lra.worker_id=?
                   ORDER BY lra.created_at DESC, lra.id DESC LIMIT ?""",
                (int(worker_id), int(limit)),
            ).fetchall()
        ]
    finally:
        conn.close()


def set_work_logs_rate_locked(log_ids, locked, reason=""):
    if not log_ids:
        return 0
    ids = [int(value) for value in log_ids]
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(
            f"""SELECT id, COALESCE(rate_locked, 0) AS rate_locked
                FROM work_logs WHERE id IN ({placeholders})
                  AND COALESCE(status, 'active')='active'""",
            ids,
        ).fetchall()
        target = 1 if locked else 0
        changed = [row["id"] for row in rows if row["rate_locked"] != target]
        now = _now()
        for log_id in changed:
            conn.execute(
                """UPDATE work_logs
                   SET rate_locked=?, rate_lock_reason=?, rate_locked_at=?,
                       updated_at=? WHERE id=?""",
                (
                    target,
                    (reason or "手动锁定").strip() if locked else None,
                    now if locked else None,
                    now,
                    log_id,
                ),
            )
            conn.execute(
                """INSERT INTO labor_rate_lock_events (
                       public_id, work_log_id, action, reason, created_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (
                    str(uuid4()),
                    log_id,
                    "lock" if locked else "unlock",
                    (reason or "手动操作").strip(),
                    now,
                ),
            )
        conn.commit()
        return len(changed)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_work_dashboard(month):
    conn = get_connection()
    try:
        params = (month,)
        summary = conn.execute(
            """SELECT COALESCE(SUM(work_days), 0) AS total_days,
                      COALESCE(SUM(CASE WHEN COALESCE(is_overtime, 0)=1
                                        THEN work_days ELSE 0 END), 0)
                          AS overtime_days,
                      COALESCE(SUM(CASE WHEN COALESCE(is_overtime, 0)=1
                                        THEN 1 ELSE 0 END), 0)
                          AS overtime_record_count,
                      COALESCE(SUM(COALESCE(
                          amount_minor / 100.0, amount
                      )), 0) AS total_amount,
                      COUNT(DISTINCT worker_id) AS worker_count,
                      COUNT(DISTINCT construction_site) AS site_count,
                      COUNT(*) AS record_count
               FROM work_logs
               WHERE substr(work_date, 1, 7)=?
                 AND COALESCE(status, 'active')='active'""",
            params,
        ).fetchone()
        by_worker = conn.execute(
            """SELECT w.id, w.name, w.trade,
                      ROUND(SUM(wl.work_days), 2) AS work_days,
                      ROUND(SUM(CASE WHEN COALESCE(wl.is_overtime, 0)=1
                                     THEN wl.work_days ELSE 0 END), 2)
                          AS overtime_days,
                      ROUND(SUM(COALESCE(
                          wl.amount_minor / 100.0, wl.amount
                      )), 2) AS amount,
                      COUNT(DISTINCT wl.construction_site) AS site_count
               FROM work_logs wl JOIN workers w ON w.id=wl.worker_id
               WHERE substr(wl.work_date, 1, 7)=?
                 AND COALESCE(wl.status, 'active')='active'
               GROUP BY w.id, w.name, w.trade
               ORDER BY work_days DESC, amount DESC""",
            params,
        ).fetchall()
        by_site = conn.execute(
            """SELECT construction_site,
                      ROUND(SUM(work_days), 2) AS work_days,
                      ROUND(SUM(CASE WHEN COALESCE(is_overtime, 0)=1
                                     THEN work_days ELSE 0 END), 2)
                          AS overtime_days,
                      ROUND(SUM(COALESCE(
                          amount_minor / 100.0, amount
                      )), 2) AS amount,
                      COUNT(DISTINCT worker_id) AS worker_count
               FROM work_logs
               WHERE substr(work_date, 1, 7)=?
                 AND COALESCE(status, 'active')='active'
               GROUP BY construction_site
               ORDER BY work_days DESC, amount DESC""",
            params,
        ).fetchall()
        return {
            "summary": dict(summary),
            "by_worker": [dict(row) for row in by_worker],
            "by_site": [dict(row) for row in by_site],
        }
    finally:
        conn.close()


def get_construction_sites(active_only=True):
    from services.construction_service import get_construction_sites as _list_sites
    return _list_sites(active_only)
