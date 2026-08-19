from services._common import now as _now, organization_id as _organization_id
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from db.connection import get_connection


TOOL_EQUIPMENT_CATEGORY = "工具和设备"
PURCHASE_COST_CATEGORIES = (
    "材料费",
    TOOL_EQUIPMENT_CATEGORY,
    "其他",
)


def _rounded_minor(value):
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def calculate_purchase_amounts(
    quantity,
    material_unit_price_cents,
    tax_rate_bps=0,
    freight_amount_cents=0,
):
    """Calculate authoritative purchase snapshots using integer minor units."""
    try:
        quantity_value = Decimal(str(quantity))
        material_unit_price_cents = int(material_unit_price_cents)
        tax_rate_bps = int(tax_rate_bps)
        freight_amount_cents = int(freight_amount_cents)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("数量、材料价、税率和运费必须是有效数字") from error
    if not quantity_value.is_finite() or quantity_value <= 0:
        raise ValueError("数量必须大于 0")
    if material_unit_price_cents < 0:
        raise ValueError("材料单价不能为负数")
    if not 0 <= tax_rate_bps <= 10000:
        raise ValueError("税率必须在 0% 到 100% 之间")
    if freight_amount_cents < 0:
        raise ValueError("运费不能为负数")

    material_amount_cents = _rounded_minor(
        quantity_value * material_unit_price_cents
    )
    tax_amount_cents = _rounded_minor(
        Decimal(material_amount_cents) * tax_rate_bps / Decimal(10000)
    )
    tax_inclusive_unit_price_cents = _rounded_minor(
        Decimal(material_unit_price_cents)
        * (Decimal(10000) + tax_rate_bps)
        / Decimal(10000)
    )
    line_amount_cents = material_amount_cents + tax_amount_cents
    return {
        "material_unit_price_cents": material_unit_price_cents,
        "tax_rate_bps": tax_rate_bps,
        "material_amount_cents": material_amount_cents,
        "tax_amount_cents": tax_amount_cents,
        "tax_inclusive_unit_price_cents": tax_inclusive_unit_price_cents,
        # Compatibility fields remain tax-inclusive for older readers.
        "unit_price_cents": tax_inclusive_unit_price_cents,
        "line_amount_cents": line_amount_cents,
        "freight_amount_cents": freight_amount_cents,
        "project_cost_cents": line_amount_cents + freight_amount_cents,
    }


def _purchase_amounts(header, item):
    freight_amount_cents = int(header.get("freight_amount_cents", 0) or 0)
    if "material_unit_price_cents" in item or "tax_rate_bps" in item:
        return calculate_purchase_amounts(
            item.get("quantity", 1),
            item.get("material_unit_price_cents", 0),
            item.get("tax_rate_bps", 0),
            freight_amount_cents,
        )

    # Backward-compatible callers provide one already-final line amount.
    # Treat it as a tax-inclusive legacy amount without inferring new tax.
    try:
        quantity = Decimal(str(item.get("quantity", 1)))
        unit_price_cents = int(item.get("unit_price_cents", 0) or 0)
        line_amount_cents = int(
            item.get(
                "line_amount_cents",
                _rounded_minor(quantity * unit_price_cents),
            )
        )
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError("采购金额格式不正确") from error
    if (
        not quantity.is_finite()
        or quantity <= 0
        or unit_price_cents < 0
        or line_amount_cents < 0
    ):
        raise ValueError("数量必须大于 0，采购金额不能为负数")
    if freight_amount_cents < 0:
        raise ValueError("运费不能为负数")
    return {
        "material_unit_price_cents": unit_price_cents,
        "tax_rate_bps": 0,
        "material_amount_cents": line_amount_cents,
        "tax_amount_cents": 0,
        "tax_inclusive_unit_price_cents": unit_price_cents,
        "unit_price_cents": unit_price_cents,
        "line_amount_cents": line_amount_cents,
        "freight_amount_cents": freight_amount_cents,
        "project_cost_cents": line_amount_cents + freight_amount_cents,
    }


