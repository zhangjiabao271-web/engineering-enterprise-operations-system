"""Read-only contract and uncontracted business navigation, in integer cents."""
from collections import defaultdict

from db.connection import db_read, use_read_snapshot
from services import contract_service
from services.attachment_service import attachment_path
from services.entity_ownership import OwnershipResolver

MONEY_KEYS = ('settlement_minor', 'receipt_minor', 'invoice_minor', 'receivable_minor')


def _facts(conn, project_id=None, resolver=None):
    resolver = resolver or OwnershipResolver(conn)
    projects = resolver.projects
    contracts = resolver.contracts
    income = []
    settlements = {}
    for source in conn.execute("SELECT * FROM settlements WHERE status='active' ORDER BY settlement_date DESC,id DESC"):
        row = dict(source)
        if project_id is not None and row['project_id'] != project_id:
            continue
        row['original_contract_id'] = row['contract_id']
        if row['contract_id'] not in contracts:
            row['contract_id'] = None
        row.update(resolver.record('settlements', row))
        row.update(project_name=projects[row['project_id']]['name'], received_minor=0)
        settlements[row['id']] = row
        income.append(row)

    receipts = {}
    # Group actual contract shares once per receipt/contract. Only an unowned
    # share is split by project; the receipt header is never counted as money.
    for source in conn.execute('''
        SELECT r.*,a.project_id AS allocation_project_id,a.contract_id AS allocation_contract_id,
               a.settlement_id AS allocation_settlement_id,a.allocated_amount_minor,
               s.status AS settlement_status,s.project_id AS settlement_project_id,
               s.contract_id AS settlement_contract_id
        FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
        LEFT JOIN settlements s ON s.id=a.settlement_id
        WHERE a.status='active' AND r.status='active'
        ORDER BY r.receipt_date DESC,r.id DESC,a.id
    '''):
        part = dict(source)
        pid = part['allocation_project_id']
        if project_id is not None and pid != project_id:
            continue
        sid = part['allocation_settlement_id']
        explicit = part['allocation_contract_id']
        valid_income = sid is not None and part['settlement_status'] == 'active' and part['settlement_project_id'] == pid
        conflict = sid is not None and not valid_income
        if valid_income and explicit is not None and explicit != part['settlement_contract_id']:
            conflict = True
        cid = explicit
        if cid is None and valid_income:
            cid = part['settlement_contract_id']
        cid = cid if cid in contracts else None
        if ('receipts', part['id']) in resolver.assignments:
            owner = resolver.record('receipts', part)
        elif cid is not None:
            owner = resolver.contract(cid)
        else:
            owner = resolver.project(pid)
        target = settlements.get(sid)
        if target and owner['entity_id'] is not None and target['entity_id'] is not None and owner['entity_id'] != target['entity_id']:
            conflict = True
        key = (part['id'], cid, pid if cid is None else None)
        if key not in receipts:
            receipts[key] = {**part, **owner, 'contract_id': cid, 'project_id': pid,
                             'project_ids': [], 'project_name': '', 'contract_receipt_minor': 0,
                             'pending_receipt_minor': 0, 'review_receipt_minor': 0,
                             'needs_review': False}
        row = receipts[key]
        amount = part['allocated_amount_minor']
        row['contract_receipt_minor'] += amount
        if sid is None or conflict:
            row['pending_receipt_minor'] += amount
        elif target:
            target['received_minor'] += amount
        if conflict:
            row['needs_review'] = True
            row['review_receipt_minor'] += amount
            row['review_reason'] = '回款关联的收入已作废或项目／合同／主体归属冲突，请核对；不抵减收入未收。'
        if pid not in row['project_ids']:
            row['project_ids'].append(pid)
            row['project_name'] = '、'.join(projects[item]['name'] for item in row['project_ids'])
    for row in income:
        row['unreceived_minor'] = max(row['amount_minor'] - row['received_minor'], 0)

    links = defaultdict(list)
    for source in conn.execute('''SELECT a.*,s.status AS settlement_status,s.project_id,
            s.contract_id FROM invoice_settlement_allocations a
            JOIN settlements s ON s.id=a.settlement_id ORDER BY a.id'''):
        links[source['invoice_id']].append(dict(source))
    invoices = []
    for source in conn.execute("SELECT * FROM sales_invoices WHERE status='active' ORDER BY invoice_date DESC,id DESC"):
        invoice = dict(source)
        pid = invoice['project_id']
        if project_id is not None and pid != project_id:
            continue
        cid = invoice['contract_id']
        portions = []
        if cid in contracts:
            # Direct ownership already covers the whole invoice; its links are
            # supporting evidence, not additional invoice amounts.
            portions.append((cid, invoice['amount_minor'], resolver.record('sales_invoices', invoice)))
        else:
            usable = [link for link in links[invoice['id']]
                      if link['settlement_status'] == 'active' and link['project_id'] == pid]
            allocated = sum(link['allocated_amount_minor'] for link in usable)
            if allocated > invoice['amount_minor']:
                invoice.update(needs_review=True, review_reason='发票收入分配超过票面金额，归属待核对。')
                usable, allocated = [], 0
            for link in usable:
                target = settlements[link['settlement_id']]
                owner = (resolver.record('sales_invoices', {**invoice, 'contract_id': None})
                         if ('sales_invoices', invoice['id']) in resolver.assignments else target)
                portions.append((target['contract_id'], link['allocated_amount_minor'], owner))
            remainder = invoice['amount_minor'] - allocated
            if remainder:
                portions.append((None, remainder, resolver.record('sales_invoices', {**invoice, 'contract_id': None})))
        grouped = {}
        for cid, amount, owner in portions:
            # Keep different fact owners distinct for exact entity filtering.
            key = (cid, owner['entity_id'], owner['ownership_status'])
            if key not in grouped:
                ownership = {key: owner[key] for key in ('entity_id', 'entity_name', 'ownership_status',
                                                        'ownership_source', 'ownership_reason', 'ownership_label')}
                grouped[key] = {**invoice, **ownership, 'contract_id': cid,
                                'contract_invoice_minor': 0, 'project_name': projects[pid]['name']}
            grouped[key]['contract_invoice_minor'] += amount
        invoices.extend(grouped.values())
    return {'settlements': income, 'receipts': list(receipts.values()), 'invoices': invoices}


