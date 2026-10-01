"""Explicit legal-entity ownership, independent of project accounting facts."""
from db.connection import db_read, db_transaction
from services._common import now

RECORDS = {
    'projects': ('项目归属建议', 'name', 'cash_agreed_amount_minor', None),
    'contracts': ('合同', 'name', 'tax_inclusive_amount_minor', 'sign_date'),
    'settlements': ('确认收入', 'settlement_no', 'amount_minor', 'settlement_date'),
    'sales_invoices': ('销项发票', 'invoice_no', 'amount_minor', 'invoice_date'),
    'receipts': ('回款', 'receipt_no', 'amount_minor', 'receipt_date'),
    'purchase_orders': ('采购', 'order_no', 'total_amount_cents', 'purchase_date'),
    'cost_entries': ('费用', 'cost_no', 'amount_minor', 'cost_date'),
    'fund_accounts': ('资金账户', 'name', 'opening_minor', 'opening_date'),
    'fund_transactions': ('资金流水', 'id', 'amount_minor', 'transaction_date'),
}


def list_entities():
    with db_read() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM operating_entities ORDER BY id')]


def entity_for(conn, kind, record_id):
    row = conn.execute('SELECT entity_id FROM record_entities WHERE record_type=? AND record_id=?',
                       (kind, record_id)).fetchone()
    return row[0] if row else None


def project_ids(conn, kind, row):
    if kind == 'projects':
        return [row['id']]
    if 'project_id' in row.keys() and row['project_id']:
        return [row['project_id']]
    links = {
        'contracts': ('contract_project_allocations', 'contract_id'),
        'receipts': ('receipt_allocations', 'receipt_id'),
        'purchase_orders': ('purchase_cost_allocation_lines', 'purchase_order_id'),
        'cost_entries': ('cost_allocation_lines', 'cost_entry_id'),
    }
    if kind not in links:
        return []
    table, column = links[kind]
    # Cost allocation layout is handled only if this application's table exists.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return []
    columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
    status = " AND status='active'" if 'status' in columns else ''
    return [r[0] for r in conn.execute(
        f'SELECT DISTINCT project_id FROM {table} WHERE {column}=?{status}', (row['id'],))]


def suggested_entity(conn, kind, row):
    ids = project_ids(conn, kind, row)
    entities = {entity_for(conn, 'projects', pid) for pid in ids}
    return next(iter(entities)) if len(entities) == 1 and None not in entities else None


def _assign(conn, kind, record_id, entity_id, reason):
    if kind not in RECORDS:
        raise ValueError('不支持的记录类型')
    record = conn.execute(f'SELECT * FROM {kind} WHERE id=?', (record_id,)).fetchone()
    if not record:
        raise ValueError('业务记录不存在，请刷新')
    if not conn.execute('SELECT 1 FROM operating_entities WHERE id=? AND active=1', (entity_id,)).fetchone():
        raise ValueError('经营主体不存在或已停用')
    old = entity_for(conn, kind, record_id)
    if old == entity_id:
        return
    if kind in ('purchase_orders', 'cost_entries'):
        column = 'purchase_order_id' if kind == 'purchase_orders' else 'cost_entry_id'
        if conn.execute(f'''SELECT 1 FROM input_invoice_links l JOIN input_invoices i ON i.id=l.invoice_id
            WHERE l.{column}=? AND i.status='active' AND i.entity_id<>?''', (record_id, entity_id)).fetchone():
            raise ValueError('已有其他主体的进项票关联，请先核对并调整关联')
    changed_at = now()
    conn.execute('''INSERT INTO record_entities(record_type,record_id,entity_id,source,updated_at)
        VALUES (?,?,?,?,?) ON CONFLICT(record_type,record_id) DO UPDATE SET
        entity_id=excluded.entity_id,source=excluded.source,updated_at=excluded.updated_at''',
        (kind, record_id, entity_id, reason, changed_at))
    conn.execute('''INSERT INTO operating_entity_audit
        (record_type,record_id,old_entity_id,new_entity_id,reason,created_at) VALUES (?,?,?,?,?,?)''',
        (kind, record_id, old, entity_id, reason, changed_at))


def assign_records(kind, record_ids, entity_id, reason='人工确认历史归属'):
    ids = list(dict.fromkeys(int(v) for v in record_ids))
    if not ids or not reason.strip():
        raise ValueError('请选择记录并说明归属依据')
    with db_transaction(immediate=True) as conn:
        for record_id in ids:
            _assign(conn, kind, record_id, int(entity_id), reason.strip())
        if kind in ('sales_invoices', 'receipts'):
            from services.finance_service import (
                _invoice_customer_id, _receipt_customer_id, _reconcile_customer_ids,
            )
            customer_for = _invoice_customer_id if kind == 'sales_invoices' else _receipt_customer_id
            _reconcile_customer_ids(conn, {customer_for(conn, record_id) for record_id in ids}, now())


def list_records(kind, *, pending_only=False, keyword=''):
    if kind not in RECORDS:
        raise ValueError('不支持的记录类型')
    _, label, amount, day = RECORDS[kind]
    with db_read() as conn:
        result = []
        for record in conn.execute(f'SELECT * FROM {kind} ORDER BY id DESC'):
            if record['status'] == 'void':
                continue
            entity = entity_for(conn, kind, record['id'])
            if pending_only and entity:
                continue
            projects = project_ids(conn, kind, record)
            names = [conn.execute('SELECT name FROM projects WHERE id=?', (pid,)).fetchone()[0] for pid in projects]
            text = ' '.join(str(v or '') for v in record)
            if keyword and keyword.casefold() not in (text+' '.join(names)).casefold():
                continue
            result.append({'id':record['id'], 'label':str(record[label]), 'date':record[day] if day else '',
                           'amount_minor':record[amount], 'entity_id':entity,
                           'suggested_entity_id':suggested_entity(conn,kind,record) if kind!='projects' else None,
                           'projects':'、'.join(names)})
        return result


def update_tax_number(entity_id, tax_number):
    import re
    tax_number = tax_number.strip().upper()
    if tax_number and not re.fullmatch(r'[A-Z0-9]{15,20}', tax_number):
        raise ValueError('税号须为15至20位字母或数字')
    with db_transaction(immediate=True) as conn:
        old = conn.execute('SELECT tax_number FROM operating_entities WHERE id=?', (entity_id,)).fetchone()
        if not old:
            raise ValueError('经营主体不存在')
        conn.execute('UPDATE operating_entities SET tax_number=? WHERE id=?', (tax_number,entity_id))
        conn.execute('''INSERT INTO operating_entity_audit
            (record_type,record_id,old_entity_id,new_entity_id,reason,created_at) VALUES ('entity',?,?,?,?,?)''',
            (entity_id,entity_id,entity_id,f'税号由{old[0] or "未填"}更新为{tax_number or "未填"}',now()))
