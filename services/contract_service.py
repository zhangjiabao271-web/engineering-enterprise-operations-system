from services._common import now as _now, organization_id as _organization_id, minor as _minor
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from db.connection import db_read, db_transaction
from services.project_service import validate_cash_income_capacity


CONTRACT_TYPES = {
    "annual": "年度框架合同",
    "project": "单项目合同",
    "supplement": "补充协议",
}
PRICING_MODES = {
    "fixed": "固定总价",
    "provisional": "暂定总价",
    "actual": "单价据实结算",
}
CONTRACT_STATUSES = {
    "draft": "草稿",
    "active": "履约中",
    "completed": "已完成",
    "void": "已作废",
}


def _optional_minor(value):
    if value is None or not str(value).strip():
        return None
    return _minor(value)


def _pricing_values(data, default="fixed"):
    pricing_mode = (data.get("pricing_mode") or default).strip()
    if pricing_mode not in PRICING_MODES:
        raise ValueError("合同计价方式无效")
    if pricing_mode == "actual":
        amount_minor = 0
    else:
        amount_minor = _minor(data.get("amount"))
    if pricing_mode == "fixed":
        control_limit_minor = None
    else:
        control_limit_minor = _optional_minor(data.get("control_limit"))
    return pricing_mode, amount_minor, control_limit_minor