def _group_facts(facts):
    grouped = {kind: defaultdict(list) for kind in facts}
    for kind, rows in facts.items():
        for row in rows:
            grouped[kind][row['contract_id']].append(row)
    return grouped


def _contract_rows(conn, project_id, grouped=None, resolver=None, *, candidate_contract_ids=()):
    if grouped is None:
        grouped = _group_facts(_facts(conn, project_id))
    files = defaultdict(list)
    for row in conn.execute("SELECT * FROM business_attachments WHERE contract_id IS NOT NULL AND status='active'"):
        files[row['contract_id']].append(row)
    resolver = resolver or OwnershipResolver(conn)
    result = contract_service.list_contracts(project_id=project_id)
    if project_id is not None:
        fact_ids = {identity for groups in grouped.values() for identity, items in groups.items()
                    if identity is not None and items}
        missing = (fact_ids | set(candidate_contract_ids)) - {row['id'] for row in result}
        if missing:
            result.extend(row for row in contract_service.list_contracts() if row['id'] in missing)
    linked_projects = defaultdict(set)
    for pid, identities in resolver.links.items():
        if project_id is None or pid == project_id:
            for identity in identities:
                linked_projects[identity].add(pid)
    for row in result:
        identity = row['id']
        row.update(resolver.record('contracts', row))
        row['customer_key'] = f"customer:{row['customer_partner_id']}" if row['customer_partner_id'] else f'unlinked:{identity}'
        row['customer_name'] = row['customer_name'] or '客户待补充'
        row['sign_year'] = (row['sign_date'] or '')[:4] or 'unknown'
        income = grouped['settlements'][identity]
        receipts = grouped['receipts'][identity]
        invoices = grouped['invoices'][identity]
        project_ids = set(linked_projects[identity])
        for fact in income + receipts + invoices:
            project_ids.update(fact.get('project_ids', [fact['project_id']]))
        row['project_ids'] = sorted(project_ids)
        row['project_names'] = [resolver.projects[pid]['name'] for pid in row['project_ids']]
        row['project_codes'] = [resolver.projects[pid]['project_code'] for pid in row['project_ids']]
        row['project_name'] = '、'.join(row['project_names'])
        row['project_code'] = '、'.join(row['project_codes'])
        row['settlement_minor'] = sum(item['amount_minor'] for item in income)
        row['receipt_minor'] = sum(item['contract_receipt_minor'] for item in receipts)
        row['invoice_minor'] = sum(item['contract_invoice_minor'] for item in invoices)
        row['receivable_minor'] = sum(item['unreceived_minor'] for item in income)
        row['pending_receipt_minor'] = sum(item['pending_receipt_minor'] for item in receipts)
        row['attachment_count'] = len(files[identity])
        row['missing_file_count'] = sum(not attachment_path(item['file_path']).is_file() for item in files[identity])
        if row['missing_file_count']:
            row['attachment_label'] = f"文件缺失 {row['missing_file_count']} 个"
        elif row['attachment_count']:
            row['attachment_label'] = f"{row['attachment_count']} 个附件"
        else:
            row['attachment_label'] = '未上传'
        row['needs_followup'] = row['status'] != 'completed' or bool(row['receivable_minor'] or row['pending_receipt_minor'])
        dates = [row['sign_date'] or '', row['updated_at'][:10]]
        dates += [item['settlement_date'] for item in income]
        dates += [item['receipt_date'] for item in receipts]
        dates += [item['invoice_date'] for item in invoices]
        row['latest_date'] = max(dates)
    return result


