"""Entity comparison and drill-downs, using the same read-only ledger snapshot."""

from collections import Counter, defaultdict

from db.business_facts import COST_FACTS_SQL, project_finance_rows
from db.connection import db_read, use_read_snapshot
from services.entity_ownership import OwnershipResolver
from services import operating_entity_service as ownership


METRICS = {
    'income': ('项目已确认收入', '名下项目的有效收入确认，不以合同金额或回款代替。'),
    'receipts': ('项目已回款', '名下项目的有效回款分配；不包含独立历史旧账。'),
    'outstanding': ('项目应收未收', '各项目有效应收余额，预收款不跨项目冲抵。'),
    'costs': ('已归集项目成本', '采购、人工和费用按项目归集后的含税成本；不是实际资金支出。'),
    'margin': ('项目账面毛利', '名下项目确认收入减已登记的含税项目成本；成本不完整时仅供参考，不是净利润。'),
    'invoices': ('销项票价税合计', '按销项发票实际确认主体统计；与项目经营归属分别保留，不改写票面事实。'),
    'output_tax': ('销项票税额', '有效销项发票税额；不等于应缴税额。'),
    'input_gross': ('已收集进项票', '有效进项发票价税合计，不重复计入项目成本。'),
    'input_tax': ('进项票税额', '已收集进项票税额，不代表全部可抵扣。'),
    'deducted_tax': ('已确认抵扣税额', '仅会计标记为已抵扣的进项税额；不自动计算应缴税。'),
}


def _facts(conn, resolver, project_id=None):
    projects = project_finance_rows(conn, project_id)
    if project_id is not None and not projects:
        raise ValueError('项目不存在，请重新选择范围')
    permitted = {row['project_id'] for row in projects}
    project_owners = {pid: resolver.project(pid) for pid in permitted}
    rows = {metric: [] for metric in METRICS}

    def add(metric, row, entity_id):
        rows[metric].append({**row, 'entity_id': entity_id,
                             'entity_name': resolver.entities[entity_id]['name'] if entity_id else '待确认归属'})

    for row in conn.execute("""SELECT id,project_id,settlement_date AS date,settlement_no AS number,
            amount_minor,basis AS description FROM settlements WHERE status='active' ORDER BY settlement_date DESC,id DESC"""):
        if row['project_id'] in permitted:
            add('income', {**dict(row), 'project': resolver.projects[row['project_id']]['name']},
                project_owners[row['project_id']]['entity_id'])
    for row in conn.execute("""SELECT r.id,a.project_id,r.receipt_date AS date,r.receipt_no AS number,
            SUM(a.allocated_amount_minor) AS amount_minor,r.payment_method AS description
            FROM receipts r JOIN receipt_allocations a ON a.receipt_id=r.id
            WHERE r.status='active' AND a.status='active' GROUP BY r.id,a.project_id
            ORDER BY r.receipt_date DESC,r.id DESC,a.project_id"""):
        if row['project_id'] in permitted:
            add('receipts', {**dict(row), 'project': resolver.projects[row['project_id']]['name']},
                project_owners[row['project_id']]['entity_id'])
    costs = defaultdict(int)
    for row in conn.execute(f'SELECT * FROM ({COST_FACTS_SQL}) WHERE project_id IS NOT NULL ORDER BY date DESC,id,project_id'):
        if row['project_id'] in permitted:
            costs[row['project_id']] += row['amount_minor']
            add('costs', dict(row), project_owners[row['project_id']]['entity_id'])
    for row in projects:
        pid = row['project_id']
        entity_id = project_owners[pid]['entity_id']
        common = {'id': pid, 'project_id': pid, 'project': row['project_name'], 'number': row['project_code'],
                  'description': row['project_status'], 'date': '', 'income_minor': row['settlement_minor'],
                  'receipt_minor': row['receipt_minor'], 'cost_minor': costs[pid]}
        if row['receivable_minor']:
            add('outstanding', {**common, 'amount_minor': row['receivable_minor']}, entity_id)
        add('margin', {**common, 'amount_minor': row['settlement_minor'] - costs[pid]}, entity_id)
    for invoice in conn.execute("SELECT * FROM sales_invoices WHERE status='active' ORDER BY invoice_date DESC,id DESC"):
        if invoice['project_id'] not in permitted:
            continue
        resolution = resolver.record('sales_invoices', invoice)
        common = {'id': invoice['id'], 'project_id': invoice['project_id'],
                  'project': resolver.projects[invoice['project_id']]['name'], 'date': invoice['invoice_date'],
                  'number': invoice['invoice_no'], 'description': resolution['ownership_source']}
        add('invoices', {**common, 'amount_minor': invoice['amount_minor']}, resolution['entity_id'])
        add('output_tax', {**common, 'amount_minor': invoice['tax_amount_minor'] or 0}, resolution['entity_id'])
    # Input invoices have their own legal buyer; a project filter cannot silently include an entire multi-project invoice.
    if project_id is None:
        for invoice in conn.execute("SELECT * FROM input_invoices WHERE status='active' ORDER BY invoice_date DESC,id DESC"):
            common = {'id': invoice['id'], 'project_id': None, 'project': '主体进项票', 'date': invoice['invoice_date'],
                      'number': invoice['invoice_no'], 'description': invoice['seller_name']}
            for metric, column in (('input_gross', 'gross_minor'), ('input_tax', 'tax_minor')):
                add(metric, {**common, 'amount_minor': invoice[column]}, invoice['entity_id'])
            if invoice['deduction_status'] == 'deducted':
                add('deducted_tax', {**common, 'amount_minor': invoice['tax_minor']}, invoice['entity_id'])
    return rows, projects, project_owners