def _resolve_customer(
    conn, organization_id, partner_id, customer_name, *, allow_inactive_id=None
):
    partner_id = int(partner_id or 0) or None
    customer_name = (customer_name or "").strip()
    if partner_id:
        partner = conn.execute(
            """SELECT bp.id, bp.legal_name, bp.status
               FROM business_partners bp
               JOIN partner_roles pr
                 ON pr.partner_id=bp.id AND pr.role_code='customer'
               WHERE bp.id=? AND bp.organization_id=?""",
            (partner_id, organization_id),
        ).fetchone()
        if not partner:
            raise ValueError("所选客户不存在或不具备客户角色")
        if partner["status"] == "inactive" and partner_id != allow_inactive_id:
            raise ValueError("所选客户已停用，不能用于新的合同关系")
        return partner_id, partner["legal_name"]
    if not customer_name:
        return None, ""
    partner = conn.execute(
        """SELECT id, status FROM business_partners
           WHERE organization_id=? AND legal_name=?""",
        (organization_id, customer_name),
    ).fetchone()
    now = _now()
    if partner:
        partner_id = partner["id"]
        if partner["status"] == "inactive" and partner_id != allow_inactive_id:
            raise ValueError("同名客户档案已停用，请先在客商档案中启用")
    else:
        partner_id = conn.execute(
            """INSERT INTO business_partners
               (public_id, organization_id, partner_code, legal_name, short_name,
                status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                str(uuid4()), organization_id, f"CUS-{uuid4().hex[:10].upper()}",
                customer_name, customer_name, now, now,
            ),
        ).lastrowid
    conn.execute(
        """INSERT OR IGNORE INTO partner_roles(partner_id, role_code, created_at)
           VALUES (?, 'customer', ?)""",
        (partner_id, now),
    )
    conn.execute(
        """INSERT OR IGNORE INTO customer_profiles(partner_id, updated_at)
           VALUES (?, ?)""",
        (partner_id, now),
    )
    return partner_id, customer_name


def _date(value, label, optional=False):
    value = (value or "").strip()
    if optional and not value:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"{label}必须使用 YYYY-MM-DD 格式")
    return value


def _number(prefix):
    return f"{prefix}-{datetime.now():%Y%m%d}-{uuid4().hex[:6].upper()}"


def _project_policy(conn, project_id):
    project = conn.execute(
        """SELECT id, name, business_mode, invoice_policy
           FROM projects WHERE id=?""",
        (int(project_id or 0),),
    ).fetchone()
    if not project:
        raise ValueError("请选择有效项目")
    return project


def list_contracts(include_void=False, keyword=""):
    with db_read() as conn:
        sql = """
            SELECT c.*, COALESCE(bp.legal_name, c.customer_name_snapshot, '')
                       AS customer_name,
                   COALESCE(parent.contract_no, '') AS parent_contract_no,
                   COALESCE((
                       SELECT SUM(a.allocated_amount_minor)
                       FROM contract_project_allocations a
                       WHERE a.contract_id=c.id AND a.status='active'
                   ), 0) AS allocated_minor,
                   COALESCE((
                       SELECT SUM(s.amount_minor) FROM settlements s
                       WHERE s.contract_id=c.id AND s.status='active'
                   ), 0) AS settled_minor,
                   (SELECT COUNT(*) FROM (
                       SELECT a.project_id
                       FROM contract_project_allocations a
                       WHERE a.contract_id=c.id AND a.status='active'
                       UNION
                       SELECT s.project_id
                       FROM settlements s
                       WHERE s.contract_id=c.id AND s.status='active'
                   )) AS project_count
            FROM contracts c
            LEFT JOIN business_partners bp ON bp.id=c.customer_partner_id
            LEFT JOIN contracts parent ON parent.id=c.parent_contract_id
        """
        conditions = []
        params = []
        if not include_void:
            conditions.append("c.status<>'void'")
        if keyword:
            conditions.append(
                "(c.contract_no LIKE ? OR c.name LIKE ? "
                "OR c.customer_name_snapshot LIKE ? OR bp.legal_name LIKE ?)"
            )
            params.extend([f"%{keyword}%"] * 4)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY c.sign_date DESC, c.id DESC"
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        for row in rows:
            row["remaining_minor"] = (
                row["tax_inclusive_amount_minor"] - row["allocated_minor"]
                if row["pricing_mode"] == "fixed"
                else None
            )
            row["reference_overrun_minor"] = (
                max(row["settled_minor"] - row["tax_inclusive_amount_minor"], 0)
                if row["pricing_mode"] == "provisional"
                else 0
            )
            control_limit = row["control_limit_minor"]
            row["control_overrun_minor"] = (
                max(row["settled_minor"] - control_limit, 0)
                if control_limit is not None
                else 0
            )
            row["pricing_warning"] = bool(
                row["reference_overrun_minor"] or row["control_overrun_minor"]
            )
        return rows


def get_contract(contract_id):
    return next(
        (
            row
            for row in list_contracts(include_void=True)
            if row["id"] == int(contract_id)
        ),
        None,
    )


def _convert_contract_to_actual_pricing(conn, contract_id, changed_at):
    note = "合同计价方式调整为单价据实结算，原金额分配改为据实关联"
    allocations = conn.execute(
        """SELECT id, notes FROM contract_project_allocations
           WHERE contract_id=? AND status='active'""",
        (contract_id,),
    ).fetchall()
    for allocation in allocations:
        current_notes = (allocation["notes"] or "").strip()
        if note not in current_notes:
            current_notes = (
                f"{current_notes}；{note}" if current_notes else note
            )
        conn.execute(
            """UPDATE contract_project_allocations
               SET allocated_amount_minor=0, notes=?, updated_at=?
               WHERE id=?""",
            (current_notes, changed_at, allocation["id"]),
        )

    linked_projects = conn.execute(
        """SELECT DISTINCT s.project_id
           FROM settlements s
           LEFT JOIN contract_project_allocations a
             ON a.contract_id=s.contract_id AND a.project_id=s.project_id
            AND a.status='active'
           WHERE s.contract_id=? AND s.status='active' AND a.id IS NULL""",
        (contract_id,),
    ).fetchall()
    for project in linked_projects:
        conn.execute(
            """INSERT INTO contract_project_allocations (
                   public_id, contract_id, project_id,
                   allocated_amount_minor, notes, status,
                   created_at, updated_at
               ) VALUES (?, ?, ?, 0, ?, 'active', ?, ?)""",
            (
                str(uuid4()),
                contract_id,
                project["project_id"],
                note,
                changed_at,
                changed_at,
            ),
        )


def contract_pricing_warning(contract_id):
    contract = get_contract(contract_id)
    if not contract or not contract["pricing_warning"]:
        return ""
    details = []
    if contract["reference_overrun_minor"]:
        details.append(
            "超过暂定金额 "
            f"¥{contract['reference_overrun_minor'] / 100:,.2f}"
        )
    if contract["control_overrun_minor"]:
        details.append(
            "超过控制上限 "
            f"¥{contract['control_overrun_minor'] / 100:,.2f}"
        )
    detail_text = "；".join(details)
    return (
        f"收入确认已保存。当前累计确认 "
        f"¥{contract['settled_minor'] / 100:,.2f}，{detail_text}。"
        "该提示不会改写合同金额，也不会阻止继续登记。"
    )


def create_contract(data):
    contract_type = data.get("contract_type", "project")
    if contract_type not in CONTRACT_TYPES:
        raise ValueError("合同类型无效")
    status = data.get("status", "active")
    if status not in ("draft", "active"):
        raise ValueError("新合同状态必须为草稿或履约中")
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("合同名称不能为空")
    pricing_mode, amount_minor, control_limit_minor = _pricing_values(data)
    sign_date = _date(data.get("sign_date"), "签订日期")
    start_date = _date(data.get("start_date"), "开始日期", optional=True)
    end_date = _date(data.get("end_date"), "结束日期", optional=True)
    if start_date and end_date and end_date < start_date:
        raise ValueError("合同结束日期不能早于开始日期")

    with db_transaction() as conn:
        organization_id = _organization_id(conn)
        contract_no = (data.get("contract_no") or "").strip() or _number("HT")
        partner_id = data.get("customer_partner_id") or None
        parent_contract_id = int(data.get("parent_contract_id") or 0) or None
        if contract_type == "supplement" and not parent_contract_id:
            raise ValueError("补充协议必须选择所属原合同")
        if parent_contract_id and not conn.execute(
            "SELECT 1 FROM contracts WHERE id=? AND status<>'void'",
            (parent_contract_id,),
        ).fetchone():
            raise ValueError("所属原合同不存在")
        partner_id, customer_name = _resolve_customer(
            conn, organization_id, partner_id, data.get("customer_name")
        )
        now = _now()
        cursor = conn.execute(
            """INSERT INTO contracts (
                   public_id, organization_id, contract_no, name,
                   customer_partner_id, customer_name_snapshot, contract_type,
                   parent_contract_id,
                   sign_date, start_date, end_date,
                   tax_inclusive_amount_minor, pricing_mode,
                   control_limit_minor, status, notes,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid4()),
                organization_id,
                contract_no,
                name,
                partner_id,
                customer_name,
                contract_type,
                parent_contract_id,
                sign_date,
                start_date,
                end_date,
                amount_minor,
                pricing_mode,
                control_limit_minor,
                status,
                (data.get("notes") or "").strip(),
                now,
                now,
            ),
        )
        from services.invoice_income_service import set_mode
        set_mode(conn, cursor.lastrowid, data.get('income_mode', 'manual'))
        return cursor.lastrowid


