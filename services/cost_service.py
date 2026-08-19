from services._common import now as _now, organization_id as _organization_id, minor as _minor
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from db.connection import get_connection


COST_CATEGORIES = [
    "用车",
    "饮食",
    "房租",
    "水电煤",
    "机械费",
]

# 历史记录和旧导入仍可识别；新界面只提供上面的日常经营费用大类。
LEGACY_COST_CATEGORIES = {
    "分包费",
    "运输费",
    "车辆燃油费",
    "设备租赁费",
    "差旅现场费",
    "管理分摊",
    "其他成本",
}
SUPPORTED_COST_CATEGORIES = set(COST_CATEGORIES) | LEGACY_COST_CATEGORIES


def _date(value):
    value = (value or "").strip()
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError("日期必须使用 YYYY-MM-DD 格式")
    return value


def _number(prefix):
    return f"{prefix}-{datetime.now():%Y%m%d}-{uuid4().hex[:6].upper()}"


ALLOCATION_METHODS = {
    "unassigned": "暂不归集",
    "direct": "单项目归集",
    "equal": "多项目均摊",
    "manual": "手工金额分摊",
}


def build_allocation_plan(total_amount, method, project_ids=None, allocations=None):
    """Validate and return a cent-exact project allocation plan."""
    if method == "unassigned":
        return []
    if method not in ("direct", "equal", "manual"):
        raise ValueError("成本归集方式无效")

    total_minor = _minor(total_amount)
    if any(not value for value in (project_ids or [])):
        raise ValueError("请选择要承担成本的项目")
    project_ids = [int(value) for value in (project_ids or [])]
    if len(project_ids) != len(set(project_ids)):
        raise ValueError("同一个项目不能重复选择")

    if method == "direct":
        if len(project_ids) != 1:
            raise ValueError("单项目归集必须选择一个项目")
        return [{"project_id": project_ids[0], "amount_minor": total_minor}]

    if method == "equal":
        if len(project_ids) < 2:
            raise ValueError("多项目均摊至少选择两个项目")
        base, remainder = divmod(total_minor, len(project_ids))
        if base <= 0:
            raise ValueError("费用金额不足以分摊到所选项目")
        return [
            {
                "project_id": project_id,
                "amount_minor": base + (1 if index < remainder else 0),
            }
            for index, project_id in enumerate(project_ids)
        ]

    normalized = []
    for item in allocations or []:
        project_id = int(item.get("project_id") or 0)
        if not project_id:
            raise ValueError("手工分摊必须选择项目")
        amount_minor = _minor(item.get("amount"))
        normalized.append(
            {"project_id": project_id, "amount_minor": amount_minor}
        )
    if not normalized:
        raise ValueError("请至少填写一个项目的分摊金额")
    if len(normalized) != len({item["project_id"] for item in normalized}):
        raise ValueError("同一个项目不能重复分摊")
    allocated_minor = sum(item["amount_minor"] for item in normalized)
    if allocated_minor != total_minor:
        difference = (total_minor - allocated_minor) / 100
        raise ValueError(f"分摊合计必须等于原费用金额，当前相差 {difference:+.2f} 元")
    return normalized


