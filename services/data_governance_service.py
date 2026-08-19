"""经营数据治理查询与安全确认操作。

本模块只处理会影响项目成本、客户应收或经营口径的缺口。历史自由文本仅
用于帮助用户判断，不作为自动匹配依据；所有归属变更都要求显式目标。
"""

from db.connection import get_connection
from services._common import now as _now
from services.project_service import _customer_id


def _rows(conn, sql, params=()):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def get_governance_summary():
    conn = get_connection()
    try:
        labor = conn.execute(
            """SELECT COUNT(*) AS record_count,
                      COALESCE(SUM(work_days), 0) AS work_days,
                      COALESCE(SUM(amount_minor), 0) AS amount_minor
               FROM work_logs
               WHERE COALESCE(status, 'active')='active' AND project_id IS NULL"""
        ).fetchone()
        purchase = conn.execute(
            """SELECT COUNT(*) AS record_count,
                      COALESCE(SUM(total_amount_cents), 0) AS amount_minor
               FROM purchase_orders
               WHERE status='有效' AND project_id IS NULL"""
        ).fetchone()
        projects = conn.execute(
            """SELECT
                   SUM(CASE WHEN customer_partner_id IS NULL THEN 1 ELSE 0 END)
                       AS missing_customer_count,
                   SUM(CASE WHEN NOT EXISTS (
                           SELECT 1 FROM project_sites ps
                           WHERE ps.project_id=projects.id AND ps.is_active=1
                       ) THEN 1 ELSE 0 END) AS missing_site_count,
                   SUM(CASE WHEN NOT EXISTS (
                           SELECT 1 FROM contract_project_allocations a
                           WHERE a.project_id=projects.id AND a.status='active'
                       ) AND projects.business_mode='contract'
                       THEN 1 ELSE 0 END) AS missing_contract_count,
                   SUM(CASE WHEN NOT EXISTS (
                           SELECT 1 FROM settlements s
                           WHERE s.project_id=projects.id AND s.status='active'
                       ) THEN 1 ELSE 0 END) AS missing_settlement_count
               FROM projects WHERE status='进行中'"""
        ).fetchone()
        pending_inspection = conn.execute(
            """SELECT COUNT(*) FROM construction_records
               WHERE record_status='有效' AND inspection_status='待验收'"""
        ).fetchone()[0]
        pending_partners = conn.execute(
            "SELECT COUNT(*) FROM business_partners WHERE status='pending'"
        ).fetchone()[0]
        cash_unpaid = conn.execute(
            """SELECT COUNT(*) FROM settlements s
               WHERE s.status='active' AND s.source_type='cash_job'
                 AND s.amount_minor > COALESCE((
                     SELECT SUM(ra.allocated_amount_minor)
                     FROM receipt_allocations ra
                     JOIN receipts r ON r.id=ra.receipt_id
                     WHERE ra.settlement_id=s.id AND r.status='active'
                 ), 0)"""
        ).fetchone()[0]
        cash_receipts_without_voucher = conn.execute(
            """SELECT COUNT(DISTINCT r.id)
               FROM receipts r
               JOIN receipt_allocations ra ON ra.receipt_id=r.id
               JOIN settlements s ON s.id=ra.settlement_id
               WHERE r.status='active' AND s.source_type='cash_job'
                 AND NOT EXISTS (
                     SELECT 1 FROM business_attachments ba
                     WHERE ba.receipt_id=r.id AND ba.status='active'
                 )"""
        ).fetchone()[0]
        return {
            "labor_record_count": int(labor["record_count"] or 0),
            "labor_work_days": float(labor["work_days"] or 0),
            "labor_amount_minor": int(labor["amount_minor"] or 0),
            "purchase_record_count": int(purchase["record_count"] or 0),
            "purchase_amount_minor": int(purchase["amount_minor"] or 0),
            "missing_customer_count": int(projects["missing_customer_count"] or 0),
            "missing_site_count": int(projects["missing_site_count"] or 0),
            "missing_contract_count": int(projects["missing_contract_count"] or 0),
            "missing_settlement_count": int(projects["missing_settlement_count"] or 0),
            "pending_inspection_count": int(pending_inspection or 0),
            "pending_partner_count": int(pending_partners or 0),
            "cash_unpaid_count": int(cash_unpaid or 0),
            "cash_receipt_missing_voucher_count": int(
                cash_receipts_without_voucher or 0
            ),
        }
    finally:
        conn.close()