def update_contract(contract_id, data):
    existing = get_contract(contract_id)
    if not existing or existing["status"] == "void":
        raise ValueError("合同不存在或已作废")
    pricing_mode, amount_minor, control_limit_minor = _pricing_values(
        data, existing["pricing_mode"]
    )
    pricing_mode_changed = pricing_mode != existing["pricing_mode"]
    converting_to_actual = pricing_mode_changed and pricing_mode == "actual"
    if (
        pricing_mode_changed
        and not converting_to_actual
        and (existing["allocated_minor"] or existing["settled_minor"])
    ):
        raise ValueError(
            "合同已有项目关系或收入确认，只能改为单价据实结算"
        )
    if pricing_mode == "fixed" and amount_minor < existing["allocated_minor"]:
        raise ValueError("合同金额不能小于已经分配到项目的金额")
    contract_no = (data.get("contract_no") or "").strip()
    name = (data.get("name") or "").strip()
    if not contract_no:
        raise ValueError("合同编号不能为空")
    if not name:
        raise ValueError("合同名称不能为空")
    contract_type = data.get("contract_type", existing["contract_type"])
    if contract_type not in CONTRACT_TYPES:
        raise ValueError("合同类型无效")
    status = data.get("status", existing["status"])
    if status not in ("draft", "active", "completed"):
        raise ValueError("合同状态无效")
    sign_date = _date(data.get("sign_date"), "签订日期")
    start_date = _date(data.get("start_date"), "开始日期", optional=True)
    end_date = _date(data.get("end_date"), "结束日期", optional=True)
    if start_date and end_date and end_date < start_date:
        raise ValueError("合同结束日期不能早于开始日期")

    with db_transaction(immediate=True) as conn:
        changed_at = _now()
        organization_id = _organization_id(conn)
        partner_id = data.get("customer_partner_id") or None
        parent_contract_id = int(data.get("parent_contract_id") or 0) or None
        if parent_contract_id == int(contract_id):
            raise ValueError("合同不能把自己设为上级合同")
        if contract_type == "supplement" and not parent_contract_id:
            raise ValueError("补充协议必须选择所属原合同")
        if parent_contract_id and not conn.execute(
            "SELECT 1 FROM contracts WHERE id=? AND status<>'void'",
            (parent_contract_id,),
        ).fetchone():
            raise ValueError("所属原合同不存在")
        partner_id, customer_name = _resolve_customer(
            conn, organization_id, partner_id, data.get("customer_name"),
            allow_inactive_id=existing.get("customer_partner_id"),
        )
        if converting_to_actual:
            _convert_contract_to_actual_pricing(conn, contract_id, changed_at)
        conn.execute(
            """UPDATE contracts
               SET contract_no=?, name=?, customer_partner_id=?,
                   customer_name_snapshot=?, contract_type=?, sign_date=?,
                   parent_contract_id=?, start_date=?, end_date=?,
                   tax_inclusive_amount_minor=?, pricing_mode=?,
                   control_limit_minor=?, status=?, notes=?, updated_at=?
               WHERE id=? AND status<>'void'""",
            (
                contract_no,
                name,
                partner_id,
                customer_name,
                contract_type,
                sign_date,
                parent_contract_id,
                start_date,
                end_date,
                amount_minor,
                pricing_mode,
                control_limit_minor,
                status,
                (data.get("notes") or "").strip(),
                changed_at,
                contract_id,
            ),
        )
        from services.invoice_income_service import set_mode
        set_mode(conn, contract_id, data.get('income_mode', existing['income_mode']))


