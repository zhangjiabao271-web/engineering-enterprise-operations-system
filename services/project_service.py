from services._common import (
    minor as _minor,
    now as _now,
    organization_id as _organization_id,
)
from datetime import datetime
from uuid import uuid4

from db.connection import db_read, db_transaction, get_connection


BUSINESS_MODES = {"contract", "cash"}
INVOICE_POLICIES = {"required", "not_required", "pending"}
ENTITY_TYPES = {"enterprise", "individual_business", "individual"}


def _project_policies(data):
    business_mode = (data.get("business_mode") or "contract").strip()
    if business_mode not in BUSINESS_MODES:
        raise ValueError("项目业务模式无效")
    default_policy = "not_required" if business_mode == "cash" else "required"
    invoice_policy = (data.get("invoice_policy") or default_policy).strip()
    if invoice_policy not in INVOICE_POLICIES:
        raise ValueError("项目开票要求无效")
    if business_mode == "cash" and invoice_policy == "required":
        raise ValueError("零星现金工程不能设置为必须开票")
    return business_mode, invoice_policy


def _cash_agreed_amount(data, business_mode):
    if business_mode != "cash":
        return None
    value = data.get("cash_agreed_amount")
    if value is None or not str(value).strip():
        return None
    return _minor(value)


def _confirmed_cash_income(conn, project_id, exclude_settlement_id=None):
    sql = """SELECT COALESCE(SUM(amount_minor), 0)
             FROM settlements
             WHERE project_id=? AND source_type='cash_job' AND status='active'"""
    params = [int(project_id)]
    if exclude_settlement_id is not None:
        sql += " AND id<>?"
        params.append(int(exclude_settlement_id))
    return int(conn.execute(sql, params).fetchone()[0] or 0)


def validate_cash_income_capacity(
    conn, project_id, amount_minor, *, exclude_settlement_id=None
):
    """Ensure a cash project's active income confirmations stay within its limit."""
    project = conn.execute(
        """SELECT business_mode, cash_agreed_amount_minor
           FROM projects WHERE id=?""",
        (int(project_id),),
    ).fetchone()
    if not project:
        raise ValueError("请选择有效项目")
    agreed_minor = project["cash_agreed_amount_minor"]
    if project["business_mode"] != "cash" or agreed_minor is None:
        return
    confirmed_minor = _confirmed_cash_income(
        conn, project_id, exclude_settlement_id
    )
    if confirmed_minor + int(amount_minor) <= int(agreed_minor):
        return
    available_minor = max(int(agreed_minor) - confirmed_minor, 0)
    raise ValueError(
        "累计完工确认金额不能超过项目约定总额 "
        f"¥{int(agreed_minor) / 100:,.2f}；"
        f"当前最多还能确认 ¥{available_minor / 100:,.2f}"
    )


def _validate_cash_agreed_amount(conn, project_id, agreed_minor):
    if agreed_minor is None:
        return
    confirmed_minor = _confirmed_cash_income(conn, project_id)
    if confirmed_minor > agreed_minor:
        raise ValueError(
            "零星工程约定总额不能低于当前有效完工确认合计 "
            f"¥{confirmed_minor / 100:,.2f}"
        )


def _entity_type(value):
    entity_type = (value or "enterprise").strip()
    if entity_type not in ENTITY_TYPES:
        raise ValueError("客户主体类型无效")
    return entity_type


def _merge_note(value, note):
    current = (value or "").strip()
    if not current:
        return note
    if note in current:
        return current
    return f"{current}；{note}"


