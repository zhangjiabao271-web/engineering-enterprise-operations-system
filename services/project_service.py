from services._common import now as _now, organization_id as _organization_id
from datetime import datetime
from uuid import uuid4

from db.connection import get_connection


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


def _entity_type(value):
    entity_type = (value or "enterprise").strip()
    if entity_type not in ENTITY_TYPES:
        raise ValueError("客户主体类型无效")
    return entity_type


def list_projects(active_only=False, keyword="", status=""):
    conn = get_connection()
    try:
        sql = """
            SELECT p.id, p.public_id, p.project_code, p.name,
                   COALESCE(bp.legal_name, p.customer_name, '') AS customer_name,
                   COALESCE(p.address, '') AS address,
                   COALESCE(e.name, p.manager, '') AS manager,
                   p.status, COALESCE(p.notes, '') AS notes,
                   p.organization_id, p.customer_partner_id, p.manager_employee_id,
                   p.planned_start_date, p.planned_end_date,
                   p.business_mode, p.invoice_policy,
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
    finally:
        conn.close()


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


def create_project(data):
    conn = get_connection()
    try:
        now = _now()
        organization_id = _organization_id(conn)
        business_mode, invoice_policy = _project_policies(data)
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
                invoice_policy)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (code, data["name"], data.get("customer_name", ""), data.get("address", ""),
             data.get("manager", ""), data.get("status", "进行中"), data.get("notes", ""),
             now, now, str(uuid4()), organization_id, customer_id, manager_id,
             data.get("planned_start_date") or None, data.get("planned_end_date") or None,
             business_mode, invoice_policy),
        )
        project_id = cursor.lastrowid
        conn.execute(
            """INSERT INTO wbs_nodes
               (public_id, project_id, wbs_code, name, created_at, updated_at)
               VALUES (?, ?, 'ROOT', '项目总项', ?, ?)""",
            (str(uuid4()), project_id, now, now),
        )
        conn.commit()
        return project_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_project(project_id):
    rows = list_projects()
    return next((row for row in rows if row["id"] == int(project_id)), None)


def update_project(project_id, data):
    conn = get_connection()
    try:
        now = _now()
        organization_id = _organization_id(conn)
        business_mode, invoice_policy = _project_policies(data)
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
        if business_mode != existing["business_mode"]:
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
                raise ValueError("项目已有合同、收入确认或回款，不能切换业务模式")
        manager_id = _manager_id(conn, organization_id, data.get("manager"), now)
        result = conn.execute(
            """UPDATE projects
               SET project_code=?, name=?, customer_name=?, customer_partner_id=?,
                   address=?, manager=?, manager_employee_id=?, status=?, notes=?,
                   planned_start_date=?, planned_end_date=?, business_mode=?,
                   invoice_policy=?, updated_at=?
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
                now,
                project_id,
            ),
        )
        if not result.rowcount:
            raise ValueError("项目不存在")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def close_projects(project_ids):
    if not project_ids:
        return
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(project_ids))
        conn.execute(
            f"UPDATE projects SET status='已关闭', updated_at=? WHERE id IN ({placeholders})",
            (_now(), *project_ids),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_project_sites(project_id, include_inactive=False):
    conn = get_connection()
    try:
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
    finally:
        conn.close()


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
    conn = get_connection()
    try:
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
        conn.commit()
        return cursor.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_project_site(site_id, data):
    conn = get_connection()
    try:
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
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def deactivate_project_sites(site_ids):
    if not site_ids:
        return
    conn = get_connection()
    try:
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
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