def build_equal_allocation_plan(total_amount_cents, project_ids):
    """Split an integer purchase amount equally and keep the cent remainder."""
    try:
        total_amount_cents = int(total_amount_cents)
        normalized = [int(value) for value in (project_ids or [])]
    except (TypeError, ValueError) as error:
        raise ValueError("分摊项目和采购金额格式不正确") from error
    if total_amount_cents < 0:
        raise ValueError("采购金额不能为负数")
    if len(normalized) < 2:
        raise ValueError("多项目平均分摊至少需要选择两个项目")
    has_invalid_project = any(value <= 0 for value in normalized)
    if len(normalized) != len(set(normalized)) or has_invalid_project:
        raise ValueError("分摊项目不能重复或为空")
    base, remainder = divmod(total_amount_cents, len(normalized))
    return [
        {
            "project_id": project_id,
            "amount_minor": base + (1 if index < remainder else 0),
        }
        for index, project_id in enumerate(normalized)
    ]


def _allocation_request(header, item, total_amount_cents):
    method = header.get("allocation_method") or (
        "direct" if header.get("project_id") else "unassigned"
    )
    if method not in ("direct", "equal", "unassigned"):
        raise ValueError("项目归集方式无效")
    if method == "equal":
        if item.get("cost_category") != TOOL_EQUIPMENT_CATEGORY:
            raise ValueError("只有“工具和设备”采购可以多项目平均分摊")
        return None, build_equal_allocation_plan(
            total_amount_cents, header.get("project_ids")
        )
    project_id = int(header.get("project_id") or 0) or None
    if method == "direct" and not project_id:
        raise ValueError("单项目归集需要选择所属项目")
    if method == "unassigned":
        project_id = None
    return project_id, []


def _replace_purchase_allocations(conn, order_id, plan, now):
    current_version = conn.execute(
        """SELECT COALESCE(MAX(allocation_version), 0)
           FROM purchase_cost_allocation_lines WHERE purchase_order_id=?""",
        (order_id,),
    ).fetchone()[0]
    conn.execute(
        """UPDATE purchase_cost_allocation_lines
           SET status='void', voided_at=?, updated_at=?
           WHERE purchase_order_id=? AND status='active'""",
        (now, now, order_id),
    )
    if not plan:
        return
    project_ids = [line["project_id"] for line in plan]
    placeholders = ",".join("?" * len(project_ids))
    existing_ids = {
        row["id"]
        for row in conn.execute(
            f"SELECT id FROM projects WHERE id IN ({placeholders})", project_ids
        ).fetchall()
    }
    if existing_ids != set(project_ids):
        raise ValueError("部分分摊项目不存在，请刷新后重试")
    version = current_version + 1
    for line in plan:
        conn.execute(
            """INSERT INTO purchase_cost_allocation_lines (
                   public_id, purchase_order_id, project_id, amount_minor,
                   allocation_method, allocation_version, status,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, 'equal', ?, 'active', ?, ?)""",
            (
                str(uuid4()),
                order_id,
                line["project_id"],
                line["amount_minor"],
                version,
                now,
                now,
            ),
        )


def _next_order_no(conn, purchase_type, purchase_date):
    prefix = "LS" if purchase_type == "零星采购" else "CG"
    base = f"{prefix}-{purchase_date.replace('-', '')}-"
    row = conn.execute(
        "SELECT order_no FROM purchase_orders WHERE order_no LIKE ? ORDER BY order_no DESC LIMIT 1",
        (base + "%",),
    ).fetchone()
    sequence = int(row["order_no"].split("-")[-1]) + 1 if row else 1
    return f"{base}{sequence:03d}"