def _summary(rows):
    return {key: sum(row[key] for row in rows) for key in MONEY_KEYS}


def _fact_summary(facts):
    return {
        'settlement_minor': sum(row['amount_minor'] for row in facts['settlements']),
        'receipt_minor': sum(row['contract_receipt_minor'] for row in facts['receipts']),
        'invoice_minor': sum(row['contract_invoice_minor'] for row in facts['invoices']),
        'receivable_minor': sum(row['unreceived_minor'] for row in facts['settlements']),
    }


def _entity_facts(facts, entity_id):
    if not entity_id:
        return facts
    return {kind: [row for row in rows if str(row['entity_id'] or 'unknown') == entity_id]
            for kind, rows in facts.items()}


def _project_business_rows(conn, facts, resolver, entity_id=''):
    grouped = defaultdict(lambda: {kind: [] for kind in facts})
    for kind, rows in facts.items():
        for row in rows:
            if row['contract_id'] is None and (not entity_id or str(row['entity_id'] or 'unknown') == entity_id):
                grouped[row['project_id']][kind].append(row)
    names = {row['id']: row['legal_name'] for row in conn.execute('SELECT id,legal_name FROM business_partners')}
    result = []
    for pid, project in resolver.projects.items():
        parts = grouped.get(pid)
        owner = resolver.project(pid)
        # An agreed price is context only, never income. Keep an otherwise empty
        # cash job visible, unless all its business is already on real contracts.
        agreed_only = (project['business_mode'] == 'cash' and project['cash_agreed_amount_minor']
                       and not resolver.links.get(pid)
                       and (not entity_id or str(owner['entity_id'] or 'unknown') == entity_id))
        if not parts and not agreed_only:
            continue
        parts = parts or {kind: [] for kind in facts}
        records = [row for rows in parts.values() for row in rows]
        if records:
            owner = resolver._combine(records, '项目业务事实', '按有效业务单据归属；未确认部分不猜测主体。')
        customer = project['customer_partner_id']
        dates = []
        for kind, date_key in (('settlements', 'settlement_date'), ('receipts', 'receipt_date'), ('invoices', 'invoice_date')):
            dates.extend(row[date_key] for row in parts[kind])
        if not records:
            dates.append(str(project.get('updated_at') or '')[:10])
        row = {
            'project_id': pid, 'name': project['name'], 'project_name': project['name'],
            'project_code': project['project_code'], 'customer_partner_id': customer,
            'customer_key': f'customer:{customer}' if customer else f'unlinked:project:{pid}',
            'customer_name': names.get(customer) or project.get('customer_name') or '客户待补充',
            'business_mode': project['business_mode'], 'project_status': project['status'],
            'cash_agreed_amount_minor': project['cash_agreed_amount_minor'],
            'notes': project.get('notes') or '', **owner, **_fact_summary(parts),
            'pending_receipt_minor': sum(item['pending_receipt_minor'] for item in parts['receipts']),
            'review_receipt_minor': sum(item['review_receipt_minor'] for item in parts['receipts']),
            'needs_review': any(item.get('needs_review') for item in records),
            'latest_date': max(dates),
        }
        row['needs_followup'] = (project['status'] != '已完工' or bool(
            row['receivable_minor'] or row['pending_receipt_minor'] or row['needs_review']))
        result.append(row)
    return result