def get_overview(project_id=None):
    with db_read() as conn, use_read_snapshot(conn):
        resolver = OwnershipResolver(conn)
        facts, projects, project_owners = _facts(conn, resolver, project_id)
        buckets = {identity: {metric + '_minor': 0 for metric in METRICS} for identity in [*resolver.entities, None]}
        for metric, records in facts.items():
            for row in records:
                buckets[row['entity_id']][metric + '_minor'] += row['amount_minor']
        kinds = []
        counts = Counter()
        for kind, (label, _, _, _) in ownership.RECORDS.items():
            records = ownership.list_records(kind, project_id=project_id)
            statuses = Counter(row['ownership_status'] for row in records)
            counts.update(statuses)
            kinds.append({'kind': kind, 'label': label, 'total': len(records),
                          'pending': statuses['pending'], 'conflict': statuses['conflict'],
                          'confirmed': statuses['confirmed'], 'inherited': statuses['inherited']})
        entities = []
        for identity, entity in resolver.entities.items():
            owned = [row for row in projects if project_owners[row['project_id']]['entity_id'] == identity]
            entities.append({**entity, 'metrics': buckets[identity], 'project_count': len(owned),
                             'active_project_count': sum(row['project_status'] in ('进行中', '筹备中') for row in owned),
                             'outstanding_project_count': sum(row['receivable_minor'] > 0 for row in owned)})
        return {'scope': projects[0]['project_name'] if project_id is not None else '全部工程项目',
                'project_id': project_id, 'entities': entities, 'pending_metrics': buckets[None],
                'total_metrics': {metric + '_minor': sum(bucket[metric + '_minor'] for bucket in buckets.values()) for metric in METRICS},
                'counts': {status: counts[status] for status in ('confirmed', 'inherited', 'pending', 'conflict')},
                'project_counts': dict(Counter(row['ownership_status'] for row in project_owners.values())),
                'kinds': kinds, 'input_scope_available': project_id is None,
                'definitions': {key: {'label': label, 'definition': definition} for key, (label, definition) in METRICS.items()}}


def get_detail(metric, entity_id=None, project_id=None):
    if metric not in METRICS:
        raise ValueError('不支持的经营主体明细')
    with db_read() as conn, use_read_snapshot(conn):
        resolver = OwnershipResolver(conn)
        if entity_id not in (None, 'all') and entity_id not in resolver.entities:
            raise ValueError('经营主体不存在')
        if project_id is not None and metric in ('input_gross', 'input_tax', 'deducted_tax'):
            raise ValueError('进项票按实际购买主体统计，请切回全部项目查看，不将整张发票重复归入一个项目')
        facts, projects, _ = _facts(conn, resolver, project_id)
        rows = [row for row in facts[metric] if entity_id == 'all' or row['entity_id'] == entity_id]
        label, definition = METRICS[metric]
        return {'metric': metric, 'label': label, 'definition': definition,
                'entity_name': '全部主体及待归属' if entity_id == 'all' else
                    (resolver.entities[entity_id]['name'] if entity_id else '待确认归属'),
                'scope': projects[0]['project_name'] if project_id is not None else '全部工程项目',
                'rows': rows, 'total_minor': sum(row['amount_minor'] for row in rows)}
