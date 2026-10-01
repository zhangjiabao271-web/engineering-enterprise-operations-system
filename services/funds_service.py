"""Actual money is independent of revenue, invoices and project cost allocation."""

from datetime import date
from decimal import Decimal, InvalidOperation
import json

from db.connection import db_read, db_transaction
from services._common import now
from services._constraint_errors import translate_constraints


ACCOUNT_KINDS = {'bank': '公司银行', 'personal': '经营用个人银行卡',
                 'wallet': '微信 / 支付宝', 'cash': '现金'}
SOURCE_LABELS = {'receipt': '业务回款', 'purchase': '采购付款',
                 'cost': '费用付款', 'wage': '工资发放'}
OTHER_CATEGORIES = {
    'owner_in': ('老板投入', 'in'), 'loan_in': ('借款到账', 'in'),
    'other_in': ('其他到账（非工程收入）', 'in'),
    'owner_out': ('老板取款', 'out'), 'loan_out': ('归还借款本金', 'out'),
    'other_out': ('其他支出（不新增成本）', 'out'),
}
CATEGORY_LABELS = {**SOURCE_LABELS,
                   **{key: value[0] for key, value in OTHER_CATEGORIES.items()},
                   'transfer': '内部转账'}

_funds_write = translate_constraints(
    duplicate='资金记录已存在，请刷新后核对，避免重复登记',
    related='关联的账户、计划或业务来源已变化，请刷新后重选',
    invalid='资金记录不符合保存条件，请核对后重试',
)


def _connection(*, write=False):
    if write:
        return db_transaction(immediate=True)
    return db_read()


def _money(value, *, zero=False):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError
        cents = number * 100
        if cents != cents.to_integral_value():
            raise ValueError
        result = int(cents)
        if (not zero and result <= 0) or result > 9_000_000_000_000_000:
            raise ValueError
        return result
    except (ValueError, InvalidOperation, OverflowError):
        raise ValueError('请填写有效金额（最多两位小数，收付款必须大于零）') from None


def _date(value, *, future=False):
    try:
        parsed = date.fromisoformat(str(value))
        if parsed.isoformat() != value or (not future and parsed > date.today()):
            raise ValueError
        return parsed.isoformat()
    except (TypeError, ValueError):
        raise ValueError('请选择有效日期；实际收支、期初和核对不能使用未来日期') from None


def _text(value, label):
    value = str(value or '').strip()
    if not value:
        raise ValueError(f'请填写{label}')
    return value


def _audit(conn, kind, entity_id, action, before, after, reason):
    conn.execute('''INSERT INTO fund_audit
        (entity_type,entity_id,action,before_json,after_json,reason,created_at)
        VALUES (?,?,?,?,?,?,?)''',
        (kind, str(entity_id), action, json.dumps(before, ensure_ascii=False),
         json.dumps(after, ensure_ascii=False), reason, now()))


def _account(conn, account_id):
    row = conn.execute('SELECT * FROM fund_accounts WHERE id=?', (account_id,)).fetchone()
    if not row:
        raise ValueError('资金账户不存在')
    return dict(row)


def _start_date(conn):
    return conn.execute('SELECT MIN(opening_date) FROM fund_accounts').fetchone()[0]