def list_unassigned_labor(keyword=""):
    conn = get_connection()
    try:
        sql = """SELECT wl.id, wl.work_date, w.name AS worker_name,
                        wl.construction_site, COALESCE(wl.work_type, '') AS work_type,
                        wl.work_days, COALESCE(wl.amount_minor, ROUND(wl.amount * 100), 0)
                            AS amount_minor,
                        COALESCE(wl.notes, '') AS notes
                 FROM work_logs wl
                 JOIN workers w ON w.id=wl.worker_id
                 WHERE COALESCE(wl.status, 'active')='active'
                   AND wl.project_id IS NULL"""
        params = []
        if keyword:
            sql += """ AND (w.name LIKE ? OR wl.construction_site LIKE ?
                             OR wl.work_type LIKE ? OR wl.notes LIKE ?)"""
            params.extend([f"%{keyword}%"] * 4)
        sql += " ORDER BY wl.work_date DESC, wl.construction_site, wl.id DESC"
        return _rows(conn, sql, params)
    finally:
        conn.close()


def list_unassigned_labor_groups():
    conn = get_connection()
    try:
        return _rows(
            conn,
            """SELECT construction_site,
                      COUNT(*) AS record_count,
                      COUNT(DISTINCT worker_id) AS worker_count,
                      COALESCE(SUM(work_days), 0) AS work_days,
                      COALESCE(SUM(amount_minor), 0) AS amount_minor,
                      MIN(work_date) AS first_date, MAX(work_date) AS last_date
               FROM work_logs
               WHERE COALESCE(status, 'active')='active' AND project_id IS NULL
               GROUP BY construction_site
               ORDER BY amount_minor DESC, record_count DESC""",
        )
    finally:
        conn.close()


def assign_labor_records(work_log_ids, project_id, project_site_id=None):
    ids = sorted({int(value) for value in work_log_ids})
    if not ids:
        raise ValueError("请先选择需要归集的人工记录")
    project_id = int(project_id or 0)
    if not project_id:
        raise ValueError("请选择目标项目")
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        project = conn.execute(
            "SELECT id FROM projects WHERE id=? AND status<>'已关闭'", (project_id,)
        ).fetchone()
        if not project:
            raise ValueError("目标项目不存在或已关闭")
        site_name = None
        site_id = int(project_site_id or 0) or None
        if site_id:
            site = conn.execute(
                """SELECT name FROM project_sites
                   WHERE id=? AND project_id=? AND is_active=1""",
                (site_id, project_id),
            ).fetchone()
            if not site:
                raise ValueError("目标施工地点不属于所选项目或已停用")
            site_name = site["name"]
        placeholders = ",".join("?" * len(ids))
        found = conn.execute(
            f"""SELECT COUNT(*) FROM work_logs
                WHERE id IN ({placeholders})
                  AND COALESCE(status, 'active')='active' AND project_id IS NULL""",
            ids,
        ).fetchone()[0]
        if found != len(ids):
            raise ValueError("部分记录已被归集、作废或不存在，请刷新后重试")
        if site_name:
            result = conn.execute(
                f"""UPDATE work_logs
                    SET project_id=?, project_site_id=?, construction_site=?, updated_at=?
                    WHERE id IN ({placeholders})""",
                (project_id, site_id, site_name, _now(), *ids),
            )
        else:
            result = conn.execute(
                f"""UPDATE work_logs
                    SET project_id=?, project_site_id=NULL, updated_at=?
                    WHERE id IN ({placeholders})""",
                (project_id, _now(), *ids),
            )
        conn.commit()
        return result.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_unassigned_purchases(keyword=""):
    conn = get_connection()
    try:
        sql = """SELECT po.id, po.order_no, po.purchase_date,
                        po.merchant_name_snapshot AS supplier_name,
                        GROUP_CONCAT(poi.material_name_snapshot, '、') AS materials,
                        po.total_amount_cents AS amount_minor,
                        COALESCE(po.notes, '') AS notes
                 FROM purchase_orders po
                 LEFT JOIN purchase_order_items poi ON poi.purchase_order_id=po.id
                 WHERE po.status='有效' AND po.project_id IS NULL
                   AND NOT EXISTS (
                     SELECT 1 FROM purchase_cost_allocation_lines pal
                     WHERE pal.purchase_order_id=po.id AND pal.status='active'
                   )"""
        params = []
        if keyword:
            sql += """ AND (po.order_no LIKE ? OR po.merchant_name_snapshot LIKE ?
                             OR poi.material_name_snapshot LIKE ? OR po.notes LIKE ?)"""
            params.extend([f"%{keyword}%"] * 4)
        sql += " GROUP BY po.id ORDER BY po.purchase_date DESC, po.id DESC"
        return _rows(conn, sql, params)
    finally:
        conn.close()


