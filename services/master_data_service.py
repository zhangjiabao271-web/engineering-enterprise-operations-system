from services._common import now as _now, organization_id as _organization_id
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from db.connection import db_read, db_transaction
from services.procurement_service import calculate_purchase_amounts, record_price_revision


def _offer_price(data, tax_rate_bps):
    basis = data.get("price_basis", "exclusive")
    quoted = _price_minor(data.get("price"))
    amounts = calculate_purchase_amounts(1, quoted, tax_rate_bps,
        price_basis=basis, tax_inclusive_unit_price_cents=quoted)
    return basis, quoted, amounts["material_unit_price_cents"]


def _enrich_offer_price(row):
    if row["price_basis"] == "inclusive":
        row["tax_inclusive_price_minor"] = row["quoted_price_minor"]
    else:
        row["tax_inclusive_price_minor"] = _tax_inclusive_minor(row["price_minor"], row["tax_rate_bps"])
    row["tax_inclusive_price"] = row["tax_inclusive_price_minor"] / 100
    row["quoted_price"] = (row["quoted_price_minor"] if row["quoted_price_minor"] is not None else row["price_minor"]) / 100


SUPPLIER_KIND_LABELS = {"unclassified": "未分类", "manufacturer": "生产厂家", "distributor": "经销门店"}


def _public_id():
    return str(uuid4())


def _partner_code(conn, prefix):
    organization_id = _organization_id(conn)
    return organization_id, f"{prefix}-{uuid4().hex[:10].upper()}"