def _relations(conn, partner_id, offer_id):
    legacy_supplier_id = None
    if partner_id:
        partner = conn.execute(
            """SELECT bp.legacy_supplier_id
               FROM business_partners bp
               JOIN partner_roles pr
                 ON pr.partner_id=bp.id AND pr.role_code='supplier'
               WHERE bp.id=? AND bp.status='active'""",
            (partner_id,),
        ).fetchone()
        if not partner:
            raise ValueError("供应商不存在、已停用或不具备供应商角色")
        legacy_supplier_id = partner["legacy_supplier_id"]

    legacy_product_id = material_id = None
    if offer_id:
        offer = conn.execute(
            "SELECT legacy_product_id, material_id, supplier_partner_id FROM supplier_offers WHERE id=?",
            (offer_id,),
        ).fetchone()
        if not offer:
            raise ValueError("供应商报价不存在")
        if partner_id and offer["supplier_partner_id"] != int(partner_id):
            raise ValueError("所选报价不属于当前供应商")
        legacy_product_id = offer["legacy_product_id"]
        material_id = offer["material_id"]
    return legacy_supplier_id, legacy_product_id, material_id


def add_purchase_order(header, item):
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = _now()
        partner_id = header.get("supplier_id")
        offer_id = item.get("product_id")
        legacy_supplier_id, legacy_product_id, material_id = _relations(
            conn, partner_id, offer_id
        )
        order_no = header.get("order_no") or _next_order_no(
            conn, header["purchase_type"], header["purchase_date"]
        )
        amounts = _purchase_amounts(header, item)
        project_id, allocation_plan = _allocation_request(
            header, item, amounts["project_cost_cents"]
        )
        cursor = conn.execute(
            """INSERT INTO purchase_orders
               (order_no, purchase_type, project_id, supplier_id, merchant_name_snapshot,
                purchase_date, payment_method, payment_status, invoice_status, purchaser,
                total_amount_cents, freight_amount_cents, status, notes, created_at, updated_at,
                public_id, organization_id, supplier_partner_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '有效', ?, ?, ?, ?, ?, ?)""",
            (order_no, header["purchase_type"], project_id, legacy_supplier_id,
             header["merchant_name_snapshot"], header["purchase_date"],
             header.get("payment_method", "未记录"), header.get("payment_status", "未确认"),
             header.get("invoice_status", "未确认"), header.get("purchaser", ""),
             amounts["project_cost_cents"], amounts["freight_amount_cents"],
             header.get("notes", ""), now, now, str(uuid4()),
             _organization_id(conn), partner_id),
        )
        order_id = cursor.lastrowid
        conn.execute(
            """INSERT INTO purchase_order_items
               (purchase_order_id, product_id, material_name_snapshot,
                specification_snapshot, unit_snapshot, cost_category, quantity,
                unit_price_cents, line_amount_cents,
                material_unit_price_cents, tax_rate_bps, material_amount_cents,
                tax_amount_cents, tax_inclusive_unit_price_cents, purpose, notes,
                public_id, material_id, supplier_offer_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (order_id, legacy_product_id, item["material_name_snapshot"],
             item.get("specification_snapshot", ""), item.get("unit_snapshot", ""),
             item.get("cost_category", "材料费"), item.get("quantity", 1),
             amounts["unit_price_cents"], amounts["line_amount_cents"],
             amounts["material_unit_price_cents"], amounts["tax_rate_bps"],
             amounts["material_amount_cents"], amounts["tax_amount_cents"],
             amounts["tax_inclusive_unit_price_cents"], item.get("purpose", ""),
             item.get("notes", ""), str(uuid4()), material_id, offer_id),
        )
        _replace_purchase_allocations(conn, order_id, allocation_plan, now)
        conn.commit()
        return order_id
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _base_query():
    return """
        SELECT COALESCE(po.supplier_partner_id, bp.id) AS supplier_id,
               poi.supplier_offer_id AS product_id,
               po.*,
               CASE
                 WHEN po.project_id IS NOT NULL THEN pr.name
                 WHEN COALESCE(pa.allocation_count, 0) > 0
                   THEN pa.allocation_count || '个项目均摊'
                 ELSE NULL
               END AS project_name,
               pa.allocation_project_names,
               pa.allocation_project_ids,
               COALESCE(pa.allocation_count, 0) AS allocation_project_count,
               CASE WHEN COALESCE(pa.allocation_count, 0) > 0
                    THEN 'equal'
                    WHEN po.project_id IS NOT NULL THEN 'direct'
                    ELSE 'unassigned' END AS allocation_method,
               COALESCE(bp.legal_name, po.merchant_name_snapshot) AS supplier_name,
               poi.id AS item_id, poi.material_id, poi.supplier_offer_id,
               poi.material_name_snapshot, poi.specification_snapshot,
               poi.unit_snapshot, poi.cost_category, poi.quantity,
               poi.unit_price_cents, poi.line_amount_cents,
               poi.material_unit_price_cents, poi.tax_rate_bps,
               poi.material_amount_cents, poi.tax_amount_cents,
               poi.tax_inclusive_unit_price_cents, po.freight_amount_cents,
               po.total_amount_cents AS project_cost_cents, poi.purpose,
               poi.notes AS item_notes
        FROM purchase_orders po
        LEFT JOIN projects pr ON po.project_id=pr.id
        LEFT JOIN business_partners bp ON po.supplier_partner_id=bp.id
        LEFT JOIN (
            SELECT pal.purchase_order_id,
                   COUNT(*) AS allocation_count,
                   GROUP_CONCAT(p.name, '、') AS allocation_project_names,
                   GROUP_CONCAT(pal.project_id) AS allocation_project_ids
            FROM purchase_cost_allocation_lines pal
            JOIN projects p ON p.id=pal.project_id
            WHERE pal.status='active'
            GROUP BY pal.purchase_order_id
        ) pa ON pa.purchase_order_id=po.id
        JOIN purchase_order_items poi ON poi.purchase_order_id=po.id
    """


def list_purchase_orders(month="", purchase_type="", project_id=None, keyword="", unassigned_only=False):
    conn = get_connection()
    try:
        sql = _base_query() + " WHERE po.status='有效'"
        params = []
        if month:
            sql += " AND substr(po.purchase_date, 1, 7)=?"
            params.append(month)
        if purchase_type:
            sql += " AND po.purchase_type=?"
            params.append(purchase_type)
        if project_id:
            sql += """ AND (
                po.project_id=? OR EXISTS (
                    SELECT 1 FROM purchase_cost_allocation_lines match_line
                    WHERE match_line.purchase_order_id=po.id
                      AND match_line.project_id=? AND match_line.status='active'
                )
            )"""
            params.extend((project_id, project_id))
        if unassigned_only:
            sql += """ AND po.project_id IS NULL AND NOT EXISTS (
                SELECT 1 FROM purchase_cost_allocation_lines active_line
                WHERE active_line.purchase_order_id=po.id
                  AND active_line.status='active'
            )"""
        if keyword:
            sql += """ AND (po.order_no LIKE ? OR po.merchant_name_snapshot LIKE ?
                              OR poi.material_name_snapshot LIKE ? OR poi.specification_snapshot LIKE ?
                              OR poi.purpose LIKE ? OR po.purchaser LIKE ?)"""
            params.extend([f"%{keyword}%"] * 6)
        sql += " ORDER BY po.purchase_date DESC, po.id DESC, poi.id"
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def get_purchase_order(order_id):
    conn = get_connection()
    try:
        row = conn.execute(
            _base_query() + " WHERE po.id=? AND po.status='有效' ORDER BY poi.id LIMIT 1",
            (order_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_purchase_allocations(order_id, include_void=False):
    conn = get_connection()
    try:
        where = "pal.purchase_order_id=?"
        if not include_void:
            where += " AND pal.status='active'"
        return [
            dict(row)
            for row in conn.execute(
                f"""SELECT pal.*, p.name AS project_name,
                           p.project_code
                    FROM purchase_cost_allocation_lines pal
                    JOIN projects p ON p.id=pal.project_id
                    WHERE {where}
                    ORDER BY pal.allocation_version, pal.project_id""",
                (order_id,),
            ).fetchall()
        ]
    finally:
        conn.close()


def update_purchase_order(order_id, header, item):
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            "SELECT purchase_type, status FROM purchase_orders WHERE id=?", (order_id,)
        ).fetchone()
        if not existing or existing["status"] != "有效":
            raise ValueError("采购单不存在或已作废，不能修改。")
        if existing["purchase_type"] != header["purchase_type"]:
            raise ValueError("采购类型不能直接变更，请作废后重新录入。")
        item_row = conn.execute(
            "SELECT id FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id LIMIT 1",
            (order_id,),
        ).fetchone()
        if not item_row:
            raise ValueError("采购单缺少明细，无法修改。")
        partner_id = header.get("supplier_id")
        offer_id = item.get("product_id")
        legacy_supplier_id, legacy_product_id, material_id = _relations(
            conn, partner_id, offer_id
        )
        now = _now()
        amounts = _purchase_amounts(header, item)
        project_id, allocation_plan = _allocation_request(
            header, item, amounts["project_cost_cents"]
        )
        conn.execute(
            """UPDATE purchase_orders SET project_id=?, supplier_id=?, supplier_partner_id=?,
               merchant_name_snapshot=?, purchase_date=?, payment_method=?, payment_status=?,
               invoice_status=?, purchaser=?, total_amount_cents=?, freight_amount_cents=?,
               notes=?, updated_at=?
               WHERE id=?""",
            (project_id, legacy_supplier_id, partner_id,
             header["merchant_name_snapshot"], header["purchase_date"],
             header.get("payment_method", "未记录"), header.get("payment_status", "未确认"),
             header.get("invoice_status", "未确认"), header.get("purchaser", ""),
             amounts["project_cost_cents"], amounts["freight_amount_cents"],
             header.get("notes", ""), now, order_id),
        )
        conn.execute(
            """UPDATE purchase_order_items SET product_id=?, material_id=?, supplier_offer_id=?,
               material_name_snapshot=?, specification_snapshot=?, unit_snapshot=?,
               cost_category=?, quantity=?, unit_price_cents=?, line_amount_cents=?,
               material_unit_price_cents=?, tax_rate_bps=?, material_amount_cents=?,
               tax_amount_cents=?, tax_inclusive_unit_price_cents=?,
               purpose=?, notes=? WHERE id=?""",
            (legacy_product_id, material_id, offer_id, item["material_name_snapshot"],
             item.get("specification_snapshot", ""), item.get("unit_snapshot", ""),
             item.get("cost_category", "材料费"), item.get("quantity", 1),
             amounts["unit_price_cents"], amounts["line_amount_cents"],
             amounts["material_unit_price_cents"], amounts["tax_rate_bps"],
             amounts["material_amount_cents"], amounts["tax_amount_cents"],
             amounts["tax_inclusive_unit_price_cents"], item.get("purpose", ""),
             item.get("notes", ""), item_row["id"]),
        )
        _replace_purchase_allocations(conn, order_id, allocation_plan, now)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def assign_purchase_project(order_ids, project_id):
    if not order_ids:
        return 0
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" * len(order_ids))
        now = _now()
        conn.execute(
            f"""UPDATE purchase_cost_allocation_lines
                SET status='void', voided_at=?, updated_at=?
                WHERE purchase_order_id IN ({placeholders})
                  AND status='active'""",
            (now, now, *order_ids),
        )
        result = conn.execute(
            f"""UPDATE purchase_orders SET project_id=?, updated_at=?
                WHERE id IN ({placeholders}) AND status='有效'""",
            (project_id, now, *order_ids),
        )
        conn.commit()
        return result.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_purchase_order_status(
    order_ids, payment_method, payment_status, invoice_status
):
    if not order_ids:
        return 0
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(order_ids))
        result = conn.execute(
            f"""UPDATE purchase_orders
                SET payment_method=?, payment_status=?, invoice_status=?, updated_at=?
                WHERE id IN ({placeholders}) AND status='有效'""",
            (payment_method, payment_status, invoice_status, _now(), *order_ids),
        )
        conn.commit()
        return result.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def void_purchase_orders(order_ids):
    if not order_ids:
        return 0
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        placeholders = ",".join("?" * len(order_ids))
        now = _now()
        result = conn.execute(
            f"""UPDATE purchase_orders SET status='作废', updated_at=?
                WHERE id IN ({placeholders}) AND status='有效'""",
            (now, *order_ids),
        )
        conn.execute(
            f"""UPDATE purchase_cost_allocation_lines
                SET status='void', voided_at=?, updated_at=?
                WHERE purchase_order_id IN ({placeholders})
                  AND status='active'""",
            (now, now, *order_ids),
        )
        conn.commit()
        return result.rowcount
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_purchase_months():
    conn = get_connection()
    try:
        return [
            row["month"]
            for row in conn.execute(
                """SELECT DISTINCT substr(purchase_date, 1, 7) AS month
                   FROM purchase_orders WHERE status='有效'
                   ORDER BY month DESC"""
            ).fetchall()
            if row["month"]
        ]
    finally:
        conn.close()


def get_purchase_dashboard(month, project_id=None):
    conn = get_connection()
    try:
        if project_id:
            params = (project_id, month)
            summary = conn.execute(
                """SELECT COALESCE(SUM(ppc.cost_minor), 0) AS total_cents,
                          COALESCE(SUM(CASE WHEN po.purchase_type='正式采购'
                                           THEN ppc.cost_minor ELSE 0 END), 0)
                              AS formal_cents,
                          COALESCE(SUM(CASE WHEN po.purchase_type='零星采购'
                                           THEN ppc.cost_minor ELSE 0 END), 0)
                              AS petty_cents,
                          COUNT(DISTINCT po.merchant_name_snapshot)
                              AS merchant_count,
                          0 AS unassigned_cents,
                          COALESCE(SUM(CASE WHEN po.invoice_status
                                                   IN ('无发票', '未确认')
                                           THEN ppc.cost_minor ELSE 0 END), 0)
                              AS no_invoice_cents,
                          COALESCE(SUM(CASE WHEN po.payment_method='员工垫付'
                                                AND po.payment_status<>'已付款'
                                           THEN ppc.cost_minor ELSE 0 END), 0)
                              AS reimbursement_cents,
                          COUNT(DISTINCT po.id) AS order_count
                   FROM purchase_project_costs ppc
                   JOIN purchase_orders po ON po.id=ppc.purchase_order_id
                   WHERE ppc.project_id=? AND po.status='有效'
                     AND substr(po.purchase_date, 1, 7)=?""",
                params,
            ).fetchone()
            by_project = _dashboard_rows(
                conn,
                """SELECT p.name AS label, SUM(ppc.cost_minor) AS amount_cents,
                          COUNT(DISTINCT po.id) AS order_count
                   FROM purchase_project_costs ppc
                   JOIN purchase_orders po ON po.id=ppc.purchase_order_id
                   JOIN projects p ON p.id=ppc.project_id
                   WHERE ppc.project_id=? AND po.status='有效'
                     AND substr(po.purchase_date, 1, 7)=?
                   GROUP BY p.id, p.name""",
                params,
            )
            by_merchant = _dashboard_rows(
                conn,
                """SELECT po.merchant_name_snapshot AS label,
                          SUM(ppc.cost_minor) AS amount_cents,
                          COUNT(DISTINCT po.id) AS order_count
                   FROM purchase_project_costs ppc
                   JOIN purchase_orders po ON po.id=ppc.purchase_order_id
                   WHERE ppc.project_id=? AND po.status='有效'
                     AND substr(po.purchase_date, 1, 7)=?
                   GROUP BY po.merchant_name_snapshot
                   ORDER BY amount_cents DESC LIMIT 8""",
                params,
            )
        else:
            summary = conn.execute(
                """SELECT COALESCE(SUM(po.total_amount_cents), 0)
                              AS total_cents,
                          COALESCE(SUM(CASE WHEN po.purchase_type='正式采购'
                                           THEN po.total_amount_cents ELSE 0 END), 0)
                              AS formal_cents,
                          COALESCE(SUM(CASE WHEN po.purchase_type='零星采购'
                                           THEN po.total_amount_cents ELSE 0 END), 0)
                              AS petty_cents,
                          COUNT(DISTINCT po.merchant_name_snapshot)
                              AS merchant_count,
                          COALESCE(SUM(CASE WHEN po.project_id IS NULL
                                                AND NOT EXISTS (
                                                  SELECT 1
                                                  FROM purchase_cost_allocation_lines pal
                                                  WHERE pal.purchase_order_id=po.id
                                                    AND pal.status='active'
                                                )
                                           THEN po.total_amount_cents ELSE 0 END), 0)
                              AS unassigned_cents,
                          COALESCE(SUM(CASE WHEN po.invoice_status
                                                   IN ('无发票', '未确认')
                                           THEN po.total_amount_cents ELSE 0 END), 0)
                              AS no_invoice_cents,
                          COALESCE(SUM(CASE WHEN po.payment_method='员工垫付'
                                                AND po.payment_status<>'已付款'
                                           THEN po.total_amount_cents ELSE 0 END), 0)
                              AS reimbursement_cents,
                          COUNT(*) AS order_count
                   FROM purchase_orders po
                   WHERE po.status='有效'
                     AND substr(po.purchase_date, 1, 7)=?""",
                (month,),
            ).fetchone()
            by_project = _dashboard_rows(
                conn,
                """WITH month_orders AS (
                       SELECT * FROM purchase_orders
                       WHERE status='有效'
                         AND substr(purchase_date, 1, 7)=?
                   ), assignments AS (
                       SELECT id AS order_id, project_id,
                              total_amount_cents AS amount_cents
                       FROM month_orders WHERE project_id IS NOT NULL
                       UNION ALL
                       SELECT mo.id, pal.project_id, pal.amount_minor
                       FROM month_orders mo
                       JOIN purchase_cost_allocation_lines pal
                         ON pal.purchase_order_id=mo.id AND pal.status='active'
                       WHERE mo.project_id IS NULL
                       UNION ALL
                       SELECT mo.id, NULL, mo.total_amount_cents
                       FROM month_orders mo
                       WHERE mo.project_id IS NULL AND NOT EXISTS (
                           SELECT 1 FROM purchase_cost_allocation_lines pal
                           WHERE pal.purchase_order_id=mo.id
                             AND pal.status='active'
                       )
                   )
                   SELECT COALESCE(p.name, '待归集') AS label,
                          SUM(a.amount_cents) AS amount_cents,
                          COUNT(DISTINCT a.order_id) AS order_count
                   FROM assignments a
                   LEFT JOIN projects p ON p.id=a.project_id
                   GROUP BY a.project_id, COALESCE(p.name, '待归集')
                   ORDER BY amount_cents DESC LIMIT 8""",
                (month,),
            )
            by_merchant = _dashboard_rows(
                conn,
                """SELECT po.merchant_name_snapshot AS label,
                          SUM(po.total_amount_cents) AS amount_cents,
                          COUNT(*) AS order_count
                   FROM purchase_orders po
                   WHERE po.status='有效'
                     AND substr(po.purchase_date, 1, 7)=?
                   GROUP BY po.merchant_name_snapshot
                   ORDER BY amount_cents DESC LIMIT 8""",
                (month,),
            )
        year, month_number = map(int, month.split("-"))
        previous_month = (
            f"{year - 1}-12" if month_number == 1 else f"{year}-{month_number - 1:02d}"
        )
        if project_id:
            previous_cents = conn.execute(
                """SELECT COALESCE(SUM(ppc.cost_minor), 0)
                   FROM purchase_project_costs ppc
                   JOIN purchase_orders po ON po.id=ppc.purchase_order_id
                   WHERE ppc.project_id=? AND po.status='有效'
                     AND substr(po.purchase_date, 1, 7)=?""",
                (project_id, previous_month),
            ).fetchone()[0]
        else:
            previous_cents = conn.execute(
                """SELECT COALESCE(SUM(total_amount_cents), 0)
                   FROM purchase_orders
                   WHERE status='有效' AND substr(purchase_date, 1, 7)=?""",
                (previous_month,),
            ).fetchone()[0]
        result = dict(summary)
        result["previous_cents"] = previous_cents
        return {"summary": result, "by_project": by_project, "by_merchant": by_merchant}
    finally:
        conn.close()


def _dashboard_rows(conn, sql, params):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]