def _parameters(project_id, year, status, entity_id, business_scope):
    year, status, entity_id = str(year or ''), str(status or ''), str(entity_id or '')
    if project_id not in (None, ''):
        if isinstance(project_id, bool) or not str(project_id).isdigit() or int(project_id) <= 0:
            raise ValueError('请选择有效项目')
        project_id = int(project_id)
    else:
        project_id = None
    if business_scope is None:
        business_scope = 'contracts' if year or status else 'all'
    if business_scope not in ('all', 'contracts', 'uncontracted'):
        raise ValueError('业务范围无效，请选择全部业务、正式合同或未关联合同业务')
    if business_scope != 'contracts' and (year or status):
        raise ValueError('签约年份和合同状态仅适用于正式合同范围，请清除这两项筛选')
    if year and year != 'unknown' and (len(year) != 4 or not year.isdigit()):
        raise ValueError('签约年份无效')
    if status and status not in ('draft', 'active', 'completed'):
        raise ValueError('合同状态无效')
    if entity_id and entity_id != 'unknown' and not entity_id.isdigit():
        raise ValueError('经营主体无效')
    return project_id, year, status, entity_id, business_scope


def _select(conn, *, project_id=None, customer_key='', year='', status='', entity_id='', keyword='', business_scope=None):
    project_id, year, status, entity_id, business_scope = _parameters(project_id, year, status, entity_id, business_scope)
    resolver = OwnershipResolver(conn)
    facts = _facts(conn, project_id, resolver)
    candidate_ids = {row['contract_id'] for items in facts.values() for row in items
                     if row['contract_id'] is not None}
    # Contract-only views retain historical header-entity semantics. Business
    # views select fact shares before any contract/customer money is summed.
    if business_scope != 'contracts':
        facts = _entity_facts(facts, entity_id)
    fact_contract_ids = {row['contract_id'] for items in facts.values() for row in items
                         if row['contract_id'] is not None}
    rows = _contract_rows(conn, project_id, _group_facts(facts), resolver, candidate_contract_ids=candidate_ids)
    project_rows = _project_business_rows(conn, facts, resolver, entity_id)
    if project_id is not None:
        project_rows = [row for row in project_rows if row['project_id'] == project_id]
    if business_scope == 'contracts':
        project_rows = []
    elif business_scope == 'uncontracted':
        rows = []
    if customer_key:
        rows = [row for row in rows if row['customer_key'] == customer_key]
        project_rows = [row for row in project_rows if row['customer_key'] == customer_key]
    years = sorted({row['sign_year'] for row in rows}, reverse=True)
    selected_name = next((row['customer_name'] for row in rows + project_rows), '当前客户') if customer_key else ''
    if customer_key and customer_key.startswith('customer:'):
        customer = conn.execute('SELECT legal_name FROM business_partners WHERE id=?',
                                (customer_key.removeprefix('customer:'),)).fetchone()
        if customer:
            selected_name = customer['legal_name']
    if year:
        rows = [row for row in rows if row['sign_year'] == year]
    if status:
        rows = [row for row in rows if row['status'] == status]
    if entity_id:
        rows = [row for row in rows if str(row.get('entity_id') or 'unknown') == entity_id
                or (business_scope != 'contracts' and row['id'] in fact_contract_ids)]
    if keyword.strip():
        needle = keyword.strip().casefold()
        def matches(row):
            return needle in ' '.join(str(row.get(key) or '') for key in
                ('name', 'contract_no', 'project_name', 'project_code', 'customer_name', 'notes', 'entity_name')).casefold()
        rows = [row for row in rows if matches(row)]
        project_rows = [row for row in project_rows if matches(row)]
    rows.sort(key=lambda row: (row['needs_followup'], row['sign_date'] or '', row['id']), reverse=True)
    project_rows.sort(key=lambda row: (row['needs_followup'], row['latest_date'], row['project_id']), reverse=True)
    grouped = defaultdict(lambda: ([], []))
    for row in rows:
        grouped[row['customer_key']][0].append(row)
    for row in project_rows:
        grouped[row['customer_key']][1].append(row)
    customer_rows = []
    for key, (contracts, projects) in grouped.items():
        items = contracts + projects
        customer_rows.append({
            'customer_key': key, 'customer_name': items[0]['customer_name'],
            'customer_partner_id': items[0]['customer_partner_id'],
            'contract_count': len(contracts), 'project_business_count': len(projects),
            'cash_project_count': sum(item['business_mode'] == 'cash' for item in projects),
            'followup_count': sum(item['needs_followup'] for item in items),
            'contract_followup_count': sum(item['needs_followup'] for item in contracts),
            'project_business_followup_count': sum(item['needs_followup'] for item in projects),
            'missing_attachment_count': sum(not item['attachment_count'] for item in contracts),
            'missing_file_count': sum(item['missing_file_count'] for item in contracts),
            'latest_date': max(item['latest_date'] for item in items), **_summary(items),
        })
    customer_rows.sort(key=lambda row: (bool(row['followup_count']), row['latest_date'], row['customer_name']), reverse=True)
    overview = {'customers': customer_rows, 'contracts': rows, 'project_business': project_rows,
                'summary': _summary(rows + project_rows), 'contract_summary': _summary(rows),
                'project_business_summary': _summary(project_rows), 'business_scope': business_scope,
                'years': years, 'customer_name': selected_name,
                'contract_count': len(rows), 'project_business_count': len(project_rows),
                'cash_project_count': sum(row['business_mode'] == 'cash' for row in project_rows),
                'customer_count': len(customer_rows), 'project_id': project_id, 'year_basis': 'sign_date'}
    contract_ids = {row['id'] for row in rows}
    project_ids = {row['project_id'] for row in project_rows}
    selected = {kind: [row for row in items if row['contract_id'] in contract_ids or (
        row['contract_id'] is None and row['project_id'] in project_ids)]
        for kind, items in facts.items()}
    return overview, selected