def _percent_to_bps(value):
    try:
        percent = Decimal(str(value if value not in (None, "") else 0))
    except Exception as error:
        raise ValueError("税率必须是数字") from error
    if not percent.is_finite() or percent < 0 or percent > 100:
        raise ValueError("税率必须在 0% 到 100% 之间")
    return int(
        (percent * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _money_to_minor(value, label="金额"):
    try:
        amount = Decimal(str(value if value not in (None, "") else 0))
    except Exception as error:
        raise ValueError(f"{label}必须是数字") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{label}不能为负数")
    return int(
        (amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _tax_inclusive_minor(price_minor, tax_rate_bps):
    return int(
        (
            Decimal(int(price_minor or 0))
            * (Decimal(10000) + Decimal(int(tax_rate_bps or 0)))
            / Decimal(10000)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def list_business_partners(keyword="", role="", status=""):
    with db_read() as conn:
        sql = """
            SELECT bp.id, bp.partner_code, bp.legal_name,
                   COALESCE(bp.short_name, '') AS short_name,
                   bp.entity_type,
                   COALESCE(bp.unified_credit_code, '') AS unified_credit_code,
                   COALESCE(bp.registered_address, '') AS registered_address,
                   COALESCE(bp.business_address, '') AS business_address,
                   COALESCE(bp.invoice_phone, '') AS invoice_phone,
                   COALESCE(bp.bank_name, '') AS bank_name,
                   COALESCE(bp.bank_account, '') AS bank_account,
                   bp.status, COALESCE(bp.notes, '') AS notes,
                   GROUP_CONCAT(DISTINCT pr.role_code) AS role_codes,
                   COALESCE((SELECT pc.name FROM partner_contacts pc
                             WHERE pc.partner_id=bp.id AND pc.is_active=1
                             ORDER BY pc.is_primary DESC, pc.id LIMIT 1), '') AS contact,
                   COALESCE((SELECT pc.phone FROM partner_contacts pc
                             WHERE pc.partner_id=bp.id AND pc.is_active=1
                             ORDER BY pc.is_primary DESC, pc.id LIMIT 1), '') AS contact_phone,
                   bp.created_at, bp.updated_at
            FROM business_partners bp
            JOIN partner_roles pr ON pr.partner_id=bp.id
            WHERE 1=1
        """
        params = []
        if status:
            sql += " AND bp.status=?"
            params.append(status)
        if role:
            sql += " AND EXISTS (SELECT 1 FROM partner_roles selected_role WHERE selected_role.partner_id=bp.id AND selected_role.role_code=?)"
            params.append(role)
        if keyword:
            like = f"%{keyword}%"
            sql += """ AND (bp.legal_name LIKE ? OR bp.short_name LIKE ?
                             OR bp.partner_code LIKE ? OR bp.unified_credit_code LIKE ?
                             OR bp.notes LIKE ? OR EXISTS (
                                 SELECT 1 FROM partner_contacts pc
                                 WHERE pc.partner_id=bp.id
                                   AND (pc.name LIKE ? OR pc.phone LIKE ?)
                             ))"""
            params.extend([like] * 7)
        sql += " GROUP BY bp.id ORDER BY CASE bp.status WHEN 'active' THEN 1 WHEN 'pending' THEN 2 ELSE 3 END, bp.legal_name"
        rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
        for row in rows:
            row["name"] = row["legal_name"]
            row["roles"] = set((row.pop("role_codes") or "").split(",")) - {""}
        return rows


def list_suppliers(keyword="", category="", active_only=True, status=""):
    with db_read() as conn:
        sql = """
            SELECT bp.id, bp.partner_code, bp.legal_name AS name,
                   bp.entity_type,
                   COALESCE(sp.supplier_kind, 'unclassified') AS supplier_kind,
                   COALESCE(sp.supplier_category, '') AS category,
                   COALESCE((SELECT pc.name FROM partner_contacts pc
                             WHERE pc.partner_id=bp.id AND pc.is_active=1
                             ORDER BY pc.is_primary DESC, pc.id LIMIT 1), '') AS contact,
                   COALESCE(sp.price_level, '') AS price_level,
                   COALESCE(sp.delivery_rating, '') AS delivery,
                   COALESCE(sp.quality_rating, '') AS quality,
                   COALESCE(sp.export_capability, '否') AS export,
                   COALESCE(sp.default_tax_rate_bps, 0) AS default_tax_rate_bps,
                   COALESCE(bp.notes, '') AS notes,
                   bp.created_at, bp.status, bp.public_id
            FROM business_partners bp
            JOIN partner_roles pr ON pr.partner_id=bp.id AND pr.role_code='supplier'
            LEFT JOIN supplier_profiles sp ON sp.partner_id=bp.id
            WHERE 1=1
        """
        params = []
        if active_only:
            sql += " AND bp.status='active'"
        if status:
            sql += " AND bp.status=?"
            params.append(status)
        if keyword:
            sql += """ AND (bp.legal_name LIKE ? OR bp.partner_code LIKE ? OR bp.notes LIKE ?
                             OR EXISTS (SELECT 1 FROM partner_contacts pc
                                        WHERE pc.partner_id=bp.id AND pc.name LIKE ?))"""
            params.extend([f"%{keyword}%"] * 4)
        if category:
            sql += " AND sp.supplier_category=?"
            params.append(category)
        sql += " ORDER BY bp.id DESC"
        result = [dict(row) for row in conn.execute(sql, params).fetchall()]
        for row in result:
            row["default_tax_rate_percent"] = row["default_tax_rate_bps"] / 100
        return result


def _business_year(value=None):
    try:
        year = int(value or datetime.now().year)
    except (TypeError, ValueError) as error:
        raise ValueError("经营年度必须是有效年份") from error
    if year < 1900 or year > 9998:
        raise ValueError("经营年度必须在 1900 到 9998 之间")
    return year


def _business_period(value=None):
    year = _business_year(value)
    return year, f"{year:04d}-01-01", f"{year + 1:04d}-01-01"


def list_customer_business_years():
    """Return available operating years and always include the current year."""
    with db_read() as conn:
        years = {datetime.now().year}
        rows = conn.execute(
            """SELECT business_year FROM (
                   SELECT substr(settlement_date, 1, 4) AS business_year
                   FROM settlements WHERE status='active'
                   UNION
                   SELECT substr(invoice_date, 1, 4)
                   FROM sales_invoices WHERE status='active'
                   UNION
                   SELECT substr(receipt_date, 1, 4)
                   FROM receipts WHERE status='active'
               )
               WHERE length(business_year)=4
                 AND business_year GLOB '[0-9][0-9][0-9][0-9]'"""
        ).fetchall()
        years.update(int(row["business_year"]) for row in rows)
        return sorted(years, reverse=True)


def summarize_customer_business(rows):
    """Summarize customer rows for the archive dashboard."""
    customers = list(rows or [])
    total_income_minor = sum(
        int(row.get("yearly_business_minor") or 0) for row in customers
    )
    total_receivable_minor = sum(
        int(row.get("current_receivable_minor") or 0) for row in customers
    )

    def ranked_rows(amount_key, total_minor):
        result = []
        for row in customers:
            amount_minor = int(row.get(amount_key) or 0)
            if amount_minor <= 0:
                continue
            result.append(
                {
                    "customer_id": row.get("id"),
                    "label": (
                        row.get("short_name") or row.get("name") or "未命名客户"
                    ).strip(),
                    "amount_minor": amount_minor,
                    "share_percent": (
                        amount_minor / total_minor * 100 if total_minor else 0.0
                    ),
                }
            )
        return sorted(
            result,
            key=lambda item: (-item["amount_minor"], item["label"]),
        )

    return {
        "total_income_minor": total_income_minor,
        "income_customer_count": sum(
            1 for row in customers
            if int(row.get("yearly_business_minor") or 0) > 0
        ),
        "total_receivable_minor": total_receivable_minor,
        "receivable_customer_count": sum(
            1 for row in customers
            if int(row.get("current_receivable_minor") or 0) > 0
        ),
        "income_rows": ranked_rows("yearly_business_minor", total_income_minor),
        "receivable_rows": ranked_rows(
            "current_receivable_minor", total_receivable_minor
        ),
    }


def list_customers(keyword="", active_only=False, status="", year=None):
    _, period_start, period_end = _business_period(year)
    with db_read() as conn:
        sql = """
            WITH yearly_income AS (
                SELECT COALESCE(p.customer_partner_id, c.customer_partner_id)
                           AS customer_id,
                       SUM(s.amount_minor) AS amount_minor,
                       MAX(s.settlement_date) AS last_date
                FROM settlements s
                JOIN projects p ON p.id=s.project_id
                LEFT JOIN contracts c ON c.id=s.contract_id
                WHERE s.status='active'
                  AND s.settlement_date>=? AND s.settlement_date<?
                GROUP BY COALESCE(p.customer_partner_id, c.customer_partner_id)
            ),
            yearly_receipts AS (
                SELECT COALESCE(p.customer_partner_id, c.customer_partner_id)
                           AS customer_id,
                       SUM(ra.allocated_amount_minor) AS amount_minor,
                       MAX(r.receipt_date) AS last_date
                FROM receipt_allocations ra
                JOIN receipts r ON r.id=ra.receipt_id AND r.status='active'
                JOIN projects p ON p.id=ra.project_id
                LEFT JOIN contracts c ON c.id=ra.contract_id
                WHERE r.receipt_date>=? AND r.receipt_date<?
                GROUP BY COALESCE(p.customer_partner_id, c.customer_partner_id)
            ),
            yearly_invoices AS (
                SELECT COALESCE(p.customer_partner_id, c.customer_partner_id)
                           AS customer_id,
                       MAX(i.invoice_date) AS last_date
                FROM sales_invoices i
                JOIN projects p ON p.id=i.project_id
                LEFT JOIN contracts c ON c.id=i.contract_id
                WHERE i.status='active'
                  AND i.invoice_date>=? AND i.invoice_date<?
                GROUP BY COALESCE(p.customer_partner_id, c.customer_partner_id)
            ),
            all_income AS (
                SELECT COALESCE(p.customer_partner_id, c.customer_partner_id)
                           AS customer_id,
                       SUM(s.amount_minor) AS amount_minor
                FROM settlements s
                JOIN projects p ON p.id=s.project_id
                LEFT JOIN contracts c ON c.id=s.contract_id
                WHERE s.status='active'
                GROUP BY COALESCE(p.customer_partner_id, c.customer_partner_id)
            ),
            all_receipts AS (
                SELECT COALESCE(p.customer_partner_id, c.customer_partner_id)
                           AS customer_id,
                       SUM(ra.allocated_amount_minor) AS amount_minor
                FROM receipt_allocations ra
                JOIN receipts r ON r.id=ra.receipt_id AND r.status='active'
                JOIN projects p ON p.id=ra.project_id
                LEFT JOIN contracts c ON c.id=ra.contract_id
                GROUP BY COALESCE(p.customer_partner_id, c.customer_partner_id)
            ),
            active_projects AS (
                SELECT customer_partner_id AS customer_id, COUNT(*) AS project_count
                FROM projects
                WHERE customer_partner_id IS NOT NULL
                  AND status IN ('筹备中', '进行中')
                GROUP BY customer_partner_id
            )
            SELECT bp.id, bp.partner_code, bp.legal_name AS name,
                   bp.legal_name, COALESCE(bp.short_name, '') AS short_name,
                   bp.entity_type,
                   COALESCE(bp.unified_credit_code, '') AS unified_credit_code,
                   bp.status, COALESCE(bp.notes, '') AS notes,
                   COALESCE(cp.customer_category, '') AS customer_category,
                   COALESCE(cp.settlement_terms, '') AS settlement_terms,
                   COALESCE(cp.credit_limit_minor, 0) AS credit_limit_minor,
                   COALESCE((SELECT pc.name FROM partner_contacts pc
                             WHERE pc.partner_id=bp.id AND pc.is_active=1
                             ORDER BY pc.is_primary DESC, pc.id LIMIT 1), '') AS contact,
                   COALESCE((SELECT pc.phone FROM partner_contacts pc
                             WHERE pc.partner_id=bp.id AND pc.is_active=1
                             ORDER BY pc.is_primary DESC, pc.id LIMIT 1), '') AS contact_phone,
                   COALESCE(yi.amount_minor, 0) AS yearly_business_minor,
                   COALESCE(yr.amount_minor, 0) AS yearly_receipt_minor,
                   COALESCE(ai.amount_minor, 0) AS total_business_minor,
                   COALESCE(ar.amount_minor, 0) AS total_receipt_minor,
                   COALESCE(ap.project_count, 0) AS active_project_count,
                   MAX(
                       COALESCE(yi.last_date, ''),
                       COALESCE(yr.last_date, ''),
                       COALESCE(yiv.last_date, '')
                   ) AS latest_business_date
            FROM business_partners bp
            JOIN partner_roles pr
              ON pr.partner_id=bp.id AND pr.role_code='customer'
            LEFT JOIN customer_profiles cp ON cp.partner_id=bp.id
            LEFT JOIN yearly_income yi ON yi.customer_id=bp.id
            LEFT JOIN yearly_receipts yr ON yr.customer_id=bp.id
            LEFT JOIN yearly_invoices yiv ON yiv.customer_id=bp.id
            LEFT JOIN all_income ai ON ai.customer_id=bp.id
            LEFT JOIN all_receipts ar ON ar.customer_id=bp.id
            LEFT JOIN active_projects ap ON ap.customer_id=bp.id
            WHERE 1=1
        """
        params = [
            period_start, period_end,
            period_start, period_end,
            period_start, period_end,
        ]
        if active_only:
            sql += " AND bp.status='active'"
        if status:
            sql += " AND bp.status=?"
            params.append(status)
        if keyword:
            sql += """ AND (
                bp.legal_name LIKE ? OR bp.short_name LIKE ?
                OR bp.partner_code LIKE ? OR bp.unified_credit_code LIKE ?
            )"""
            params.extend([f"%{keyword}%"] * 4)
        sql += " ORDER BY yearly_business_minor DESC, bp.legal_name, bp.id"
        result = [dict(row) for row in conn.execute(sql, params).fetchall()]
        from db.business_facts import customer_receivable_rows
        current_balances = {}
        for balance in customer_receivable_rows(conn):
            key = balance['customer_id']
            current_balances[key] = current_balances.get(key, 0) + balance['receivable_minor']
        for row in result:
            row["credit_limit"] = row["credit_limit_minor"] / 100
            row["current_receivable_minor"] = current_balances.get(row['id'], 0)
        return result


def get_customer_business_detail(partner_id, year=None):
    """Return one customer's selected-year project activity and current balance."""
    partner_id = int(partner_id)
    year, period_start, period_end = _business_period(year)
    with db_read() as conn:
        customer = conn.execute(
            """SELECT bp.id, bp.legal_name AS name, bp.status,
                      COALESCE((SELECT pc.name FROM partner_contacts pc
                                WHERE pc.partner_id=bp.id AND pc.is_active=1
                                ORDER BY pc.is_primary DESC, pc.id LIMIT 1), '') AS contact,
                      COALESCE((SELECT pc.phone FROM partner_contacts pc
                                WHERE pc.partner_id=bp.id AND pc.is_active=1
                                ORDER BY pc.is_primary DESC, pc.id LIMIT 1), '') AS contact_phone
               FROM business_partners bp
               JOIN partner_roles pr
                 ON pr.partner_id=bp.id AND pr.role_code='customer'
               WHERE bp.id=?""",
            (partner_id,),
        ).fetchone()
        if not customer:
            raise ValueError("客户档案不存在")

        sql = """
            WITH yearly_income AS (
                SELECT s.project_id, SUM(s.amount_minor) AS amount_minor,
                       MAX(s.settlement_date) AS last_date
                FROM settlements s
                JOIN projects p ON p.id=s.project_id
                LEFT JOIN contracts c ON c.id=s.contract_id
                WHERE s.status='active'
                  AND COALESCE(p.customer_partner_id, c.customer_partner_id)=?
                  AND s.settlement_date>=? AND s.settlement_date<?
                GROUP BY s.project_id
            ),
            yearly_invoices AS (
                SELECT i.project_id, SUM(i.amount_minor) AS amount_minor,
                       MAX(i.invoice_date) AS last_date
                FROM sales_invoices i
                JOIN projects p ON p.id=i.project_id
                LEFT JOIN contracts c ON c.id=i.contract_id
                WHERE i.status='active'
                  AND COALESCE(p.customer_partner_id, c.customer_partner_id)=?
                  AND i.invoice_date>=? AND i.invoice_date<?
                GROUP BY i.project_id
            ),
            yearly_receipts AS (
                SELECT ra.project_id, SUM(ra.allocated_amount_minor) AS amount_minor,
                       MAX(r.receipt_date) AS last_date
                FROM receipt_allocations ra
                JOIN receipts r ON r.id=ra.receipt_id AND r.status='active'
                JOIN projects p ON p.id=ra.project_id
                LEFT JOIN contracts c ON c.id=ra.contract_id
                WHERE COALESCE(p.customer_partner_id, c.customer_partner_id)=?
                  AND r.receipt_date>=? AND r.receipt_date<?
                GROUP BY ra.project_id
            ),
            all_income AS (
                SELECT s.project_id, SUM(s.amount_minor) AS amount_minor
                FROM settlements s
                JOIN projects p ON p.id=s.project_id
                LEFT JOIN contracts c ON c.id=s.contract_id
                WHERE s.status='active'
                  AND COALESCE(p.customer_partner_id, c.customer_partner_id)=?
                GROUP BY s.project_id
            ),
            all_receipts AS (
                SELECT ra.project_id, SUM(ra.allocated_amount_minor) AS amount_minor
                FROM receipt_allocations ra
                JOIN receipts r ON r.id=ra.receipt_id AND r.status='active'
                JOIN projects p ON p.id=ra.project_id
                LEFT JOIN contracts c ON c.id=ra.contract_id
                WHERE COALESCE(p.customer_partner_id, c.customer_partner_id)=?
                GROUP BY ra.project_id
            ),
            customer_projects AS (
                SELECT id AS project_id FROM projects WHERE customer_partner_id=?
                UNION SELECT project_id FROM yearly_income
                UNION SELECT project_id FROM yearly_invoices
                UNION SELECT project_id FROM yearly_receipts
                UNION SELECT project_id FROM all_income
                UNION SELECT project_id FROM all_receipts
            )
            SELECT p.id AS project_id, p.project_code, p.name AS project_name,
                   p.status AS project_status, p.business_mode,
                   COALESCE(yi.amount_minor, 0) AS yearly_business_minor,
                   COALESCE(yiv.amount_minor, 0) AS yearly_invoice_minor,
                   COALESCE(yr.amount_minor, 0) AS yearly_receipt_minor,
                   COALESCE(ai.amount_minor, 0) AS total_business_minor,
                   COALESCE(ar.amount_minor, 0) AS total_receipt_minor,
                   MAX(
                       COALESCE(yi.last_date, ''),
                       COALESCE(yiv.last_date, ''),
                       COALESCE(yr.last_date, '')
                   ) AS latest_business_date
            FROM customer_projects cp
            JOIN projects p ON p.id=cp.project_id
            LEFT JOIN yearly_income yi ON yi.project_id=p.id
            LEFT JOIN yearly_invoices yiv ON yiv.project_id=p.id
            LEFT JOIN yearly_receipts yr ON yr.project_id=p.id
            LEFT JOIN all_income ai ON ai.project_id=p.id
            LEFT JOIN all_receipts ar ON ar.project_id=p.id
            ORDER BY yearly_business_minor DESC,
                     CASE p.status WHEN '进行中' THEN 1 WHEN '筹备中' THEN 2 ELSE 3 END,
                     p.id DESC
        """
        params = (
            partner_id, period_start, period_end,
            partner_id, period_start, period_end,
            partner_id, period_start, period_end,
            partner_id, partner_id, partner_id,
        )
        projects = [dict(row) for row in conn.execute(sql, params).fetchall()]
        from db.business_facts import customer_receivable_rows
        current_balances = {r['project_id']: r['receivable_minor'] for r in customer_receivable_rows(conn)
                            if r['customer_id'] == partner_id}
        for project in projects:
            project["current_receivable_minor"] = current_balances.get(project['project_id'], 0)
        summary = {
            "yearly_business_minor": sum(
                row["yearly_business_minor"] for row in projects
            ),
            "yearly_invoice_minor": sum(
                row["yearly_invoice_minor"] for row in projects
            ),
            "yearly_receipt_minor": sum(
                row["yearly_receipt_minor"] for row in projects
            ),
            "current_receivable_minor": sum(
                row["current_receivable_minor"] for row in projects
            ),
        }
        return {
            "customer": dict(customer),
            "year": year,
            "summary": summary,
            "projects": projects,
        }


def get_business_partner(partner_id):
    with db_read() as conn:
        row = conn.execute(
            """SELECT bp.*,
                      COALESCE(cp.customer_category, '') AS customer_category,
                      COALESCE(cp.settlement_terms, '') AS settlement_terms,
                      COALESCE(cp.credit_limit_minor, 0) AS credit_limit_minor,
                      COALESCE(sp.supplier_kind, 'unclassified') AS supplier_kind,
                      COALESCE(sp.supplier_category, '') AS supplier_category,
                      COALESCE(sp.price_level, '') AS price_level,
                      COALESCE(sp.delivery_rating, '') AS delivery_rating,
                      COALESCE(sp.quality_rating, '') AS quality_rating,
                      COALESCE(sp.export_capability, '否') AS export_capability,
                      COALESCE(sp.default_tax_rate_bps, 0) AS default_tax_rate_bps
               FROM business_partners bp
               LEFT JOIN customer_profiles cp ON cp.partner_id=bp.id
               LEFT JOIN supplier_profiles sp ON sp.partner_id=bp.id
               WHERE bp.id=?""",
            (int(partner_id),),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        result["roles"] = {
            item["role_code"] for item in conn.execute(
                "SELECT role_code FROM partner_roles WHERE partner_id=?",
                (int(partner_id),),
            ).fetchall()
        }
        contact = conn.execute(
            """SELECT name, department, title, phone, email, wechat
               FROM partner_contacts
               WHERE partner_id=? AND is_active=1
               ORDER BY is_primary DESC, id LIMIT 1""",
            (int(partner_id),),
        ).fetchone()
        result.update(
            {
                "contact_name": contact["name"] if contact else "",
                "contact_department": contact["department"] if contact else "",
                "contact_title": contact["title"] if contact else "",
                "contact_phone": contact["phone"] if contact else "",
                "contact_email": contact["email"] if contact else "",
                "contact_wechat": contact["wechat"] if contact else "",
                "default_tax_rate_percent": result["default_tax_rate_bps"] / 100,
                "credit_limit": result["credit_limit_minor"] / 100,
            }
        )
        return result


def _validate_partner_data(data):
    legal_name = (data.get("legal_name") or data.get("name") or "").strip()
    roles = set(data.get("roles") or ())
    if not legal_name:
        raise ValueError("企业 / 单位名称不能为空")
    if not roles or not roles <= {"customer", "supplier", "subcontractor", "other"}:
        raise ValueError("至少选择一个有效客商角色")
    if data.get("status", "active") not in {"active", "pending", "inactive"}:
        raise ValueError("客商状态无效")
    if data.get("entity_type", "enterprise") not in {
        "enterprise", "individual_business", "individual"
    }:
        raise ValueError("客商主体类型无效")
    return legal_name, roles


def _replace_primary_contact_detail(conn, partner_id, data, now):
    contact = {
        "name": (data.get("contact_name") or data.get("contact") or "").strip(),
        "department": (data.get("contact_department") or "").strip(),
        "title": (data.get("contact_title") or "").strip(),
        "phone": (data.get("contact_phone") or "").strip(),
        "email": (data.get("contact_email") or "").strip(),
        "wechat": (data.get("contact_wechat") or "").strip(),
    }
    existing = conn.execute(
        "SELECT id FROM partner_contacts WHERE partner_id=? AND is_primary=1 ORDER BY id LIMIT 1",
        (partner_id,),
    ).fetchone()
    if existing and contact["name"]:
        conn.execute(
            """UPDATE partner_contacts SET name=?, department=?, title=?, phone=?,
                      email=?, wechat=?, is_active=1, updated_at=? WHERE id=?""",
            (*contact.values(), now, existing["id"]),
        )
    elif existing:
        conn.execute(
            "UPDATE partner_contacts SET is_active=0, updated_at=? WHERE id=?",
            (now, existing["id"]),
        )
    elif contact["name"]:
        conn.execute(
            """INSERT INTO partner_contacts
               (public_id, partner_id, name, department, title, phone, email,
                wechat, is_primary, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)""",
            (_public_id(), partner_id, *contact.values(), now, now),
        )


def _assert_role_removable(conn, partner_id, role):
    references = {
        "customer": (
            ("projects", "customer_partner_id", "项目"),
            ("contracts", "customer_partner_id", "合同"),
        ),
        "supplier": (
            ("supplier_offers", "supplier_partner_id", "供应商报价"),
            ("purchase_orders", "supplier_partner_id", "采购单"),
        ),
    }
    for table, column, label in references.get(role, ()):
        if conn.execute(
            f"SELECT 1 FROM {table} WHERE {column}=? LIMIT 1", (partner_id,)
        ).fetchone():
            raise ValueError(f"该客商已有{label}历史，不能移除对应角色；可以停用客商")


def _save_partner_profiles(
    conn, partner_id, roles, data, now, *, profile_roles=None
):
    profile_roles = roles if profile_roles is None else set(profile_roles)
    existing_roles = {
        row["role_code"] for row in conn.execute(
            "SELECT role_code FROM partner_roles WHERE partner_id=?", (partner_id,)
        ).fetchall()
    }
    for role in existing_roles - roles:
        _assert_role_removable(conn, partner_id, role)
        conn.execute(
            "DELETE FROM partner_roles WHERE partner_id=? AND role_code=?",
            (partner_id, role),
        )
    for role in roles - existing_roles:
        conn.execute(
            "INSERT INTO partner_roles(partner_id, role_code, created_at) VALUES (?, ?, ?)",
            (partner_id, role, now),
        )
    if "customer" in profile_roles:
        conn.execute(
            """INSERT INTO customer_profiles
               (partner_id, customer_category, settlement_terms,
                credit_limit_minor, updated_at) VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(partner_id) DO UPDATE SET
                   customer_category=excluded.customer_category,
                   settlement_terms=excluded.settlement_terms,
                   credit_limit_minor=excluded.credit_limit_minor,
                   updated_at=excluded.updated_at""",
            (
                partner_id,
                (data.get("customer_category") or "").strip(),
                (data.get("settlement_terms") or "").strip(),
                _money_to_minor(data.get("credit_limit"), "信用额度"),
                now,
            ),
        )
    if "supplier" in profile_roles:
        if "supplier_kind" in data and data["supplier_kind"] not in SUPPLIER_KIND_LABELS:
            raise ValueError("供应商类型只能为生产厂家、经销门店或未分类")
        conn.execute(
            """INSERT INTO supplier_profiles
               (partner_id, supplier_category, price_level, delivery_rating,
                quality_rating, export_capability, default_tax_rate_bps, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(partner_id) DO UPDATE SET
                   supplier_category=excluded.supplier_category,
                   price_level=excluded.price_level,
                   delivery_rating=excluded.delivery_rating,
                   quality_rating=excluded.quality_rating,
                   export_capability=excluded.export_capability,
                   default_tax_rate_bps=excluded.default_tax_rate_bps,
                   updated_at=excluded.updated_at""",
            (
                partner_id,
                (data.get("supplier_category") or data.get("category") or "").strip(),
                data.get("price_level", ""),
                data.get("delivery_rating", data.get("delivery", "")),
                data.get("quality_rating", data.get("quality", "")),
                data.get("export_capability", data.get("export", "否")),
                _percent_to_bps(data.get("default_tax_rate_percent", 0)),
                now,
            ),
        )


        if "supplier_kind" in data:
            conn.execute("UPDATE supplier_profiles SET supplier_kind=? WHERE partner_id=?", (data["supplier_kind"], partner_id))


def _same_role_message(roles):
    if roles == {"customer"}:
        return "同名客户档案已存在"
    if roles == {"supplier"}:
        return "同名供应商档案已存在"
    return "同名客商已包含所选角色"


def _extend_partner_roles(conn, partner_id, roles, data, now):
    existing_roles = {
        row["role_code"]
        for row in conn.execute(
            "SELECT role_code FROM partner_roles WHERE partner_id=?",
            (partner_id,),
        ).fetchall()
    }
    missing_roles = roles - existing_roles
    if not missing_roles:
        raise ValueError(_same_role_message(roles))

    updates = {}
    for field in (
        "short_name",
        "unified_credit_code",
        "registered_address",
        "business_address",
        "invoice_phone",
        "bank_name",
        "bank_account",
        "notes",
    ):
        value = (data.get(field) or "").strip()
        if value:
            updates[field] = value
    if data.get("entity_type"):
        updates["entity_type"] = data["entity_type"]
    if data.get("status"):
        updates["status"] = data["status"]
    assignments = [f"{field}=?" for field in updates]
    assignments.append("updated_at=?")
    conn.execute(
        f"UPDATE business_partners SET {', '.join(assignments)} WHERE id=?",
        (*updates.values(), now, partner_id),
    )

    merged_roles = existing_roles | roles
    _save_partner_profiles(
        conn,
        partner_id,
        merged_roles,
        data,
        now,
        profile_roles=missing_roles,
    )
    if (data.get("contact_name") or data.get("contact") or "").strip():
        _replace_primary_contact_detail(conn, partner_id, data, now)


def create_business_partner(data):
    legal_name, roles = _validate_partner_data(data)
    with db_transaction(immediate=True) as conn:
        organization_id = _organization_id(conn)
        existing = conn.execute(
            """SELECT id FROM business_partners
               WHERE organization_id=? AND lower(trim(legal_name))=lower(?)""",
            (organization_id, legal_name),
        ).fetchone()
        if existing:
            now = _now()
            _extend_partner_roles(conn, existing["id"], roles, data, now)
            return existing["id"]
        prefix = "CUS" if roles == {"customer"} else "SUP" if roles == {"supplier"} else "BP"
        _, code = _partner_code(conn, prefix)
        now = _now()
        partner_id = conn.execute(
            """INSERT INTO business_partners
               (public_id, organization_id, partner_code, legal_name, short_name,
                unified_credit_code, entity_type, registered_address, business_address,
                invoice_phone, bank_name, bank_account, status, notes,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _public_id(), organization_id, code, legal_name,
                (data.get("short_name") or legal_name).strip(),
                (data.get("unified_credit_code") or "").strip(),
                data.get("entity_type", "enterprise"),
                (data.get("registered_address") or "").strip(),
                (data.get("business_address") or "").strip(),
                (data.get("invoice_phone") or "").strip(),
                (data.get("bank_name") or "").strip(),
                (data.get("bank_account") or "").strip(),
                data.get("status", "active"), (data.get("notes") or "").strip(),
                now, now,
            ),
        ).lastrowid
        _save_partner_profiles(conn, partner_id, roles, data, now)
        _replace_primary_contact_detail(conn, partner_id, data, now)
        return partner_id


def update_business_partner(partner_id, data):
    legal_name, roles = _validate_partner_data(data)
    with db_transaction(immediate=True) as conn:
        organization_id = _organization_id(conn)
        duplicate = conn.execute(
            """SELECT 1 FROM business_partners
               WHERE organization_id=? AND lower(trim(legal_name))=lower(?) AND id<>?""",
            (organization_id, legal_name, int(partner_id)),
        ).fetchone()
        if duplicate:
            raise ValueError("同名客商已存在，不能形成重复档案")
        now = _now()
        result = conn.execute(
            """UPDATE business_partners SET legal_name=?, short_name=?,
                      unified_credit_code=?, entity_type=?, registered_address=?, business_address=?,
                      invoice_phone=?, bank_name=?, bank_account=?, status=?, notes=?,
                      updated_at=? WHERE id=?""",
            (
                legal_name, (data.get("short_name") or legal_name).strip(),
                (data.get("unified_credit_code") or "").strip(),
                data.get("entity_type", "enterprise"),
                (data.get("registered_address") or "").strip(),
                (data.get("business_address") or "").strip(),
                (data.get("invoice_phone") or "").strip(),
                (data.get("bank_name") or "").strip(),
                (data.get("bank_account") or "").strip(),
                data.get("status", "active"), (data.get("notes") or "").strip(),
                now, int(partner_id),
            ),
        )
        if not result.rowcount:
            raise ValueError("客商不存在")
        _save_partner_profiles(conn, int(partner_id), roles, data, now)
        _replace_primary_contact_detail(conn, int(partner_id), data, now)


def deactivate_business_partners(partner_ids):
    if not partner_ids:
        return
    with db_transaction() as conn:
        placeholders = ",".join("?" * len(partner_ids))
        now = _now()
        conn.execute(
            f"UPDATE business_partners SET status='inactive', updated_at=? WHERE id IN ({placeholders})",
            (now, *partner_ids),
        )
        conn.execute(
            f"UPDATE supplier_offers SET status='inactive', updated_at=? WHERE supplier_partner_id IN ({placeholders})",
            (now, *partner_ids),
        )


def get_supplier(partner_id):
    rows = [row for row in list_suppliers(active_only=False) if row["id"] == int(partner_id)]
    return rows[0] if rows else None


def get_supplier_by_legacy_id(legacy_supplier_id):
    with db_read() as conn:
        row = conn.execute(
            "SELECT id FROM business_partners WHERE legacy_supplier_id=?",
            (legacy_supplier_id,),
        ).fetchone()
    return get_supplier(row["id"]) if row else None


def create_supplier(data):
    payload = dict(data)
    payload.update(
        {
            "legal_name": data.get("name", ""),
            "roles": {"supplier"},
            "supplier_category": data.get("category", ""),
            "delivery_rating": data.get("delivery", ""),
            "quality_rating": data.get("quality", ""),
            "export_capability": data.get("export", "否"),
            "contact_name": data.get("contact", ""),
        }
    )
    return create_business_partner(payload)


def update_supplier(partner_id, data):
    existing = get_business_partner(partner_id)
    if not existing or "supplier" not in existing["roles"]:
        raise ValueError("供应商不存在")
    payload = dict(existing)
    payload.update(data)
    payload.update(
        {
            "legal_name": data.get("name", existing["legal_name"]),
            "roles": existing["roles"],
            "supplier_category": data.get("category", ""),
            "delivery_rating": data.get("delivery", ""),
            "quality_rating": data.get("quality", ""),
            "export_capability": data.get("export", "否"),
            "contact_name": data.get("contact", existing.get("contact_name", "")),
        }
    )
    update_business_partner(partner_id, payload)


def deactivate_suppliers(partner_ids):
    deactivate_business_partners(partner_ids)


def _unit_id(conn, unit_name):
    unit_name = (unit_name or "").strip()
    if not unit_name:
        return None
    row = conn.execute("SELECT id FROM units_of_measure WHERE name=?", (unit_name,)).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        "INSERT INTO units_of_measure(unit_code, name) VALUES (?, ?)",
        (f"U-{uuid4().hex[:10].upper()}", unit_name),
    ).lastrowid


def _material_id(conn, organization_id, data):
    name = data["name"].strip()
    specification = (data.get("specification") or "").strip()
    unit_id = _unit_id(conn, data.get("unit", ""))
    row = conn.execute(
        """SELECT id FROM materials WHERE organization_id=? AND name=? AND specification=?
           AND ((unit_id IS NULL AND ? IS NULL) OR unit_id=?) ORDER BY id LIMIT 1""",
        (organization_id, name, specification, unit_id, unit_id),
    ).fetchone()
    if row:
        return row["id"]
    return conn.execute(
        """INSERT INTO materials
           (public_id, organization_id, material_code, name, specification, unit_id,
            status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
        (_public_id(), organization_id, f"MAT-{uuid4().hex[:10].upper()}", name,
         specification, unit_id, _now(), _now()),
    ).lastrowid


def _price_minor(value):
    try:
        amount = Decimal(str(value or 0))
    except Exception as error:
        raise ValueError("材料单价必须是数字") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("材料单价不能为负数")
    return int(
        (amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def create_supplier_offer(data):
    with db_transaction() as conn:
        now = _now()
        organization_id = _organization_id(conn)
        supplier = conn.execute(
            """SELECT COALESCE(sp.default_tax_rate_bps, 0) AS default_tax_rate_bps
               FROM business_partners bp
               JOIN partner_roles pr ON pr.partner_id=bp.id
               LEFT JOIN supplier_profiles sp ON sp.partner_id=bp.id
               WHERE bp.id=? AND bp.status='active' AND pr.role_code='supplier'""",
            (data["supplier_id"],),
        ).fetchone()
        if not supplier:
            raise ValueError("供应商不存在或已停用")
        material_id = _material_id(conn, organization_id, data)
        tax_rate_bps = (
            _percent_to_bps(data["tax_rate_percent"])
            if "tax_rate_percent" in data
            else supplier["default_tax_rate_bps"]
        )
        basis, quoted, net = _offer_price(data, tax_rate_bps)
        cursor = conn.execute(
            """INSERT INTO supplier_offers
               (public_id, organization_id, supplier_partner_id, material_id,
                price_minor, tax_rate_bps, notes, status, created_at, updated_at, price_basis, quoted_price_minor)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)""",
            (_public_id(), organization_id, data["supplier_id"], material_id,
             net, tax_rate_bps,
             data.get("notes", ""), now, now, basis, quoted),
        )
        return cursor.lastrowid


def update_supplier_offer(offer_id, data):
    with db_transaction(immediate=True) as conn:
        before = dict(conn.execute("SELECT * FROM supplier_offers WHERE id=?", (offer_id,)).fetchone() or {})
        organization_id = _organization_id(conn)
        material_id = _material_id(conn, organization_id, data)
        tax_rate_bps = _percent_to_bps(data.get("tax_rate_percent", 0))
        basis, quoted, net = _offer_price(data, tax_rate_bps)
        result = conn.execute(
            """UPDATE supplier_offers SET supplier_partner_id=?, material_id=?,
               price_minor=?, tax_rate_bps=?, notes=?, status='active', updated_at=?, price_basis=?, quoted_price_minor=?
               WHERE id=?""",
            (data["supplier_id"], material_id, net,
             tax_rate_bps, data.get("notes", ""), _now(), basis, quoted, offer_id),
        )
        if not result.rowcount:
            raise ValueError("供应商报价不存在")
        after = dict(conn.execute("SELECT * FROM supplier_offers WHERE id=?", (offer_id,)).fetchone())
        record_price_revision(conn, "offer", offer_id, before, after, "供应商报价修改", _now())


def list_supplier_offers(supplier_id=None, keyword="", active_only=True):
    with db_read() as conn:
        sql = """
            SELECT so.id, so.supplier_partner_id AS supplier_id,
                   bp.legal_name AS supplier_name, m.name, m.specification,
                   COALESCE(u.name, '') AS unit, so.price_minor, so.price_basis, so.quoted_price_minor,
                   so.tax_rate_bps AS offer_tax_rate_bps,
                   COALESCE(sp.default_tax_rate_bps, 0) AS default_tax_rate_bps,
                   COALESCE(so.notes, '') AS notes, so.material_id, so.public_id,
                   so.status, so.updated_at,
                   COALESCE(sp.supplier_category, '') AS category,
                   COALESCE(sp.quality_rating, '') AS quality,
                   COALESCE(sp.price_level, '') AS price_level,
                   COALESCE(sp.delivery_rating, '') AS delivery,
                   COALESCE(sp.export_capability, '否') AS export
            FROM supplier_offers so
            JOIN business_partners bp ON bp.id=so.supplier_partner_id
            JOIN materials m ON m.id=so.material_id
            LEFT JOIN units_of_measure u ON u.id=m.unit_id
            LEFT JOIN supplier_profiles sp ON sp.partner_id=bp.id
            WHERE 1=1
        """
        params = []
        if active_only:
            sql += " AND so.status='active' AND bp.status='active' AND m.status='active'"
        if supplier_id:
            sql += " AND so.supplier_partner_id=?"
            params.append(supplier_id)
        if keyword:
            sql += " AND (m.name LIKE ? OR m.specification LIKE ? OR bp.legal_name LIKE ?)"
            params.extend([f"%{keyword}%"] * 3)
        sql += " ORDER BY trim(m.name) COLLATE NOCASE, trim(m.specification) COLLATE NOCASE, so.id DESC"
        result = [dict(row) for row in conn.execute(sql, params).fetchall()]
        for row in result:
            # An offer stores its own explicit rate. The supplier default is
            # only a convenient prefill when a new offer is created.
            effective_bps = row["offer_tax_rate_bps"]
            row["tax_rate_bps"] = effective_bps
            row["tax_rate_percent"] = effective_bps / 100
            row["price"] = row["price_minor"] / 100
            _enrich_offer_price(row)
        return result


def get_supplier_offer(offer_id):
    with db_read() as conn:
        row = conn.execute(
            """SELECT so.id, so.supplier_partner_id AS supplier_id,
                      m.name, m.specification, COALESCE(u.name, '') AS unit,
                      so.price_minor, so.price_basis, so.quoted_price_minor, so.tax_rate_bps AS offer_tax_rate_bps,
                      COALESCE(sp.default_tax_rate_bps, 0) AS default_tax_rate_bps,
                      COALESCE(so.notes, '') AS notes, so.material_id
               FROM supplier_offers so JOIN materials m ON m.id=so.material_id
               LEFT JOIN units_of_measure u ON u.id=m.unit_id
               LEFT JOIN supplier_profiles sp ON sp.partner_id=so.supplier_partner_id
               WHERE so.id=?""",
            (offer_id,),
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        effective_bps = result["offer_tax_rate_bps"]
        result["tax_rate_bps"] = effective_bps
        result["tax_rate_percent"] = effective_bps / 100
        result["price"] = result["price_minor"] / 100
        _enrich_offer_price(result)
        return result


def get_supplier_offer_by_legacy_id(legacy_product_id):
    with db_read() as conn:
        row = conn.execute(
            "SELECT id FROM supplier_offers WHERE legacy_product_id=?",
            (legacy_product_id,),
        ).fetchone()
    return get_supplier_offer(row["id"]) if row else None


def deactivate_supplier_offers(offer_ids):
    if not offer_ids:
        return
    with db_transaction() as conn:
        placeholders = ",".join("?" * len(offer_ids))
        conn.execute(
            f"UPDATE supplier_offers SET status='inactive', updated_at=? WHERE id IN ({placeholders})",
            (_now(), *offer_ids),
        )
