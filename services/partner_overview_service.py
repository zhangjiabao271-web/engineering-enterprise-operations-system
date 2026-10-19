"""Read-only partner operating summaries, independent of the contract ledger.

Money stays in integer cents. No name matching, invoice offsets or inferred
cash/payables. All nested master-data reads share the same SQLite snapshot.
"""
from collections import defaultdict
from datetime import date

from db.connection import db_read, use_read_snapshot
from services import historical_receivable_service as history
from services import master_data_service as partners


CUSTOMER_MONEY = ('yearly_business_minor', 'yearly_receipt_minor',
                  'project_receivable_minor', 'historical_receivable_minor',
                  'current_receivable_minor', 'yearly_historical_receipt_minor',
                  'pending_receipt_minor')


def _parameters(role, year, project_id):
    role = {'customers': 'customer', 'suppliers': 'supplier'}.get(role, role)
    if role not in ('customer', 'supplier'):
        raise ValueError('请选择客户或供应商经营概览')
    year = date.today().year if year in (None, '') else year
    if isinstance(year, bool) or not str(year).isdigit() or not 1900 <= int(year) <= 9998:
        raise ValueError('经营年度必须在 1900 到 9998 之间')
    if project_id not in (None, ''):
        if isinstance(project_id, bool) or not str(project_id).isdigit() or int(project_id) <= 0:
            raise ValueError('请选择有效项目')
        project_id = int(project_id)
    else:
        project_id = None
    return role, int(year), project_id


def _in_year(day, year):
    return str(day or '')[:4] == str(year)


