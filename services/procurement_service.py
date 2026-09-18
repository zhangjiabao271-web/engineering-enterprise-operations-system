from services._common import now as _now, organization_id as _organization_id
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4
import json

from db.connection import db_read, db_transaction


TOOL_EQUIPMENT_CATEGORY = "工具和设备"
SETTLEMENT_MODES = {"quantity": "按数量", "weight": "按过磅重量", "total": "按结算总额"}
PURCHASE_STATUS_LABELS = {"active": "有效", "void": "作废"}


def _decimal_input(value, label, *, positive=False):
    try:
        number = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{label}必须为有效数字") from error
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise ValueError(f"{label}必须为{'正' if positive else '非负'}有限数字")
    return number


def prepare_purchase_item(item):
    item = dict(item)
    mode = item.get("settlement_mode", "quantity")
    if mode not in SETTLEMENT_MODES:
        raise ValueError("采购计价方式无效")
    item["settlement_mode"] = mode
    if mode == "weight":
        weight = _decimal_input(item.get("net_weight"), "实际净重", positive=True)
        price = _decimal_input(item.get("weight_unit_price"), "重量单价")
        if item.get("weight_unit") not in ("吨", "公斤"):
            raise ValueError("过磅单位只能为吨或公斤，单价单位须与重量一致")
        item.update(net_weight=format(weight, "f"), weight_unit_price=format(price, "f"),
                    quantity=str(weight), unit_snapshot=item["weight_unit"], settlement_total_cents=None)
    elif mode == "total":
        total = _decimal_input(item.get("settlement_total_cents"), "结算总额（分）")
        if total != total.to_integral_value():
            raise ValueError("结算总额必须精确到分")
        item.update(settlement_total_cents=int(total), quantity=1, unit_snapshot="批",
                    net_weight=None, weight_unit=None, weight_unit_price=None)
    else:
        item.update(net_weight=None, weight_unit=None, weight_unit_price=None, settlement_total_cents=None)
    item["weigh_ticket_no"] = str(item.get("weigh_ticket_no") or "").strip()
    return item


PURCHASE_COST_CATEGORIES = (
    "材料费",
    TOOL_EQUIPMENT_CATEGORY,
    "其他",
)


def _purchase_date(value):
    normalized = str(value or "").strip()
    try:
        datetime.strptime(normalized, "%Y-%m-%d")
    except ValueError as error:
        raise ValueError("采购日期必须是 YYYY-MM-DD") from error
    return normalized