def void_contracts(contract_ids):
    if not contract_ids:
        return
    with db_transaction() as conn:
        placeholders = ",".join("?" * len(contract_ids))
        active_facts = conn.execute(
            f"""SELECT
                    (SELECT COUNT(*) FROM settlements
                     WHERE contract_id IN ({placeholders}) AND status='active')
                  + (SELECT COUNT(*) FROM sales_invoices
                     WHERE contract_id IN ({placeholders}) AND status='active')
                  + (SELECT COUNT(*) FROM receipt_allocations ra
                     JOIN receipts r ON r.id=ra.receipt_id
                     WHERE ra.contract_id IN ({placeholders})
                       AND r.status='active')""",
            (*contract_ids, *contract_ids, *contract_ids),
        ).fetchone()[0]
        if active_facts:
            raise ValueError("合同已有结算、发票或回款，不能直接作废")
        conn.execute(
            f"""UPDATE contract_project_allocations
                SET status='void', updated_at=?
                WHERE contract_id IN ({placeholders}) AND status='active'""",
            (_now(), *contract_ids),
        )
        conn.execute(
            f"""UPDATE contracts SET status='void', updated_at=?
                WHERE id IN ({placeholders})""",
            (_now(), *contract_ids),
        )


def list_allocations(contract_id=None, project_id=None):
    with db_read() as conn:
        sql = """
            SELECT a.*, c.contract_no, c.name AS contract_name, c.pricing_mode,
                   c.income_mode, c.status AS contract_status, p.invoice_policy,
                   p.project_code, p.name AS project_name
            FROM contract_project_allocations a
            JOIN contracts c ON c.id=a.contract_id
            JOIN projects p ON p.id=a.project_id
            WHERE a.status='active'
        """
        params = []
        if contract_id:
            sql += " AND a.contract_id=?"
            params.append(contract_id)
        if project_id:
            sql += " AND a.project_id=?"
            params.append(project_id)
        sql += " ORDER BY c.sign_date DESC, a.id DESC"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def create_allocation(data):
    contract_id = int(data.get("contract_id") or 0)
    project_id = int(data.get("project_id") or 0)
    with db_transaction(immediate=True) as conn:
        contract = conn.execute(
            """SELECT tax_inclusive_amount_minor, pricing_mode,
                      customer_partner_id
               FROM contracts
               WHERE id=? AND status IN ('active', 'draft')""",
            (contract_id,),
        ).fetchone()
        if not contract:
            raise ValueError("合同不存在或不可分配")
        amount_minor = (
            0
            if contract["pricing_mode"] == "actual"
            else _minor(data.get("amount"))
        )
        project = conn.execute(
            "SELECT id, customer_partner_id, business_mode FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
        if not project:
            raise ValueError("项目不存在")
        if (
            contract["customer_partner_id"]
            and project["customer_partner_id"] != contract["customer_partner_id"]
        ):
            raise ValueError("合同与项目不属于同一客户，不能建立关联")
        allocated = conn.execute(
            """SELECT COALESCE(SUM(allocated_amount_minor), 0)
               FROM contract_project_allocations
               WHERE contract_id=? AND status='active'""",
            (contract_id,),
        ).fetchone()[0]
        if (
            contract["pricing_mode"] == "fixed"
            and allocated + amount_minor > contract["tax_inclusive_amount_minor"]
        ):
            raise ValueError("本次分配会超过合同总金额")
        existing_pair = conn.execute(
            """SELECT id, allocated_amount_minor, notes
               FROM contract_project_allocations
               WHERE contract_id=? AND project_id=? AND status='active'""",
            (contract_id, project_id),
        ).fetchone()
        now = _now()
        if project["business_mode"] == "cash":
            has_history = conn.execute(
                """SELECT 1 WHERE EXISTS (
                    SELECT 1 FROM settlements WHERE project_id=? AND status='active'
                ) OR EXISTS (
                    SELECT 1 FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
                    WHERE a.project_id=? AND r.status='active'
                )""", (project_id, project_id),
            ).fetchone()
            if has_history:
                raise ValueError(
                    "该项目已有零星收入确认或回款，请先核对这些历史记录的合同归属；"
                    "本次未修改原记录。无需开票不影响关联合同。"
                )
            conversion_note = "关联合同后按合同管理，保留原开票要求。"
            conn.execute(
                """UPDATE projects SET business_mode='contract', updated_at=?,
                   notes=CASE WHEN COALESCE(notes,'')='' THEN ? ELSE notes||char(10)||? END
                   WHERE id=?""",
                (now, conversion_note, conversion_note, project_id),
            )
        if existing_pair:
            # 同一合同+项目只保留一条生效分配：追加金额合并到原记录
            merged_notes = existing_pair["notes"] or ""
            new_note = (data.get("notes") or "").strip()
            if new_note and new_note not in merged_notes:
                merged_notes = (
                    f"{merged_notes}；{new_note}" if merged_notes else new_note
                )
            conn.execute(
                """UPDATE contract_project_allocations
                   SET allocated_amount_minor=?, notes=?, updated_at=?
                   WHERE id=?""",
                (
                    existing_pair["allocated_amount_minor"] + amount_minor,
                    merged_notes,
                    now,
                    existing_pair["id"],
                ),
            )
            return existing_pair["id"]
        cursor = conn.execute(
            """INSERT INTO contract_project_allocations (
                   public_id, contract_id, project_id, allocated_amount_minor,
                   notes, status, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                str(uuid4()),
                contract_id,
                project_id,
                amount_minor,
                (data.get("notes") or "").strip(),
                now,
                now,
            ),
        )
        return cursor.lastrowid


def update_allocation_amount(allocation_id, new_amount, notes=None):
    """调整项目分配金额：调减即释放回合同待分配池，调增受合同总额约束。"""
    allocation_id = int(allocation_id or 0)
    amount_minor = _minor(new_amount)
    if amount_minor <= 0:
        raise ValueError("分配金额必须大于零；如需清零请使用作废分配")
    with db_transaction() as conn:
        allocation = conn.execute(
            """SELECT id, contract_id, project_id, allocated_amount_minor, notes
               FROM contract_project_allocations
               WHERE id=? AND status='active'""",
            (allocation_id,),
        ).fetchone()
        if not allocation:
            raise ValueError("项目分配不存在或已作废")
        settled = conn.execute(
            """SELECT COALESCE(SUM(amount_minor), 0) FROM settlements
               WHERE contract_id=? AND project_id=? AND status='active'""",
            (allocation["contract_id"], allocation["project_id"]),
        ).fetchone()[0]
        if amount_minor < settled:
            raise ValueError(
                f"该项目已有结算 ¥{settled / 100:,.2f}，"
                "分配额不能调到低于已结算金额"
            )
        contract = conn.execute(
            """SELECT tax_inclusive_amount_minor, pricing_mode FROM contracts
               WHERE id=? AND status IN ('active', 'draft')""",
            (allocation["contract_id"],),
        ).fetchone()
        if not contract:
            raise ValueError("合同不存在或已作废，不能调整分配")
        if contract["pricing_mode"] == "actual":
            raise ValueError("单价据实结算合同只关联项目，无需调整分配金额")
        others = conn.execute(
            """SELECT COALESCE(SUM(allocated_amount_minor), 0)
               FROM contract_project_allocations
               WHERE contract_id=? AND status='active' AND id<>?""",
            (allocation["contract_id"], allocation_id),
        ).fetchone()[0]
        if (
            contract["pricing_mode"] == "fixed"
            and others + amount_minor > contract["tax_inclusive_amount_minor"]
        ):
            raise ValueError("调整后会超过合同总金额")
        merged_notes = allocation["notes"] or ""
        new_note = (notes or "").strip()
        if new_note and new_note not in merged_notes:
            merged_notes = (
                f"{merged_notes}；{new_note}" if merged_notes else new_note
            )
        conn.execute(
            """UPDATE contract_project_allocations
               SET allocated_amount_minor=?, notes=?, updated_at=?
               WHERE id=?""",
            (amount_minor, merged_notes, _now(), allocation_id),
        )
        return allocation_id


def void_allocations(allocation_ids):
    if not allocation_ids:
        return
    with db_transaction() as conn:
        placeholders = ",".join("?" * len(allocation_ids))
        linked = conn.execute(
            f"""SELECT COUNT(*) FROM settlements s
                JOIN contract_project_allocations a
                  ON a.contract_id=s.contract_id AND a.project_id=s.project_id
                WHERE a.id IN ({placeholders}) AND s.status='active'""",
            allocation_ids,
        ).fetchone()[0]
        if linked:
            raise ValueError("该项目分配已有结算，不能直接作废")
        conn.execute(
            f"""UPDATE contract_project_allocations
                SET status='void', updated_at=?
                WHERE id IN ({placeholders}) AND status='active'""",
            (_now(), *allocation_ids),
        )


def list_settlements(project_id=None, contract_id=None):
    with db_read() as conn:
        sql = """
            SELECT s.*, p.project_code, p.name AS project_name, p.business_mode,
                    p.invoice_policy,
                    COALESCE(c.contract_no, '') AS contract_no,
                    COALESCE(c.name, '') AS contract_name,
                    COALESCE(c.pricing_mode, '') AS pricing_mode,
                    COALESCE(ip.invoiced_minor, 0) AS invoiced_minor,
                   COALESCE(ip.invoice_count, 0) AS invoice_count,
                   COALESCE(rp.received_minor, 0) AS received_minor
            FROM settlements s
            JOIN projects p ON p.id=s.project_id
            LEFT JOIN contracts c ON c.id=s.contract_id
            LEFT JOIN (
                SELECT a.settlement_id,
                       SUM(a.allocated_amount_minor) AS invoiced_minor,
                       COUNT(DISTINCT i.id) AS invoice_count
                FROM invoice_settlement_allocations a
                JOIN sales_invoices i ON i.id=a.invoice_id
                WHERE i.status='active'
                GROUP BY a.settlement_id
            ) ip ON ip.settlement_id=s.id
            LEFT JOIN (
                SELECT ra.settlement_id,
                       SUM(ra.allocated_amount_minor) AS received_minor
                FROM receipt_allocations ra
                JOIN receipts r ON r.id=ra.receipt_id
                WHERE ra.settlement_id IS NOT NULL AND r.status='active'
                GROUP BY ra.settlement_id
            ) rp ON rp.settlement_id=s.id
            WHERE s.status='active'
        """
        params = []
        if project_id:
            sql += " AND s.project_id=?"
            params.append(project_id)
        if contract_id:
            sql += " AND s.contract_id=?"
            params.append(contract_id)
        sql += " ORDER BY s.settlement_date DESC, s.id DESC"
        settlements = []
        for source in conn.execute(sql, params).fetchall():
            row = dict(source)
            row["uninvoiced_minor"] = (
                0 if row["invoice_policy"] == "not_required"
                else max(row["amount_minor"] - row["invoiced_minor"], 0)
            )
            row["invoice_rate_percent"] = (
                None if row["invoice_policy"] == "not_required"
                else row["invoiced_minor"] / row["amount_minor"] * 100
                if row["amount_minor"] else 0.0
            )
            row["unreceived_minor"] = max(
                row["amount_minor"] - row["received_minor"], 0
            )
            row["receipt_rate_percent"] = (
                row["received_minor"] / row["amount_minor"] * 100
                if row["amount_minor"] else 0.0
            )
            if row["unreceived_minor"] == 0:
                row["collection_status"] = "已结清"
            elif row["received_minor"]:
                row["collection_status"] = "部分回款"
            else:
                row["collection_status"] = "待回款"
            settlements.append(row)
        return settlements


def get_settlement(settlement_id):
    return next(
        (
            row
            for row in list_settlements()
            if row["id"] == int(settlement_id)
        ),
        None,
    )


def _validate_contract_settlement(
    conn, contract_id, project_id, amount_minor, *, settlement_id=None
):
    allowed_statuses = ("active", "draft", "completed") if settlement_id else (
        "active",
        "draft",
    )
    placeholders = ",".join("?" * len(allowed_statuses))
    contract = conn.execute(
        f"""SELECT pricing_mode FROM contracts
            WHERE id=? AND status IN ({placeholders})""",
        (contract_id, *allowed_statuses),
    ).fetchone()
    if not contract:
        raise ValueError("合同不存在或当前状态不能登记收入确认")
    allocation = conn.execute(
        """SELECT allocated_amount_minor
           FROM contract_project_allocations
           WHERE contract_id=? AND project_id=? AND status='active'""",
        (contract_id, project_id),
    ).fetchone()
    if not allocation:
        if contract["pricing_mode"] == "fixed":
            raise ValueError("该固定总价合同尚未分配到所选项目")
        raise ValueError("该合同尚未关联到所选项目")
    if contract["pricing_mode"] != "fixed":
        return
    sql = """SELECT COALESCE(SUM(amount_minor), 0) FROM settlements
             WHERE contract_id=? AND project_id=? AND status='active'"""
    params = [contract_id, project_id]
    if settlement_id:
        sql += " AND id<>?"
        params.append(settlement_id)
    settled = conn.execute(sql, params).fetchone()[0]
    if settled + amount_minor > allocation["allocated_amount_minor"]:
        raise ValueError("累计结算金额不能超过该项目的合同分配额")


def create_settlement(data):
    contract_id = int(data.get("contract_id") or 0) or None
    project_id = int(data.get("project_id") or 0)
    amount_minor = _minor(data.get("amount"))
    settlement_date = _date(data.get("settlement_date"), "结算日期")
    period_start = _date(data.get("period_start"), "结算开始日期", optional=True)
    period_end = _date(data.get("period_end"), "结算结束日期", optional=True)
    if period_start and period_end and period_end < period_start:
        raise ValueError("结算结束日期不能早于开始日期")
    with db_transaction(immediate=True) as conn:
        from services.invoice_income_service import protect_manual
        protect_manual(conn, contract_id=contract_id)
        project = _project_policy(conn, project_id)
        source_type = (
            "cash_job" if project["business_mode"] == "cash" else "contract"
        )
        if source_type == "cash_job":
            if contract_id:
                raise ValueError("零星现金工程不关联合同")
            contract_id = None
            validate_cash_income_capacity(conn, project_id, amount_minor)
        else:
            if not contract_id:
                raise ValueError("正式合同工程必须选择合同项目分配")
            _validate_contract_settlement(
                conn, contract_id, project_id, amount_minor
            )
        organization_id = _organization_id(conn)
        now = _now()
        cursor = conn.execute(
            """INSERT INTO settlements (
                   public_id, organization_id, settlement_no, contract_id,
                   project_id, settlement_date, period_start, period_end,
                   amount_minor, basis, source_type, status, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                str(uuid4()),
                organization_id,
                (data.get("settlement_no") or "").strip()
                or _number("WG" if source_type == "cash_job" else "JS"),
                contract_id,
                project_id,
                settlement_date,
                period_start,
                period_end,
                amount_minor,
                (data.get("basis") or "").strip(),
                source_type,
                now,
                now,
            ),
        )
        from services.advance_receipt_service import reconcile
        reconcile(conn, project_id, contract_id)
        return cursor.lastrowid


def update_settlement(settlement_id, data):
    """Edit an existing settlement confirmation with boundary checks."""
    settlement_id = int(settlement_id)
    amount_minor = _minor(data.get("amount"))
    settlement_date = _date(data.get("settlement_date"), "结算日期")
    period_start = _date(data.get("period_start"), "结算开始日期", optional=True)
    period_end = _date(data.get("period_end"), "结算结束日期", optional=True)
    if period_start and period_end and period_end < period_start:
        raise ValueError("结算结束日期不能早于开始日期")
    with db_transaction(immediate=True) as conn:
        from services.invoice_income_service import protect_manual
        protect_manual(conn, settlement_id=settlement_id,
                       contract_id=int(data.get('contract_id') or 0))
        current = conn.execute(
            """SELECT * FROM settlements WHERE id=? AND status='active'""",
            (settlement_id,),
        ).fetchone()
        if not current:
            raise ValueError("结算记录不存在或已作废")
        project_id = int(data.get("project_id") or current["project_id"])
        project = _project_policy(conn, project_id)
        source_type = (
            "cash_job" if project["business_mode"] == "cash" else "contract"
        )
        if source_type == "cash_job":
            if data.get("contract_id"):
                raise ValueError("零星现金工程不关联合同")
            contract_id = None
            validate_cash_income_capacity(
                conn,
                project_id,
                amount_minor,
                exclude_settlement_id=settlement_id,
            )
        else:
            contract_id = int(data.get("contract_id") or current["contract_id"] or 0)
            if not contract_id:
                raise ValueError("正式合同工程必须选择合同项目分配")
            _validate_contract_settlement(
                conn,
                contract_id,
                project_id,
                amount_minor,
                settlement_id=settlement_id,
            )
        invoiced_minor = conn.execute(
            """SELECT COALESCE(SUM(a.allocated_amount_minor), 0)
               FROM invoice_settlement_allocations a
               JOIN sales_invoices i ON i.id=a.invoice_id
               WHERE a.settlement_id=? AND i.status='active'""",
            (settlement_id,),
        ).fetchone()[0]
        if amount_minor < invoiced_minor:
            raise ValueError("结算金额不能低于已关联开票金额")
        received_minor = conn.execute(
            """SELECT COALESCE(SUM(ra.allocated_amount_minor), 0)
               FROM receipt_allocations ra
               JOIN receipts r ON r.id=ra.receipt_id
               WHERE ra.settlement_id=? AND r.status='active'""",
            (settlement_id,),
        ).fetchone()[0]
        if amount_minor < received_minor:
            raise ValueError("收入确认金额不能低于已回款金额")
        if (
            (invoiced_minor or received_minor)
            and (
                contract_id != current["contract_id"]
                or project_id != current["project_id"]
            )
        ):
            raise ValueError("收入确认已关联发票或回款，不能修改所属项目或合同")
        now = _now()
        conn.execute(
            """UPDATE settlements SET
                   contract_id=?, project_id=?, settlement_date=?,
                   period_start=?, period_end=?, amount_minor=?,
                   basis=?, source_type=?, updated_at=?
               WHERE id=?""",
            (
                contract_id,
                project_id,
                settlement_date,
                period_start,
                period_end,
                amount_minor,
                (data.get("basis") or "").strip(),
                source_type,
                now,
                settlement_id,
            ),
        )
        from services.advance_receipt_service import reconcile
        reconcile(conn, project_id, contract_id)
        return settlement_id


def void_settlements(settlement_ids):
    if not settlement_ids:
        return
    with db_transaction(immediate=True) as conn:
        from services.invoice_income_service import protect_manual
        for settlement_id in settlement_ids:
            protect_manual(conn, settlement_id=settlement_id)
        placeholders = ",".join("?" * len(settlement_ids))
        downstream = conn.execute(
            f"""SELECT COUNT(*) FROM settlements s
                WHERE s.id IN ({placeholders})
                  AND (
                      EXISTS (
                          SELECT 1
                          FROM invoice_settlement_allocations a
                          JOIN sales_invoices i ON i.id=a.invoice_id
                          WHERE a.settlement_id=s.id
                            AND i.status='active'
                      )
                      OR EXISTS (
                          SELECT 1 FROM sales_invoices i
                          WHERE i.contract_id=s.contract_id
                            AND i.project_id=s.project_id
                            AND i.status='active'
                            AND NOT EXISTS (
                                SELECT 1
                                FROM invoice_settlement_allocations a
                                WHERE a.invoice_id=i.id
                            )
                      )
                      OR EXISTS (
                          SELECT 1 FROM receipt_allocations ra
                          JOIN receipts r ON r.id=ra.receipt_id
                          WHERE (ra.settlement_id=s.id OR (
                                 ra.settlement_id IS NULL
                             AND ra.contract_id=s.contract_id
                             AND ra.project_id=s.project_id
                          ))
                            AND r.status='active'
                      )
                  )""",
            settlement_ids,
        ).fetchone()[0]
        if downstream:
            raise ValueError("该合同项目已有发票或回款，不能直接作废结算")
        conn.execute(
            f"""UPDATE settlements SET status='void', updated_at=?
                WHERE id IN ({placeholders}) AND status='active'""",
            (_now(), *settlement_ids),
        )