def _replace_allocations(conn, cost_entry_id, method, plan, now):
    cost = conn.execute(
        "SELECT amount_minor, status FROM cost_entries WHERE id=?",
        (cost_entry_id,),
    ).fetchone()
    if not cost or cost["status"] != "active":
        raise ValueError("成本记录不存在或已作废")
    if not plan:
        conn.execute(
            """UPDATE cost_allocation_lines
               SET status='void', voided_at=?, updated_at=?
               WHERE cost_entry_id=? AND status='active'""",
            (now, now, cost_entry_id),
        )
        conn.execute(
            """UPDATE cost_entries
               SET project_id=NULL, allocation_status='unassigned', updated_at=?
               WHERE id=?""",
            (now, cost_entry_id),
        )
        return

    if sum(item["amount_minor"] for item in plan) != cost["amount_minor"]:
        raise ValueError("分摊合计必须等于原费用金额")
    project_ids = [item["project_id"] for item in plan]
    placeholders = ",".join("?" * len(project_ids))
    existing_projects = {
        row[0]
        for row in conn.execute(
            f"SELECT id FROM projects WHERE id IN ({placeholders})", project_ids
        ).fetchall()
    }
    if existing_projects != set(project_ids):
        raise ValueError("所选项目中包含已不存在的项目")

    version = conn.execute(
        """SELECT COALESCE(MAX(allocation_version), 0) + 1
           FROM cost_allocation_lines WHERE cost_entry_id=?""",
        (cost_entry_id,),
    ).fetchone()[0]
    conn.execute(
        """UPDATE cost_allocation_lines
           SET status='void', voided_at=?, updated_at=?
           WHERE cost_entry_id=? AND status='active'""",
        (now, now, cost_entry_id),
    )
    for item in plan:
        conn.execute(
            """INSERT INTO cost_allocation_lines (
                   public_id, cost_entry_id, project_id, amount_minor,
                   allocation_method, allocation_version, status,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                str(uuid4()),
                cost_entry_id,
                item["project_id"],
                item["amount_minor"],
                method,
                version,
                now,
                now,
            ),
        )
    conn.execute(
        """UPDATE cost_entries
           SET project_id=NULL, allocation_status='assigned', updated_at=?
           WHERE id=?""",
        (now, cost_entry_id),
    )


def list_cost_entries(project_id=None, include_unassigned=True):
    conn = get_connection()
    try:
        sql = """
            SELECT ce.*,
                   CASE
                     WHEN ce.project_id IS NOT NULL THEN p.name
                     WHEN COUNT(cal.id)=0 THEN '待归集'
                     WHEN COUNT(cal.id)=1 THEN MAX(ap.name)
                     ELSE CAST(COUNT(cal.id) AS TEXT) || '个项目分摊'
                   END AS project_name,
                   COALESCE(SUM(cal.amount_minor),
                            CASE WHEN ce.project_id IS NOT NULL
                                 THEN ce.amount_minor ELSE 0 END)
                       AS allocated_amount_minor
            FROM cost_entries ce
            LEFT JOIN projects p ON p.id=ce.project_id
            LEFT JOIN cost_allocation_lines cal
              ON cal.cost_entry_id=ce.id AND cal.status='active'
            LEFT JOIN projects ap ON ap.id=cal.project_id
            WHERE ce.status='active'
        """
        params = []
        if project_id:
            sql += """ AND (
                ce.project_id=? OR EXISTS (
                    SELECT 1 FROM cost_allocation_lines match_line
                    WHERE match_line.cost_entry_id=ce.id
                      AND match_line.project_id=? AND match_line.status='active'
                )
            )"""
            params.extend((project_id, project_id))
        elif not include_unassigned:
            sql += " AND (ce.project_id IS NOT NULL OR ce.allocation_status='assigned')"
        sql += " GROUP BY ce.id ORDER BY ce.cost_date DESC, ce.id DESC"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def list_cost_ledger(project_id=None):
    conn = get_connection()
    try:
        if project_id:
            purchase_sql = """SELECT po.id, ppc.project_id,
                       po.purchase_date AS business_date,
                       po.order_no AS source_no, '采购成本' AS category,
                       po.merchant_name_snapshot AS counterparty,
                       ppc.cost_minor AS amount_minor,
                       'purchase' AS source_type,
                       p.name AS allocation_project_names
                FROM purchase_project_costs ppc
                JOIN purchase_orders po ON po.id=ppc.purchase_order_id
                JOIN projects p ON p.id=ppc.project_id
                WHERE po.status='有效' AND ppc.project_id=?"""
            purchase_params = (project_id,)
        else:
            purchase_sql = """SELECT po.id, po.project_id,
                       po.purchase_date AS business_date,
                       po.order_no AS source_no, '采购成本' AS category,
                       po.merchant_name_snapshot AS counterparty,
                       po.total_amount_cents AS amount_minor,
                       'purchase' AS source_type,
                       CASE
                         WHEN po.project_id IS NOT NULL THEN p.name
                         WHEN COUNT(pal.id)=0 THEN '待归集'
                         WHEN COUNT(pal.id)=1 THEN MAX(ap.name)
                         ELSE COUNT(pal.id) || '个项目均摊'
                       END AS allocation_project_names
                FROM purchase_orders po
                LEFT JOIN projects p ON p.id=po.project_id
                LEFT JOIN purchase_cost_allocation_lines pal
                  ON pal.purchase_order_id=po.id AND pal.status='active'
                LEFT JOIN projects ap ON ap.id=pal.project_id
                WHERE po.status='有效'
                GROUP BY po.id"""
            purchase_params = ()
        rows = []
        for row in conn.execute(purchase_sql, purchase_params).fetchall():
            rows.append(dict(row))

        labor_params = [project_id] if project_id else []
        labor_filter = " AND wl.project_id=?" if project_id else ""
        for row in conn.execute(
            f"""SELECT wl.id, wl.project_id, wl.work_date AS business_date,
                       'GT-' || printf('%06d', wl.id) AS source_no,
                       '人工成本' AS category, w.name AS counterparty,
                       COALESCE(
                           wl.amount_minor,
                           CAST(ROUND(COALESCE(wl.amount, 0) * 100) AS INTEGER)
                       ) AS amount_minor,
                       'labor' AS source_type
                FROM work_logs wl
                JOIN workers w ON w.id=wl.worker_id
                WHERE COALESCE(wl.status, 'active')='active'{labor_filter}""",
            labor_params,
        ).fetchall():
            rows.append(dict(row))

        if project_id:
            manual_sql = """
                SELECT ce.id, cal.project_id,
                       ce.cost_date AS business_date, ce.cost_no AS source_no,
                       ce.category, ce.counterparty_name_snapshot AS counterparty,
                       ce.vehicle_no, cal.amount_minor, 'manual' AS source_type,
                       p.name AS allocation_project_names
                FROM cost_entries ce
                JOIN cost_allocation_lines cal
                  ON cal.cost_entry_id=ce.id AND cal.status='active'
                JOIN projects p ON p.id=cal.project_id
                WHERE ce.status='active' AND cal.project_id=?
                UNION ALL
                SELECT ce.id, ce.project_id, ce.cost_date, ce.cost_no,
                       ce.category, ce.counterparty_name_snapshot,
                       ce.vehicle_no, ce.amount_minor, 'manual', p.name
                FROM cost_entries ce
                JOIN projects p ON p.id=ce.project_id
                WHERE ce.status='active' AND ce.project_id=?
                  AND NOT EXISTS (
                    SELECT 1 FROM cost_allocation_lines cal
                    WHERE cal.cost_entry_id=ce.id AND cal.status='active'
                  )
            """
            manual_params = (project_id, project_id)
        else:
            manual_sql = """
                SELECT ce.id,
                       CASE WHEN ce.project_id IS NOT NULL THEN ce.project_id
                            WHEN COUNT(cal.id)=1 THEN MAX(cal.project_id)
                            ELSE NULL END AS project_id,
                       ce.cost_date AS business_date, ce.cost_no AS source_no,
                       ce.category, ce.counterparty_name_snapshot AS counterparty,
                       ce.vehicle_no, ce.amount_minor, 'manual' AS source_type,
                       CASE WHEN ce.project_id IS NOT NULL THEN MAX(lp.name)
                            WHEN COUNT(cal.id)=0 THEN '待归集'
                            WHEN COUNT(cal.id)=1 THEN MAX(ap.name)
                            ELSE GROUP_CONCAT(ap.name, '、') END
                         AS allocation_project_names
                FROM cost_entries ce
                LEFT JOIN projects lp ON lp.id=ce.project_id
                LEFT JOIN cost_allocation_lines cal
                  ON cal.cost_entry_id=ce.id AND cal.status='active'
                LEFT JOIN projects ap ON ap.id=cal.project_id
                WHERE ce.status='active'
                GROUP BY ce.id
            """
            manual_params = ()
        for row in conn.execute(manual_sql, manual_params).fetchall():
            rows.append(dict(row))
        rows.sort(
            key=lambda item: (item["business_date"] or "", item["id"]),
            reverse=True,
        )
        return rows
    finally:
        conn.close()


def create_cost(data):
    project_id = int(data.get("project_id") or 0) or None
    category = (data.get("category") or "").strip()
    if category not in SUPPORTED_COST_CATEGORIES:
        raise ValueError("成本分类无效")
    conn = get_connection()
    try:
        now = _now()
        amount_minor = _minor(data.get("amount"))
        method = data.get("allocation_method")
        if not method:
            method = "direct" if project_id else "unassigned"
        if method == "manual":
            plan = build_allocation_plan(
                data.get("amount"), method, allocations=data.get("allocations")
            )
        else:
            selected_projects = data.get("project_ids") or (
                [project_id] if project_id else []
            )
            plan = build_allocation_plan(
                data.get("amount"), method, project_ids=selected_projects
            )
        cursor = conn.execute(
            """INSERT INTO cost_entries (
                   public_id, organization_id, cost_no, project_id, cost_date,
                   category, amount_minor, counterparty_name_snapshot,
                   source_type, allocation_status, notes, vehicle_no, status,
                   created_at, updated_at
               ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, 'manual', ?, ?, ?, 'active',
                         ?, ?)""",
            (
                str(uuid4()),
                _organization_id(conn),
                (data.get("cost_no") or "").strip() or _number("CB"),
                _date(data.get("cost_date")),
                category,
                amount_minor,
                (data.get("counterparty_name") or "").strip(),
                "assigned" if plan else "unassigned",
                (data.get("notes") or "").strip(),
                (data.get("vehicle_no") or "").strip(),
                now,
                now,
            ),
        )
        _replace_allocations(conn, cursor.lastrowid, method, plan, now)
        conn.commit()
        return cursor.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def assign_costs(cost_ids, project_id):
    if not cost_ids:
        return
    for cost_id in cost_ids:
        allocate_cost(cost_id, "direct", project_ids=[project_id])


def get_cost_entry(cost_id):
    rows = list_cost_entries()
    return next((row for row in rows if row["id"] == int(cost_id)), None)


def get_cost_allocations(cost_id):
    conn = get_connection()
    try:
        cost = conn.execute(
            """SELECT id, project_id, allocation_status
               FROM cost_entries WHERE id=? AND status='active'""",
            (cost_id,),
        ).fetchone()
        if not cost:
            raise ValueError("成本记录不存在或已作废")
        rows = [
            dict(row)
            for row in conn.execute(
                """SELECT project_id, amount_minor, allocation_method,
                          allocation_version
                   FROM cost_allocation_lines
                   WHERE cost_entry_id=? AND status='active'
                   ORDER BY id""",
                (cost_id,),
            ).fetchall()
        ]
        if rows:
            return {"method": rows[0]["allocation_method"], "lines": rows}
        if cost["project_id"]:
            amount_minor = conn.execute(
                "SELECT amount_minor FROM cost_entries WHERE id=?", (cost_id,)
            ).fetchone()[0]
            return {
                "method": "direct",
                "lines": [
                    {
                        "project_id": cost["project_id"],
                        "amount_minor": amount_minor,
                        "allocation_method": "direct",
                        "allocation_version": 0,
                    }
                ],
            }
        return {"method": "unassigned", "lines": []}
    finally:
        conn.close()


def allocate_cost(cost_id, method, project_ids=None, allocations=None):
    conn = get_connection()
    try:
        cost = conn.execute(
            "SELECT amount_minor FROM cost_entries WHERE id=? AND status='active'",
            (cost_id,),
        ).fetchone()
        if not cost:
            raise ValueError("成本记录不存在或已作废")
        amount = Decimal(cost["amount_minor"]) / 100
        plan = build_allocation_plan(
            amount,
            method,
            project_ids=project_ids,
            allocations=allocations,
        )
        _replace_allocations(conn, int(cost_id), method, plan, _now())
        conn.commit()
        return plan
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def void_costs(cost_ids):
    _void("cost_entries", cost_ids)


def _void(table, ids):
    if not ids:
        return
    if table != "cost_entries":
        raise ValueError("台账类型无效")
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(ids))
        now = _now()
        if table == "cost_entries":
            conn.execute(
                f"""UPDATE cost_allocation_lines
                    SET status='void', voided_at=?, updated_at=?
                    WHERE cost_entry_id IN ({placeholders})
                      AND status='active'""",
                (now, now, *ids),
            )
        conn.execute(
            f"""UPDATE {table} SET status='void', updated_at=?
                WHERE id IN ({placeholders}) AND status='active'""",
            (now, *ids),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _month_bounds(month):
    year, month_number = map(int, month.split("-"))
    start = f"{year:04d}-{month_number:02d}-01"
    if month_number == 12:
        end = f"{year + 1:04d}-01-01"
    else:
        end = f"{year:04d}-{month_number + 1:02d}-01"
    return start, end

def get_cost_dashboard(month=None, project_id=None):
    """Dashboard aggregates: KPI totals, composition and project ranking.

    Costs are unified in minor units: purchase orders (total_amount_cents),
    work logs (amount_minor with REAL fallback) and manual cost allocations.
    """
    month = month or datetime.now().strftime("%Y-%m")
    month_start, next_month = _month_bounds(month)
    conn = get_connection()
    try:
        project_filter = " AND project_id=?" if project_id else ""
        params = [month_start, next_month] + ([project_id] if project_id else [])

        if project_id:
            purchase = conn.execute(
                """SELECT COUNT(DISTINCT po.id) AS count,
                          COALESCE(SUM(ppc.cost_minor), 0) AS amount_minor
                   FROM purchase_project_costs ppc
                   JOIN purchase_orders po ON po.id=ppc.purchase_order_id
                   WHERE po.status='有效'
                     AND po.purchase_date >= ? AND po.purchase_date < ?
                     AND ppc.project_id=?""",
                params,
            ).fetchone()
        else:
            purchase = conn.execute(
                """SELECT COUNT(*) AS count,
                          COALESCE(SUM(total_amount_cents), 0) AS amount_minor
                   FROM purchase_orders
                   WHERE status='有效'
                     AND purchase_date >= ? AND purchase_date < ?""",
                params,
            ).fetchone()

        labor = conn.execute(
            f"""SELECT COUNT(*) AS count,
                       COALESCE(SUM(COALESCE(
                           amount_minor,
                           CAST(ROUND(COALESCE(amount, 0) * 100) AS INTEGER)
                       )), 0) AS amount_minor
                FROM work_logs
                WHERE COALESCE(status, 'active')='active'
                  AND work_date >= ? AND work_date < ?
                  {project_filter}""",
            params,
        ).fetchone()

        manual = conn.execute(
            f"""SELECT COUNT(DISTINCT ce.id) AS count,
                       COALESCE(SUM(cal.amount_minor), 0) AS amount_minor
                FROM cost_entries ce
                JOIN cost_allocation_lines cal
                  ON cal.cost_entry_id=ce.id AND cal.status='active'
                WHERE ce.status='active'
                  AND ce.cost_date >= ? AND ce.cost_date < ?
                  {'AND cal.project_id=?' if project_id else ''}""",
            params,
        ).fetchone()

        if not project_id:
            unassigned = conn.execute(
                """SELECT COALESCE(SUM(amount_minor), 0) AS amount_minor,
                          COUNT(*) AS count
                   FROM cost_entries
                   WHERE status='active'
                     AND cost_date >= ? AND cost_date < ?
                     AND project_id IS NULL
                     AND NOT EXISTS (
                       SELECT 1 FROM cost_allocation_lines cal
                       WHERE cal.cost_entry_id=cost_entries.id AND cal.status='active'
                     )""",
                (month_start, next_month),
            ).fetchone()
        else:
            unassigned = None

        if unassigned is not None:
            manual_amount = manual["amount_minor"] + unassigned["amount_minor"]
            manual_count = manual["count"] + unassigned["count"]
        else:
            manual_amount, manual_count = manual["amount_minor"], manual["count"]
        total_minor = purchase["amount_minor"] + labor["amount_minor"] + manual_amount

        by_source = [
            ("采购", purchase["amount_minor"]),
            ("人工", labor["amount_minor"]),
            ("手工/其他", manual_amount),
        ]

        purchase_allocation_join = (
            "JOIN purchase_project_costs ppc ON ppc.purchase_order_id=po.id"
            if project_id
            else ""
        )
        purchase_amount_column = (
            "ppc.tax_inclusive_material_minor"
            if project_id
            else "poi.line_amount_cents"
        )
        purchase_project_filter = "AND ppc.project_id=?" if project_id else ""
        category_params = params + params + params
        category_rows = conn.execute(
            f"""SELECT '材料费' AS category,
                       COALESCE(SUM({purchase_amount_column}), 0) AS amount_minor
                FROM purchase_order_items poi
                JOIN purchase_orders po ON po.id=poi.purchase_order_id
                {purchase_allocation_join}
                WHERE po.status='有效'
                  AND po.purchase_date >= ? AND po.purchase_date < ?
                  {purchase_project_filter}
                UNION ALL
                SELECT '人工成本' AS category,
                       COALESCE(SUM(COALESCE(
                           wl.amount_minor,
                           CAST(ROUND(COALESCE(wl.amount, 0) * 100) AS INTEGER)
                       )), 0) AS amount_minor
                FROM work_logs wl
                WHERE COALESCE(wl.status, 'active')='active'
                  AND wl.work_date >= ? AND wl.work_date < ?
                  {'AND wl.project_id=?' if project_id else ''}
                UNION ALL
                SELECT ce.category AS category,
                       COALESCE(SUM(cal.amount_minor), 0) AS amount_minor
                FROM cost_entries ce
                JOIN cost_allocation_lines cal
                  ON cal.cost_entry_id=ce.id AND cal.status='active'
                WHERE ce.status='active'
                  AND ce.cost_date >= ? AND ce.cost_date < ?
                  {'AND cal.project_id=?' if project_id else ''}
                GROUP BY ce.category""",
            category_params,
        ).fetchall()
        if unassigned is not None and unassigned["amount_minor"] > 0:
            category_rows = list(category_rows) + [
                {"category": "待归集", "amount_minor": unassigned["amount_minor"]}
            ]
        by_category = [
            (row["category"], row["amount_minor"])
            for row in category_rows
            if row["amount_minor"] > 0
        ]

        project_params = [month_start, next_month] * 3 + (
            [project_id] if project_id else []
        )
        project_rows = conn.execute(
            f"""SELECT project_id, SUM(amount_minor) AS amount_minor FROM (
                    SELECT ppc.project_id, ppc.cost_minor AS amount_minor
                    FROM purchase_project_costs ppc
                    JOIN purchase_orders po ON po.id=ppc.purchase_order_id
                    WHERE po.status='有效'
                      AND po.purchase_date >= ? AND po.purchase_date < ?
                    UNION ALL
                    SELECT project_id, COALESCE(
                        amount_minor,
                        CAST(ROUND(COALESCE(amount, 0) * 100) AS INTEGER)
                    ) AS amount_minor
                    FROM work_logs
                    WHERE COALESCE(status, 'active')='active'
                      AND work_date >= ? AND work_date < ?
                    UNION ALL
                    SELECT cal.project_id, cal.amount_minor
                    FROM cost_entries ce
                    JOIN cost_allocation_lines cal
                      ON cal.cost_entry_id=ce.id AND cal.status='active'
                    WHERE ce.status='active'
                      AND ce.cost_date >= ? AND ce.cost_date < ?
                ) unified
                WHERE project_id IS NOT NULL {project_filter}
                GROUP BY project_id
                ORDER BY amount_minor DESC""",
            project_params,
        ).fetchall()
        project_names = {
            row["id"]: row["name"]
            for row in conn.execute(
                "SELECT id, name FROM projects WHERE status != '已关闭'"
            ).fetchall()
        }
        by_project = []
        for row in project_rows:
            label = project_names.get(row["project_id"], f"项目#{row['project_id']}")
            by_project.append({"label": label, "amount_minor": row["amount_minor"]})
        if unassigned is not None and unassigned["amount_minor"] > 0:
            by_project.append({"label": "待归集", "amount_minor": unassigned["amount_minor"]})

        prev_year, prev_mon = map(int, month.split("-"))
        prev_month = f"{prev_year - 1}-12" if prev_mon == 1 else f"{prev_year}-{prev_mon - 1:02d}"
        prev_start, prev_next = _month_bounds(prev_month)
        prev_params = [prev_start, prev_next] + ([project_id] if project_id else [])
        purchase_previous_sql = (
            """SELECT COALESCE(SUM(ppc.cost_minor), 0)
               FROM purchase_project_costs ppc
               JOIN purchase_orders po ON po.id=ppc.purchase_order_id
               WHERE po.status='有效'
                 AND po.purchase_date >= ? AND po.purchase_date < ?
                 AND ppc.project_id=?"""
            if project_id
            else """SELECT COALESCE(SUM(total_amount_cents), 0)
                    FROM purchase_orders
                    WHERE status='有效'
                      AND purchase_date >= ? AND purchase_date < ?"""
        )
        prev_total = conn.execute(
            f"""SELECT COALESCE(
                ({purchase_previous_sql})
                + (SELECT COALESCE(SUM(COALESCE(amount_minor,
                        CAST(ROUND(COALESCE(amount, 0) * 100) AS INTEGER))), 0)
                   FROM work_logs
                   WHERE COALESCE(status, 'active')='active'
                     AND work_date >= ? AND work_date < ?
                     {project_filter})
                + (SELECT COALESCE(SUM(cal.amount_minor), 0)
                   FROM cost_entries ce
                   JOIN cost_allocation_lines cal
                     ON cal.cost_entry_id=ce.id AND cal.status='active'
                   WHERE ce.status='active'
                     AND ce.cost_date >= ? AND ce.cost_date < ?
                     {'AND cal.project_id=?' if project_id else ''})
            , 0)""",
            prev_params + prev_params + prev_params,
        ).fetchone()[0]

        return {
            "month": month,
            "summary": {
                "total_minor": total_minor,
                "purchase_minor": purchase["amount_minor"],
                "purchase_count": purchase["count"],
                "labor_minor": labor["amount_minor"],
                "labor_count": labor["count"],
                "manual_minor": manual_amount,
                "manual_count": manual_count,
                "unassigned_minor": unassigned["amount_minor"] if unassigned is not None else 0,
                "previous_total_minor": prev_total,
            },
            "by_source": by_source,
            "by_category": by_category,
            "by_project": by_project,
        }
    finally:
        conn.close()


def list_cost_months():
    """Available months across cost sources, newest first."""
    conn = get_connection()
    try:
        months = set()
        for row in conn.execute(
            "SELECT DISTINCT substr(purchase_date, 1, 7) AS m FROM purchase_orders WHERE status='有效'"
        ).fetchall():
            if row["m"]:
                months.add(row["m"])
        for row in conn.execute(
            "SELECT DISTINCT substr(work_date, 1, 7) AS m FROM work_logs WHERE COALESCE(status, 'active')='active'"
        ).fetchall():
            if row["m"]:
                months.add(row["m"])
        for row in conn.execute(
            "SELECT DISTINCT substr(cost_date, 1, 7) AS m FROM cost_entries WHERE status='active'"
        ).fetchall():
            if row["m"]:
                months.add(row["m"])
        return sorted(months, reverse=True)
    finally:
        conn.close()
