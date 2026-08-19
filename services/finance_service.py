from services._common import now as _now, organization_id as _organization_id, minor as _minor
import sqlite3
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from db.connection import get_connection


def _date(value, label):
    value = (value or "").strip()
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError(f"{label}必须使用 YYYY-MM-DD 格式")
    return value


def _number(prefix):
    return f"{prefix}-{datetime.now():%Y%m%d}-{uuid4().hex[:6].upper()}"


def _percent(numerator, denominator):
    return numerator / denominator * 100 if denominator else None


def _duplicate_invoice_message(row):
    invoice_no = row["invoice_no"]
    if row["status"] == "void":
        return (
            f"发票号码“{invoice_no}”已有作废记录。请在销项发票页勾选"
            "“显示已作废”，选择该记录后点击“修改发票”恢复。"
        )
    amount = int(row["amount_minor"] or 0) / 100
    project_name = row["project_name"] or "未知项目"
    return (
        f"发票号码“{invoice_no}”已经登记过：{project_name}，"
        f"{row['invoice_date']}，¥{amount:,.2f}。请检查现有发票记录，"
        "不要重复录入。"
    )


def _invoice_values(data):
    try:
        tax_rate_bps = int(
            (Decimal(str(data.get("tax_rate", 0))) * 100).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    except (InvalidOperation, TypeError):
        raise ValueError("税率必须是有效数字")
    if not 0 <= tax_rate_bps <= 10000:
        raise ValueError("税率必须在 0% 到 100% 之间")
    return {
        "project_id": int(data.get("project_id") or 0),
        "contract_id": int(data.get("contract_id") or 0),
        "settlement_id": (
            int(data["settlement_id"]) if data.get("settlement_id") else None
        ),
        "invoice_no": (data.get("invoice_no") or "").strip()
        or _number("FP"),
        "invoice_date": _date(data.get("invoice_date"), "开票日期"),
        "amount_minor": _minor(data.get("amount")),
        "tax_rate_bps": tax_rate_bps,
        "buyer_name": (data.get("buyer_name") or "").strip(),
        "notes": (data.get("notes") or "").strip(),
    }


def _find_duplicate_invoice(conn, organization_id, invoice_no, exclude_id=None):
    sql = """SELECT i.invoice_no, i.invoice_date, i.amount_minor, i.status,
                    p.name AS project_name
             FROM sales_invoices i
             LEFT JOIN projects p ON p.id=i.project_id
             WHERE i.organization_id=? AND i.invoice_no=?"""
    params = [organization_id, invoice_no]
    if exclude_id is not None:
        sql += " AND i.id<>?"
        params.append(int(exclude_id))
    return conn.execute(sql, params).fetchone()


def _validate_invoice_capacity(conn, values, exclude_id=None):
    settled = conn.execute(
        """SELECT COALESCE(SUM(amount_minor), 0) FROM settlements
           WHERE project_id=? AND contract_id=? AND status='active'""",
        (values["project_id"], values["contract_id"]),
    ).fetchone()[0]
    if not settled:
        raise ValueError("请先为该项目登记收入确认")
    sql = """SELECT COALESCE(SUM(amount_minor), 0) FROM sales_invoices
             WHERE project_id=? AND contract_id=? AND status='active'"""
    params = [values["project_id"], values["contract_id"]]
    if exclude_id is not None:
        sql += " AND id<>?"
        params.append(int(exclude_id))
    invoiced = conn.execute(sql, params).fetchone()[0]
    if invoiced + values["amount_minor"] > settled:
        raise ValueError("累计开票金额不能超过已确认结算金额")


def _resolve_invoice_settlement(conn, values, exclude_id=None):
    params = [values["project_id"], values["contract_id"]]
    sql = """SELECT * FROM settlements
             WHERE project_id=? AND contract_id=? AND status='active'"""
    if values["settlement_id"] is not None:
        sql += " AND id=?"
        params.append(values["settlement_id"])
    sql += " ORDER BY settlement_date, id"
    settlements = conn.execute(sql, params).fetchall()
    if not settlements:
        raise ValueError("请选择有效的收入确认记录")
    if values["settlement_id"] is None and len(settlements) > 1:
        raise ValueError("该合同项目有多笔收入确认，请选择本次发票对应的记录")
    settlement = settlements[0]

    allocation_sql = """SELECT COALESCE(SUM(a.allocated_amount_minor), 0)
                        FROM invoice_settlement_allocations a
                        JOIN sales_invoices i ON i.id=a.invoice_id
                        WHERE a.settlement_id=? AND i.status='active'"""
    allocation_params = [settlement["id"]]
    if exclude_id is not None:
        allocation_sql += " AND i.id<>?"
        allocation_params.append(int(exclude_id))
    already_invoiced = conn.execute(
        allocation_sql, allocation_params
    ).fetchone()[0]
    remaining = settlement["amount_minor"] - already_invoiced
    if values["amount_minor"] > remaining:
        raise ValueError(
            f"本次开票金额不能超过该笔结算的待开票金额"
            f" ¥{remaining / 100:,.2f}"
        )
    return settlement


def _replace_invoice_settlement_allocation(
    conn, invoice_id, settlement_id, amount_minor, changed_at
):
    conn.execute(
        "DELETE FROM invoice_settlement_allocations WHERE invoice_id=?",
        (int(invoice_id),),
    )
    conn.execute(
        """INSERT INTO invoice_settlement_allocations (
               public_id, invoice_id, settlement_id,
               allocated_amount_minor, created_at, updated_at
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        (
            str(uuid4()),
            int(invoice_id),
            int(settlement_id),
            int(amount_minor),
            changed_at,
            changed_at,
        ),
    )


def _insert_invoice_revision(conn, invoice, action, changed_at):
    cursor = conn.execute(
        """INSERT INTO sales_invoice_revisions (
               invoice_id, action, previous_invoice_no, previous_project_id,
               previous_contract_id, previous_invoice_date,
               previous_amount_minor, previous_tax_rate_bps,
               previous_buyer_name_snapshot, previous_notes,
               previous_status, changed_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            invoice["id"],
            action,
            invoice["invoice_no"],
            invoice["project_id"],
            invoice["contract_id"],
            invoice["invoice_date"],
            invoice["amount_minor"],
            invoice["tax_rate_bps"],
            invoice["buyer_name_snapshot"],
            invoice["notes"],
            invoice["status"],
            changed_at,
        ),
    )
    allocations = conn.execute(
        """SELECT settlement_id, allocated_amount_minor
           FROM invoice_settlement_allocations
           WHERE invoice_id=? ORDER BY settlement_id""",
        (invoice["id"],),
    ).fetchall()
    for allocation in allocations:
        conn.execute(
            """INSERT INTO invoice_settlement_allocation_revisions (
                   invoice_revision_id, settlement_id, allocated_amount_minor
               ) VALUES (?, ?, ?)""",
            (
                cursor.lastrowid,
                allocation["settlement_id"],
                allocation["allocated_amount_minor"],
            ),
        )


def get_finance_dashboard(project_id=None):
    """Return settlement, invoice and receipt totals without merging projects."""
    conn = get_connection()
    try:
        sql = """
            SELECT p.id AS project_id, p.project_code, p.name AS project_name,
                   p.status AS project_status, p.business_mode,
                   p.invoice_policy,
                   COALESCE((
                       SELECT SUM(a.allocated_amount_minor)
                       FROM contract_project_allocations a
                       WHERE a.project_id=p.id AND a.status='active'
                   ), 0) AS allocated_minor,
                   COALESCE((
                       SELECT SUM(s.amount_minor)
                       FROM settlements s
                       WHERE s.project_id=p.id AND s.status='active'
                   ), 0) AS settlement_minor,
                   COALESCE((
                       SELECT SUM(i.amount_minor)
                       FROM sales_invoices i
                       WHERE i.project_id=p.id AND i.status='active'
                   ), 0) AS invoice_minor,
                   COALESCE((
                       SELECT SUM(ra.allocated_amount_minor)
                       FROM receipt_allocations ra
                       JOIN receipts r ON r.id=ra.receipt_id
                       WHERE ra.project_id=p.id AND r.status='active'
                   ), 0) AS receipt_minor,
                   COALESCE((
                       SELECT SUM(ra.allocated_amount_minor)
                       FROM receipt_allocations ra
                       JOIN receipts r ON r.id=ra.receipt_id
                       WHERE ra.project_id=p.id AND ra.settlement_id IS NULL
                         AND r.status='active'
                   ), 0) AS pending_receipt_minor
            FROM projects p
        """
        params = []
        if project_id:
            sql += " WHERE p.id=?"
            params.append(int(project_id))
        sql += """
            ORDER BY CASE p.status
                WHEN '进行中' THEN 1 WHEN '筹备中' THEN 2 ELSE 3 END,
                p.id DESC
        """
        projects = []
        for source in conn.execute(sql, params).fetchall():
            row = dict(source)
            row["invoice_applicable_minor"] = (
                0 if row["invoice_policy"] == "not_required"
                else row["settlement_minor"]
            )
            row["uninvoiced_minor"] = max(
                row["invoice_applicable_minor"] - row["invoice_minor"], 0
            )
            row["receivable_minor"] = max(
                row["settlement_minor"] - row["receipt_minor"], 0
            )
            row["invoice_rate_percent"] = _percent(
                row["invoice_minor"], row["invoice_applicable_minor"]
            )
            row["receipt_rate_percent"] = _percent(
                row["receipt_minor"], row["settlement_minor"]
            )
            projects.append(row)

        summary_keys = (
            "allocated_minor",
            "settlement_minor",
            "invoice_applicable_minor",
            "invoice_minor",
            "receipt_minor",
            "pending_receipt_minor",
            "uninvoiced_minor",
            "receivable_minor",
        )
        summary = {
            key: sum(row[key] for row in projects) for key in summary_keys
        }
        summary["invoice_rate_percent"] = _percent(
            summary["invoice_minor"], summary["invoice_applicable_minor"]
        )
        summary["receipt_rate_percent"] = _percent(
            summary["receipt_minor"], summary["settlement_minor"]
        )
        summary["unlinked_receipt_minor"] = summary["pending_receipt_minor"]
        activity_keys = (
            "allocated_minor",
            "settlement_minor",
            "invoice_minor",
            "receipt_minor",
        )
        summary["project_count"] = sum(
            1 for row in projects if any(row[key] for key in activity_keys)
        )
        return {"summary": summary, "projects": projects}
    finally:
        conn.close()


def _invoice_collection_values(source):
    row = dict(source)
    row["received_minor"] = int(row.get("received_minor") or 0)
    row["unreceived_minor"] = max(
        int(row["amount_minor"]) - row["received_minor"], 0
    )
    if row["status"] == "void":
        row["collection_status"] = "已作废"
    elif row["unreceived_minor"] == 0:
        row["collection_status"] = "已结清"
    elif row["received_minor"] > 0:
        row["collection_status"] = "部分回款"
    else:
        row["collection_status"] = "待回款"
    return row


def list_invoices(project_id=None, include_void=False):
    conn = get_connection()
    try:
        sql = """
            SELECT i.*, p.name AS project_name,
                   COALESCE(c.contract_no, '') AS contract_no,
                   COALESCE((
                       SELECT GROUP_CONCAT(s.settlement_no, '、')
                       FROM invoice_settlement_allocations a
                       JOIN settlements s ON s.id=a.settlement_id
                       WHERE a.invoice_id=i.id
                   ), '') AS settlement_no,
                   (
                       SELECT MIN(a.settlement_id)
                       FROM invoice_settlement_allocations a
                       WHERE a.invoice_id=i.id
                   ) AS settlement_id,
                   COALESCE((
                       SELECT SUM(ra.allocated_amount_minor)
                       FROM receipt_allocations ra
                       JOIN receipts r ON r.id=ra.receipt_id
                       WHERE ra.invoice_id=i.id AND r.status='active'
                   ), 0) AS received_minor
            FROM sales_invoices i
            JOIN projects p ON p.id=i.project_id
            LEFT JOIN contracts c ON c.id=i.contract_id
            WHERE 1=1
        """
        params = []
        if not include_void:
            sql += " AND i.status='active'"
        if project_id:
            sql += " AND i.project_id=?"
            params.append(project_id)
        sql += " ORDER BY i.invoice_date DESC, i.id DESC"
        return [
            _invoice_collection_values(row)
            for row in conn.execute(sql, params).fetchall()
        ]
    finally:
        conn.close()


def get_invoice(invoice_id):
    conn = get_connection()
    try:
        row = conn.execute(
            """SELECT i.*, p.name AS project_name,
                      COALESCE(c.contract_no, '') AS contract_no,
                      COALESCE((
                          SELECT GROUP_CONCAT(s.settlement_no, '、')
                          FROM invoice_settlement_allocations a
                          JOIN settlements s ON s.id=a.settlement_id
                          WHERE a.invoice_id=i.id
                      ), '') AS settlement_no,
                      (
                          SELECT MIN(a.settlement_id)
                          FROM invoice_settlement_allocations a
                          WHERE a.invoice_id=i.id
                      ) AS settlement_id,
                      COALESCE((
                          SELECT SUM(ra.allocated_amount_minor)
                          FROM receipt_allocations ra
                          JOIN receipts r ON r.id=ra.receipt_id
                          WHERE ra.invoice_id=i.id AND r.status='active'
                      ), 0) AS received_minor
               FROM sales_invoices i
               JOIN projects p ON p.id=i.project_id
               LEFT JOIN contracts c ON c.id=i.contract_id
               WHERE i.id=?""",
            (int(invoice_id),),
        ).fetchone()
        return _invoice_collection_values(row) if row else None
    finally:
        conn.close()


def create_invoice(data):
    values = _invoice_values(data)
    invoice_no = values["invoice_no"]

    conn = get_connection()
    try:
        organization_id = _organization_id(conn)
        existing = _find_duplicate_invoice(conn, organization_id, invoice_no)
        if existing:
            raise ValueError(_duplicate_invoice_message(existing))
        _validate_invoice_capacity(conn, values)
        settlement = _resolve_invoice_settlement(conn, values)
        now = _now()
        cursor = conn.execute(
            """INSERT INTO sales_invoices (
                   public_id, organization_id, invoice_no, project_id,
                   contract_id, invoice_date, amount_minor, tax_rate_bps,
                   buyer_name_snapshot, notes, status, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                str(uuid4()),
                organization_id,
                invoice_no,
                values["project_id"],
                values["contract_id"],
                values["invoice_date"],
                values["amount_minor"],
                values["tax_rate_bps"],
                values["buyer_name"],
                values["notes"],
                now,
                now,
            ),
        )
        _replace_invoice_settlement_allocation(
            conn,
            cursor.lastrowid,
            settlement["id"],
            values["amount_minor"],
            now,
        )
        conn.commit()
        return cursor.lastrowid
    except sqlite3.IntegrityError as error:
        conn.rollback()
        if "sales_invoices.organization_id, sales_invoices.invoice_no" in str(
            error
        ):
            raise ValueError(
                f"发票号码“{invoice_no}”已经存在，请检查现有发票记录。"
            ) from None
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_invoice(invoice_id, data):
    values = _invoice_values(data)
    conn = get_connection()
    try:
        invoice = conn.execute(
            "SELECT * FROM sales_invoices WHERE id=?", (int(invoice_id),)
        ).fetchone()
        if not invoice:
            raise ValueError("发票记录不存在")
        organization_id = invoice["organization_id"]
        duplicate = _find_duplicate_invoice(
            conn, organization_id, values["invoice_no"], invoice_id
        )
        if duplicate:
            raise ValueError(_duplicate_invoice_message(duplicate))

        linked = conn.execute(
            """SELECT COUNT(*) AS record_count,
                      COALESCE(SUM(ra.allocated_amount_minor), 0) AS amount_minor
               FROM receipt_allocations ra
               JOIN receipts r ON r.id=ra.receipt_id
               WHERE ra.invoice_id=? AND r.status='active'""",
            (int(invoice_id),),
        ).fetchone()
        if linked["record_count"] and (
            values["project_id"] != invoice["project_id"]
            or values["contract_id"] != invoice["contract_id"]
        ):
            raise ValueError("发票已关联回款，不能修改所属项目或合同")
        if values["amount_minor"] < linked["amount_minor"]:
            raise ValueError("发票金额不能低于已关联回款金额")

        _validate_invoice_capacity(conn, values, exclude_id=invoice_id)
        settlement = _resolve_invoice_settlement(
            conn, values, exclude_id=invoice_id
        )
        now = _now()
        action = "restore" if invoice["status"] == "void" else "update"
        _insert_invoice_revision(conn, invoice, action, now)
        conn.execute(
            """UPDATE sales_invoices
               SET invoice_no=?, project_id=?, contract_id=?, invoice_date=?,
                   amount_minor=?, tax_rate_bps=?, buyer_name_snapshot=?,
                   notes=?, status='active', updated_at=?
               WHERE id=?""",
            (
                values["invoice_no"],
                values["project_id"],
                values["contract_id"],
                values["invoice_date"],
                values["amount_minor"],
                values["tax_rate_bps"],
                values["buyer_name"],
                values["notes"],
                now,
                int(invoice_id),
            ),
        )
        _replace_invoice_settlement_allocation(
            conn,
            invoice_id,
            settlement["id"],
            values["amount_minor"],
            now,
        )
        conn.commit()
    except sqlite3.IntegrityError as error:
        conn.rollback()
        if "sales_invoices.organization_id, sales_invoices.invoice_no" in str(
            error
        ):
            raise ValueError(
                f"发票号码“{values['invoice_no']}”已经存在，请检查现有记录。"
            ) from None
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def void_invoices(invoice_ids):
    if not invoice_ids:
        return
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(invoice_ids))
        linked = conn.execute(
            f"""SELECT COUNT(*) FROM receipt_allocations ra
                JOIN receipts r ON r.id=ra.receipt_id
                WHERE ra.invoice_id IN ({placeholders})
                  AND r.status='active'""",
            invoice_ids,
        ).fetchone()[0]
        if linked:
            raise ValueError("发票已关联回款，不能直接作废")
        now = _now()
        invoices = conn.execute(
            f"""SELECT * FROM sales_invoices
                WHERE id IN ({placeholders}) AND status='active'""",
            invoice_ids,
        ).fetchall()
        for invoice in invoices:
            _insert_invoice_revision(conn, invoice, "void", now)
        conn.execute(
            f"""UPDATE sales_invoices SET status='void', updated_at=?
                WHERE id IN ({placeholders}) AND status='active'""",
            (now, *invoice_ids),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _receipt_allocations(conn, receipt_id):
    return conn.execute(
        """SELECT ra.*, COALESCE(s.settlement_no, '') AS settlement_no,
                  COALESCE(s.settlement_date, '') AS settlement_date
           FROM receipt_allocations ra
           LEFT JOIN settlements s ON s.id=ra.settlement_id
           WHERE ra.receipt_id=?
           ORDER BY s.settlement_date, s.id, ra.id""",
        (int(receipt_id),),
    ).fetchall()


def _receipt_record(conn, receipt_id):
    return conn.execute(
        """SELECT r.*, MIN(ra.project_id) AS project_id,
                  MIN(ra.contract_id) AS contract_id,
                  MIN(ra.invoice_id) AS invoice_id,
                  CASE WHEN COUNT(*)=1 THEN MIN(ra.settlement_id) END
                      AS settlement_id,
                  SUM(ra.allocated_amount_minor) AS allocation_amount_minor,
                  SUM(CASE WHEN ra.settlement_id IS NULL
                           THEN ra.allocated_amount_minor ELSE 0 END)
                      AS pending_allocation_minor,
                  COUNT(DISTINCT ra.settlement_id) AS settlement_count,
                  p.business_mode, p.invoice_policy,
                  COALESCE(bp.legal_name, p.customer_name, '') AS customer_name
           FROM receipts r
           JOIN receipt_allocations ra ON ra.receipt_id=r.id
           JOIN projects p ON p.id=ra.project_id
           LEFT JOIN business_partners bp ON bp.id=p.customer_partner_id
           WHERE r.id=?
           GROUP BY r.id""",
        (int(receipt_id),),
    ).fetchone()


def _receipt_listing_row(source):
    row = dict(source)
    pending = int(row.get("pending_allocation_minor") or 0)
    count = int(row.get("settlement_count") or 0)
    if pending:
        row["allocation_status"] = "待分配收入确认"
    elif count > 1:
        row["allocation_status"] = f"已关联 {count} 笔收入确认"
    elif count == 1:
        row["allocation_status"] = "已关联收入确认"
    else:
        row["allocation_status"] = "待分配收入确认"
    return row


def list_receipts(project_id=None):
    conn = get_connection()
    try:
        sql = """
            SELECT r.*, MIN(ra.project_id) AS project_id,
                   MIN(ra.contract_id) AS contract_id,
                   MIN(ra.invoice_id) AS invoice_id,
                   CASE WHEN COUNT(*)=1 THEN MIN(ra.settlement_id) END
                       AS settlement_id,
                   SUM(ra.allocated_amount_minor) AS allocated_amount_minor,
                   SUM(CASE WHEN ra.settlement_id IS NULL
                            THEN ra.allocated_amount_minor ELSE 0 END)
                       AS pending_allocation_minor,
                   COUNT(DISTINCT ra.settlement_id) AS settlement_count,
                   REPLACE(GROUP_CONCAT(DISTINCT s.settlement_no), ',', '、')
                       AS settlement_no,
                   p.name AS project_name, p.business_mode, p.invoice_policy,
                   COALESCE(c.contract_no, '') AS contract_no,
                   COALESCE(i.invoice_no, '') AS invoice_no
            FROM receipts r
            JOIN receipt_allocations ra ON ra.receipt_id=r.id
            JOIN projects p ON p.id=ra.project_id
            LEFT JOIN contracts c ON c.id=ra.contract_id
            LEFT JOIN sales_invoices i ON i.id=ra.invoice_id
            LEFT JOIN settlements s ON s.id=ra.settlement_id
            WHERE r.status='active'
        """
        params = []
        if project_id:
            sql += " AND ra.project_id=?"
            params.append(int(project_id))
        sql += " GROUP BY r.id ORDER BY r.receipt_date DESC, r.id DESC"
        return [
            _receipt_listing_row(row)
            for row in conn.execute(sql, params).fetchall()
        ]
    finally:
        conn.close()


def get_receipt(receipt_id):
    conn = get_connection()
    try:
        source = _receipt_record(conn, receipt_id)
        if not source or source["status"] != "active":
            return None
        receipt = _receipt_listing_row(source)
        receipt["allocated_amount_minor"] = receipt.pop(
            "allocation_amount_minor"
        )
        receipt["allocations"] = [
            dict(row) for row in _receipt_allocations(conn, receipt_id)
        ]
        return receipt
    finally:
        conn.close()


def _insert_receipt_revision(conn, receipt, action, changed_at):
    allocations = _receipt_allocations(conn, receipt["id"])
    if not allocations:
        raise ValueError("回款记录缺少项目归属，不能保存修改历史")
    first = allocations[0]
    invoice_ids = {row["invoice_id"] for row in allocations}
    settlement_ids = {row["settlement_id"] for row in allocations}
    invoice_id = next(iter(invoice_ids)) if len(invoice_ids) == 1 else None
    settlement_id = (
        next(iter(settlement_ids)) if len(settlement_ids) == 1 else None
    )
    allocated_minor = sum(
        int(row["allocated_amount_minor"]) for row in allocations
    )
    cursor = conn.execute(
        """INSERT INTO receipt_revisions (
               receipt_id, action, previous_receipt_no, previous_receipt_date,
               previous_payer_name_snapshot, previous_amount_minor,
               previous_payment_method, previous_notes, previous_status,
               previous_project_id, previous_contract_id, previous_invoice_id,
               previous_settlement_id, previous_allocated_amount_minor,
               changed_at
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            receipt["id"],
            action,
            receipt["receipt_no"],
            receipt["receipt_date"],
            receipt["payer_name_snapshot"],
            receipt["amount_minor"],
            receipt["payment_method"],
            receipt["notes"],
            receipt["status"],
            first["project_id"],
            first["contract_id"],
            invoice_id,
            settlement_id,
            allocated_minor,
            changed_at,
        ),
    )
    for allocation in allocations:
        conn.execute(
            """INSERT INTO receipt_allocation_revisions (
                   receipt_revision_id, previous_project_id,
                   previous_contract_id, previous_invoice_id,
                   previous_settlement_id, previous_allocated_amount_minor,
                   previous_notes
               ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                cursor.lastrowid,
                allocation["project_id"],
                allocation["contract_id"],
                allocation["invoice_id"],
                allocation["settlement_id"],
                allocation["allocated_amount_minor"],
                allocation["notes"],
            ),
        )


def _create_cash_settlement_for_receipt(
    conn, project_id, settlement_date, amount_minor, basis, now
):
    return conn.execute(
        """INSERT INTO settlements (
               public_id, organization_id, settlement_no, contract_id,
               project_id, settlement_date, period_start, period_end,
               amount_minor, basis, source_type, status, created_at, updated_at
           ) VALUES (?, ?, ?, NULL, ?, ?, NULL, NULL, ?, ?, 'cash_job',
                     'active', ?, ?)""",
        (
            str(uuid4()),
            _organization_id(conn),
            _number("WG"),
            project_id,
            settlement_date,
            amount_minor,
            (basis or "").strip() or "回款补录时同步建立完工金额确认",
            now,
            now,
        ),
    ).lastrowid


def _settlement_capacity_rows(
    conn, project_id, contract_id, exclude_receipt_id=None
):
    rows = conn.execute(
        """SELECT s.*,
                  COALESCE((
                      SELECT SUM(ra.allocated_amount_minor)
                      FROM receipt_allocations ra
                      JOIN receipts r ON r.id=ra.receipt_id
                      WHERE ra.settlement_id=s.id AND r.status='active'
                        AND (? IS NULL OR ra.receipt_id<>?)
                  ), 0) AS received_minor
           FROM settlements s
           WHERE s.project_id=? AND s.status='active'
             AND ((? IS NULL AND s.contract_id IS NULL) OR s.contract_id=?)
           ORDER BY s.settlement_date, s.id""",
        (
            exclude_receipt_id,
            exclude_receipt_id,
            int(project_id),
            contract_id,
            contract_id,
        ),
    ).fetchall()
    result = []
    for source in rows:
        row = dict(source)
        row["available_minor"] = max(
            int(row["amount_minor"]) - int(row["received_minor"]), 0
        )
        result.append(row)
    return result


def _requested_settlement_allocations(data):
    requested = data.get("settlement_allocations")
    if requested is None:
        return None
    if not requested:
        raise ValueError("手动分配至少需要选择一笔收入确认")
    allocations = []
    seen = set()
    for item in requested:
        settlement_id = int(item.get("settlement_id") or 0)
        if not settlement_id or settlement_id in seen:
            raise ValueError("手动分配包含无效或重复的收入确认")
        seen.add(settlement_id)
        if item.get("amount_minor") is not None:
            amount_minor = int(item["amount_minor"])
            if amount_minor <= 0:
                raise ValueError("手动分配金额必须大于 0")
        else:
            amount_minor = _minor(item.get("amount"))
        allocations.append(
            {"settlement_id": settlement_id, "amount_minor": amount_minor}
        )
    return allocations


def _invoice_target(conn, invoice_id, project_id, contract_id):
    rows = conn.execute(
        """SELECT i.project_id, i.contract_id, i.amount_minor,
                  a.settlement_id
           FROM sales_invoices i
           JOIN invoice_settlement_allocations a ON a.invoice_id=i.id
           JOIN settlements s ON s.id=a.settlement_id
           WHERE i.id=? AND i.status='active' AND s.status='active'""",
        (int(invoice_id),),
    ).fetchall()
    if len(rows) != 1:
        raise ValueError("该发票未明确关联一笔有效收入确认")
    invoice = rows[0]
    if (
        invoice["project_id"] != int(project_id)
        or invoice["contract_id"] != contract_id
    ):
        raise ValueError("发票与所选合同项目不一致")
    return invoice


def _plan_receipt_allocations(
    conn,
    *,
    project_id,
    contract_id,
    invoice_id,
    amount_minor,
    data,
    exclude_receipt_id=None,
):
    capacities = _settlement_capacity_rows(
        conn, project_id, contract_id, exclude_receipt_id
    )
    by_id = {row["id"]: row for row in capacities}
    if not capacities:
        raise ValueError("请先为该项目登记收入确认")

    requested = _requested_settlement_allocations(data)
    selected_settlement_id = int(data.get("settlement_id") or 0) or None
    if invoice_id:
        if requested is not None:
            raise ValueError("关联发票后，收入确认由发票关系自动确定")
        invoice = _invoice_target(conn, invoice_id, project_id, contract_id)
        target_id = int(invoice["settlement_id"])
        target = by_id.get(target_id)
        if not target:
            raise ValueError("发票对应的收入确认与所选合同项目不一致")
        already_received = conn.execute(
            """SELECT COALESCE(SUM(ra.allocated_amount_minor), 0)
               FROM receipt_allocations ra
               JOIN receipts r ON r.id=ra.receipt_id
               WHERE ra.invoice_id=? AND r.status='active'
                 AND (? IS NULL OR ra.receipt_id<>?)""",
            (invoice_id, exclude_receipt_id, exclude_receipt_id),
        ).fetchone()[0]
        if already_received + amount_minor > invoice["amount_minor"]:
            raise ValueError("关联到该发票的累计回款不能超过发票金额")
        if amount_minor > target["available_minor"]:
            raise ValueError("本次回款不能超过该笔收入确认的未回款金额")
        return [{"settlement_id": target_id, "amount_minor": amount_minor}]

    if requested is not None:
        if sum(row["amount_minor"] for row in requested) != amount_minor:
            raise ValueError("手动分配金额合计必须等于本次回款金额")
        for allocation in requested:
            target = by_id.get(allocation["settlement_id"])
            if not target:
                raise ValueError("手动分配的收入确认与所选合同项目不一致")
            if allocation["amount_minor"] > target["available_minor"]:
                raise ValueError(
                    f"收入确认 {target['settlement_no']} 的分配金额"
                    "不能超过未回款金额"
                )
        return requested

    if selected_settlement_id:
        target = by_id.get(selected_settlement_id)
        if not target:
            raise ValueError("所选收入确认与项目、合同不一致")
        if amount_minor > target["available_minor"]:
            raise ValueError("累计回款金额不能超过已确认收入金额")
        return [
            {
                "settlement_id": selected_settlement_id,
                "amount_minor": amount_minor,
            }
        ]

    remaining = amount_minor
    allocations = []
    for target in capacities:
        allocated = min(remaining, target["available_minor"])
        if allocated:
            allocations.append(
                {"settlement_id": target["id"], "amount_minor": allocated}
            )
            remaining -= allocated
        if remaining == 0:
            break
    if remaining:
        raise ValueError("累计回款金额不能超过已确认收入金额")
    return allocations


def preview_receipt_allocations(data, exclude_receipt_id=None):
    project_id = int(data.get("project_id") or 0)
    contract_id = int(data.get("contract_id") or 0) or None
    invoice_id = int(data.get("invoice_id") or 0) or None
    amount_minor = _minor(data.get("amount"))
    conn = get_connection()
    try:
        planned = _plan_receipt_allocations(
            conn,
            project_id=project_id,
            contract_id=contract_id,
            invoice_id=invoice_id,
            amount_minor=amount_minor,
            data=data,
            exclude_receipt_id=exclude_receipt_id,
        )
        capacities = {
            row["id"]: row
            for row in _settlement_capacity_rows(
                conn, project_id, contract_id, exclude_receipt_id
            )
        }
        return [
            {
                **allocation,
                "settlement_no": capacities[allocation["settlement_id"]][
                    "settlement_no"
                ],
                "settlement_date": capacities[allocation["settlement_id"]][
                    "settlement_date"
                ],
                "available_minor": capacities[allocation["settlement_id"]][
                    "available_minor"
                ],
            }
            for allocation in planned
        ]
    finally:
        conn.close()


def _insert_receipt_allocations(
    conn,
    receipt_id,
    project_id,
    contract_id,
    invoice_id,
    allocations,
    notes,
    created_at,
):
    for allocation in allocations:
        conn.execute(
            """INSERT INTO receipt_allocations (
                   public_id, receipt_id, project_id, contract_id, invoice_id,
                   settlement_id, allocated_amount_minor, notes, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(uuid4()),
                receipt_id,
                project_id,
                contract_id,
                invoice_id,
                allocation["settlement_id"],
                allocation["amount_minor"],
                notes,
                created_at,
            ),
        )


def create_receipt(data):
    project_id = int(data.get("project_id") or 0)
    contract_id = int(data.get("contract_id") or 0) or None
    invoice_id = int(data.get("invoice_id") or 0) or None
    settlement_id = int(data.get("settlement_id") or 0) or None
    amount_minor = _minor(data.get("amount"))
    receipt_date = _date(data.get("receipt_date"), "回款日期")

    conn = get_connection()
    try:
        project = conn.execute(
            """SELECT p.business_mode, p.invoice_policy,
                      COALESCE(bp.legal_name, p.customer_name, '') AS customer_name
               FROM projects p
               LEFT JOIN business_partners bp ON bp.id=p.customer_partner_id
               WHERE p.id=?""",
            (project_id,),
        ).fetchone()
        if not project:
            raise ValueError("请选择有效项目")
        is_cash = project["business_mode"] == "cash"
        now = _now()
        if is_cash:
            contract_id = None
            invoice_id = None
            if not settlement_id:
                if not str(data.get("settlement_amount") or "").strip():
                    raise ValueError("请填写零星工程的完工金额")
                settled = _minor(data.get("settlement_amount"))
                settlement_date = _date(
                    data.get("settlement_date") or receipt_date,
                    "完工确认日期",
                )
                settlement_id = _create_cash_settlement_for_receipt(
                    conn,
                    project_id,
                    settlement_date,
                    settled,
                    data.get("settlement_basis"),
                    now,
                )
            else:
                settlement = conn.execute(
                    """SELECT id FROM settlements
                       WHERE id=? AND project_id=? AND contract_id IS NULL
                         AND source_type='cash_job' AND status='active'""",
                    (settlement_id, project_id),
                ).fetchone()
                if not settlement:
                    raise ValueError("所选完工金额确认与零星现金工程不一致")
        else:
            if not contract_id:
                raise ValueError("正式合同工程必须选择合同项目分配")
        allocation_data = dict(data)
        allocation_data["settlement_id"] = settlement_id
        allocations = _plan_receipt_allocations(
            conn,
            project_id=project_id,
            contract_id=contract_id,
            invoice_id=invoice_id,
            amount_minor=amount_minor,
            data=allocation_data,
        )
        default_payment_method = "现金" if is_cash else "银行转账"
        notes = (data.get("notes") or "").strip()
        receipt_id = conn.execute(
            """INSERT INTO receipts (
                   public_id, organization_id, receipt_no, receipt_date,
                   payer_name_snapshot, amount_minor, payment_method, notes,
                   status, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                str(uuid4()),
                _organization_id(conn),
                (data.get("receipt_no") or "").strip() or _number("HK"),
                receipt_date,
                (data.get("payer_name") or project["customer_name"] or "").strip(),
                amount_minor,
                (data.get("payment_method") or default_payment_method).strip(),
                notes,
                now,
                now,
            ),
        ).lastrowid
        _insert_receipt_allocations(
            conn,
            receipt_id,
            project_id,
            contract_id,
            invoice_id,
            allocations,
            notes,
            now,
        )
        conn.commit()
        return receipt_id
    except sqlite3.IntegrityError as error:
        conn.rollback()
        if "receipts.organization_id, receipts.receipt_no" in str(error):
            raise ValueError("回款单号已经存在，请检查现有回款记录") from None
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_receipt(receipt_id, data):
    receipt_id = int(receipt_id)
    amount_minor = _minor(data.get("amount"))
    receipt_date = _date(data.get("receipt_date"), "回款日期")
    requested_invoice_id = int(data.get("invoice_id") or 0) or None

    conn = get_connection()
    try:
        receipt = _receipt_record(conn, receipt_id)
        if not receipt or receipt["status"] != "active":
            raise ValueError("有效回款记录不存在")

        is_cash = receipt["business_mode"] == "cash"
        invoice_id = None if is_cash else requested_invoice_id
        allocation_data = dict(data)
        if is_cash:
            current_allocations = _receipt_allocations(conn, receipt_id)
            settlement_ids = {
                row["settlement_id"] for row in current_allocations
            }
            if len(settlement_ids) != 1 or None in settlement_ids:
                raise ValueError("现金回款缺少有效的完工金额确认，不能修改")
            allocation_data["settlement_id"] = next(iter(settlement_ids))

        allocations = _plan_receipt_allocations(
            conn,
            project_id=receipt["project_id"],
            contract_id=receipt["contract_id"],
            invoice_id=invoice_id,
            amount_minor=amount_minor,
            data=allocation_data,
            exclude_receipt_id=receipt_id,
        )

        now = _now()
        _insert_receipt_revision(conn, receipt, "update", now)
        receipt_no = (data.get("receipt_no") or "").strip() or _number("HK")
        payer_name = (
            data.get("payer_name") or receipt["customer_name"] or ""
        ).strip()
        default_payment_method = "现金" if is_cash else "银行转账"
        payment_method = (
            data.get("payment_method") or default_payment_method
        ).strip()
        notes = (data.get("notes") or "").strip()
        conn.execute(
            """UPDATE receipts
               SET receipt_no=?, receipt_date=?, payer_name_snapshot=?,
                   amount_minor=?, payment_method=?, notes=?, updated_at=?
               WHERE id=?""",
            (
                receipt_no,
                receipt_date,
                payer_name,
                amount_minor,
                payment_method,
                notes,
                now,
                receipt_id,
            ),
        )
        conn.execute(
            "DELETE FROM receipt_allocations WHERE receipt_id=?",
            (receipt_id,),
        )
        _insert_receipt_allocations(
            conn,
            receipt_id,
            receipt["project_id"],
            receipt["contract_id"],
            invoice_id,
            allocations,
            notes,
            now,
        )
        conn.commit()
    except sqlite3.IntegrityError as error:
        conn.rollback()
        if "receipts.organization_id, receipts.receipt_no" in str(error):
            raise ValueError("回款单号已经存在，请检查现有回款记录") from None
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def void_receipts(receipt_ids):
    if not receipt_ids:
        return
    conn = get_connection()
    try:
        placeholders = ",".join("?" * len(receipt_ids))
        now = _now()
        for receipt_id in receipt_ids:
            receipt = _receipt_record(conn, receipt_id)
            if receipt and receipt["status"] == "active":
                _insert_receipt_revision(conn, receipt, "void", now)
        conn.execute(
            f"""UPDATE receipts SET status='void', updated_at=?
                WHERE id IN ({placeholders}) AND status='active'""",
            (now, *receipt_ids),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