def assign_purchase_orders(order_ids, project_id):
    ids = sorted({int(value) for value in order_ids})
    if not ids:
        raise ValueError("请先选择需要归集的采购记录")
    project_id = int(project_id or 0)
    if not project_id:
        raise ValueError("请选择目标项目")
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        project = conn.execute(
            "SELECT id FROM projects WHERE id=? AND status<>'已关闭'", (project_id,)
        ).fetchone()
        if not project:
            raise ValueError("目标项目不存在或已关闭")
        placeholders = ",".join("?" * len(ids))
        found = conn.execute(
            f"""SELECT COUNT(*) FROM purchase_orders
                WHERE id IN ({placeholders}) AND status='有效'
                  AND project_id IS NULL AND NOT EXISTS (
                    SELECT 1 FROM purchase_cost_allocation_lines pal
                    WHERE pal.purchase_order_id=purchase_orders.id
                      AND pal.status='active'
                  )""",
            ids,
        ).fetchone()[0]
        if found != len(ids):
            raise ValueError("部分采购已被归集、作废或不存在，请刷新后重试")
        result = conn.execute(
            f"""UPDATE purchase_orders SET project_id=?, updated_at=?
                WHERE id IN ({placeholders})""",
            (project_id, _now(), *ids),
        )
        conn.commit()
        return result.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_project_completeness(active_only=True):
    conn = get_connection()
    try:
        where = "WHERE p.status='进行中'" if active_only else ""
        return _rows(
            conn,
            f"""SELECT p.id, p.project_code, p.name, p.status,
                       p.business_mode, p.invoice_policy,
                       COALESCE(bp.legal_name, p.customer_name, '') AS customer_name,
                       p.customer_partner_id,
                       (SELECT COUNT(*) FROM project_sites ps
                        WHERE ps.project_id=p.id AND ps.is_active=1) AS site_count,
                       (SELECT COUNT(*) FROM contract_project_allocations a
                        WHERE a.project_id=p.id AND a.status='active') AS contract_count,
                       (SELECT COUNT(*) FROM settlements s
                        WHERE s.project_id=p.id AND s.status='active') AS settlement_count
                FROM projects p
                LEFT JOIN business_partners bp ON bp.id=p.customer_partner_id
                {where}
                ORDER BY CASE p.status WHEN '进行中' THEN 1 ELSE 2 END, p.id DESC""",
        )
    finally:
        conn.close()