@translate_constraints(
    duplicate="账户名称已存在，请使用可区分的名称",
    related="账户关联的记录已变化，请刷新后重试",
    invalid="账户资料不符合保存条件，请核对后重试",
)
def save_account(data, account_id=None):
    name = _text(data.get('name'), '账户名称')
    kind = data.get('kind')
    if kind not in ACCOUNT_KINDS:
        raise ValueError('请选择账户类型')
    opening_date = _date(data.get('opening_date'))
    opening_minor = _money(data.get('opening_amount'), zero=True)
    reason = _text(data.get('reason') or ('建立账户' if not account_id else ''), '修改原因')
    with _connection(write=True) as conn:
        before = _account(conn, account_id) if account_id else {}
        if conn.execute('SELECT 1 FROM fund_accounts WHERE name=? AND id<>?',
                        (name, account_id or 0)).fetchone():
            raise ValueError('账户名称已存在，请使用可区分的名称（无需填完整卡号）')
        if before and (opening_date != before['opening_date'] or opening_minor != before['opening_minor']):
            used = conn.execute('''SELECT 1 FROM fund_transactions
                WHERE from_account_id=? OR to_account_id=? UNION ALL
                SELECT 1 FROM fund_reconciliations WHERE account_id=? LIMIT 1''',
                (account_id, account_id, account_id)).fetchone()
            if used:
                raise ValueError('账户已有流水或核对历史，不能直接改期初；请核对遗漏流水')
        old_start = _start_date(conn)
        other_dates = [r[0] for r in conn.execute('SELECT opening_date FROM fund_accounts WHERE id<>?',(account_id or 0,))]
        new_start = min([opening_date,*other_dates])
        if old_start and new_start != old_start and conn.execute(
                'SELECT 1 FROM fund_source_openings LIMIT 1').fetchone():
            raise ValueError('已有期初付款核实，不能改变资金启用日期')
        notes = str(data.get('notes') or '').strip()
        if account_id:
            conn.execute('''UPDATE fund_accounts SET name=?,kind=?,opening_date=?,
                opening_minor=?,notes=?,updated_at=? WHERE id=?''',
                (name, kind, opening_date, opening_minor, notes, now(), account_id))
        else:
            account_id = conn.execute('''INSERT INTO fund_accounts
                (name,kind,opening_date,opening_minor,notes,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?)''',
                (name, kind, opening_date, opening_minor, notes, now(), now())).lastrowid
        _audit(conn, 'account', account_id, 'save', before, _account(conn, account_id), reason)
        return account_id


def _balance(conn, account_id, as_of):
    account = _account(conn, account_id)
    if as_of < account['opening_date']:
        return None
    movement = conn.execute('''SELECT COALESCE(SUM(
        CASE WHEN to_account_id=? THEN amount_minor ELSE -amount_minor END),0)
        FROM fund_transactions WHERE status='active' AND transaction_date<=?
        AND (from_account_id=? OR to_account_id=?)''',
        (account_id, as_of, account_id, account_id)).fetchone()[0]
    return account['opening_minor'] + movement


def list_accounts(as_of=None):
    as_of = _date(as_of or date.today().isoformat())
    with _connection() as conn:
        rows = [dict(row) for row in conn.execute('SELECT * FROM fund_accounts ORDER BY status,id')]
        for row in rows:
            row['balance_minor'] = _balance(conn, row['id'], as_of)
        return rows


@translate_constraints(
    duplicate="账户状态与现有记录冲突，请刷新后重试",
    related="账户仍有相关记录，不能更改状态",
    invalid="账户状态不符合变更条件，请刷新后重试",
)
def set_account_archived(account_id, archived):
    with _connection(write=True) as conn:
        before = _account(conn, account_id)
        if archived and _balance(conn, account_id, date.today().isoformat()) != 0:
            raise ValueError('只能停用余额为零的账户，请先转出真实余额或核对账目')
        status = 'archived' if archived else 'active'
        conn.execute('UPDATE fund_accounts SET status=?,updated_at=? WHERE id=?', (status, now(), account_id))
        _audit(conn, 'account', account_id, 'status', before, _account(conn, account_id), '停用' if archived else '启用')


