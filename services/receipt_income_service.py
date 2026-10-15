"""Explicit correction of receipt-owned income; never infer a reversal."""
from services._common import now
from services import invoice_income_service

ACTIONS = {'retain': '仅更正回款，保留真实结算', 'reverse': '同步撤销录错的自动补足收入'}


def preview(conn, receipt_ids, *, amount_minor=None):
    ids = sorted({int(value) for value in receipt_ids})
    if not ids:
        raise ValueError('请选择回款记录')
    if amount_minor is not None and (len(ids) != 1 or amount_minor < 0):
        raise ValueError('修改预览必须选择一笔回款并提供非负金额')
    slots = ','.join('?' for _ in ids)
    receipts = [dict(row) for row in conn.execute(
        f"SELECT * FROM receipts WHERE id IN ({slots}) AND status='active' ORDER BY id", ids)]
    if len(receipts) != len(ids):
        raise ValueError('部分回款已作废或不存在，请重新核对')
    rows = []
    for receipt in receipts:
        reduction = receipt['amount_minor'] - (amount_minor if amount_minor is not None else 0)
        if reduction <= 0:
            continue
        owned = conn.execute('''SELECT s.* FROM receipt_confirmed_settlements l
            JOIN settlements s ON s.id=l.settlement_id
            WHERE l.receipt_id=? AND s.status='active' ORDER BY s.settlement_date DESC,s.id DESC''',
            (receipt['id'],)).fetchall()
        remaining = reduction
        for settlement in owned:
            adjustment = min(remaining, settlement['amount_minor'])
            remaining -= adjustment
            if not adjustment:
                continue
            invoiced = conn.execute('''SELECT COALESCE(SUM(a.allocated_amount_minor),0)
                FROM invoice_settlement_allocations a JOIN sales_invoices i ON i.id=a.invoice_id
                WHERE a.settlement_id=? AND i.status='active' ''', (settlement['id'],)).fetchone()[0]
            received = conn.execute(f'''SELECT COALESCE(SUM(a.allocated_amount_minor),0)
                FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
                WHERE a.settlement_id=? AND a.status='active' AND r.status='active'
                  AND r.id NOT IN ({slots})''', (settlement['id'], *ids)).fetchone()[0]
            proposed = settlement['amount_minor'] - adjustment
            blocked = proposed < max(invoiced, received)
            rows.append({'receipt_id': receipt['id'], 'receipt_no': receipt['receipt_no'],
                'settlement_id': settlement['id'], 'settlement_no': settlement['settlement_no'],
                'contract_id': settlement['contract_id'], 'project_id': settlement['project_id'],
                'before_minor': settlement['amount_minor'], 'after_minor': proposed,
                'reduction_minor': adjustment, 'invoiced_minor': invoiced,
                'other_received_minor': received, 'blocked': blocked,
                'restriction': '已关联发票或其他有效回款，不能降到关联金额以下' if blocked else ''})
    return {'requires_decision': bool(rows), 'rows': rows,
            'reduction_minor': sum(row['reduction_minor'] for row in rows),
            'can_reverse': not any(row['blocked'] for row in rows),
            'notice': '回款更正不等于真实收入变化；请确认保留结算，或撤销确实录错的自动补足部分。'}


def validate_choice(impact, action, reason):
    if not impact['requires_decision']:
        return
    if not isinstance(action,str) or action not in ACTIONS or not isinstance(reason,str) or not reason.strip():
        raise ValueError('本笔回款关联自动补足收入，请先预览影响，明确选择保留真实结算或撤销误补收入，并填写更正说明')
    if action == 'reverse' and not impact['can_reverse']:
        raise ValueError('自动补足收入已有发票或其他回款关联，不能同步撤销；请先核对关联业务，或明确保留真实结算')


def apply(conn, impact, action, reason):
    validate_choice(impact, action, reason)
    if not impact['requires_decision']:
        return
    changed_at = now()
    for row in impact['rows']:
        before = dict(conn.execute('SELECT * FROM settlements WHERE id=?', (row['settlement_id'],)).fetchone())
        if before['status'] != 'active' or before['amount_minor'] != row['before_minor']:
            raise ValueError('关联收入在核对后已变化，本次更正未提交')
        if action == 'reverse':
            floor = conn.execute('''SELECT COALESCE(SUM(a.allocated_amount_minor),0)
                FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
                WHERE a.settlement_id=? AND a.status='active' AND r.status='active' ''',
                (row['settlement_id'],)).fetchone()[0]
            if row['after_minor'] < max(floor, row['invoiced_minor']):
                raise ValueError('更正后的回款仍占用这笔收入，请先核对分配；本次更正未提交')
            status = 'active' if row['after_minor'] else 'void'
            # A full reversal keeps the original amount, just like other voided facts.
            amount = row['after_minor'] if status == 'active' else row['before_minor']
            conn.execute('UPDATE settlements SET amount_minor=?,status=?,updated_at=? WHERE id=?',
                         (amount, status, changed_at, row['settlement_id']))
        after = dict(conn.execute('SELECT * FROM settlements WHERE id=?', (row['settlement_id'],)).fetchone())
        invoice_income_service.audit(conn, row['contract_id'], None, 'receipt_income_' + action,
            {**before, 'receipt_id': row['receipt_id']},
            {**after, 'receipt_id': row['receipt_id'], 'reason': str(reason).strip()})


def public_preview(receipt_id, amount=None):
    from db.connection import db_read
    from services._common import minor
    amount_minor = minor(amount) if amount is not None else None
    with db_read() as conn:
        return preview(conn, [receipt_id], amount_minor=amount_minor)


def history(conn, receipt_id):
    import json
    rows = conn.execute('''SELECT a.* FROM invoice_income_audit a
        WHERE a.contract_id=(SELECT contract_id FROM receipts WHERE id=?)
          AND a.action IN ('receipt_income_retain','receipt_income_reverse') ORDER BY a.id DESC''', (receipt_id,))
    return [dict(row) for row in rows if json.loads(row['after_json']).get('receipt_id') == receipt_id]