def _convert_contract_project_to_cash(conn, project_id, changed_at):
    active_invoice_count = conn.execute(
        """SELECT COUNT(DISTINCT i.id)
           FROM sales_invoices i
           LEFT JOIN invoice_settlement_allocations a ON a.invoice_id=i.id
           LEFT JOIN settlements s ON s.id=a.settlement_id
           WHERE i.status='active'
             AND (i.project_id=? OR s.project_id=?)""",
        (project_id, project_id),
    ).fetchone()[0]
    if active_invoice_count:
        raise ValueError("项目已有有效发票，不能改为零星现金工程")

    invoiced_receipt = conn.execute(
        """SELECT 1 FROM receipt_allocations ra
           JOIN receipts r ON r.id=ra.receipt_id
           WHERE ra.project_id=? AND r.status='active'
             AND ra.invoice_id IS NOT NULL
           LIMIT 1""",
        (project_id,),
    ).fetchone()
    if invoiced_receipt:
        raise ValueError("项目回款已经关联发票，不能改为零星现金工程")

    incomplete_receipt = conn.execute(
        """SELECT 1 FROM receipt_allocations ra
           JOIN receipts r ON r.id=ra.receipt_id
           WHERE ra.project_id=? AND r.status='active'
           GROUP BY ra.receipt_id
           HAVING COUNT(DISTINCT ra.settlement_id)<>1
               OR SUM(CASE WHEN ra.settlement_id IS NULL THEN 1 ELSE 0 END)>0
           LIMIT 1""",
        (project_id,),
    ).fetchone()
    if incomplete_receipt:
        raise ValueError("项目存在未明确对应一笔收入确认的回款，不能直接切换")

    shared_receipt = conn.execute(
        """SELECT 1 FROM receipt_allocations own
           JOIN receipts r ON r.id=own.receipt_id
           JOIN receipt_allocations other ON other.receipt_id=own.receipt_id
           WHERE own.project_id=? AND r.status='active'
             AND other.project_id<>?
           LIMIT 1""",
        (project_id, project_id),
    ).fetchone()
    if shared_receipt:
        raise ValueError("项目与其他项目共用回款，不能直接切换业务模式")

    settlement_note = "项目业务模式调整为零星现金工程，解除原合同归属"
    settlements = conn.execute(
        """SELECT id, basis FROM settlements
           WHERE project_id=? AND status='active'""",
        (project_id,),
    ).fetchall()
    confirmed_minor = int(
        conn.execute(
            """SELECT COALESCE(SUM(amount_minor), 0) FROM settlements
               WHERE project_id=? AND status='active'""",
            (project_id,),
        ).fetchone()[0]
        or 0
    )
    fixed_boundary_minor = int(
        conn.execute(
            """SELECT COALESCE(SUM(a.allocated_amount_minor), 0)
               FROM contract_project_allocations a
               JOIN contracts c ON c.id=a.contract_id
               WHERE a.project_id=? AND a.status='active'
                 AND c.status<>'void' AND c.pricing_mode='fixed'""",
            (project_id,),
        ).fetchone()[0]
        or 0
    )
    for settlement in settlements:
        conn.execute(
            """UPDATE settlements
               SET contract_id=NULL, source_type='cash_job', basis=?, updated_at=?
               WHERE id=?""",
            (
                _merge_note(settlement["basis"], settlement_note),
                changed_at,
                settlement["id"],
            ),
        )

    receipt_note = "项目转为零星现金工程，解除原合同归属"
    receipt_allocations = conn.execute(
        """SELECT ra.id, ra.notes FROM receipt_allocations ra
           JOIN receipts r ON r.id=ra.receipt_id
           WHERE ra.project_id=? AND r.status='active'""",
        (project_id,),
    ).fetchall()
    for allocation in receipt_allocations:
        conn.execute(
            """UPDATE receipt_allocations
               SET contract_id=NULL, invoice_id=NULL, notes=?
               WHERE id=?""",
            (
                _merge_note(allocation["notes"], receipt_note),
                allocation["id"],
            ),
        )

    allocation_note = "项目转为零星现金工程，原合同项目关系已解除"
    project_allocations = conn.execute(
        """SELECT id, notes FROM contract_project_allocations
           WHERE project_id=? AND status='active'""",
        (project_id,),
    ).fetchall()
    for allocation in project_allocations:
        conn.execute(
            """UPDATE contract_project_allocations
               SET status='void', notes=?, updated_at=?
               WHERE id=?""",
            (
                _merge_note(allocation["notes"], allocation_note),
                changed_at,
                allocation["id"],
            ),
        )
    return max(confirmed_minor, fixed_boundary_minor) or None