def get_overview(*, project_id=None, customer_key='', year='', status='', entity_id='', keyword='', business_scope=None):
    with db_read() as conn, use_read_snapshot(conn):
        overview, _ = _select(conn, project_id=project_id, customer_key=customer_key, year=year,
                              status=status, entity_id=entity_id, keyword=keyword, business_scope=business_scope)
        return overview


def _details(summary, facts):
    definitions = {
        'settlements': (facts['settlements'], 'amount_minor', 'settlement_minor'),
        'receipts': (facts['receipts'], 'contract_receipt_minor', 'receipt_minor'),
        'invoices': (facts['invoices'], 'contract_invoice_minor', 'invoice_minor'),
        'outstanding': ([row for row in facts['settlements'] if row['unreceived_minor'] > 0], 'unreceived_minor', 'receivable_minor'),
    }
    details = {}
    for kind, (rows, amount_key, total_key) in definitions.items():
        total = sum(row[amount_key] for row in rows)
        details[kind] = {'rows': rows, 'amount_key': amount_key, 'total_minor': summary[total_key],
                         'detail_total_minor': total, 'reconciled': total == summary[total_key]}
    return details


def get_business_details(*, project_id=None, customer_key='', year='', status='', entity_id='', keyword='', business_scope=None):
    """Lazy four-card drilldown; select exactly the overview's fact shares."""
    with db_read() as conn, use_read_snapshot(conn):
        overview, facts = _select(conn, project_id=project_id, customer_key=customer_key, year=year,
                                  status=status, entity_id=entity_id, keyword=keyword, business_scope=business_scope)
        contracts = {row['id']: row for row in overview['contracts']}
        for rows in facts.values():
            for row in rows:
                contract = contracts.get(row['contract_id'])
                row['scope_label'] = '本合同归属份额' if contract else '未关联合同项目业务'
                row['contract_name'] = contract['name'] if contract else ''
        return {'summary': overview['summary'], 'details': _details(overview['summary'], facts),
                'project_id': overview['project_id'], 'business_scope': overview['business_scope']}