def _share(amount, total):
    # Basis points, rounded half up without a float conversion of large amounts.
    return (amount * 10000 + total // 2) // total if total > 0 else None


def _recent(row, kind, identity, day, amount, number='', project_id=None):
    events = row['_recent']
    key = (kind, identity)
    if key not in events:
        events[key] = dict(module=kind, id=identity, date=day, amount_minor=0,
                           number=number, project_ids=[])
    event = events[key]
    event['amount_minor'] += amount
    if project_id is not None and project_id not in event['project_ids']:
        event['project_ids'].append(project_id)


def _finish_recent(row):
    events = sorted(row.pop('_recent').values(),
                    key=lambda event: (event['date'], event['module'], event['id']), reverse=True)
    row['recent_activity'] = events[:3]
    row['latest_business_date'] = events[0]['date'] if events else ''


def _customer_overview(conn, rows, year, project_id):
    by_id = {row['id']: row for row in rows}
    projects = {p['id']: dict(p) for p in conn.execute('SELECT id,name,project_code,status,customer_partner_id FROM projects')}
    if project_id is not None and project_id not in projects:
        raise ValueError('项目不存在，请刷新后重新选择')
    scoped_projects = {key: value for key, value in projects.items() if project_id is None or key == project_id}
    for row in rows:
        row.update({key: 0 for key in CUSTOMER_MONEY})
        row.update(projects={}, historical_projects=[], historical_unknown_count=0,
                   active_project_count=0, _recent={})

    def link(customer_id, pid):
        if customer_id not in by_id or pid not in scoped_projects:
            return None
        row = by_id[customer_id]
        if pid not in row['projects']:
            p = projects[pid]
            row['projects'][pid] = dict(project_id=pid, name=p['name'], project_code=p['project_code'],
                status=p['status'], yearly_business_minor=0, yearly_receipt_minor=0,
                total_business_minor=0, applied_receipt_minor=0, current_receivable_minor=0)
        return row

    for pid, p in scoped_projects.items():
        link(p['customer_partner_id'], pid)
    # Explicit project/contract relationships only; never infer from names or dates.
    for item in conn.execute('''SELECT DISTINCT a.project_id,c.customer_partner_id
        FROM contract_project_allocations a JOIN contracts c ON c.id=a.contract_id
        JOIN projects p ON p.id=a.project_id
        WHERE a.status='active' AND c.status IN ('draft','active','completed')
          AND p.customer_partner_id IS NULL'''):
        link(item['customer_partner_id'], item['project_id'])

    unattributed = dict(yearly_business_minor=0, yearly_receipt_minor=0,
                        current_receivable_minor=0, unallocated_receipt_minor=0,
                        overallocated_receipt_count=0)
    years = {year, date.today().year}
    unknown_balances = defaultdict(lambda: [0, 0])
    for item in conn.execute('''SELECT s.*,COALESCE(p.customer_partner_id,c.customer_partner_id) AS customer_id
        FROM settlements s JOIN projects p ON p.id=s.project_id
        LEFT JOIN contracts c ON c.id=s.contract_id WHERE s.status='active' '''):
        pid = item['project_id']
        if pid not in scoped_projects:
            continue
        amount = item['amount_minor']
        years.add(int(item['settlement_date'][:4]))
        row = link(item['customer_id'], pid)
        if row is None:
            unknown_balances[pid][0] += amount
            if _in_year(item['settlement_date'], year):
                unattributed['yearly_business_minor'] += amount
            continue
        p = row['projects'][pid]
        p['total_business_minor'] += amount
        if _in_year(item['settlement_date'], year):
            row['yearly_business_minor'] += amount
            p['yearly_business_minor'] += amount
        _recent(row, 'settlements', item['id'], item['settlement_date'], amount,
                item['settlement_no'], pid)

    allocated = defaultdict(int)
    overallocated = set()
    # Read each allocation exactly once. Invoices and contract allocation joins
    # would multiply an actual receipt, so neither is used as a receipt source.
    for item in conn.execute('''SELECT a.*,r.receipt_date,r.receipt_no,r.amount_minor AS receipt_amount_minor,
            SUM(a.allocated_amount_minor) OVER (PARTITION BY a.receipt_id) AS receipt_allocated_minor,
            COALESCE(p.customer_partner_id,sc.customer_partner_id,c.customer_partner_id) AS customer_id
        FROM receipt_allocations a JOIN receipts r ON r.id=a.receipt_id
        JOIN projects p ON p.id=a.project_id
        LEFT JOIN settlements s ON s.id=a.settlement_id
        LEFT JOIN contracts sc ON sc.id=s.contract_id
        LEFT JOIN contracts c ON c.id=a.contract_id
        WHERE a.status='active' AND r.status='active'
          AND (a.settlement_id IS NULL OR (s.status='active' AND s.project_id=a.project_id))'''):
        amount = item['allocated_amount_minor']
        pid = item['project_id']
        # An inconsistent allocation set cannot justify customer ownership of
        # more money than arrived. Leave the entire receipt for reconciliation.
        if item['receipt_allocated_minor'] > item['receipt_amount_minor']:
            if pid in scoped_projects and _in_year(item['receipt_date'], year):
                overallocated.add(item['receipt_id'])
            continue
        allocated[item['receipt_id']] += amount
        if pid not in scoped_projects:
            continue
        years.add(int(item['receipt_date'][:4]))
        row = link(item['customer_id'], pid)
        if row is None:
            if item['settlement_id'] is not None:
                unknown_balances[pid][1] += amount
            if _in_year(item['receipt_date'], year):
                unattributed['yearly_receipt_minor'] += amount
            continue
        p = row['projects'][pid]
        if item['settlement_id'] is not None:
            p['applied_receipt_minor'] += amount
        else:
            row['pending_receipt_minor'] += amount
        if _in_year(item['receipt_date'], year):
            row['yearly_receipt_minor'] += amount
            p['yearly_receipt_minor'] += amount
        _recent(row, 'receipts', item['receipt_id'], item['receipt_date'], amount,
                item['receipt_no'], pid)

    for item in conn.execute("SELECT id,receipt_date,amount_minor FROM receipts WHERE status='active'"):
        if project_id is None:
            years.add(int(item['receipt_date'][:4]))
            if _in_year(item['receipt_date'], year):
                unattributed['unallocated_receipt_minor'] += max(item['amount_minor'] - allocated[item['id']], 0)
    unattributed['overallocated_receipt_count'] = len(overallocated)

    # Invoices contribute only a recent-event date and a link, never revenue/debt.
    for item in conn.execute('''SELECT i.*,COALESCE(p.customer_partner_id,c.customer_partner_id) AS customer_id
        FROM sales_invoices i JOIN projects p ON p.id=i.project_id
        LEFT JOIN contracts c ON c.id=i.contract_id WHERE i.status='active' '''):
        if item['project_id'] not in scoped_projects:
            continue
        years.add(int(item['invoice_date'][:4]))
        row = link(item['customer_id'], item['project_id'])
        if row is not None:
            _recent(row, 'invoices', item['id'], item['invoice_date'], item['amount_minor'],
                    item['invoice_no'], item['project_id'])

    # Old debt has its own ID space. Explicit customer ownership permits a
    # company-level subtotal, never a mapping to an engineering project ID.
    if project_id is None:
        for old in history.list_projects():
            row = by_id.get(old['customer_id'])
            if row is None:
                continue
            row['historical_projects'].append(dict(historical_project_id=old['id'], name=old['name'],
                                                   remaining_minor=old['remaining_minor']))
            if old['remaining_minor'] is None:
                row['historical_unknown_count'] += 1
            else:
                row['historical_receivable_minor'] += max(old['remaining_minor'], 0)
        for item in conn.execute('''SELECT r.*,p.customer_id FROM historical_receipts r
            JOIN historical_receivable_projects p ON p.id=r.project_id WHERE r.status='active' '''):
            years.add(int(item['receipt_date'][:4]))
            row = by_id.get(item['customer_id'])
            if row is not None:
                if _in_year(item['receipt_date'], year):
                    row['yearly_receipt_minor'] += item['amount_minor']
                    row['yearly_historical_receipt_minor'] += item['amount_minor']
                _recent(row, 'historical', item['id'], item['receipt_date'], item['amount_minor'])

    if project_id is not None:
        rows = [row for row in rows if row['projects']]
    for row in rows:
        row['projects'] = list(row['projects'].values())
        for p in row['projects']:
            p['current_receivable_minor'] = max(p['total_business_minor'] - p.pop('applied_receipt_minor'), 0)
        row['project_receivable_minor'] = sum(p['current_receivable_minor'] for p in row['projects'])
        row['current_receivable_minor'] = row['project_receivable_minor'] + row['historical_receivable_minor']
        row['active_project_count'] = sum(p['status'] in ('筹备中', '进行中') for p in row['projects'])
        row['receivable_complete'] = row['historical_unknown_count'] == 0
        _finish_recent(row)
    summary = {key: sum(row[key] for row in rows) for key in CUSTOMER_MONEY}
    summary['active_project_count'] = len({p['project_id'] for row in rows for p in row['projects']
                                          if p['status'] in ('筹备中', '进行中')})
    summary['historical_unknown_count'] = sum(row['historical_unknown_count'] for row in rows)
    summary['partner_count'] = len(rows)
    for row in rows:
        row['income_share_bps'] = _share(row['yearly_business_minor'], summary['yearly_business_minor'])
        row['receivable_share_bps'] = None
        if not summary['historical_unknown_count']:
            row['receivable_share_bps'] = _share(row['current_receivable_minor'], summary['current_receivable_minor'])
    unattributed['current_receivable_minor'] = sum(max(income - received, 0) for income, received in unknown_balances.values())
    rows.sort(key=lambda row: (-row['yearly_business_minor'], row['name'], row['id']))
    return rows, summary, unattributed, years


def _supplier_overview(conn, rows, year, project_id):
    if project_id is not None and not conn.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
        raise ValueError('项目不存在，请刷新后重新选择')
    profiles = {row['id']: row for row in partners.list_suppliers(active_only=False)}
    by_id = {row['id']: row for row in rows}
    for row in rows:
        profile = profiles.get(row['id'], {})
        row.update(supplier_kind=profile.get('supplier_kind', 'unclassified'),
                   category=profile.get('category', ''), yearly_purchase_minor=0,
                   purchase_count=0, latest_purchase_date='', latest_purchase_id=None,
                   _recent={}, _has_purchase=False)
    # One header per order, not per material. In a project scope use the shared
    # cost view's allocated portion, including tax/freight, not the whole order.
    if project_id is None:
        sql = "SELECT po.*,po.total_amount_cents AS scoped_minor FROM purchase_orders po WHERE po.status='active'"
        params = ()
    else:
        sql = '''SELECT po.*,cost.amount_minor AS scoped_minor FROM purchase_orders po
            JOIN (SELECT purchase_order_id,SUM(cost_minor) AS amount_minor FROM purchase_project_costs
                  WHERE project_id=? GROUP BY purchase_order_id) cost ON cost.purchase_order_id=po.id
            WHERE po.status='active' '''
        params = (project_id,)
    years = {year, date.today().year}
    unattributed = dict(yearly_purchase_minor=0, purchase_count=0)
    for item in conn.execute(sql, params):
        years.add(int(item['purchase_date'][:4]))
        row = by_id.get(item['supplier_partner_id'])
        if row is None:
            if _in_year(item['purchase_date'], year):
                unattributed['yearly_purchase_minor'] += item['scoped_minor']
                unattributed['purchase_count'] += 1
            continue
        row['_has_purchase'] = True
        if _in_year(item['purchase_date'], year):
            row['yearly_purchase_minor'] += item['scoped_minor']
            row['purchase_count'] += 1
        if (item['purchase_date'], item['id']) > (row['latest_purchase_date'], row['latest_purchase_id'] or 0):
            row['latest_purchase_date'] = item['purchase_date']
            row['latest_purchase_id'] = item['id']
        _recent(row, 'purchases', item['id'], item['purchase_date'], item['scoped_minor'], item['order_no'])
    if project_id is not None:
        rows = [row for row in rows if row['_has_purchase']]
    for row in rows:
        row.pop('_has_purchase')
        _finish_recent(row)
    summary = {key: sum(row[key] for row in rows) for key in ('yearly_purchase_minor', 'purchase_count')}
    summary['partner_count'] = len(rows)
    summary['purchasing_supplier_count'] = sum(row['purchase_count'] > 0 for row in rows)
    for row in rows:
        row['purchase_share_bps'] = _share(row['yearly_purchase_minor'], summary['yearly_purchase_minor'])
    rows.sort(key=lambda row: (-row['yearly_purchase_minor'], row['name'], row['id']))
    return rows, summary, unattributed, years


def get_overview(role, year=None, project_id=None):
    """Return rows/summary/years for dashboard?view=customers|suppliers.

    Search, archive status, supplier grouping and paging are view-only filters.
    Shares use every attributed partner in the API's project/year scope, not
    just the visible page. Money is left as int for the existing JSON adapter.
    """
    role, year, project_id = _parameters(role, year, project_id)
    with db_read() as conn, use_read_snapshot(conn):
        rows = partners.list_business_partners(role=role)
        build = _customer_overview if role == 'customer' else _supplier_overview
        rows, summary, unattributed, years = build(conn, rows, year, project_id)
        return dict(role=role, year=year, project_id=project_id, rows=rows, summary=summary,
                    years=sorted(years, reverse=True), unattributed=unattributed,
                    share_basis='当前项目范围内全部已归属客商；不随搜索、分组或分页变化',
                    receipt_basis='到账日期；仅有效分配份额，独立旧账仅计公司范围',
                    receivable_basis='各项目有效收入减已关联收入的有效回款，逐项目不低于零；另列已知旧账余额',
                    purchase_basis='采购日期；含税含运费，按单计数；项目范围只计分摊份额',
                    historical_included=project_id is None,
                    unallocated_receipt_scope='company' if project_id is None else 'excluded')