def _sources(conn, kind):
    if kind == 'receipt':
        sql = '''SELECT id,receipt_no AS label,receipt_date AS source_date,
            payer_name_snapshot AS counterparty,amount_minor AS total_minor,
            payment_method AS legacy_status,'' AS period
            FROM receipts WHERE status='active' ORDER BY receipt_date DESC,id DESC'''
    elif kind == 'purchase':
        sql = '''SELECT po.id,po.order_no || COALESCE((SELECT ' · ' || GROUP_CONCAT(i.material_name_snapshot,' / ')
            FROM purchase_order_items i WHERE i.purchase_order_id=po.id),'') AS label,purchase_date AS source_date,
            CASE WHEN payment_method='员工垫付' THEN purchaser ELSE merchant_name_snapshot END AS counterparty,
            total_amount_cents AS total_minor,
            payment_status||' / '||payment_method AS legacy_status,'' AS period
            FROM purchase_orders po WHERE status='active' ORDER BY purchase_date DESC,po.id DESC'''
    elif kind == 'cost':
        sql = '''SELECT id,cost_no||' · '||category AS label,cost_date AS source_date,
            counterparty_name_snapshot AS counterparty,amount_minor AS total_minor,
            '' AS legacy_status,'' AS period FROM cost_entries
            WHERE status='active' AND source_type IN ('manual','legacy_manual')
            ORDER BY cost_date DESC,id DESC'''
    elif kind == 'wage':
        sql = '''SELECT w.id,w.name||' · '||substr(l.work_date,1,7) AS label,
            MIN(l.work_date) AS source_date,w.name AS counterparty,
            SUM(COALESCE(l.amount_minor,CAST(ROUND(l.amount*100) AS INTEGER))) AS total_minor,
            '' AS legacy_status,substr(l.work_date,1,7) AS period
            FROM work_logs l JOIN workers w ON w.id=l.worker_id WHERE l.status='active'
            GROUP BY w.id,substr(l.work_date,1,7) ORDER BY period DESC,w.name'''
    else:
        raise ValueError('业务来源无效')
    return [dict(row) for row in conn.execute(sql)]


def _decorate_sources(conn, kind):
    rows = _sources(conn, kind)
    totals = {(row['source_id'], row['source_period']): row['paid'] for row in conn.execute(
        '''SELECT source_id,source_period,SUM(amount_minor) AS paid FROM fund_transactions
           WHERE source_kind=? AND status='active' GROUP BY source_id,source_period''', (kind,))}
    openings = {(row['source_id'], row['source_period']): dict(row) for row in conn.execute(
        'SELECT * FROM fund_source_openings WHERE source_kind=?', (kind,))}
    start = _start_date(conn)
    for row in rows:
        key = (row['id'], row['period'])
        opening = openings.get(key)
        row.update(source_kind=kind, paid_minor=totals.get(key, 0),
                   opening_settled_minor=opening['settled_minor'] if opening else 0,
                   verified=bool(opening) or kind == 'receipt', tracking_start=start)
        if opening:
            row['counterparty'] = opening['payee']
        row['remaining_minor'] = row['total_minor'] - row['paid_minor'] - row['opening_settled_minor']
        if not start:
            row['state'] = '先建立账户'
        elif kind == 'receipt' and row['source_date'] < start and row['legacy_status'] != '票据':
            row['state'] = '启用前回款（计入期初）'
        elif row['remaining_minor'] < 0:
            row['state'] = '金额异常，请核对'
        elif not row['verified']:
            row['state'] = '待核实'
        elif row['remaining_minor'] == 0:
            row['state'] = '已记录' if kind == 'receipt' else '已付清'
        else:
            row['state'] = '待指定到账账户' if kind == 'receipt' else '待付款'
    return rows


def list_sources(kind, *, include_closed=False):
    with _connection() as conn:
        rows = _decorate_sources(conn, kind)
        if not include_closed:
            rows = [r for r in rows if r['state'] not in ('已记录', '已付清', '启用前回款（计入期初）')]
        return rows


def _source(conn, kind, source_id, period=''):
    if not source_id:
        raise ValueError('请选择有效业务来源')
    row = next((r for r in _decorate_sources(conn, kind)
                if r['id'] == int(source_id) and r['period'] == period), None)
    if not row:
        raise ValueError('业务来源已不存在或已作废，请刷新后重选')
    return row


