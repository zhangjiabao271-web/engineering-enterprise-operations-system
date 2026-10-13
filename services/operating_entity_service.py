"""Explicit legal-entity ownership, independent of project accounting facts."""
from db.connection import db_read, db_transaction
from services._common import now

RECORDS = {
    'projects': ('无合同 / 零星项目', 'name', 'cash_agreed_amount_minor', None),
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


def effective_entity_for(conn, kind, record_id, resolver=None):
    """Resolve explicit or inherited ownership without manufacturing a confirmation row."""
    if kind not in RECORDS:
        raise ValueError('不支持的记录类型')
    if kind not in ('contracts', 'projects'):
        explicit = entity_for(conn, kind, record_id)
        if explicit is not None:
            return explicit
    from services.entity_ownership import OwnershipResolver
    resolver = resolver or OwnershipResolver(conn)
    row = conn.execute(f'SELECT * FROM {kind} WHERE id=?', (record_id,)).fetchone()
    return resolver.record(kind, row)['entity_id'] if row else None


def project_ids(conn, kind, row, resolver=None):
    if kind == 'projects':
        return [row['id']]
    if kind == 'contracts':
        from services.entity_ownership import OwnershipResolver
        resolver = resolver or OwnershipResolver(conn)
        return sorted(pid for pid, contracts in resolver.links.items() if row['id'] in contracts)
    links = {
        'receipts': ('receipt_allocations', 'receipt_id'),
        'purchase_orders': ('purchase_cost_allocation_lines', 'purchase_order_id'),
        'cost_entries': ('cost_allocation_lines', 'cost_entry_id'),
    }
    direct = [row['project_id']] if 'project_id' in row.keys() and row['project_id'] else []
    if kind not in links:
        return direct
    table, column = links[kind]
    # Cost allocation layout is handled only if this application's table exists.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return direct
    columns = {r[1] for r in conn.execute(f'PRAGMA table_info({table})')}
    status = " AND status='active'" if 'status' in columns else ''
    return sorted(set(direct + [r[0] for r in conn.execute(
        f'SELECT DISTINCT project_id FROM {table} WHERE {column}=?{status}', (row['id'],)) if r[0]]))


def suggested_entity(conn, kind, row):
    ids = project_ids(conn, kind, row)
    entities = {entity_for(conn, 'projects', pid) for pid in ids}
    return next(iter(entities)) if len(entities) == 1 and None not in entities else None


def validate_inherited_ownership_change(conn, before, after=None):
    """Reject newly introduced conflicts with immutable linked evidence."""
    from services.entity_ownership import OwnershipResolver
    sources = {'receipt': 'receipts', 'purchase': 'purchase_orders', 'cost': 'cost_entries'}
    references = []
    for row in conn.execute('''SELECT t.source_kind,t.source_id,e.entity_id
        FROM fund_transactions t JOIN record_entities e
          ON e.record_type='fund_transactions' AND e.record_id=t.id
        WHERE t.status='active' '''):
        kind = sources.get(row['source_kind'])
        if kind:
            references.append((kind, row['source_id'], row['entity_id'], '资金流水'))

    for row in conn.execute('''SELECT l.purchase_order_id,l.cost_entry_id,i.entity_id
        FROM input_invoice_links l JOIN input_invoices i ON i.id=l.invoice_id
        WHERE i.status='active' '''):
        for kind, identity in (('purchase_orders', row['purchase_order_id']),
                               ('cost_entries', row['cost_entry_id'])):
            if identity is not None:
                references.append((kind, identity, row['entity_id'], '进项票'))
    if not references:
        return
    after = after or OwnershipResolver(conn)
    resolved = {}
    for kind, identity, entity_id, label in references:
        key = (kind, identity)
        if key not in resolved:
            record = conn.execute(f"SELECT * FROM {kind} WHERE id=? AND status='active'", (identity,)).fetchone()
            resolved[key] = None if record is None else (
                before.record(kind, record)['entity_id'], after.record(kind, record)['entity_id'])
        if resolved[key] is None:
            continue
        previous, current = resolved[key]
        if previous != current and current != entity_id:
            raise ValueError(f'合同／项目主体变更会与已关联的{label}经营主体冲突；'
                             '请先核对关联业务与资料归属，原记录未改动')


def _assign(conn, kind, record_id, entity_id, reason):
    if kind not in RECORDS:
        raise ValueError('不支持的记录类型')
    record = conn.execute(f'SELECT * FROM {kind} WHERE id=?', (record_id,)).fetchone()
    if not record:
        raise ValueError('业务记录不存在，请刷新')
    if not conn.execute('SELECT 1 FROM operating_entities WHERE id=? AND active=1', (entity_id,)).fetchone():
        raise ValueError('经营主体不存在或已停用')
    if kind == 'projects':
        from services.entity_ownership import OwnershipResolver
        if not OwnershipResolver(conn).project(record_id)['assignable']:
            raise ValueError('项目已关联合同，请确认合同的经营主体；项目会自动沿用，不需重复归集')
    old = entity_for(conn, kind, record_id)
    if old == entity_id:
        return False
    sources = {'receipts': 'receipt', 'purchase_orders': 'purchase', 'cost_entries': 'cost'}
    if kind in sources:
        conflict = conn.execute('''SELECT 1 FROM fund_transactions t
            JOIN record_entities e ON e.record_type='fund_transactions' AND e.record_id=t.id
            WHERE t.source_kind=? AND t.source_id=? AND t.status='active' AND e.entity_id<>?''',
            (sources[kind], record_id, entity_id)).fetchone()
        if conflict:
            raise ValueError('关联的有效资金流水已确认其他经营主体，请先核对两边归属；原记录未改动')
    elif kind == 'fund_transactions' and record['status'] == 'active':
        source_table = next((table for table, source in sources.items() if source == record['source_kind']), None)
        source_entity = effective_entity_for(conn, source_table, record['source_id']) if source_table else None
        if source_entity is not None and source_entity != entity_id:
            raise ValueError('关联的业务单据已确认其他经营主体，请先核对两边归属；原记录未改动')
    if kind in ('purchase_orders', 'cost_entries'):
        column = 'purchase_order_id' if kind == 'purchase_orders' else 'cost_entry_id'
        if conn.execute(f'''SELECT 1 FROM input_invoice_links l JOIN input_invoices i ON i.id=l.invoice_id
            WHERE l.{column}=? AND i.status='active' AND i.entity_id<>?''', (record_id, entity_id)).fetchone():
            raise ValueError('已有其他主体的进项票关联，请先核对并调整关联')
    before = None
    if kind in ('contracts', 'projects'):
        from services.entity_ownership import OwnershipResolver
        before = OwnershipResolver(conn)
    changed_at = now()
    conn.execute('''INSERT INTO record_entities(record_type,record_id,entity_id,source,updated_at)
        VALUES (?,?,?,?,?) ON CONFLICT(record_type,record_id) DO UPDATE SET
        entity_id=excluded.entity_id,source=excluded.source,updated_at=excluded.updated_at''',
        (kind, record_id, entity_id, reason, changed_at))
    conn.execute('''INSERT INTO operating_entity_audit
        (record_type,record_id,old_entity_id,new_entity_id,reason,created_at) VALUES (?,?,?,?,?,?)''',
        (kind, record_id, old, entity_id, reason, changed_at))
    if kind in ('contracts', 'projects'):
        validate_inherited_ownership_change(conn, before)
        _reconcile_inherited_invoices(conn, kind, record_id, changed_at)
    return True


def _reconcile_inherited_invoices(conn, kind, record_id, changed_at):
    """Refresh derived invoice allocations when a user confirms a parent owner."""
    from services.entity_ownership import OwnershipResolver
    from services.finance_service import _reconcile_customer_ids
    resolver = OwnershipResolver(conn)
    project_ids = {record_id} if kind == 'projects' else set()
    contracts = set()
    if kind == 'contracts':
        contracts.add(record_id)
        while True:
            descendants = {identity for identity, row in resolver.contracts.items()
                           if row.get('parent_contract_id') in contracts}
            expanded = contracts | descendants
            if expanded == contracts:
                break
            contracts = expanded
        project_ids.update(pid for pid, linked in resolver.links.items() if linked & contracts)
    customers = {resolver.projects[pid].get('customer_partner_id') for pid in project_ids
                 if pid in resolver.projects}
    customers.update(resolver.contracts[identity].get('customer_partner_id') for identity in contracts
                     if identity in resolver.contracts)
    _reconcile_customer_ids(conn, customers, changed_at)


def assign_records(kind, record_ids, entity_id, reason='人工确认历史归属'):
    ids = list(dict.fromkeys(int(v) for v in record_ids))
    if not ids or not reason.strip():
        raise ValueError('请选择记录并说明归属依据')
    with db_transaction(immediate=True) as conn:
        changed = 0
        for record_id in ids:
            changed += int(_assign(conn, kind, record_id, int(entity_id), reason.strip()))
        if kind in ('sales_invoices', 'receipts'):
            from services.finance_service import (
                _invoice_customer_id, _receipt_customer_id, _reconcile_customer_ids,
            )
            customer_for = _invoice_customer_id if kind == 'sales_invoices' else _receipt_customer_id
            _reconcile_customer_ids(conn, {customer_for(conn, record_id) for record_id in ids}, now())
        return {'updated_count': changed, 'unchanged_count': len(ids) - changed}


def list_records(kind, *, pending_only=False, keyword='', assignable_only=False, project_id=None):
    if kind not in RECORDS:
        raise ValueError('不支持的记录类型')
    _, label, amount, day = RECORDS[kind]
    with db_read() as conn:
        from services.entity_ownership import OwnershipResolver
        resolver = OwnershipResolver(conn)
        result = []
        for record in conn.execute(f'SELECT * FROM {kind} ORDER BY id DESC'):
            if 'status' in record.keys() and record['status'] == 'void':
                continue
            ownership = resolver.record(kind, record)
            if pending_only and ownership['ownership_status'] not in ('pending', 'conflict'):
                continue
            if assignable_only and not ownership['assignable']:
                continue
            projects = project_ids(conn, kind, record, resolver)
            if project_id is not None and int(project_id) not in projects:
                continue
            names = [resolver.projects[pid]['name'] for pid in projects if pid in resolver.projects]
            text = ' '.join(str(v or '') for v in record)
            if keyword and keyword.casefold() not in (text+' '.join(names)).casefold():
                continue
            result.append({'id':record['id'], 'label':str(record[label]), 'date':record[day] if day else '',
                           'kind': kind, 'kind_label': RECORDS[kind][0],
                           'amount_minor':record[amount], **ownership,
                           'suggested_entity_id':suggested_entity(conn,kind,record) if kind!='projects' else None,
                           'project_ids': projects, 'projects':'、'.join(names)})
        return result


def preview_assignment(kind, record_ids, entity_id):
    """Describe direct confirmations and their derived project impact without writes."""
    if kind not in RECORDS:
        raise ValueError('不支持的记录类型')
    ids = list(dict.fromkeys(int(value) for value in record_ids))
    with db_read() as conn:
        from services.entity_ownership import OwnershipResolver
        before = OwnershipResolver(conn)
        after = OwnershipResolver(conn)
        if entity_id not in before.entities or not before.entities[entity_id]['active']:
            raise ValueError('经营主体不存在或已停用')
        records = []
        for identity in ids:
            row = conn.execute(f'SELECT * FROM {kind} WHERE id=?', (identity,)).fetchone()
            if row is None or ('status' in row.keys() and row['status'] == 'void'):
                raise ValueError('部分业务记录已不存在或已作废')
            if kind == 'projects' and not before.project(identity)['assignable']:
                raise ValueError('项目已关联合同，请确认合同的经营主体；项目会自动沿用，不需重复归集')
            records.append(row)
            after.set_assignment(kind, identity, entity_id)
        if kind in ('contracts', 'projects'):
            validate_inherited_ownership_change(conn, before, after)
        rows = []
        for row in records:
            old = before.record(kind, row)
            rows.append({'label': str(row[RECORDS[kind][1]]), 'id': row['id'],
                         'before_entity': old['entity_name'], 'after_entity': after.entities[entity_id]['name'],
                         'change': '已确认相同主体，无需重复写入' if entity_for(conn, kind, row['id']) == entity_id else '确认单据主体'})
        projects = []
        if kind == 'contracts':
            for pid in before.projects:
                old, new = before.project(pid), after.project(pid)
                if old != new:
                    projects.append({'project_id': pid, 'project_name': before.projects[pid]['name'],
                                     'before_entity': old['entity_name'], 'after_entity': new['entity_name'],
                                     'ownership_label': new['ownership_label'], 'ownership_reason': new['ownership_reason']})
        return {'rows': rows, 'projects': projects,
                'notice': '只确认所选单据的经营主体；关联项目动态沿用合同。收入、回款、成本金额和票面主体不会被改写。'}


def ownership_history(kind, record_id):
    if kind not in RECORDS:
        raise ValueError('不支持的记录类型')
    with db_read() as conn:
        return [dict(row) for row in conn.execute('''SELECT a.created_at,a.reason,
            COALESCE(old.name,'未确认') AS before_entity, current.name AS after_entity
            FROM operating_entity_audit a LEFT JOIN operating_entities old ON old.id=a.old_entity_id
            JOIN operating_entities current ON current.id=a.new_entity_id
            WHERE a.record_type=? AND a.record_id=? ORDER BY a.id DESC''', (kind, int(record_id)))]


def save_record_entity(conn, kind, record_id, data):
    """Optional ownership in the original contract form, within the same transaction."""
    if 'entity_id' not in data:
        return
    import re
    value = data['entity_id']
    if value in ('', None):
        if entity_for(conn, kind, record_id) is not None:
            raise ValueError('已确认的经营主体不能直接清空，请更正为实际主体')
        return
    if not re.fullmatch(r'[1-9][0-9]*', str(value).strip()):
        raise ValueError('请选择有效的经营主体')
    _assign(conn, kind, record_id, int(value), '在合同表单确认经营主体，关联项目自动沿用')


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
