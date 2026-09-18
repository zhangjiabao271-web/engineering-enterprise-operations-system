from services._common import now as _now, organization_id as _organization_id, minor as _minor
import sqlite3
from contextlib import nullcontext
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import uuid4

from db.connection import db_read, db_transaction
from services.project_service import validate_cash_income_capacity
from services.attachment_service import invoice_attachment_statuses
from services import invoice_income_service


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
    tax_label = str(data.get('tax_rate_label') or '').strip()
    rate_input = str(data.get('tax_rate', 0)).strip()
    if rate_input in ('多税率', '免税', '不征税'):
        tax_label = tax_label or rate_input
        rate_input = '0'
    try:
        tax_rate_bps = int(
            (Decimal(rate_input) * 100).quantize(
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
        'tax_rate_label': tax_label,
    }


def _save_invoice_tax(conn, invoice_id, data, values):
    amounts = []
    for field in ('net_amount', 'tax_amount'):
        raw = str(data.get(field) if data.get(field) is not None else '').strip()
        if not raw:
            amounts.append(None)
            continue
        try:
            decimal = Decimal(raw)
            if not decimal.is_finite() or decimal < 0 or decimal * 100 != (decimal * 100).to_integral_value():
                raise ValueError('票面金额必须为非负数且最多两位小数')
            amounts.append(int(decimal * 100))
        except InvalidOperation:
            raise ValueError('票面未税金额和税额必须是有效数字') from None
    net, tax = amounts
    if (net is None) != (tax is None):
        raise ValueError('请同时填写票面未税金额和税额，未知时两项均留空')
    if net is not None and net + tax != values['amount_minor']:
        raise ValueError('未税金额 + 税额必须等于价税合计')
    if values['tax_rate_label'] and net is None:
        raise ValueError('多税率或免税信息需要填写票面未税金额及税额')
    if values['tax_rate_label'] in ('免税', '不征税') and tax != 0:
        raise ValueError('免税或不征税发票的票面税额应为0，请核对原票')
    conn.execute('''UPDATE sales_invoices SET net_amount_minor=?, tax_amount_minor=?,
                    tax_rate_label=? WHERE id=?''',
                 (net, tax, values['tax_rate_label'], invoice_id))


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


def _invoice_settlement_capacities(conn, values, exclude_id=None):
    params = [values["project_id"], values["contract_id"]]
    sql = """SELECT * FROM settlements
             WHERE project_id=? AND contract_id=? AND status='active'"""
    sql += " ORDER BY settlement_date, id"
    settlements = conn.execute(sql, params).fetchall()
    if not settlements:
        raise ValueError("请选择有有效收入确认的项目和合同")

    capacities = []
    for source in settlements:
        settlement = dict(source)
        allocation_sql = """SELECT COALESCE(
                                    SUM(a.allocated_amount_minor), 0
                                )
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
        linked_receipt_minor = 0
        if exclude_id is not None:
            linked_receipt_minor = conn.execute(
                """SELECT COALESCE(SUM(ra.allocated_amount_minor), 0)
                   FROM receipt_allocations ra
                   JOIN receipts r ON r.id=ra.receipt_id
                   WHERE ra.invoice_id=? AND ra.settlement_id=?
                     AND r.status='active'""",
                (int(exclude_id), settlement["id"]),
            ).fetchone()[0]
        settlement["available_minor"] = max(
            int(settlement["amount_minor"]) - int(already_invoiced), 0
        )
        settlement["linked_receipt_minor"] = int(linked_receipt_minor)
        capacities.append(settlement)
    return capacities


def _plan_invoice_settlement_allocations(conn, values, exclude_id=None):
    capacities = _invoice_settlement_capacities(conn, values, exclude_id)

    selected_id = values["settlement_id"]
    if selected_id is not None:
        selected = next(
            (row for row in capacities if row["id"] == selected_id), None
        )
        if not selected:
            raise ValueError("请选择有效的收入确认记录")
        linked_elsewhere = sum(
            row["linked_receipt_minor"]
            for row in capacities
            if row["id"] != selected_id
        )
        if linked_elsewhere:
            raise ValueError("发票已有回款，不能改到其他收入确认")
        if values["amount_minor"] < selected["linked_receipt_minor"]:
            raise ValueError("发票在该收入确认下的金额不能低于已关联回款金额")
        if values["amount_minor"] > selected["available_minor"]:
            remaining = selected["available_minor"]
            raise ValueError(
                f"本次开票金额不能超过该笔结算的待开票金额"
                f" ¥{remaining / 100:,.2f}"
            )
        return [
            {
                "settlement_id": selected_id,
                "amount_minor": values["amount_minor"],
            }
        ]

    allocations = {
        row["id"]: row["linked_receipt_minor"]
        for row in capacities
        if row["linked_receipt_minor"]
    }
    reserved = sum(allocations.values())
    if reserved > values["amount_minor"]:
        raise ValueError("发票金额不能低于已关联回款金额")
    remaining = values["amount_minor"] - reserved
    for row in capacities:
        available = row["available_minor"] - allocations.get(row["id"], 0)
        allocated = min(remaining, max(available, 0))
        if allocated:
            allocations[row["id"]] = allocations.get(row["id"], 0) + allocated
            remaining -= allocated
        if remaining == 0:
            break
    if remaining:
        available = values["amount_minor"] - remaining
        raise ValueError(
            "本次开票金额不能超过该项目合同的待开票金额 "
            f"¥{available / 100:,.2f}"
        )
    return [
        {"settlement_id": row["id"], "amount_minor": allocations[row["id"]]}
        for row in capacities
        if allocations.get(row["id"])
    ]


def _replace_invoice_settlement_allocations(
    conn, invoice_id, allocations, changed_at
):
    conn.execute(
        "DELETE FROM invoice_settlement_allocations WHERE invoice_id=?",
        (int(invoice_id),),
    )
    for allocation in allocations:
        conn.execute(
            """INSERT INTO invoice_settlement_allocations (
                   public_id, invoice_id, settlement_id,
                   allocated_amount_minor, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                str(uuid4()),
                int(invoice_id),
                int(allocation["settlement_id"]),
                int(allocation["amount_minor"]),
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
    conn.execute('''UPDATE sales_invoice_revisions SET previous_net_amount_minor=?,
        previous_tax_amount_minor=?,previous_tax_rate_label=? WHERE id=?''',
        (invoice['net_amount_minor'], invoice['tax_amount_minor'], invoice['tax_rate_label'], cursor.lastrowid))
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


FINANCE_SUMMARY_KEYS = (
    "allocated_minor",
    "settlement_minor",
    "invoice_applicable_minor",
    "invoice_minor",
    "receipt_minor",
    "pending_receipt_minor",
    "advance_minor",
    "uninvoiced_minor",
    "receivable_minor",
)


def summarize_finance_projects(projects):
    """Aggregate a project subset using the same finance-dashboard口径."""
    summary = {
        key: sum(int(row.get(key) or 0) for row in projects)
        for key in FINANCE_SUMMARY_KEYS
    }
    summary["invoice_rate_percent"] = _percent(
        summary["invoice_minor"], summary["invoice_applicable_minor"]
    )
    summary["receipt_rate_percent"] = _percent(
        summary["receipt_minor"] - summary['pending_receipt_minor'], summary["settlement_minor"]
    )
    summary["unlinked_receipt_minor"] = summary["pending_receipt_minor"]
    summary["project_count"] = sum(
        1
        for row in projects
        if any(
            int(row.get(key) or 0)
            for key in (
                "allocated_minor",
                "settlement_minor",
                "invoice_minor",
                "receipt_minor",
            )
        )
    )
    return summary


def get_finance_dashboard(project_id=None):
    """Return settlement, invoice and receipt totals without merging projects."""
    with db_read() as conn:
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
            row['advance_minor'] = conn.execute('''SELECT COALESCE(SUM(a.allocated_amount_minor),0)
                FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
                WHERE a.project_id=? AND a.settlement_id IS NULL
                  AND r.status='active' AND r.automatic_income_allocation=1''',
                (row['project_id'],)).fetchone()[0]
            row["invoice_applicable_minor"] = (
                0 if row["invoice_policy"] == "not_required"
                else row["settlement_minor"]
            )
            row["uninvoiced_minor"] = max(
                row["invoice_applicable_minor"] - row["invoice_minor"], 0
            )
            row["receivable_minor"] = max(
                row["settlement_minor"] - row["receipt_minor"] + row['pending_receipt_minor'], 0
            )
            row["invoice_rate_percent"] = _percent(
                row["invoice_minor"], row["invoice_applicable_minor"]
            )
            row["receipt_rate_percent"] = _percent(
                row["receipt_minor"] - row['pending_receipt_minor'], row["settlement_minor"]
            )
            if (
                row["settlement_minor"]
                and row["receivable_minor"] == 0
                and row["pending_receipt_minor"] == 0
            ):
                row["collection_status"] = "已结清"
            elif row['advance_minor'] and row['receivable_minor'] == 0:
                row['collection_status'] = '有预收款'
            elif row["receipt_minor"]:
                row["collection_status"] = "部分回款"
            else:
                row["collection_status"] = "待回款"
            projects.append(row)

        summary = summarize_finance_projects(projects)
        return {"summary": summary, "projects": projects}


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


def _invoice_customer_id(conn, invoice_id):
    row = conn.execute(
        """SELECT COALESCE(p.customer_partner_id, c.customer_partner_id)
                      AS customer_id
           FROM sales_invoices i
           JOIN projects p ON p.id=i.project_id
           LEFT JOIN contracts c ON c.id=i.contract_id
           WHERE i.id=?""",
        (int(invoice_id),),
    ).fetchone()
    return int(row["customer_id"]) if row and row["customer_id"] else None


def _receipt_customer_id(conn, receipt_id):
    row = conn.execute(
        """SELECT MIN(COALESCE(
                      p.customer_partner_id, c.customer_partner_id
                  )) AS customer_id,
                  COUNT(DISTINCT COALESCE(
                      p.customer_partner_id, c.customer_partner_id
                  )) AS customer_count
           FROM receipt_allocations ra
           JOIN projects p ON p.id=ra.project_id
           LEFT JOIN contracts c ON c.id=ra.contract_id
           WHERE ra.receipt_id=? AND ra.status='active'
             AND p.business_mode='contract'""",
        (int(receipt_id),),
    ).fetchone()
    if not row or row["customer_count"] != 1 or not row["customer_id"]:
        return None
    return int(row["customer_id"])


def _customer_invoice_receipt_plan(conn, customer_id):
    invoices = conn.execute(
        """SELECT i.id, i.invoice_date, i.amount_minor
           FROM sales_invoices i
           JOIN projects p ON p.id=i.project_id
           LEFT JOIN contracts c ON c.id=i.contract_id
           WHERE i.status='active'
             AND COALESCE(p.customer_partner_id, c.customer_partner_id)=?
           ORDER BY i.invoice_date, i.id""",
        (int(customer_id),),
    ).fetchall()
    invoice_remaining = {
        int(row["id"]): int(row["amount_minor"]) for row in invoices
    }
    receipts = conn.execute(
        """SELECT r.id, r.receipt_date, r.amount_minor,
                  CASE
                    WHEN COUNT(DISTINCT ra.invoice_id)=1
                     AND SUM(CASE WHEN ra.invoice_id IS NULL THEN 1 ELSE 0 END)=0
                    THEN MIN(ra.invoice_id)
                  END AS manual_invoice_id
           FROM receipts r
           JOIN receipt_allocations ra
             ON ra.receipt_id=r.id AND ra.status='active'
           JOIN projects p ON p.id=ra.project_id
           LEFT JOIN contracts c ON c.id=ra.contract_id
           WHERE r.status='active' AND p.business_mode='contract'
           GROUP BY r.id
           HAVING COUNT(DISTINCT COALESCE(
                      p.customer_partner_id, c.customer_partner_id
                  ))=1
              AND MIN(COALESCE(
                      p.customer_partner_id, c.customer_partner_id
                  ))=?
           ORDER BY r.receipt_date, r.id""",
        (int(customer_id),),
    ).fetchall()

    plan = []
    automatic_receipts = []
    for receipt in receipts:
        receipt_id = int(receipt["id"])
        remaining = int(receipt["amount_minor"])
        manual_invoice_id = receipt["manual_invoice_id"]
        if manual_invoice_id in invoice_remaining:
            manual_invoice_id = int(manual_invoice_id)
            allocated = min(remaining, invoice_remaining[manual_invoice_id])
            if allocated:
                plan.append(
                    (manual_invoice_id, receipt_id, allocated, "manual")
                )
                invoice_remaining[manual_invoice_id] -= allocated
                remaining -= allocated
        if remaining:
            automatic_receipts.append((receipt_id, remaining))

    invoice_index = 0
    for receipt_id, receipt_remaining in automatic_receipts:
        while receipt_remaining and invoice_index < len(invoices):
            invoice_id = int(invoices[invoice_index]["id"])
            available = invoice_remaining[invoice_id]
            if not available:
                invoice_index += 1
                continue
            allocated = min(receipt_remaining, available)
            plan.append((invoice_id, receipt_id, allocated, "fifo"))
            invoice_remaining[invoice_id] -= allocated
            receipt_remaining -= allocated
            if not invoice_remaining[invoice_id]:
                invoice_index += 1
    return plan


def _reconcile_customer_invoice_receipts(conn, customer_id, changed_at):
    if not customer_id:
        return
    desired = sorted(_customer_invoice_receipt_plan(conn, customer_id))
    current = [
        (
            int(row["invoice_id"]),
            int(row["receipt_id"]),
            int(row["allocated_amount_minor"]),
            row["allocation_method"],
        )
        for row in conn.execute(
            """SELECT invoice_id, receipt_id, allocated_amount_minor,
                      allocation_method
               FROM invoice_receipt_allocations
               WHERE customer_partner_id=? AND status='active'
               ORDER BY invoice_id, receipt_id, allocated_amount_minor,
                        allocation_method""",
            (int(customer_id),),
        ).fetchall()
    ]
    if current == desired:
        return

    conn.execute(
        """UPDATE invoice_receipt_allocations
           SET status='void', updated_at=?, voided_at=?
           WHERE customer_partner_id=? AND status='active'""",
        (changed_at, changed_at, int(customer_id)),
    )
    if not desired:
        return
    batch_id = str(uuid4())
    for invoice_id, receipt_id, amount_minor, method in desired:
        conn.execute(
            """INSERT INTO invoice_receipt_allocations (
                   public_id, batch_id, customer_partner_id,
                   invoice_id, receipt_id, allocated_amount_minor,
                   allocation_method, status, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (
                str(uuid4()),
                batch_id,
                int(customer_id),
                invoice_id,
                receipt_id,
                amount_minor,
                method,
                changed_at,
                changed_at,
            ),
        )


def _reconcile_customer_ids(conn, customer_ids, changed_at):
    for customer_id in sorted({item for item in customer_ids if item}):
        _reconcile_customer_invoice_receipts(conn, customer_id, changed_at)


def list_invoices(project_id=None, include_void=False):
    with db_read() as conn:
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
                       SELECT CASE WHEN COUNT(*)=1
                                   THEN MIN(a.settlement_id) END
                       FROM invoice_settlement_allocations a
                       WHERE a.invoice_id=i.id
                   ) AS settlement_id,
                   (
                       SELECT COUNT(*)
                       FROM invoice_settlement_allocations a
                       WHERE a.invoice_id=i.id
                   ) AS settlement_count,
                   COALESCE((
                       SELECT COALESCE(
                           (
                               SELECT SUM(ira.allocated_amount_minor)
                               FROM invoice_receipt_allocations ira
                               WHERE ira.invoice_id=i.id
                                 AND ira.status='active'
                           ),
                           (
                               SELECT SUM(ra.allocated_amount_minor)
                               FROM receipt_allocations ra
                               JOIN receipts r ON r.id=ra.receipt_id
                               WHERE ra.invoice_id=i.id
                                 AND ra.status='active'
                                 AND r.status='active'
                           )
                       )
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
        rows = [
            _invoice_collection_values(row)
            for row in conn.execute(sql, params).fetchall()
        ]
        statuses = invoice_attachment_statuses(conn, [row["id"] for row in rows])
        for row in rows:
            row.update(statuses[row["id"]])
        return rows


def get_invoice(invoice_id):
    with db_read() as conn:
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
                          SELECT CASE WHEN COUNT(*)=1
                                      THEN MIN(a.settlement_id) END
                          FROM invoice_settlement_allocations a
                          WHERE a.invoice_id=i.id
                      ) AS settlement_id,
                      (
                          SELECT COUNT(*)
                          FROM invoice_settlement_allocations a
                          WHERE a.invoice_id=i.id
                      ) AS settlement_count,
                      COALESCE((
                          SELECT COALESCE(
                              (
                                  SELECT SUM(ira.allocated_amount_minor)
                                  FROM invoice_receipt_allocations ira
                                  WHERE ira.invoice_id=i.id
                                    AND ira.status='active'
                              ),
                              (
                                  SELECT SUM(ra.allocated_amount_minor)
                                  FROM receipt_allocations ra
                                  JOIN receipts r ON r.id=ra.receipt_id
                                  WHERE ra.invoice_id=i.id
                                    AND ra.status='active'
                                    AND r.status='active'
                              )
                          )
                      ), 0) AS received_minor
               FROM sales_invoices i
               JOIN projects p ON p.id=i.project_id
               LEFT JOIN contracts c ON c.id=i.contract_id
               WHERE i.id=?""",
            (int(invoice_id),),
        ).fetchone()
        return _invoice_collection_values(row) if row else None


def create_invoice(data, *, _conn=None):
    values = _invoice_values(data)
    invoice_no = values["invoice_no"]

    with (nullcontext(_conn) if _conn is not None else db_transaction(immediate=True)) as conn:
        try:
            organization_id = _organization_id(conn)
            existing = _find_duplicate_invoice(conn, organization_id, invoice_no)
            if existing:
                raise ValueError(_duplicate_invoice_message(existing))
            income_id = invoice_income_service.prepare_income(conn, values)
            if income_id:
                values['settlement_id'] = income_id
            _validate_invoice_capacity(conn, values)
            allocations = _plan_invoice_settlement_allocations(conn, values)
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
            if income_id:
                invoice_income_service.link_created_income(conn, cursor.lastrowid, income_id)
            _save_invoice_tax(conn, cursor.lastrowid, data, values)
            _replace_invoice_settlement_allocations(
                conn,
                cursor.lastrowid,
                allocations,
                now,
            )
            _reconcile_customer_invoice_receipts(
                conn, _invoice_customer_id(conn, cursor.lastrowid), now
            )
            from services.advance_receipt_service import reconcile
            reconcile(conn, values['project_id'], values['contract_id'])
            return cursor.lastrowid
        except sqlite3.IntegrityError as error:
            if "sales_invoices.organization_id, sales_invoices.invoice_no" in str(
                error
            ):
                raise ValueError(
                    f"发票号码“{invoice_no}”已经存在，请检查现有发票记录。"
                ) from None
            raise


def update_invoice(invoice_id, data, *, _conn=None):
    values = _invoice_values(data)
    with (nullcontext(_conn) if _conn is not None else db_transaction(immediate=True)) as conn:
        try:
            invoice = conn.execute(
                "SELECT * FROM sales_invoices WHERE id=?", (int(invoice_id),)
            ).fetchone()
            if not invoice:
                raise ValueError("发票记录不存在")
            previous_customer_id = _invoice_customer_id(conn, invoice_id)
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

            income_id = invoice_income_service.prepare_income(conn, values, invoice_id)
            if income_id:
                values['settlement_id'] = income_id
            _validate_invoice_capacity(conn, values, exclude_id=invoice_id)
            allocations = _plan_invoice_settlement_allocations(
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
            tax_data = data
            if ('net_amount' not in data and 'tax_amount' not in data
                    and values['amount_minor'] == invoice['amount_minor']
                    and values['tax_rate_bps'] == invoice['tax_rate_bps']):
                tax_data = dict(data,
                    net_amount=None if invoice['net_amount_minor'] is None else str(Decimal(invoice['net_amount_minor']) / 100),
                    tax_amount=None if invoice['tax_amount_minor'] is None else str(Decimal(invoice['tax_amount_minor']) / 100))
                values['tax_rate_label'] = invoice['tax_rate_label']
            _save_invoice_tax(conn, invoice_id, tax_data, values)
            _replace_invoice_settlement_allocations(
                conn,
                invoice_id,
                allocations,
                now,
            )
            _reconcile_customer_ids(
                conn,
                {previous_customer_id, _invoice_customer_id(conn, invoice_id)},
                now,
            )
            from services.advance_receipt_service import reconcile
            reconcile(conn, values['project_id'], values['contract_id'])
        except sqlite3.IntegrityError as error:
            if "sales_invoices.organization_id, sales_invoices.invoice_no" in str(
                error
            ):
                raise ValueError(
                    f"发票号码“{values['invoice_no']}”已经存在，请检查现有记录。"
                ) from None
            raise


def void_invoices(invoice_ids):
    if not invoice_ids:
        return
    with db_transaction(immediate=True) as conn:
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
        customer_ids = {
            _invoice_customer_id(conn, invoice["id"])
            for invoice in invoices
        }
        for invoice in invoices:
            invoice_income_service.prepare_income(conn, dict(invoice), invoice['id'], void=True)
            _insert_invoice_revision(conn, invoice, "void", now)
        conn.execute(
            f"""UPDATE sales_invoices SET status='void', updated_at=?
                WHERE id IN ({placeholders}) AND status='active'""",
            (now, *invoice_ids),
        )
        _reconcile_customer_ids(conn, customer_ids, now)


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


def _invoice_matches_for_receipt(conn, receipt_id):
    rows = conn.execute(
        """SELECT i.id AS invoice_id, i.invoice_no, i.invoice_date,
                  SUM(ira.allocated_amount_minor) AS allocated_amount_minor,
                  MIN(ira.allocation_method) AS allocation_method
           FROM invoice_receipt_allocations ira
           JOIN sales_invoices i ON i.id=ira.invoice_id
           WHERE ira.receipt_id=? AND ira.status='active'
             AND i.status='active'
           GROUP BY i.id, i.invoice_no, i.invoice_date
           ORDER BY i.invoice_date, i.id""",
        (int(receipt_id),),
    ).fetchall()
    if rows:
        return [dict(row) for row in rows]
    return [
        dict(row)
        for row in conn.execute(
            """SELECT i.id AS invoice_id, i.invoice_no, i.invoice_date,
                      SUM(ra.allocated_amount_minor) AS allocated_amount_minor,
                      'manual' AS allocation_method
               FROM receipt_allocations ra
               JOIN sales_invoices i ON i.id=ra.invoice_id
               WHERE ra.receipt_id=? AND ra.status='active'
                 AND i.status='active'
               GROUP BY i.id, i.invoice_no, i.invoice_date
               ORDER BY i.invoice_date, i.id""",
            (int(receipt_id),),
        ).fetchall()
    ]


def _apply_receipt_invoice_values(row, invoice_matches):
    matched_minor = sum(
        int(match["allocated_amount_minor"]) for match in invoice_matches
    )
    row["invoice_matches"] = invoice_matches
    row["invoice_no"] = "、".join(
        match["invoice_no"] for match in invoice_matches
    )
    row["invoice_matched_minor"] = matched_minor
    row["invoice_unmatched_minor"] = max(
        int(row["amount_minor"]) - matched_minor, 0
    )
    return row


def _receipt_listing_row(source):
    row = dict(source)
    pending = int(row.get("pending_allocation_minor") or 0)
    count = int(row.get("settlement_count") or 0)
    if pending:
        if row.get('automatic_income_allocation'):
            row['allocation_status'] = f"预收款 ¥{pending / 100:,.2f}"
        else:
            row["allocation_status"] = "待分配收入确认"
    elif count > 1:
        row["allocation_status"] = f"已关联 {count} 笔收入确认"
    elif count == 1:
        row["allocation_status"] = "已关联收入确认"
    else:
        row["allocation_status"] = "待分配收入确认"
    return row


def list_receipts(project_id=None):
    with db_read() as conn:
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
                   COALESCE(c.contract_no, '') AS contract_no
            FROM receipts r
            JOIN receipt_allocations ra ON ra.receipt_id=r.id
            JOIN projects p ON p.id=ra.project_id
            LEFT JOIN contracts c ON c.id=ra.contract_id
            LEFT JOIN settlements s ON s.id=ra.settlement_id
            WHERE r.status='active'
        """
        params = []
        if project_id:
            sql += " AND ra.project_id=?"
            params.append(int(project_id))
        sql += " GROUP BY r.id ORDER BY r.receipt_date DESC, r.id DESC"
        receipts = []
        for source in conn.execute(sql, params).fetchall():
            row = _receipt_listing_row(source)
            receipts.append(
                _apply_receipt_invoice_values(
                    row, _invoice_matches_for_receipt(conn, row["id"])
                )
            )
        return receipts


def get_receipt(receipt_id):
    with db_read() as conn:
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
        return _apply_receipt_invoice_values(
            receipt, _invoice_matches_for_receipt(conn, receipt_id)
        )


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


def _invoice_targets(conn, invoice_id, project_id, contract_id):
    invoice = conn.execute(
        """SELECT project_id, contract_id, amount_minor
           FROM sales_invoices WHERE id=? AND status='active'""",
        (int(invoice_id),),
    ).fetchone()
    if not invoice:
        raise ValueError("发票不存在或已作废")
    if (
        invoice["project_id"] != int(project_id)
        or invoice["contract_id"] != contract_id
    ):
        raise ValueError("发票与所选合同项目不一致")
    allocations = conn.execute(
        """SELECT a.settlement_id, a.allocated_amount_minor
           FROM invoice_settlement_allocations a
           JOIN settlements s ON s.id=a.settlement_id
           WHERE a.invoice_id=? AND s.status='active'
           ORDER BY s.settlement_date, s.id""",
        (int(invoice_id),),
    ).fetchall()
    if not allocations:
        raise ValueError("该发票未关联有效收入确认")
    return invoice, allocations


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
    if not capacities and not data.get('allow_advance'):
        raise ValueError("请先为该项目登记收入确认")

    requested = _requested_settlement_allocations(data)
    selected_settlement_id = int(data.get("settlement_id") or 0) or None
    if invoice_id:
        if requested is not None:
            raise ValueError("关联发票后，收入确认由发票关系自动确定")
        invoice, invoice_allocations = _invoice_targets(
            conn, invoice_id, project_id, contract_id
        )
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
        remaining = amount_minor
        planned = []
        for invoice_allocation in invoice_allocations:
            settlement_id = int(invoice_allocation["settlement_id"])
            target = by_id.get(settlement_id)
            if not target:
                raise ValueError("发票对应的收入确认与所选合同项目不一致")
            received_for_target = conn.execute(
                """SELECT COALESCE(SUM(ra.allocated_amount_minor), 0)
                   FROM receipt_allocations ra
                   JOIN receipts r ON r.id=ra.receipt_id
                   WHERE ra.invoice_id=? AND ra.settlement_id=?
                     AND r.status='active'
                     AND (? IS NULL OR ra.receipt_id<>?)""",
                (
                    invoice_id,
                    settlement_id,
                    exclude_receipt_id,
                    exclude_receipt_id,
                ),
            ).fetchone()[0]
            invoice_available = max(
                int(invoice_allocation["allocated_amount_minor"])
                - int(received_for_target),
                0,
            )
            allocated = min(
                remaining, invoice_available, target["available_minor"]
            )
            if allocated:
                planned.append(
                    {"settlement_id": settlement_id, "amount_minor": allocated}
                )
                remaining -= allocated
            if remaining == 0:
                break
        if remaining:
            raise ValueError("本次回款不能超过该发票可回款金额")
        return planned

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
        if data.get('allow_advance'):
            allocations.append({'settlement_id': None, 'amount_minor': remaining})
        else:
            raise ValueError("累计回款金额不能超过已确认收入金额")
    return allocations


def preview_receipt_allocations(data, exclude_receipt_id=None):
    project_id = int(data.get("project_id") or 0)
    contract_id = int(data.get("contract_id") or 0) or None
    invoice_id = int(data.get("invoice_id") or 0) or None
    amount_minor = _minor(data.get("amount"))
    with db_read() as conn:
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
        capacities[None] = {'settlement_no': '预收款', 'settlement_date': '',
                            'available_minor': None}
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


def default_receipt_payer(project_id, contract_id=None):
    """Resolve the payer suggestion without changing the receipt's attribution."""
    with db_read() as conn:
        if contract_id:
            row = conn.execute("""SELECT COALESCE(NULLIF(b.legal_name,''),c.customer_name_snapshot,'')
                FROM contracts c LEFT JOIN business_partners b ON b.id=c.customer_partner_id
                WHERE c.id=?""", (contract_id,)).fetchone()
            if row and row[0]:
                return row[0]
        row = conn.execute("""SELECT COALESCE(NULLIF(b.legal_name,''),p.customer_name,'')
            FROM projects p LEFT JOIN business_partners b ON b.id=p.customer_partner_id
            WHERE p.id=?""", (project_id,)).fetchone()
        return row[0] if row else ''


def create_receipt(data):
    project_id = int(data.get("project_id") or 0)
    contract_id = int(data.get("contract_id") or 0) or None
    invoice_id = int(data.get("invoice_id") or 0) or None
    settlement_id = int(data.get("settlement_id") or 0) or None
    amount_minor = _minor(data.get("amount"))
    receipt_date = _date(data.get("receipt_date"), "回款日期")

    with db_transaction(immediate=True) as conn:
        try:
            from services.cash_closure_service import guard_receipt_change
            guard_receipt_change(conn, project_id)
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
                if not settlement_id and str(data.get('settlement_amount') or '').strip():
                    settled = _minor(data.get("settlement_amount"))
                    validate_cash_income_capacity(conn, project_id, settled)
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
                elif settlement_id:
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
                if not conn.execute('''SELECT 1 FROM contract_project_allocations
                    WHERE contract_id=? AND project_id=? AND status='active' ''',
                    (contract_id, project_id)).fetchone():
                    raise ValueError('所选合同尚未关联到该项目')
            allocation_data = dict(data)
            allocation_data["settlement_id"] = settlement_id
            automatic = not invoice_id and not settlement_id and data.get('settlement_allocations') is None
            allocation_data['allow_advance'] = automatic
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
                    (str(data.get("payer_name") or "").strip() or default_receipt_payer(project_id, contract_id)),
                    amount_minor,
                    (data.get("payment_method") or default_payment_method).strip(),
                    notes,
                    now,
                    now,
                ),
            ).lastrowid
            conn.execute('UPDATE receipts SET automatic_income_allocation=? WHERE id=?',
                         (int(automatic), receipt_id))
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
            from services.advance_receipt_service import reconcile
            reconcile(conn, project_id, contract_id)
            _reconcile_customer_invoice_receipts(
                conn, _receipt_customer_id(conn, receipt_id), now
            )
            return receipt_id
        except sqlite3.IntegrityError as error:
            if "receipts.organization_id, receipts.receipt_no" in str(error):
                raise ValueError("回款单号已经存在，请检查现有回款记录") from None
            raise


def update_receipt(receipt_id, data):
    receipt_id = int(receipt_id)
    amount_minor = _minor(data.get("amount"))
    receipt_date = _date(data.get("receipt_date"), "回款日期")
    requested_invoice_id = int(data.get("invoice_id") or 0) or None

    with db_transaction(immediate=True) as conn:
        try:
            receipt = _receipt_record(conn, receipt_id)
            if not receipt or receipt["status"] != "active":
                raise ValueError("有效回款记录不存在")
            from services.cash_closure_service import guard_receipt_change
            guard_receipt_change(conn, receipt['project_id'])
            previous_customer_id = _receipt_customer_id(conn, receipt_id)

            is_cash = receipt["business_mode"] == "cash"
            invoice_id = None if is_cash else requested_invoice_id
            allocation_data = dict(data)
            automatic = (
                (bool(receipt['automatic_income_allocation']) or bool(data.get('allow_advance')))
                and not invoice_id and not data.get('settlement_id')
                and data.get('settlement_allocations') is None
            )
            if automatic:
                invoice_id = None
                allocation_data = {'allow_advance': True}
            elif is_cash:
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
            conn.execute('UPDATE receipts SET automatic_income_allocation=? WHERE id=?',
                         (int(automatic), receipt_id))
            from services.advance_receipt_service import reconcile
            reconcile(conn, receipt['project_id'], receipt['contract_id'])
            _reconcile_customer_ids(
                conn,
                {previous_customer_id, _receipt_customer_id(conn, receipt_id)},
                now,
            )
        except sqlite3.IntegrityError as error:
            if "receipts.organization_id, receipts.receipt_no" in str(error):
                raise ValueError("回款单号已经存在，请检查现有回款记录") from None
            raise


def void_receipts(receipt_ids):
    if not receipt_ids:
        return
    with db_transaction(immediate=True) as conn:
        targets = set()
        placeholders = ",".join("?" * len(receipt_ids))
        now = _now()
        customer_ids = {
            _receipt_customer_id(conn, receipt_id)
            for receipt_id in receipt_ids
        }
        for receipt_id in receipt_ids:
            receipt = _receipt_record(conn, receipt_id)
            if receipt and receipt["status"] == "active":
                from services.cash_closure_service import guard_receipt_change
                guard_receipt_change(conn, receipt['project_id'])
                targets.add((receipt['project_id'], receipt['contract_id']))
                _insert_receipt_revision(conn, receipt, "void", now)
        conn.execute(
            f"""UPDATE receipts SET status='void', updated_at=?
                WHERE id IN ({placeholders}) AND status='active'""",
            (now, *receipt_ids),
        )
        conn.execute(
            f"""UPDATE receipt_allocations SET status='void'
                WHERE receipt_id IN ({placeholders}) AND status='active'""",
            receipt_ids,
        )
        from services.advance_receipt_service import reconcile
        for project_id, contract_id in targets:
            reconcile(conn, project_id, contract_id)
        _reconcile_customer_ids(conn, customer_ids, now)