def _verify_source(conn, kind, source_id, period, settled_minor, payee, reason):
    if kind not in ('purchase', 'cost', 'wage'):
        raise ValueError('该来源不需要核实期初付款')
    row = _source(conn, kind, source_id, period)
    if not row['tracking_start']:
        raise ValueError('请先建立账户并确定资金启用日期')
    if settled_minor + row['paid_minor'] > row['total_minor']:
        raise ValueError('启用前已付加上资金流水不能超过业务金额')
    if settled_minor and row['source_date'] >= row['tracking_start']:
        raise ValueError('启用后发生的业务请通过真实付款登记，不能记为启用前已付')
    payee = _text(payee, '实际收款人（员工垫付请填报销给谁）')
    reason = _text(reason, '核实说明')
    key = (kind, source_id, period)
    previous = conn.execute('''SELECT * FROM fund_source_openings
        WHERE source_kind=? AND source_id=? AND source_period=?''', key).fetchone()
    conn.execute('''INSERT INTO fund_source_openings
        (source_kind,source_id,source_period,settled_minor,payee,reason,updated_at)
        VALUES (?,?,?,?,?,?,?) ON CONFLICT(source_kind,source_id,source_period)
        DO UPDATE SET settled_minor=excluded.settled_minor,payee=excluded.payee,
            reason=excluded.reason,updated_at=excluded.updated_at''',
        (*key, settled_minor, payee, reason, now()))
    _audit(conn, 'source', ':'.join(map(str, key)), 'verify', dict(previous or {}),
           {'settled_minor': settled_minor, 'payee': payee}, reason)


@_funds_write
def verify_source(kind, source_id, period, data):
    with _connection(write=True) as conn:
        _verify_source(conn, kind, int(source_id), period,
                       _money(data.get('settled_amount'), zero=True), data.get('payee'), data.get('reason'))


def _plan_paid(conn, plan_id):
    return conn.execute('''SELECT COALESCE(SUM(amount_minor),0) FROM fund_transactions
        WHERE plan_id=? AND status='active' ''', (plan_id,)).fetchone()[0]


def _planned_remaining(conn, kind, source_id, period, exclude_id=0):
    return conn.execute('''SELECT COALESCE(SUM(p.amount_minor - COALESCE((
        SELECT SUM(t.amount_minor) FROM fund_transactions t WHERE t.plan_id=p.id AND t.status='active'),0)),0)
        FROM fund_plans p WHERE source_kind=? AND source_id=? AND source_period=?
          AND status='planned' AND id<>?''', (kind,source_id,period,exclude_id)).fetchone()[0]


def _project_receivable(conn, project_id):
    return conn.execute('''SELECT MAX(COALESCE(SUM(s.amount_minor - COALESCE((
        SELECT SUM(ra.allocated_amount_minor) FROM receipt_allocations ra
        JOIN receipts r ON r.id=ra.receipt_id
        WHERE ra.settlement_id=s.id AND ra.status='active' AND r.status='active'),0)),0),0)
        FROM settlements s WHERE s.project_id=? AND s.status='active' ''', (project_id,)).fetchone()[0]


def _validate_plan(conn, plan_id, direction, amount, kind, source_id, period):
    if not plan_id:
        return
    plan = conn.execute('SELECT * FROM fund_plans WHERE id=?', (plan_id,)).fetchone()
    if not plan or plan['status'] != 'planned' or plan['direction'] != direction:
        raise ValueError('计划不存在、已取消或收支方向不同')
    if amount > plan['amount_minor'] - _plan_paid(conn, plan_id):
        raise ValueError('本次金额超过计划未完成金额，请调整计划或拆分登记')
    if plan['source_kind'] == 'project':
        if kind != 'receipt':
            raise ValueError('项目待收计划必须关联真实业务回款')
        allocated = conn.execute('''SELECT COALESCE(SUM(allocated_amount_minor),0)
            FROM receipt_allocations WHERE receipt_id=? AND project_id=? AND status='active' ''',
            (source_id, plan['source_id'])).fetchone()[0]
        already = conn.execute('''SELECT COALESCE(SUM(t.amount_minor),0)
            FROM fund_transactions t JOIN fund_plans p ON p.id=t.plan_id
            WHERE t.source_kind='receipt' AND t.source_id=? AND t.status='active'
              AND p.source_kind='project' AND p.source_id=?''', (source_id, plan['source_id'])).fetchone()[0]
        if amount + already > allocated:
            raise ValueError('本次金额超过该回款实际归属此项目的金额')
    elif plan['source_kind'] and (kind, source_id, period) != (
            plan['source_kind'], plan['source_id'], plan['source_period']):
        raise ValueError('收付款来源与计划不一致')