def list_projects(active_only=False, keyword="", status=""):
    with db_read() as conn:
        sql = """
            SELECT p.id, p.public_id, p.project_code, p.name,
                   COALESCE(bp.legal_name, p.customer_name, '') AS customer_name,
                   COALESCE(p.address, '') AS address,
                   COALESCE(e.name, p.manager, '') AS manager,
                   p.status, COALESCE(p.notes, '') AS notes,
                   p.organization_id, p.customer_partner_id, p.manager_employee_id,
                   p.planned_start_date, p.planned_end_date,
                   p.business_mode, p.invoice_policy,
                   p.cash_agreed_amount_minor,
                   COALESCE((
                       SELECT SUM(s.amount_minor) FROM settlements s
                       WHERE s.project_id=p.id AND s.source_type='cash_job'
                         AND s.status='active'
                   ), 0) AS cash_confirmed_minor,
                   COALESCE(bp.entity_type, 'enterprise') AS customer_entity_type,
                   (SELECT COUNT(*) FROM project_sites ps
                    WHERE ps.project_id=p.id AND ps.is_active=1) AS site_count,
                   p.created_at, p.updated_at
            FROM projects p
            LEFT JOIN business_partners bp ON bp.id=p.customer_partner_id
            LEFT JOIN employees e ON e.id=p.manager_employee_id
        """
        conditions = []
        params = []
        if active_only:
            conditions.append("p.status IN ('进行中', '筹备中')")
        if status:
            conditions.append("p.status=?")
            params.append(status)
        if keyword:
            conditions.append(
                "(p.project_code LIKE ? OR p.name LIKE ? OR bp.legal_name LIKE ? "
                "OR e.name LIKE ? OR p.address LIKE ?)"
            )
            params.extend([f"%{keyword}%"] * 5)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY CASE p.status WHEN '进行中' THEN 1 WHEN '筹备中' THEN 2 ELSE 3 END, p.id DESC"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _customer_id(
    conn, organization_id, name, now, partner_id=None, allow_inactive_id=None,
    entity_type="enterprise",
):
    name = (name or "").strip()
    entity_type = _entity_type(entity_type)
    partner_id = int(partner_id or 0) or None
    if partner_id:
        row = conn.execute(
            """SELECT bp.id, bp.legal_name, bp.status
               FROM business_partners bp
               JOIN partner_roles pr ON pr.partner_id=bp.id AND pr.role_code='customer'
               WHERE bp.id=? AND bp.organization_id=?""",
            (partner_id, organization_id),
        ).fetchone()
        if not row:
            raise ValueError("所选客户不存在或不具备客户角色")
        if row["status"] == "inactive" and partner_id != allow_inactive_id:
            raise ValueError("所选客户已停用，不能用于新的项目关系")
        return partner_id
    if not name:
        return None
    row = conn.execute(
        "SELECT id, status FROM business_partners WHERE organization_id=? AND legal_name=?",
        (organization_id, name),
    ).fetchone()
    if row:
        partner_id = row["id"]
        if row["status"] == "inactive" and partner_id != allow_inactive_id:
            raise ValueError("同名客户档案已停用，请先在客商档案中启用")
    else:
        partner_id = conn.execute(
            """INSERT INTO business_partners
               (public_id, organization_id, partner_code, legal_name, short_name,
                entity_type, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (str(uuid4()), organization_id, f"CUS-{uuid4().hex[:10].upper()}",
             name, name, entity_type, now, now),
        ).lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO partner_roles(partner_id, role_code, created_at) VALUES (?, 'customer', ?)",
        (partner_id, now),
    )
    conn.execute(
        """INSERT OR IGNORE INTO customer_profiles(partner_id, updated_at)
           VALUES (?, ?)""",
        (partner_id, now),
    )
    return partner_id


def _manager_id(conn, organization_id, name, now):
    name = (name or "").strip()
    if not name:
        return None
    row = conn.execute(
        "SELECT id FROM employees WHERE organization_id=? AND name=?",
        (organization_id, name),
    ).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        """INSERT INTO employees
           (public_id, organization_id, employee_code, name, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (str(uuid4()), organization_id, f"EMP-{uuid4().hex[:10].upper()}", name, now, now),
    ).lastrowid


def create_project(data, *, connection=None):
    owns_connection = connection is None
    conn = get_connection() if owns_connection else connection
    try:
        now = _now()
        organization_id = _organization_id(conn)
        business_mode, invoice_policy = _project_policies(data)
        cash_agreed_minor = _cash_agreed_amount(data, business_mode)
        customer_id = _customer_id(
            conn, organization_id, data.get("customer_name"), now,
            data.get("customer_partner_id"),
            entity_type=data.get("customer_entity_type"),
        )
        manager_id = _manager_id(conn, organization_id, data.get("manager"), now)
        code = (data.get("project_code") or "").strip()
        if not code:
            code = f"P-{datetime.now():%Y}-{uuid4().hex[:6].upper()}"
        cursor = conn.execute(
            """INSERT INTO projects
               (project_code, name, customer_name, address, manager, status, notes,
                created_at, updated_at, public_id, organization_id,
                customer_partner_id, manager_employee_id,
                planned_start_date, planned_end_date, business_mode,
                invoice_policy, cash_agreed_amount_minor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, data["name"], data.get("customer_name", ""), data.get("address", ""),
             data.get("manager", ""), data.get("status", "进行中"), data.get("notes", ""),
             now, now, str(uuid4()), organization_id, customer_id, manager_id,
             data.get("planned_start_date") or None, data.get("planned_end_date") or None,
             business_mode, invoice_policy, cash_agreed_minor),
        )
        project_id = cursor.lastrowid
        conn.execute(
            """INSERT INTO wbs_nodes
               (public_id, project_id, wbs_code, name, created_at, updated_at)
               VALUES (?, ?, 'ROOT', '项目总项', ?, ?)""",
            (str(uuid4()), project_id, now, now),
        )
        if owns_connection:
            conn.commit()
        return project_id
    except Exception:
        if owns_connection:
            conn.rollback()
        raise
    finally:
        if owns_connection:
            conn.close()


def get_project(project_id):
    rows = list_projects()
    return next((row for row in rows if row["id"] == int(project_id)), None)


def update_project(project_id, data):
    with db_transaction() as conn:
        now = _now()
        organization_id = _organization_id(conn)
        business_mode, invoice_policy = _project_policies(data)
        cash_agreed_minor = _cash_agreed_amount(data, business_mode)
        existing = conn.execute(
            "SELECT customer_partner_id, business_mode FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if not existing:
            raise ValueError("项目不存在")
        customer_id = _customer_id(
            conn, organization_id, data.get("customer_name"), now,
            data.get("customer_partner_id"),
            allow_inactive_id=existing["customer_partner_id"],
            entity_type=data.get("customer_entity_type"),
        )
        if (
            existing["business_mode"] == "contract"
            and business_mode == "cash"
        ):
            derived_limit = _convert_contract_project_to_cash(conn, project_id, now)
            if cash_agreed_minor is None:
                cash_agreed_minor = derived_limit
        elif business_mode != existing["business_mode"]:
            has_business = conn.execute(
                """SELECT 1
                   WHERE EXISTS (
                       SELECT 1 FROM contract_project_allocations
                       WHERE project_id=? AND status='active'
                   ) OR EXISTS (
                       SELECT 1 FROM settlements
                       WHERE project_id=? AND status='active'
                   ) OR EXISTS (
                       SELECT 1 FROM receipt_allocations ra
                       JOIN receipts r ON r.id=ra.receipt_id
                       WHERE ra.project_id=? AND r.status='active'
                   )""",
                (project_id, project_id, project_id),
            ).fetchone()
            if has_business:
                raise ValueError(
                    "零星现金工程已有收入或回款，不能直接改为正式合同工程"
                )
        _validate_cash_agreed_amount(conn, project_id, cash_agreed_minor)
        manager_id = _manager_id(conn, organization_id, data.get("manager"), now)
        result = conn.execute(
            """UPDATE projects
               SET project_code=?, name=?, customer_name=?, customer_partner_id=?,
                   address=?, manager=?, manager_employee_id=?, status=?, notes=?,
                   planned_start_date=?, planned_end_date=?, business_mode=?,
                   invoice_policy=?, cash_agreed_amount_minor=?, updated_at=?
               WHERE id=?""",
            (
                data["project_code"].strip(),
                data["name"].strip(),
                data.get("customer_name", "").strip(),
                customer_id,
                data.get("address", "").strip(),
                data.get("manager", "").strip(),
                manager_id,
                data.get("status", "进行中"),
                data.get("notes", "").strip(),
                data.get("planned_start_date") or None,
                data.get("planned_end_date") or None,
                business_mode,
                invoice_policy,
                cash_agreed_minor,
                now,
                project_id,
            ),
        )
        if not result.rowcount:
            raise ValueError("项目不存在")


def close_projects(project_ids):
    if not project_ids:
        return
    with db_transaction() as conn:
        placeholders = ",".join("?" * len(project_ids))
        conn.execute(
            f"UPDATE projects SET status='已关闭', updated_at=? WHERE id IN ({placeholders})",
            (_now(), *project_ids),
        )


def list_project_sites(project_id, include_inactive=False):
    with db_read() as conn:
        sql = """
            SELECT id, public_id, project_id, site_code, name AS site_name,
                   COALESCE(address, '') AS address, is_active,
                   legacy_construction_site_id, created_at, updated_at
            FROM project_sites WHERE project_id=?
        """
        params = [project_id]
        if not include_inactive:
            sql += " AND is_active=1"
        sql += " ORDER BY is_active DESC, site_code, id"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _site_code(conn, project_id):
    number = conn.execute(
        "SELECT COALESCE(COUNT(*), 0) + 1 FROM project_sites WHERE project_id=?",
        (project_id,),
    ).fetchone()[0]
    return f"SITE-{number:03d}"


def _legacy_site_name(conn, project_id, site_name, legacy_id=None):
    conflict = conn.execute(
        "SELECT id FROM construction_sites WHERE site_name=? AND (? IS NULL OR id<>?)",
        (site_name, legacy_id, legacy_id),
    ).fetchone()
    if not conflict:
        return site_name
    project = conn.execute(
        "SELECT project_code FROM projects WHERE id=?", (project_id,)
    ).fetchone()
    return f"{project['project_code']} · {site_name}"


def create_project_site(project_id, data):
    with db_transaction() as conn:
        now = _now()
        project = conn.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
        if not project:
            raise ValueError("请先选择有效项目")
        code = (data.get("site_code") or "").strip() or _site_code(conn, project_id)
        name = data["site_name"].strip()
        legacy_name = _legacy_site_name(conn, project_id, name)
        legacy_id = conn.execute(
            """INSERT INTO construction_sites
               (project_id, site_name, address, is_active, notes, created_at)
               VALUES (?, ?, ?, 1, ?, ?)""",
            (project_id, legacy_name, data.get("address", "").strip(),
             "由项目管理同步创建", now),
        ).lastrowid
        cursor = conn.execute(
            """INSERT INTO project_sites
               (public_id, project_id, site_code, name, address, is_active,
                legacy_construction_site_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
            (str(uuid4()), project_id, code, name, data.get("address", "").strip(),
             legacy_id, now, now),
        )
        return cursor.lastrowid


def update_project_site(site_id, data):
    with db_transaction() as conn:
        now = _now()
        site = conn.execute(
            "SELECT * FROM project_sites WHERE id=?", (site_id,)
        ).fetchone()
        if not site:
            raise ValueError("施工地点不存在")
        name = data["site_name"].strip()
        legacy_name = _legacy_site_name(
            conn, site["project_id"], name, site["legacy_construction_site_id"]
        )
        conn.execute(
            """UPDATE project_sites
               SET site_code=?, name=?, address=?, updated_at=? WHERE id=?""",
            (data["site_code"].strip(), name, data.get("address", "").strip(), now, site_id),
        )
        if site["legacy_construction_site_id"]:
            conn.execute(
                """UPDATE construction_sites
                   SET site_name=?, address=?, is_active=1 WHERE id=?""",
                (legacy_name, data.get("address", "").strip(),
                 site["legacy_construction_site_id"]),
            )


def deactivate_project_sites(site_ids):
    if not site_ids:
        return
    with db_transaction() as conn:
        placeholders = ",".join("?" * len(site_ids))
        rows = conn.execute(
            f"SELECT legacy_construction_site_id FROM project_sites WHERE id IN ({placeholders})",
            site_ids,
        ).fetchall()
        conn.execute(
            f"UPDATE project_sites SET is_active=0, updated_at=? WHERE id IN ({placeholders})",
            (_now(), *site_ids),
        )
        legacy_ids = [row["legacy_construction_site_id"] for row in rows if row["legacy_construction_site_id"]]
        if legacy_ids:
            legacy_placeholders = ",".join("?" * len(legacy_ids))
            conn.execute(
                f"UPDATE construction_sites SET is_active=0 WHERE id IN ({legacy_placeholders})",
                legacy_ids,
            )