def confirm_project_customer(project_id, customer_name, *, update_contracts=False):
    """确认项目客户；可同时补齐该项目已分配合同的空客户关系。"""
    project_id = int(project_id or 0)
    customer_name = (customer_name or "").strip()
    if not project_id or not customer_name:
        raise ValueError("请选择项目并填写客户名称")
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        project = conn.execute(
            "SELECT organization_id FROM projects WHERE id=?", (project_id,)
        ).fetchone()
        if not project:
            raise ValueError("项目不存在")
        now = _now()
        partner_id = _customer_id(
            conn, int(project["organization_id"]), customer_name, now
        )
        conn.execute(
            """UPDATE projects SET customer_name=?, customer_partner_id=?, updated_at=?
               WHERE id=?""",
            (customer_name, partner_id, now, project_id),
        )
        contract_count = 0
        if update_contracts:
            result = conn.execute(
                """UPDATE contracts
                   SET customer_partner_id=?, customer_name_snapshot=?, updated_at=?
                   WHERE customer_partner_id IS NULL AND status='active'
                     AND id IN (
                       SELECT contract_id FROM contract_project_allocations
                       WHERE project_id=? AND status='active'
                     )""",
                (partner_id, customer_name, now, project_id),
            )
            contract_count = result.rowcount
        conn.commit()
        return {"partner_id": partner_id, "contract_count": contract_count}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_fulfillment_gaps():
    conn = get_connection()
    try:
        rows = []
        pending_partners = _rows(
            conn,
            """SELECT bp.id, '待确认客商' AS issue_type,
                      '' AS project_name, bp.legal_name AS subject,
                      substr(bp.created_at, 1, 10) AS business_date,
                      0 AS amount_minor,
                      '进入客商档案补齐角色、联系人、税号与开票资料' AS action
               FROM business_partners bp
               WHERE bp.status='pending'
               ORDER BY bp.created_at DESC, bp.id DESC""",
        )
        rows.extend(pending_partners)
        for row in _rows(
            conn,
            """SELECT cr.id, '待验收施工' AS issue_type,
                      p.name AS project_name, cs.site_name AS subject,
                      COALESCE(cr.end_date, cr.record_date) AS business_date,
                      cr.work_amount_cents AS amount_minor,
                      '请进入施工与验收确认结果' AS action
               FROM construction_records cr
               JOIN construction_sites cs ON cs.id=cr.site_id
               JOIN projects p ON p.id=cs.project_id
               WHERE cr.record_status='有效' AND cr.inspection_status='待验收'
               ORDER BY business_date""",
        ):
            rows.append(row)
        missing_attachments = _rows(
            conn,
            """SELECT c.id, '合同缺附件' AS issue_type,
                      COALESCE(GROUP_CONCAT(DISTINCT p.name), '未分配项目') AS project_name,
                      c.name AS subject, c.sign_date AS business_date,
                      c.tax_inclusive_amount_minor AS amount_minor,
                      '补充合同原件或关键依据' AS action
               FROM contracts c
               LEFT JOIN contract_project_allocations a
                 ON a.contract_id=c.id AND a.status='active'
               LEFT JOIN projects p ON p.id=a.project_id
               WHERE c.status='active' AND NOT EXISTS (
                   SELECT 1 FROM business_attachments ba
                   WHERE ba.contract_id=c.id AND ba.status='active'
               )
               GROUP BY c.id ORDER BY c.sign_date DESC""",
        )
        rows.extend(missing_attachments)
        cash_receivables = _rows(
            conn,
            """SELECT s.id, '零星工程待收款' AS issue_type,
                      p.name AS project_name,
                      s.settlement_no || ' · 完工金额确认' AS subject,
                      s.settlement_date AS business_date,
                      s.amount_minor - COALESCE((
                          SELECT SUM(ra.allocated_amount_minor)
                          FROM receipt_allocations ra
                          JOIN receipts r ON r.id=ra.receipt_id
                          WHERE ra.settlement_id=s.id AND r.status='active'
                      ), 0) AS amount_minor,
                      '登记现金回款并关联到本次完工金额确认' AS action
               FROM settlements s
               JOIN projects p ON p.id=s.project_id
               WHERE s.status='active' AND s.source_type='cash_job'
                 AND s.amount_minor > COALESCE((
                     SELECT SUM(ra.allocated_amount_minor)
                     FROM receipt_allocations ra
                     JOIN receipts r ON r.id=ra.receipt_id
                     WHERE ra.settlement_id=s.id AND r.status='active'
                 ), 0)
               ORDER BY s.settlement_date, s.id""",
        )
        rows.extend(cash_receivables)
        cash_receipts_without_voucher = _rows(
            conn,
            """SELECT r.id, '现金回款缺凭证' AS issue_type,
                      p.name AS project_name, r.receipt_no AS subject,
                      r.receipt_date AS business_date,
                      r.amount_minor AS amount_minor,
                      '补充收据、签收单或现金收款照片' AS action
               FROM receipts r
               JOIN receipt_allocations ra ON ra.receipt_id=r.id
               JOIN settlements s ON s.id=ra.settlement_id
               JOIN projects p ON p.id=ra.project_id
               WHERE r.status='active' AND s.source_type='cash_job'
                 AND NOT EXISTS (
                     SELECT 1 FROM business_attachments ba
                     WHERE ba.receipt_id=r.id AND ba.status='active'
                 )
               ORDER BY r.receipt_date DESC, r.id DESC""",
        )
        rows.extend(cash_receipts_without_voucher)
        return rows
    finally:
        conn.close()