def get_project_business_details(project_id, scope_project_id=None, *, entity_id=''):
    project_id, _, _, _, _ = _parameters(project_id, '', '', '', 'uncontracted')
    scope_project_id, _, _, _, _ = _parameters(scope_project_id, '', '', '', 'uncontracted')
    if project_id is None or (scope_project_id is not None and scope_project_id != project_id):
        raise ValueError('当前范围内没有这项项目业务，请返回台账重新选择')
    with db_read() as conn, use_read_snapshot(conn):
        overview, facts = _select(conn, project_id=project_id, entity_id=entity_id, business_scope='uncontracted')
        if not overview['project_business']:
            raise ValueError('当前范围内没有这项未关联合同业务，请返回台账重新选择')
        for rows in facts.values():
            for row in rows:
                row['scope_label'] = '本项目未关联合同部分'
                row['contract_name'] = ''
        return {'project': overview['project_business'][0], 'summary': overview['summary'],
                'details': _details(overview['summary'], facts), 'project_id': project_id}


def get_details(contract_id, project_id=None, *, entity_id='', business_scope='contracts'):
    with db_read() as conn, use_read_snapshot(conn):
        overview, selected = _select(conn, project_id=project_id, entity_id=entity_id, business_scope=business_scope)
        project_id = overview['project_id']
        contract = next((row for row in overview['contracts'] if row['id'] == int(contract_id)), None)
        if not contract:
            raise ValueError('当前范围内没有这份合同，请返回台账重新选择')
        grouped = _group_facts(selected)
        facts = {kind: rows[contract['id']] for kind, rows in grouped.items()}
        details = _details(contract, facts)
        allocations = contract_service.list_allocations(contract_id=contract['id'], project_id=project_id)
        return {'contract': contract, 'summary': {key: contract[key] for key in MONEY_KEYS},
                'details': details, 'allocations': allocations, 'project_id': project_id,
                'entity_scope': str(entity_id or ''),
                'entity_basis': 'contract_owner' if overview['business_scope'] == 'contracts' else 'fact_owner',
                'business_scope': overview['business_scope']}