@translate_constraints(
    duplicate="这笔资金流水已登记，请刷新后核对，不要重复录入",
    related="收付款关联的账户或计划已变化，请刷新后重选",
    invalid="收付款内容不符合记录规则，请核对后重试",
)
def record_transaction(data):
    """One transaction per actual movement; a transfer is one indivisible row."""
    request_key = _text(data.get('request_key'), '本次登记标识')
    amount = _money(data.get('amount'))
    transaction_date = _date(data.get('transaction_date'))
    category = data.get('category')
    source_id = int(data.get('source_id') or 0) or None
    period = str(data.get('source_period') or '')
    from_id = int(data.get('from_account_id') or 0) or None
    to_id = int(data.get('to_account_id') or 0) or None
    plan_id = int(data.get('plan_id') or 0) or None
    with _connection(write=True) as conn:
        previous = conn.execute('SELECT * FROM fund_transactions WHERE request_key=?', (request_key,)).fetchone()
        if previous:
            fingerprint = (amount, transaction_date, category, from_id, to_id, source_id, period, plan_id)
            old = tuple(previous[k] for k in ('amount_minor','transaction_date','category',
                        'from_account_id','to_account_id','source_id','source_period','plan_id'))
            notes_match = previous['notes'] == str(data.get('notes') or '').strip()
            party_matches = category in SOURCE_LABELS or previous['counterparty'] == str(data.get('counterparty') or '').strip()
            if fingerprint != old or not notes_match or not party_matches or previous['status'] != 'active':
                raise ValueError('登记标识已使用，请刷新后重新操作')
            return previous['id']
        if category == 'transfer':
            if not from_id or not to_id or from_id == to_id or plan_id or source_id:
                raise ValueError('内部转账必须选择两个不同账户，不能关联收付计划')
            direction = 'transfer'
        else:
            direction = 'in' if category == 'receipt' else 'out'
            if category in OTHER_CATEGORIES:
                direction = OTHER_CATEGORIES[category][1]
            elif category not in SOURCE_LABELS:
                raise ValueError('收支分类无效')
            if direction == 'in' and (not to_id or from_id):
                raise ValueError('到账必须仅选择收款账户')
            if direction == 'out' and (not from_id or to_id):
                raise ValueError('付款必须仅选择付款账户')
        for account_id in (from_id, to_id):
            if account_id:
                account = _account(conn, account_id)
                if account['status'] != 'active' or transaction_date < account['opening_date']:
                    raise ValueError('账户已停用或收支日期早于账户启用日期，请核对期初与日期')
        kind, source_label = '', ''
        counterparty = str(data.get('counterparty') or '').strip()
        if category in SOURCE_LABELS:
            kind = category
            source = _source(conn, kind, source_id, period)
            if kind != 'receipt' and not source['verified']:
                opening = data.get('opening') or {}
                _verify_source(conn, kind, source_id, period,
                               _money(opening.get('settled_amount'), zero=True),
                               opening.get('payee'), opening.get('reason'))
                source = _source(conn, kind, source_id, period)
            if amount > source['remaining_minor']:
                raise ValueError('本次金额超过尚未登记的业务金额，不能重复或超额收付款')
            if transaction_date < source['source_date']:
                raise ValueError('付款日期早于业务日期；预付款请先核实业务单据，不要冲减未发生的成本')
            if kind == 'receipt' and source['legacy_status'] != '票据' and transaction_date != source['source_date']:
                raise ValueError('实际到账日期应与原回款日期一致；若原记录错误，请先修改回款')
            source_label, counterparty = source['label'], source['counterparty']
        elif source_id or period:
            raise ValueError('其他收支不能附带业务来源')
        if category != 'transfer':
            counterparty = _text(counterparty, '往来单位 / 人员')
        _validate_plan(conn, plan_id, direction, amount, kind, source_id, period)
        transaction_id = conn.execute('''INSERT INTO fund_transactions
            (request_key,from_account_id,to_account_id,transaction_date,amount_minor,category,
             counterparty,source_kind,source_id,source_period,source_label,plan_id,notes,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (request_key,from_id,to_id,transaction_date,amount,category,counterparty,kind,source_id,
             period,source_label,plan_id,str(data.get('notes') or '').strip(),now())).lastrowid
        _audit(conn, 'transaction', transaction_id, 'create', {},
               {'amount_minor': amount, 'category': category, 'source_id': source_id}, '登记真实收支')
        return transaction_id


@_funds_write
def void_transaction(transaction_id, reason):
    reason = _text(reason, '作废原因（只用于录错，真实退款应另记收支）')
    with _connection(write=True) as conn:
        row = conn.execute('SELECT * FROM fund_transactions WHERE id=?', (transaction_id,)).fetchone()
        if not row or row['status'] != 'active':
            raise ValueError('流水不存在或已作废')
        for account_id in (row['from_account_id'],row['to_account_id']):
            if account_id and _account(conn,account_id)['status'] != 'active':
                raise ValueError('流水涉及已停用账户，请先启用该账户，再更正资金记录')
        conn.execute("UPDATE fund_transactions SET status='void',void_reason=?,voided_at=? WHERE id=?",
                     (reason, now(), transaction_id))
        _audit(conn, 'transaction', transaction_id, 'void', dict(row), {'status': 'void'}, reason)


@_funds_write
def link_transaction_plan(transaction_id, plan_id, reason):
    """Changing a plan association never rewrites actual movement or business facts."""
    reason = _text(reason,'关联调整说明')
    with _connection(write=True) as conn:
        row = conn.execute('SELECT * FROM fund_transactions WHERE id=?',(transaction_id,)).fetchone()
        if not row or row['status'] != 'active' or row['category']=='transfer':
            raise ValueError('只能为有效的实际收付款关联计划；内部转账不属于收付计划')
        if row['plan_id']==plan_id:
            return
        conn.execute('UPDATE fund_transactions SET plan_id=NULL WHERE id=?',(transaction_id,))
        direction = 'in' if row['to_account_id'] else 'out'
        _validate_plan(conn,plan_id,direction,row['amount_minor'],row['source_kind'],row['source_id'],row['source_period'])
        conn.execute('UPDATE fund_transactions SET plan_id=? WHERE id=?',(plan_id,transaction_id))
        _audit(conn,'transaction',transaction_id,'plan_link',{'plan_id':row['plan_id']},{'plan_id':plan_id},reason)


def list_transactions(*, start=None, end=None, account_id=None, include_void=False):
    where, params = [], []
    if not include_void:
        where.append("t.status='active'")
    if start:
        where.append('t.transaction_date>=?')
        params.append(_date(start))
    if end:
        where.append('t.transaction_date<=?')
        params.append(_date(end))
    if start and end and start > end:
        raise ValueError('开始日期不能晚于结束日期')
    if account_id:
        where.append('(t.from_account_id=? OR t.to_account_id=?)')
        params.extend((account_id, account_id))
    with _connection() as conn:
        return [dict(row) for row in conn.execute('''SELECT t.*,a.name AS from_name,b.name AS to_name
            FROM fund_transactions t LEFT JOIN fund_accounts a ON a.id=t.from_account_id
            LEFT JOIN fund_accounts b ON b.id=t.to_account_id WHERE ''' + (' AND '.join(where) or '1=1') +
            ' ORDER BY t.transaction_date DESC,t.id DESC', params)]


@_funds_write
def save_plan(data, plan_id=None):
    direction = data.get('direction')
    if direction not in ('in', 'out'):
        raise ValueError('请选择待收或待付')
    title = _text(data.get('title'), '计划事项')
    amount = _money(data.get('amount'))
    due_date = _date(data['due_date'], future=True) if data.get('due_date') else None
    kind = data.get('source_kind') or ''
    source_id = int(data.get('source_id') or 0) or None
    period = str(data.get('source_period') or '')
    with _connection(write=True) as conn:
        previous = conn.execute('SELECT * FROM fund_plans WHERE id=?', (plan_id,)).fetchone() if plan_id else None
        if plan_id and (not previous or previous['status'] != 'planned'):
            raise ValueError('计划已不存在或已取消')
        paid = _plan_paid(conn, plan_id) if plan_id else 0
        if amount < paid:
            raise ValueError('计划金额不能小于已关联的实际收付款')
        if previous and paid and (direction, kind, source_id, period) != (
                previous['direction'], previous['source_kind'], previous['source_id'], previous['source_period']):
            raise ValueError('已有流水的计划不能更换收支方向或业务来源')
        if kind == 'project':
            if direction != 'in' or period or not conn.execute('SELECT 1 FROM projects WHERE id=?', (source_id,)).fetchone():
                raise ValueError('项目待收来源无效')
            # Existing receipts are already business facts, whether or not an account
            # has been assigned. Do not turn them into a second future inflow.
            remaining = _project_receivable(conn,source_id)
            planned = _planned_remaining(conn,kind,source_id,period,plan_id or 0)
            if amount - paid + planned > remaining:
                raise ValueError('收款计划合计超过该项目已确认未回款金额；已回款请到资金流水补关联')
        elif kind in ('purchase', 'cost', 'wage'):
            source = _source(conn, kind, source_id, period)
            if direction != 'out' or not source['verified']:
                raise ValueError('请先核实该笔应付，再制定付款计划')
            planned = _planned_remaining(conn,kind,source_id,period,plan_id or 0)
            if amount - paid + planned > source['remaining_minor']:
                raise ValueError('付款计划合计超过该笔尚未支付金额')
        elif kind or source_id or period:
            raise ValueError('计划来源无效')
        values = (direction,title,str(data.get('counterparty') or '').strip(),amount,due_date,kind,source_id,
                  period,str(data.get('notes') or '').strip())
        if plan_id:
            conn.execute('''UPDATE fund_plans SET direction=?,title=?,counterparty=?,amount_minor=?,due_date=?,
                source_kind=?,source_id=?,source_period=?,notes=?,updated_at=? WHERE id=?''', (*values,now(),plan_id))
        else:
            plan_id = conn.execute('''INSERT INTO fund_plans
                (direction,title,counterparty,amount_minor,due_date,source_kind,source_id,source_period,
                 notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)''', (*values,now(),now())).lastrowid
        _audit(conn,'plan',plan_id,'save',dict(previous or {}),dict(data),'调整收付计划，不产生现金')
        return plan_id


@_funds_write
def cancel_plan(plan_id, reason):
    reason = _text(reason, '取消原因')
    with _connection(write=True) as conn:
        row = conn.execute('SELECT * FROM fund_plans WHERE id=?', (plan_id,)).fetchone()
        if not row or row['status'] != 'planned':
            raise ValueError('计划不存在或已取消')
        conn.execute("UPDATE fund_plans SET status='cancelled',updated_at=? WHERE id=?", (now(), plan_id))
        _audit(conn,'plan',plan_id,'cancel',dict(row),{'status': 'cancelled'},reason)


def list_plans(*, include_closed=False):
    with _connection() as conn:
        rows = [dict(row) for row in conn.execute('SELECT * FROM fund_plans ORDER BY due_date IS NULL,due_date,id')]
        today = date.today().isoformat()
        source_maps = {}
        for row in rows:
            row['paid_minor'] = _plan_paid(conn, row['id'])
            row['remaining_minor'] = row['amount_minor'] - row['paid_minor']
            if row['status'] == 'cancelled':
                row['state'] = '已取消'
            elif row['remaining_minor'] == 0:
                row['state'] = '已完成'
            elif not row['due_date']:
                row['state'] = '待安排'
            elif row['due_date'] < today:
                row['state'] = '已逾期'
            else:
                row['state'] = '已安排'
            row['source_warning'] = ''
            if row['state'] in ('已完成','已取消') or not row['source_kind']:
                continue
            kind = row['source_kind']
            if kind=='project':
                remaining = _project_receivable(conn,row['source_id'])
            else:
                if kind not in source_maps:
                    source_maps[kind] = {(s['id'],s['period']):s for s in _decorate_sources(conn,kind)}
                source = source_maps[kind].get((row['source_id'],row['source_period']))
                if not source:
                    row['source_warning'] = '原业务已作废，请取消计划'
                    row['state'] = '需核对来源'
                    continue
                remaining = source['remaining_minor']
            if _planned_remaining(conn,kind,row['source_id'],row['source_period']) > remaining:
                row['source_warning'] = '计划超出当前未收未付，请补关联已有流水或调整计划'
                row['state'] = '需核对来源'
        return rows if include_closed else [r for r in rows if r['state'] not in ('已取消','已完成')]


@_funds_write
def reconcile_account(account_id, balance_date, actual_amount, notes):
    balance_date = _date(balance_date)
    actual = _money(actual_amount, zero=True)
    notes = _text(notes, '核对依据（银行余额 / 现金盘点等）')
    with _connection(write=True) as conn:
        book = _balance(conn, account_id, balance_date)
        if book is None:
            raise ValueError('核对日期不能早于账户启用日期')
        row_id = conn.execute('''INSERT INTO fund_reconciliations
            (account_id,balance_date,actual_minor,book_minor,notes,created_at) VALUES (?,?,?,?,?,?)''',
            (account_id,balance_date,actual,book,notes,now())).lastrowid
        _audit(conn,'reconciliation',row_id,'create',{},
               {'actual_minor': actual,'book_minor': book},notes)
        return row_id


def list_reconciliations():
    with _connection() as conn:
        rows = [dict(row) for row in conn.execute('''SELECT r.*,a.name AS account_name
            FROM fund_reconciliations r JOIN fund_accounts a ON a.id=r.account_id
            ORDER BY r.balance_date DESC,r.id DESC''')]
        for row in rows:
            row['current_book_minor'] = _balance(conn, row['account_id'], row['balance_date'])
            row['difference_minor'] = row['actual_minor'] - row['book_minor']
            row['needs_review'] = row['current_book_minor'] != row['book_minor']
        return rows


def get_overview():
    today = date.today().isoformat()
    with _connection() as conn:
        accounts = [dict(row) for row in conn.execute('SELECT * FROM fund_accounts ORDER BY id')]
        for row in accounts:
            row['balance_minor'] = _balance(conn,row['id'],today)
        totals = conn.execute('''SELECT
            COALESCE(SUM(CASE WHEN from_account_id IS NULL THEN amount_minor ELSE 0 END),0) AS income_minor,
            COALESCE(SUM(CASE WHEN to_account_id IS NULL THEN amount_minor ELSE 0 END),0) AS expense_minor
            FROM fund_transactions WHERE status='active' AND transaction_date BETWEEN ? AND ?''',
            (today[:7]+'-01',today)).fetchone()
        return {'accounts': accounts, 'tracking_start': _start_date(conn),
                'balance_minor': sum(row['balance_minor'] or 0 for row in accounts),
                'income_minor': totals['income_minor'], 'expense_minor': totals['expense_minor'],
                'negative_accounts': sum((row['balance_minor'] or 0) < 0 for row in accounts)}
