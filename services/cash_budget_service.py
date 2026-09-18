"""Cash runway estimates anchored to an explicit real-money checkpoint."""
from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import json

from db.connection import db_read, db_transaction
from services._common import now
from services.expense_categories import CATEGORIES, DAILY, suggestion


def connection(write=False):
    if write:
        return db_transaction(immediate=True)
    return db_read()


def money(value, zero=False):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or (not zero and number == 0):
            raise ValueError
        result = int((number * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        if not zero and result == 0:
            raise ValueError
        return result
    except (ValueError, InvalidOperation, OverflowError):
        raise ValueError('请填写有效金额，支出金额必须大于零') from None


def parse_date(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ValueError('请选择有效日期') from None


def audit(conn, entity, identity, before, after):
    conn.execute('INSERT INTO cash_budget_audit(entity,entity_id,before_json,after_json,created_at) VALUES(?,?,?,?,?)',
                 (entity, identity, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), now()))


def save_snapshot(data):
    day = parse_date(data['balance_date'])
    if day > date.today():
        raise ValueError('实际可用资金不能填写未来日期')
    amount, reserve = money(data['amount'], True), money(data['reserve'], True)
    with connection(True) as conn:
        identity = conn.execute('INSERT INTO cash_budget_snapshots(balance_date,amount_minor,reserve_minor,notes,created_at) VALUES(?,?,?,?,?)',
                                (day.isoformat(), amount, reserve, data.get('notes', ''), now())).lastrowid
        audit(conn, 'snapshot', identity, {}, data)
    return identity


def save_rule(data, rule_id=None):
    title = str(data.get('title') or '').strip()
    category = data.get('category')
    cadence = data.get('cadence')
    if not title or category not in CATEGORIES or cadence not in ('daily', 'monthly', 'yearly'):
        raise ValueError('请填写名称、费用用途和预测方式')
    first = parse_date(data['next_date']).isoformat()
    end = parse_date(data['end_date']).isoformat() if data.get('end_date') else None
    if end and end < first:
        raise ValueError('结束日期不能早于起始日期')
    average = int(data.get('use_average', 0))
    if average and (category not in DAILY or cadence != 'daily'):
        raise ValueError('两月均值仅用于燃油、水电、工人伙食和家庭日常的月预算')
    amount = money(data['amount'])
    if average:
        history = next(r for r in historical_suggestions() if r['category'] == category)
        if history['amount_minor'] is None or history['amount_minor'] <= 0:
            raise ValueError('请先确认前两个完整月份的记录，或暂用手动预算')
        amount = history['amount_minor']
    values = (title, category, amount, cadence, first, end,
              int(data.get('active', 1)), str(data.get('notes', '')), now())
    with connection(True) as conn:
        before = dict(conn.execute('SELECT * FROM cash_budget_rules WHERE id=?', (rule_id,)).fetchone() or {})
        if rule_id and not before:
            raise ValueError('预算项目不存在')
        other = conn.execute('SELECT id FROM cash_budget_rules WHERE category=? AND id<>?', (category, rule_id or 0)).fetchone()
        if other:
            raise ValueError('该用途已有预算，请修改原预算，避免重复预留')
        if rule_id:
            conn.execute('UPDATE cash_budget_rules SET title=?,category=?,amount_minor=?,cadence=?,next_date=?,end_date=?,active=?,notes=?,updated_at=? WHERE id=?', (*values, rule_id))
        else:
            rule_id = conn.execute('INSERT INTO cash_budget_rules(title,category,amount_minor,cadence,next_date,end_date,active,notes,updated_at) VALUES(?,?,?,?,?,?,?,?,?)', values).lastrowid
        conn.execute('UPDATE cash_budget_rules SET use_average=? WHERE id=?', (average, rule_id))
        audit(conn, 'rule', rule_id, before, data)
    return rule_id


def list_rules():
    with connection() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM cash_budget_rules ORDER BY category')]


def confirm_month(month):
    day = parse_date(month + '-01')
    if day >= date.today().replace(day=1):
        raise ValueError('只能确认已经结束的完整月份')
    with connection(True) as conn:
        conn.execute('INSERT OR REPLACE INTO cash_budget_months(month,confirmed_at) VALUES(?,?)', (month, now()))
        audit(conn, 'month', None, {}, {'month': month, 'meaning': '用户确认该月日常开支已录完整，包括真实零支出'})


def historical_suggestions(as_of=None):
    day = parse_date(as_of) if as_of else date.today()
    previous = day.replace(day=1) - timedelta(days=1)
    earlier = previous.replace(day=1) - timedelta(days=1)
    months = [earlier.strftime('%Y-%m'), previous.strftime('%Y-%m')]
    with connection() as conn:
        confirmed = {r[0] for r in conn.execute('SELECT month FROM cash_budget_months')}
        rows = [dict(r) for r in conn.execute("SELECT * FROM cost_entries WHERE status='active' AND source_type IN ('manual','legacy_manual')")]
    totals = {category: {m: 0 for m in months} for category in DAILY}
    for row in rows:
        category, certain = suggestion(row)
        month = row['cost_date'][:7]
        if certain and category in totals and month in months:
            totals[category][month] += row['amount_minor']
    ready = all(m in confirmed for m in months)
    return [{'category': category, 'months': months, 'totals': values,
             'amount_minor': (sum(values.values()) + 1) // 2 if ready else None,
             'state': '两个月均值（已确认完整）' if ready else '缺完整月份确认，不把漏记当零'}
            for category, values in sorted(totals.items())]


def classification_rows():
    with connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM cost_entries WHERE status='active' AND source_type IN ('manual','legacy_manual') ORDER BY cost_date DESC,id DESC")]
    for row in rows:
        row['suggested'], row['certain'] = suggestion(row)
    return rows


def classify(cost_id, category):
    from services import cost_service
    if category not in CATEGORIES:
        raise ValueError('请选择具体用途')
    row = cost_service.get_cost_entry(cost_id)
    if not row:
        raise ValueError('费用不存在')
    cost_service.update_cost_details(cost_id, {
        'category': category, 'cost_date': row['cost_date'], 'cost_no': row['cost_no'],
        'counterparty_name': row['counterparty_name_snapshot'], 'vehicle_no': row['vehicle_no'],
        'notes': row['notes'],
    })


def sources():
    """Pending facts do not require creating a second cash ledger."""
    from services import funds_service, collection_service
    result = []
    with connection() as conn:
        purchases = [dict(r) for r in conn.execute("SELECT * FROM purchase_orders WHERE status='active' AND payment_status='未付款'")]
        paid = {r[0]: r[1] for r in conn.execute("SELECT source_id,SUM(amount_minor) FROM fund_transactions WHERE source_kind='purchase' AND status='active' GROUP BY source_id")}
        prior = {r[0]: r[1] for r in conn.execute("SELECT source_id,settled_minor FROM fund_source_openings WHERE source_kind='purchase'")}
    for row in purchases:
        remaining = max(0, row['total_amount_cents'] - paid.get(row['id'], 0) - prior.get(row['id'], 0))
        if remaining:
            result.append({'source_kind': 'purchase', 'source_id': row['id'], 'source_period': '', 'direction': 'out',
                           'title': row['order_no'] + ' · ' + (row['merchant_name_snapshot'] or ''), 'amount_minor': remaining})
    for row in collection_service.list_project_cases():
        remaining = row['receivable_minor']
        if remaining > 0:
            result.append({'source_kind': 'project', 'source_id': row['project_id'], 'source_period': '',
                           'direction': 'in', 'title': row['project_name'], 'amount_minor': remaining})
    # Workday costs are not proof of unpaid wages; only expose them for manual planning.
    for row in funds_service.list_sources('wage'):
        if row['remaining_minor'] > 0:
            result.append({'source_kind': 'wage', 'source_id': row['id'], 'source_period': row['period'],
                           'direction': 'out', 'title': row['label'] + '（发薪情况待核实）',
                           'amount_minor': row['remaining_minor']})
    return result


def save_event(data, event_id=None):
    title = str(data.get('title') or '').strip()
    direction = data.get('direction')
    if not title or direction not in ('in', 'out'):
        raise ValueError('请填写事项和收付方向')
    amount = money(data['amount'])
    due = parse_date(data['due_date']).isoformat() if data.get('due_date') else None
    kind, identity, period = data.get('source_kind', ''), data.get('source_id'), data.get('source_period', '')
    if kind:
        source = next((r for r in sources() if (r['source_kind'], r['source_id'], r['source_period']) == (kind, identity, period)), None)
        if not source or source['direction'] != direction or amount > source['amount_minor']:
            raise ValueError('计划超过当前来源余额，或来源已不存在，请刷新')
    elif identity or period:
        raise ValueError('来源不完整')
    with connection(True) as conn:
        before = dict(conn.execute('SELECT * FROM cash_budget_events WHERE id=?', (event_id,)).fetchone() or {})
        if event_id and before.get('status') != 'planned':
            raise ValueError('该计划已关闭，不能修改')
        values = (title, direction, amount, due, kind, identity, period, str(data.get('notes', '')), now())
        if event_id:
            conn.execute('UPDATE cash_budget_events SET title=?,direction=?,amount_minor=?,due_date=?,source_kind=?,source_id=?,source_period=?,notes=?,updated_at=? WHERE id=?', (*values, event_id))
        else:
            event_id = conn.execute('INSERT INTO cash_budget_events(title,direction,amount_minor,due_date,source_kind,source_id,source_period,notes,updated_at) VALUES(?,?,?,?,?,?,?,?,?)', values).lastrowid
        audit(conn, 'event', event_id, before, data)
    return event_id


def close_event(event_id, status):
    if status not in ('done', 'cancelled'):
        raise ValueError('状态无效')
    with connection(True) as conn:
        before = dict(conn.execute('SELECT * FROM cash_budget_events WHERE id=?', (event_id,)).fetchone() or {})
        if not before:
            raise ValueError('计划不存在')
        conn.execute('UPDATE cash_budget_events SET status=?,updated_at=? WHERE id=?', (status, now(), event_id))
        audit(conn, 'event', event_id, before, {'status': status})


def list_events(source_rows=None):
    with connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM cash_budget_events WHERE status='planned' ORDER BY due_date IS NULL,due_date,id")]
    if source_rows is None:
        source_rows = sources()
    source_map = {(r['source_kind'], r['source_id'], r['source_period']): r for r in source_rows}
    for row in rows:
        row['warning'] = ''
        row['remaining_minor'] = row['amount_minor']
        if row['source_kind']:
            source = source_map.get((row['source_kind'], row['source_id'], row['source_period']))
            row['remaining_minor'] = min(row['amount_minor'], source['amount_minor']) if source else 0
            if not source:
                row['warning'] = '来源已结清或状态改变，请核对'
    return rows


def _month_shift(day, offset):
    number = day.year * 12 + day.month - 1 + offset
    year, month = divmod(number, 12)
    return date(year, month + 1, min(day.day, monthrange(year, month + 1)[1]))


def forecast(horizon=180):
    if not 30 <= horizon <= 730:
        raise ValueError('预测范围为30至730天')
    with connection() as conn:
        snapshot = dict(conn.execute('SELECT * FROM cash_budget_snapshots ORDER BY balance_date DESC,id DESC LIMIT 1').fetchone() or {})
        costs = [dict(r) for r in conn.execute("SELECT * FROM cost_entries WHERE status='active' AND source_type IN ('manual','legacy_manual')")]
    pending = sources()
    events = list_events(pending)
    rules = [r for r in list_rules() if r['active']]
    warnings = ['预测不是银行余额；未排期支出、未补齐工资及新增项目开销可能使资金更早不足。']
    if not snapshot:
        return {'snapshot': None, 'events': events, 'pending': pending, 'warnings': warnings, 'daily': []}
    anchor = parse_date(snapshot['balance_date'])
    if anchor < date.today():
        warnings.append('可用资金不是今天更新的；以下仍以余额日期为起点，请先更新实际可用资金。')
    if not rules:
        warnings.append('尚未设置日常和固定开支，不能据此判断资金足够。')
    end = anchor + timedelta(days=horizon)
    outflow, inflow = {}, {}
    def add(target, day, amount):
        if anchor < day <= end:
            target[day] = target.get(day, 0) + amount
    normalized = []
    for row in costs:
        category, certain = suggestion(row)
        if certain:
            normalized.append((row, category))
    averages = {r['category']: r for r in historical_suggestions(anchor.isoformat())}
    for rule in rules:
        if rule['use_average']:
            history = averages.get(rule['category'])
            if history and history['amount_minor'] is not None:
                rule['amount_minor'] = history['amount_minor']
            else:
                warnings.append(f"{rule['title']}缺少两个完整月，暂沿用最后确认的预算。")
        first = parse_date(rule['next_date'])
        last = parse_date(rule['end_date']) if rule['end_date'] else end
        # Cost dates are spending dates for these user-confirmed expense records.
        def spent(begin, finish):
            return sum(r['amount_minor'] for r, category in normalized if category == rule['category']
                       and begin.isoformat() <= r['cost_date'] <= min(finish, anchor).isoformat())
        if rule['cadence'] == 'daily':
            month = anchor.replace(day=1)
            while month <= end:
                month_end = month.replace(day=monthrange(month.year, month.month)[1])
                start = max(anchor + timedelta(days=1), month, first)
                finish = min(month_end, last)
                remaining = max(0, rule['amount_minor'] - spent(month, month_end))
                if start <= finish:
                    days = (finish - start).days + 1
                    prorated = (rule['amount_minor'] * days + month_end.day // 2) // month_end.day
                    base, extra = divmod(min(remaining, prorated), days)
                    for i in range(days):
                        add(outflow, start + timedelta(days=i), base + (i < extra))
                month = _month_shift(month, 1)
        else:
            step = 12 if rule['cadence'] == 'yearly' else 1
            offset = 0
            while True:
                due = _month_shift(first, offset)
                if due > min(end, last):
                    break
                period_start = due.replace(day=1)
                period_end = _month_shift(period_start, step) - timedelta(days=1)
                if period_end >= anchor:
                    remaining = max(0, rule['amount_minor'] - spent(period_start, period_end))
                    if due <= anchor and remaining:
                        warnings.append(f"{rule['title']}本期未匹配到足额支出，保守预留在余额日次日。")
                    add(outflow, max(due, anchor + timedelta(days=1)), remaining)
                offset += step
    undated = 0
    for event in events:
        amount = event['remaining_minor']
        if not event['due_date']:
            if event['direction'] == 'out':
                undated += amount
            continue
        due = parse_date(event['due_date'])
        if due <= anchor:
            warnings.append(f"{event['title']}计划日期已到，需确认是否完成。")
            if event['direction'] == 'in':
                continue  # An overdue promise is not available cash.
            due = anchor + timedelta(days=1)
        add(inflow if event['direction'] == 'in' else outflow, due, amount)
    scheduled = {(r['source_kind'], r['source_id'], r['source_period']): r['remaining_minor'] for r in events}
    undated += sum(max(0, r['amount_minor'] - scheduled.get((r['source_kind'], r['source_id'], r['source_period']), 0))
                   for r in pending if r['source_kind'] == 'purchase')
    if undated:
        warnings.append(f'未排期确定支出 {undated / 100:,.2f} 元：计入预留，但无法计入具体日期预测。')
    balance = expected = snapshot['amount_minor']
    first_zero = anchor.isoformat() if balance <= 0 else None
    expected_zero = first_zero
    first_warning = anchor.isoformat() if balance < snapshot['reserve_minor'] else None
    daily = []
    for i in range(1, horizon + 1):
        day = anchor + timedelta(days=i)
        paid, received = outflow.get(day, 0), inflow.get(day, 0)
        balance -= paid
        expected += received - paid
        if balance <= 0 and first_zero is None:
            first_zero = day.isoformat()
        if balance < snapshot['reserve_minor'] and first_warning is None:
            first_warning = day.isoformat()
        if expected <= 0 and expected_zero is None:
            expected_zero = day.isoformat()
        daily.append({'date': day.isoformat(), 'out_minor': paid, 'in_minor': received,
                      'balance_minor': balance, 'expected_minor': expected})
    # Unknown due dates must not make the minimum reserve look artificially small.
    need = snapshot['reserve_minor'] + undated + sum(r['out_minor'] for r in daily[:30])
    return {'snapshot': snapshot, 'daily': daily, 'events': events, 'pending': pending, 'warnings': warnings,
            'reserve30_minor': need, 'available_minor': snapshot['amount_minor'] - need,
            'undated_minor': undated, 'zero_date': first_zero, 'warning_date': first_warning,
            'expected_zero_date': expected_zero}
