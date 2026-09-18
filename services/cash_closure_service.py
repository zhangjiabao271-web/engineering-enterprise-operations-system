"""Explicit, reversible cash-project settlement discounts; never fake receipts."""
import json

from db.connection import db_read, db_transaction
from services._common import now

REASONS = ('抹零', '优惠', '最终结算调整')


def guard_receipt_change(conn, project_id):
    if conn.execute("SELECT 1 FROM cash_collection_closures WHERE project_id=? AND status='active'",
                    (project_id,)).fetchone():
        raise ValueError('项目已有收款结清调整，请先撤销结清再修改回款')


def _income(conn, project_id):
    return [dict(r) for r in conn.execute(
        'SELECT * FROM settlements WHERE project_id=? ORDER BY settlement_date DESC,id DESC',
        (project_id,))]


def _preview(conn, project_id):
    project = conn.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if not project or project['business_mode'] != 'cash' or project['invoice_policy'] != 'not_required':
        raise ValueError('此入口仅用于无需开票的零星现金工程；合同工程需单独核对结算')
    guard_receipt_change(conn, project_id)
    if conn.execute("SELECT 1 FROM sales_invoices WHERE project_id=? AND status='active'", (project_id,)).fetchone():
        raise ValueError('该项目存在有效发票，不能直接按零星抹零结清')
    income = _income(conn, project_id)
    active = [r for r in income if r['status'] == 'active']
    if any(r['contract_id'] is not None for r in active):
        raise ValueError('收入仍关联合同，请先核对合同归属')
    received = conn.execute('''SELECT COALESCE(SUM(a.allocated_amount_minor),0),
        COALESCE(SUM(CASE WHEN a.settlement_id IS NULL THEN a.allocated_amount_minor ELSE 0 END),0)
        FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
        WHERE a.project_id=? AND r.status='active' ''', (project_id,)).fetchone()
    if received[1]:
        raise ValueError('项目还有预收款或历史待分配回款，请先核对收入和回款归属')
    total = sum(r['amount_minor'] for r in active)
    if received[0] <= 0 or total <= received[0]:
        raise ValueError('没有需要抹零的正数应收差额，或尚未实际收款')
    return {'project_id': project_id, 'project_name': project['name'],
            'original_income_minor': total, 'received_minor': received[0],
            'reduction_minor': total - received[0], 'income': income}


def preview(project_id):
    with db_read() as conn:
        return _preview(conn, int(project_id))


def close_collection(project_id, reason, expected, notes=''):
    if reason not in REASONS:
        raise ValueError('请选择有效的结清原因')
    with db_transaction(immediate=True) as conn:
        data = _preview(conn, int(project_id))
        if data != expected:
            raise ValueError('金额或收入记录已变化，请重新打开结清窗口核对')
        remaining = data['reduction_minor']
        for row in data['income']:
            if row['status'] != 'active':
                continue
            paid = conn.execute('''SELECT COALESCE(SUM(a.allocated_amount_minor),0)
                FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
                WHERE a.settlement_id=? AND r.status='active' ''', (row['id'],)).fetchone()[0]
            reduction = min(remaining, max(row['amount_minor'] - paid, 0))
            if not reduction:
                continue
            amount = row['amount_minor'] - reduction
            # A wholly waived unpaid confirmation is voided, not deleted.
            conn.execute('UPDATE settlements SET amount_minor=?,status=?,updated_at=? WHERE id=?',
                         (amount or row['amount_minor'], 'active' if amount else 'void', now(), row['id']))
            remaining -= reduction
        if remaining:
            raise ValueError('回款分配与收入不一致，无法安全结清')
        result = conn.execute('''INSERT INTO cash_collection_closures
            (project_id,original_income_minor,received_minor,reduction_minor,reason,notes,
             before_json,after_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)''',
            (project_id, data['original_income_minor'], data['received_minor'], data['reduction_minor'],
             reason, notes.strip(), json.dumps(data['income'], ensure_ascii=False),
             json.dumps(_income(conn, project_id), ensure_ascii=False), now()))
        return result.lastrowid


def history(project_id):
    with db_read() as conn:
        return [dict(r) for r in conn.execute('''SELECT * FROM cash_collection_closures
            WHERE project_id=? ORDER BY id DESC''', (project_id,))]


def revoke(closure_id):
    with db_transaction(immediate=True) as conn:
        row = conn.execute("SELECT * FROM cash_collection_closures WHERE id=? AND status='active'",
                           (closure_id,)).fetchone()
        if not row:
            raise ValueError('有效结清调整不存在或已撤销')
        if _income(conn, row['project_id']) != json.loads(row['after_json']):
            raise ValueError('结清后的收入记录已变化，请先核对，不能直接覆盖')
        conn.execute("UPDATE cash_collection_closures SET status='revoked',revoked_at=? WHERE id=?",
                     (now(), closure_id))
        for original in json.loads(row['before_json']):
            conn.execute('UPDATE settlements SET amount_minor=?,status=?,updated_at=? WHERE id=?',
                         (original['amount_minor'], original['status'], now(), original['id']))
