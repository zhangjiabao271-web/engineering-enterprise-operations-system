"""Project FIFO, preserving explicit instructions and company boundaries."""

from services.entity_ownership import OwnershipResolver


def receipt_income_contract(conn, project_id):
    """Keep the existing annual policy only where one contract is unambiguous."""
    rows = conn.execute('''SELECT DISTINCT a.contract_id FROM contract_project_allocations a
        JOIN contracts c ON c.id=a.contract_id WHERE a.project_id=? AND a.status='active'
        AND c.status<>'void' ''', (project_id,)).fetchall()
    if len(rows) != 1:
        return None
    from services.invoice_income_service import is_receipt_driven
    identity = rows[0]['contract_id']
    if is_receipt_driven(conn, identity) and not conn.execute('''SELECT 1 FROM settlements
        WHERE project_id=? AND status='active' AND contract_id IS NOT ?''', (project_id, identity)).fetchone():
        return identity
    return None


def receipt_entity(conn, project_id, entity_id=None, receipt_id=None):
    resolver = OwnershipResolver(conn)
    if entity_id:
        entity_id = int(entity_id)
        if entity_id not in resolver.entities:
            raise ValueError('请选择有效的经营主体')
        return entity_id
    if receipt_id:
        row = conn.execute('SELECT * FROM receipts WHERE id=?', (receipt_id,)).fetchone()
        existing = resolver.record('receipts', row)
        if existing['entity_id']:
            return existing['entity_id']
    resolved = resolver.project(project_id)
    if not resolved['entity_id']:
        raise ValueError('跨合同顺序抵扣需要明确经营主体，请先选择本笔回款的经营主体')
    return resolved['entity_id']


def capacity_rows(conn, project_id, entity_id, exclude_receipt_id=None, *, automatic_pool=False):
    project = conn.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
    if not project:
        raise ValueError('请选择有效项目')
    resolver = OwnershipResolver(conn)
    rows = conn.execute('''SELECT s.*,COALESCE(c.name,'合同待补') AS contract_name,
        COALESCE(c.contract_no,'') AS contract_no,
        COALESCE(s.business_date,c.sign_date,s.settlement_date) AS receivable_date,
        COALESCE((SELECT SUM(a.allocated_amount_minor)
            FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
            WHERE a.settlement_id=s.id AND a.status='active' AND r.status='active'
              AND (? IS NULL OR r.id<>?)
              AND (?=0 OR r.allocation_scope<>'project' OR r.automatic_income_allocation=0)),0)
            AS received_minor
        FROM settlements s LEFT JOIN contracts c ON c.id=s.contract_id
        WHERE s.project_id=? AND s.status='active'
          AND (s.contract_id IS NULL OR c.status<>'void')
        ORDER BY receivable_date,s.settlement_date,s.id''',
        (exclude_receipt_id, exclude_receipt_id, int(automatic_pool), project_id)).fetchall()
    result = []
    for source in rows:
        row = dict(source)
        contract = resolver.contracts.get(row['contract_id'])
        if contract and contract['customer_partner_id'] != project['customer_partner_id']:
            continue
        owner = resolver.record('settlements', row)
        if owner['entity_id'] != entity_id:
            continue
        row['available_minor'] = max(row['amount_minor'] - row['received_minor'], 0)
        result.append(row)
    return result


def plan(amount_minor, capacities):
    remaining = amount_minor
    result = []
    for row in capacities:
        amount = min(remaining, row['available_minor'])
        if amount:
            result.append({'settlement_id': row['id'], 'contract_id': row['contract_id'],
                           'amount_minor': amount, 'settlement_no': row['settlement_no'],
                           'settlement_date': row['settlement_date'], 'contract_name': row['contract_name'],
                           'receivable_date': row['receivable_date'], 'available_minor': row['available_minor']})
            remaining -= amount
        if not remaining:
            break
    if remaining:
        result.append({'settlement_id': None, 'contract_id': None, 'amount_minor': remaining,
                       'settlement_no': '预收款，待后续确认业务', 'settlement_date': '',
                       'contract_name': '', 'receivable_date': '', 'available_minor': None})
    return result


def preview(conn, project_id, entity_id, amount_minor, receipt_date, exclude_receipt_id=None):
    capacities = capacity_rows(conn, project_id, entity_id, automatic_pool=True)
    rows = [dict(row) for row in conn.execute('''SELECT DISTINCT r.* FROM receipts r
        JOIN receipt_allocations a ON a.receipt_id=r.id
        WHERE a.project_id=? AND a.status='active' AND r.status='active'
          AND r.allocation_scope='project' AND r.automatic_income_allocation=1
          AND (? IS NULL OR r.id<>?)''', (project_id, exclude_receipt_id, exclude_receipt_id))]
    candidate = {'id': exclude_receipt_id or 9223372036854775807,
                 'receipt_date': receipt_date, 'amount_minor': amount_minor, '_candidate': True}
    rows.append(candidate)
    for row in sorted(rows, key=lambda item: (item['receipt_date'], item['id'])):
        if not row.get('_candidate') and receipt_entity(conn, project_id, receipt_id=row['id']) != entity_id:
            continue
        allocations = plan(row['amount_minor'], capacities)
        if row.get('_candidate'):
            return allocations
        used = {item['settlement_id']: item['amount_minor'] for item in allocations}
        for target in capacities:
            target['available_minor'] -= used.get(target['id'], 0)


def reconcile(conn, project_id):
    from services import finance_service as finance
    rows = conn.execute('''SELECT DISTINCT r.* FROM receipts r
        JOIN receipt_allocations a ON a.receipt_id=r.id
        WHERE a.project_id=? AND a.status='active' AND r.status='active'
          AND r.allocation_scope='project' AND r.automatic_income_allocation=1
        ORDER BY r.receipt_date,r.id''', (project_id,)).fetchall()
    pools = {}
    for receipt in rows:
        entity_id = receipt_entity(conn, project_id, receipt_id=receipt['id'])
        if entity_id not in pools:
            pools[entity_id] = capacity_rows(conn, project_id, entity_id, automatic_pool=True)
        capacities = pools[entity_id]
        allocations = plan(receipt['amount_minor'], capacities)
        old = finance._receipt_allocations(conn, receipt['id'])
        before = sorted((r['settlement_id'] or 0, r['contract_id'] or 0, r['allocated_amount_minor'])
                        for r in old if r['status'] == 'active')
        after = sorted((r['settlement_id'] or 0, r['contract_id'] or 0, r['amount_minor']) for r in allocations)
        if before != after:
            stamp = finance._now()
            finance._insert_receipt_revision(conn, receipt, 'update', stamp)
            conn.execute('DELETE FROM receipt_allocations WHERE receipt_id=?', (receipt['id'],))
            finance._insert_receipt_allocations(conn, receipt['id'], project_id, None, None,
                                                allocations, receipt['notes'], stamp)
        used = {row['settlement_id']: row['amount_minor'] for row in allocations}
        for row in capacities:
            row['available_minor'] -= used.get(row['id'], 0)
