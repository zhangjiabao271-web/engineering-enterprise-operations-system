"""Allocate explicitly automatic receipts within their own project/contract."""


def reconcile(conn, project_id, contract_id):
    from services import finance_service as finance
    rows = conn.execute('''SELECT DISTINCT r.* FROM receipts r
        JOIN receipt_allocations a ON a.receipt_id=r.id
        WHERE r.status='active' AND r.automatic_income_allocation=1
          AND a.project_id=? AND a.contract_id IS ?
        ORDER BY r.receipt_date, r.id''', (project_id, contract_id)).fetchall()
    if not rows:
        return
    settlements = conn.execute('''SELECT s.id, s.amount_minor - COALESCE((
        SELECT SUM(a.allocated_amount_minor) FROM receipt_allocations a
        JOIN receipts r ON r.id=a.receipt_id
        WHERE a.settlement_id=s.id AND r.status='active'
          AND r.automatic_income_allocation=0),0) AS available
        FROM settlements s WHERE s.project_id=? AND s.contract_id IS ?
          AND s.status='active' ORDER BY s.settlement_date,s.id''',
        (project_id, contract_id)).fetchall()
    capacity = {s['id']: max(s['available'], 0) for s in settlements}
    for receipt in rows:
        remaining = receipt['amount_minor']
        plan = []
        for sid, available in capacity.items():
            amount = min(remaining, available)
            if amount:
                plan.append({'settlement_id': sid, 'amount_minor': amount})
                capacity[sid] -= amount
                remaining -= amount
        if remaining:
            plan.append({'settlement_id': None, 'amount_minor': remaining})
        old = finance._receipt_allocations(conn, receipt['id'])
        previous = {r['settlement_id']: r['allocated_amount_minor'] for r in old}
        if previous == {r['settlement_id']: r['amount_minor'] for r in plan}:
            continue
        changed_at = finance._now()
        finance._insert_receipt_revision(conn, receipt, 'update', changed_at)
        conn.execute('DELETE FROM receipt_allocations WHERE receipt_id=?', (receipt['id'],))
        finance._insert_receipt_allocations(conn, receipt['id'], project_id, contract_id,
                                            None, plan, receipt['notes'], changed_at)