def _rounded_minor(value):
    return int(Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def decimal_minor(value):
    """Parse a nonnegative currency/rate input without binary-float rounding."""
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError("金额或税率必须为非负有限数字")
        return _rounded_minor(number * 100)
    except InvalidOperation as error:
        raise ValueError("金额或税率必须为有效数字") from error


def calculate_purchase_amounts(
    quantity,
    material_unit_price_cents,
    tax_rate_bps=0,
    freight_amount_cents=0,
    *, price_basis="exclusive", tax_inclusive_unit_price_cents=None,
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

    if price_basis == "inclusive":
        try:
            gross = Decimal(str(tax_inclusive_unit_price_cents))
            if not gross.is_finite() or gross < 0 or gross != gross.to_integral_value():
                raise ValueError("含税单价必须为非负整数分")
            tax_inclusive_unit_price_cents = int(gross)
        except InvalidOperation as error:
            raise ValueError("请填写有效含税单价") from error
        divisor = Decimal(10000) + tax_rate_bps
        line_amount_cents = _rounded_minor(quantity_value * tax_inclusive_unit_price_cents)
        material_amount_cents = _rounded_minor(Decimal(line_amount_cents) * 10000 / divisor)
        tax_amount_cents = line_amount_cents - material_amount_cents
        material_unit_price_cents = _rounded_minor(Decimal(tax_inclusive_unit_price_cents) * 10000 / divisor)
    elif price_basis == "exclusive":
        material_amount_cents = _rounded_minor(quantity_value * material_unit_price_cents)
        tax_amount_cents = _rounded_minor(Decimal(material_amount_cents) * tax_rate_bps / 10000)
        tax_inclusive_unit_price_cents = _rounded_minor(
            Decimal(material_unit_price_cents) * (10000 + tax_rate_bps) / 10000
        )
        line_amount_cents = material_amount_cents + tax_amount_cents
    else:
        raise ValueError("计价方式必须为含税或未税")
    return {
        "price_basis": price_basis,
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


def calculate_purchase_item_amounts(header, item):
    item = prepare_purchase_item(item)
    freight_amount_cents = int(header.get("freight_amount_cents", 0) or 0)
    mode = item["settlement_mode"]
    if mode != "quantity":
        if mode == "weight":
            unit_price = Decimal(item["weight_unit_price"]) * 100
            subtotal = _rounded_minor(Decimal(item["net_weight"]) * unit_price)
        else:
            subtotal = item["settlement_total_cents"]
            unit_price = Decimal(subtotal)
        basis = item.get("price_basis", "inclusive")
        rate = item.get("tax_rate_bps", 0)
        amounts = calculate_purchase_amounts(1, subtotal, rate, freight_amount_cents,
            price_basis=basis, tax_inclusive_unit_price_cents=subtotal)
        # Unit prices are display/legacy snapshots; weight math uses the exact input above.
        if basis == "inclusive":
            gross_price = _rounded_minor(unit_price)
            net_price = _rounded_minor(unit_price * 10000 / (10000 + int(rate)))
        else:
            net_price = _rounded_minor(unit_price)
            gross_price = _rounded_minor(unit_price * (10000 + int(rate)) / 10000)
        amounts.update(material_unit_price_cents=net_price, tax_inclusive_unit_price_cents=gross_price, unit_price_cents=gross_price)
        return amounts
    if "material_unit_price_cents" in item or "tax_rate_bps" in item or "price_basis" in item:
        return calculate_purchase_amounts(
            item.get("quantity", 1),
            item.get("material_unit_price_cents", 0),
            item.get("tax_rate_bps", 0),
            freight_amount_cents,
            price_basis=item.get("price_basis", "exclusive"),
            tax_inclusive_unit_price_cents=item.get("tax_inclusive_unit_price_cents"),
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


def _prepare_order_items(header, items):
    items = items if isinstance(items, (list, tuple)) else [items]
    if not items:
        raise ValueError("采购单至少需要一条材料明细")
    prepared = [prepare_purchase_item(item) for item in items]
    for item in prepared:
        if not str(item.get("material_name_snapshot") or "").strip():
            raise ValueError("每条明细都需要材料名称")
    amounts = [calculate_purchase_item_amounts({"freight_amount_cents": 0}, item) for item in prepared]
    freight = int(header.get("freight_amount_cents", 0) or 0)
    if freight < 0:
        raise ValueError("运费不能为负数")
    total = sum(amount["line_amount_cents"] for amount in amounts) + freight
    for item in prepared:
        _allocation_request(header, item, total)
    return prepared, amounts, {"project_cost_cents": total, "freight_amount_cents": freight}


def _write_order_items(conn, order_id, header, items, amounts):
    existing = {r["id"] for r in conn.execute("SELECT id FROM purchase_order_items WHERE purchase_order_id=?", (order_id,))}
    retained = set()
    for item, amount in zip(items, amounts):
        offer_id = item.get("product_id")
        _, legacy_product_id, material_id = _relations(conn, header.get("supplier_id"), offer_id)
        values = {key: item.get(key) for key in (
            "settlement_mode", "net_weight", "weight_unit", "weight_unit_price", "settlement_total_cents", "weigh_ticket_no")}
        values.update({key: amount[key] for key in (
            "unit_price_cents", "line_amount_cents", "material_unit_price_cents", "tax_rate_bps",
            "material_amount_cents", "tax_amount_cents", "tax_inclusive_unit_price_cents")})
        values.update(product_id=legacy_product_id, material_id=material_id, supplier_offer_id=offer_id,
                      material_name_snapshot=item["material_name_snapshot"],
                      specification_snapshot=item.get("specification_snapshot", ""),
                      unit_snapshot=item.get("unit_snapshot", ""), cost_category=item.get("cost_category", "材料费"),
                      quantity=item.get("quantity", 1), purpose=item.get("purpose", ""), notes=item.get("notes", ""),
                      price_basis=amount.get("price_basis", "exclusive"))
        item_id = item.get("item_id")
        if item_id:
            if item_id not in existing or item_id in retained:
                raise ValueError("采购明细归属错误或重复")
            conn.execute("UPDATE purchase_order_items SET " + ",".join(f"{key}=?" for key in values) + " WHERE id=?",
                         [*values.values(), item_id])
        else:
            values.update(purchase_order_id=order_id, public_id=str(uuid4()))
            item_id = conn.execute("INSERT INTO purchase_order_items (" + ",".join(values) + ") VALUES (" +
                                   ",".join("?" for _ in values) + ")", list(values.values())).lastrowid
        retained.add(item_id)
    for item_id in existing - retained:
        conn.execute("DELETE FROM purchase_order_items WHERE id=?", (item_id,))


def add_purchase_order(header, item):
    header = dict(header)
    items, item_amounts, amounts = _prepare_order_items(header, item)
    item = items[0]
    purchase_date = _purchase_date(header.get("purchase_date"))
    with db_transaction(immediate=True) as conn:
        now = _now()
        partner_id = header.get("supplier_id")
        offer_id = item.get("product_id")
        legacy_supplier_id, legacy_product_id, material_id = _relations(
            conn, partner_id, offer_id
        )
        order_no = header.get("order_no") or _next_order_no(
            conn, header["purchase_type"], purchase_date
        )
        project_name = str(header.get("project_name") or "").strip()
        if project_name and not header.get("project_id"):
            existing = conn.execute("SELECT id FROM projects WHERE name=?", (project_name,)).fetchall()
            if len(existing) > 1:
                raise ValueError("存在同名项目，请先明确项目归属")
            if existing:
                header["project_id"] = existing[0]["id"]
            else:
                from services.project_service import create_project
                header["project_id"] = create_project(
                    {"name": project_name, "notes": "由采购 Excel 导入创建"}, connection=conn
                )
        project_id, allocation_plan = _allocation_request(
            header, item, amounts["project_cost_cents"]
        )
        cursor = conn.execute(
            """INSERT INTO purchase_orders
               (order_no, purchase_type, project_id, supplier_id, merchant_name_snapshot,
                purchase_date, payment_method, payment_status, invoice_status, purchaser,
                total_amount_cents, freight_amount_cents, status, notes, created_at, updated_at,
                public_id, organization_id, supplier_partner_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)""",
            (order_no, header["purchase_type"], project_id, legacy_supplier_id,
             header["merchant_name_snapshot"], purchase_date,
             header.get("payment_method", "未记录"), header.get("payment_status", "未确认"),
             header.get("invoice_status", "未确认"), header.get("purchaser", ""),
             amounts["project_cost_cents"], amounts["freight_amount_cents"],
             header.get("notes", ""), now, now, str(uuid4()),
             _organization_id(conn), partner_id),
        )
        order_id = cursor.lastrowid
        _write_order_items(conn, order_id, header, items, item_amounts)
        _replace_purchase_allocations(conn, order_id, allocation_plan, now)
        return order_id


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
               poi.unit_price_cents, poi.line_amount_cents, poi.price_basis,
               poi.settlement_mode, poi.net_weight, poi.weight_unit, poi.weight_unit_price,
               poi.settlement_total_cents, poi.weigh_ticket_no,
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


def list_purchase_orders(month="", purchase_type="", project_id=None, keyword="", unassigned_only=False, *, detail_rows=False):
    with db_read() as conn:
        sql = _base_query() + " WHERE po.status='active'"
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
                              OR EXISTS (SELECT 1 FROM purchase_order_items search_item
                                  WHERE search_item.purchase_order_id=po.id AND (
                                      search_item.material_name_snapshot LIKE ? OR search_item.specification_snapshot LIKE ?
                                      OR search_item.purpose LIKE ?)) OR po.purchaser LIKE ?)"""
            params.extend([f"%{keyword}%"] * 6)
        sql += " ORDER BY po.purchase_date DESC, po.id DESC, poi.id"
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        if detail_rows:
            return rows
        grouped = {}
        for row in rows:
            grouped.setdefault(row["id"], []).append(row)
        return [_summarize_order(items) for items in grouped.values()]


def get_purchase_order(order_id):
    with db_read() as conn:
        rows = conn.execute(
            _base_query() + " WHERE po.id=? AND po.status='active' ORDER BY poi.id",
            (order_id,),
        ).fetchall()
        return _summarize_order([dict(row) for row in rows]) if rows else None


def _summarize_order(items):
    result = dict(items[0])
    result["items"] = items
    result["item_count"] = len(items)
    if len(items) > 1:
        result["material_name_snapshot"] = "、".join(item["material_name_snapshot"] for item in items)
        result["specification_snapshot"] = f"共{len(items)}条材料明细"
        for key in ("line_amount_cents", "material_amount_cents", "tax_amount_cents"):
            result[key] = sum(item[key] for item in items)
    return result


def get_purchase_allocations(order_id, include_void=False):
    with db_read() as conn:
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


def update_purchase_order(order_id, header, item):
    single_item_request = isinstance(item, dict)
    items, item_amounts, amounts = _prepare_order_items(header, item)
    item = items[0]
    purchase_date = _purchase_date(header.get("purchase_date"))
    with db_transaction(immediate=True) as conn:
        before = purchase_revision_snapshot(conn, order_id)
        if single_item_request and len(before["items"]) > 1:
            raise ValueError("这张采购单有多条材料，请提交整单明细后保存，不能用单条材料覆盖。")
        existing = conn.execute(
            "SELECT purchase_type, status FROM purchase_orders WHERE id=?", (order_id,)
        ).fetchone()
        if not existing or existing["status"] != "active":
            raise ValueError("采购单不存在或已作废，不能修改。")
        if existing["purchase_type"] != header["purchase_type"]:
            raise ValueError("采购类型不能直接变更，请作废后重新录入。")
        item_row = conn.execute(
            "SELECT id FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id LIMIT 1",
            (order_id,),
        ).fetchone()
        if not item_row:
            raise ValueError("采购单缺少明细，无法修改。")
        if len(items) == 1 and not items[0].get("item_id"):
            items[0]["item_id"] = item_row["id"]
        partner_id = header.get("supplier_id")
        offer_id = item.get("product_id")
        legacy_supplier_id, legacy_product_id, material_id = _relations(
            conn, partner_id, offer_id
        )
        now = _now()
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
             header["merchant_name_snapshot"], purchase_date,
             header.get("payment_method", "未记录"), header.get("payment_status", "未确认"),
             header.get("invoice_status", "未确认"), header.get("purchaser", ""),
             amounts["project_cost_cents"], amounts["freight_amount_cents"],
             header.get("notes", ""), now, order_id),
        )
        _write_order_items(conn, order_id, header, items, item_amounts)
        _replace_purchase_allocations(conn, order_id, allocation_plan, now)
        record_price_revision(conn, "purchase", order_id, before,
                              purchase_revision_snapshot(conn, order_id), "采购修改", now)


def purchase_revision_snapshot(conn, order_id):
    return {
        "order": dict(conn.execute("SELECT * FROM purchase_orders WHERE id=?", (order_id,)).fetchone() or {}),
        "items": [dict(row) for row in conn.execute("SELECT * FROM purchase_order_items WHERE purchase_order_id=? ORDER BY id", (order_id,))],
        "allocations": [dict(row) for row in conn.execute("SELECT * FROM purchase_cost_allocation_lines WHERE purchase_order_id=? AND status='active' ORDER BY id", (order_id,))],
    }


def record_price_revision(conn, entity_type, entity_id, before, after, reason, now):
    conn.execute(
        "INSERT INTO purchase_price_revisions(entity_type, entity_id, before_json, after_json, reason, changed_at) VALUES (?,?,?,?,?,?)",
        (entity_type, entity_id, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), reason, now),
    )


def assign_purchase_project(order_ids, project_id):
    if not order_ids:
        return 0
    with db_transaction(immediate=True) as conn:
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
                WHERE id IN ({placeholders}) AND status='active'""",
            (project_id, now, *order_ids),
        )
        return result.rowcount


def update_purchase_order_status(
    order_ids, payment_method, payment_status, invoice_status
):
    if not order_ids:
        return 0
    with db_transaction() as conn:
        placeholders = ",".join("?" * len(order_ids))
        result = conn.execute(
            f"""UPDATE purchase_orders
                SET payment_method=?, payment_status=?, invoice_status=?, updated_at=?
                WHERE id IN ({placeholders}) AND status='active'""",
            (payment_method, payment_status, invoice_status, _now(), *order_ids),
        )
        return result.rowcount


def void_purchase_orders(order_ids):
    if not order_ids:
        return 0
    with db_transaction(immediate=True) as conn:
        placeholders = ",".join("?" * len(order_ids))
        now = _now()
        result = conn.execute(
            f"""UPDATE purchase_orders SET status='void', updated_at=?
                WHERE id IN ({placeholders}) AND status='active'""",
            (now, *order_ids),
        )
        conn.execute(
            f"""UPDATE purchase_cost_allocation_lines
                SET status='void', voided_at=?, updated_at=?
                WHERE purchase_order_id IN ({placeholders})
                  AND status='active'""",
            (now, now, *order_ids),
        )
        return result.rowcount


def list_purchase_months():
    with db_read() as conn:
        return [
            row["month"]
            for row in conn.execute(
                """SELECT DISTINCT substr(purchase_date, 1, 7) AS month
                   FROM purchase_orders WHERE status='active'
                   ORDER BY month DESC"""
            ).fetchall()
            if row["month"]
        ]


def get_purchase_dashboard(month, project_id=None):
    with db_read() as conn:
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
                   WHERE ppc.project_id=? AND po.status='active'
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
                   WHERE ppc.project_id=? AND po.status='active'
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
                   WHERE ppc.project_id=? AND po.status='active'
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
                   WHERE po.status='active'
                     AND substr(po.purchase_date, 1, 7)=?""",
                (month,),
            ).fetchone()
            by_project = _dashboard_rows(
                conn,
                """WITH month_orders AS (
                       SELECT * FROM purchase_orders
                       WHERE status='active'
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
                   WHERE po.status='active'
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
                   WHERE ppc.project_id=? AND po.status='active'
                     AND substr(po.purchase_date, 1, 7)=?""",
                (project_id, previous_month),
            ).fetchone()[0]
        else:
            previous_cents = conn.execute(
                """SELECT COALESCE(SUM(total_amount_cents), 0)
                   FROM purchase_orders
                   WHERE status='active' AND substr(purchase_date, 1, 7)=?""",
                (previous_month,),
            ).fetchone()[0]
        result = dict(summary)
        result["previous_cents"] = previous_cents
        return {"summary": result, "by_project": by_project, "by_merchant": by_merchant}


def _dashboard_rows(conn, sql, params):
    return [dict(row) for row in conn.execute(sql, params).fetchall()]
