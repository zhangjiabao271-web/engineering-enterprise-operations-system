"""Local proactive checks with manual daily completion and traceable follow-up."""
import hashlib
import json
from datetime import datetime, timedelta, timezone, date

from db.connection import db_read, db_transaction, use_read_snapshot
from db.business_facts import COST_FACTS_SQL
from services import collection_service, attachment_service

DAILY = {'worklogs': '今日工天核对', 'purchases': '今日采购核对'}


def clock():
    return datetime.now(timezone(timedelta(hours=8)))


def today():
    return clock().date().isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     default=str).encode()).hexdigest()


def _day(value):
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError('请选择有效的自检日期') from None
    if parsed.isoformat() != value or value > today():
        raise ValueError('不能核对未来日期')
    return value


def _daily_basis(conn, kind, day):
    table, column = ('work_logs', 'work_date') if kind == 'worklogs' else ('purchase_orders', 'purchase_date')
    rows = [dict(row) for row in conn.execute(
        f"SELECT * FROM {table} WHERE {column}=? AND status='active' ORDER BY id", (day,))]
    count = len({row['worker_id'] for row in rows}) if kind == 'worklogs' else len(rows)
    return digest(rows), count, len(rows)


def _checks(conn, day):
    saved = {row['kind']: dict(row) for row in conn.execute(
        'SELECT * FROM daily_self_checks WHERE day=?', (day,))}
    result = []
    for kind, label in DAILY.items():
        basis, count, records = _daily_basis(conn, kind, day)
        row = saved.get(kind, {'status': 'pending', 'basis': '', 'updated_at': None})
        changed = row['status'] in ('done', 'none') and row['basis'] != basis
        result.append({'kind': kind, 'day': day, 'label': label,
                       'status': 'pending' if changed else row['status'], 'saved_status': row['status'],
                       'changed_after_check': changed, 'count': count, 'record_count': records,
                       'basis': basis, 'updated_at': row['updated_at']})
    return result


def start_day():
    day = today()
    with db_transaction(immediate=True) as conn:
        for kind in DAILY:
            conn.execute('''INSERT OR IGNORE INTO daily_self_checks
                (day,kind,status,basis,updated_at) VALUES (?,?,'pending','',?)''',
                (day, kind, clock().isoformat(timespec='seconds')))
    return day


def set_check(kind, day, status, expected):
    if kind not in DAILY or status not in ('pending', 'done', 'none', 'later'):
        raise ValueError('自检项目或状态无效')
    _day(day)
    with db_transaction(immediate=True) as conn:
        basis, count, _ = _daily_basis(conn, kind, day)
        if basis != expected:
            raise ValueError('本日记录已变化，请刷新后重新核对')
        if status == 'none' and count:
            raise ValueError('当天已有有效记录，不能标记为无业务；请核对后选择已录完')
        old = conn.execute('SELECT status,basis FROM daily_self_checks WHERE day=? AND kind=?', (day, kind)).fetchone()
        if old and old['status'] == status and old['basis'] == basis:
            return {'changed': False}
        stamp = clock().isoformat(timespec='seconds')
        conn.execute('''INSERT INTO daily_self_checks VALUES (?,?,?,?,?)
            ON CONFLICT(day,kind) DO UPDATE SET status=excluded.status,basis=excluded.basis,
            updated_at=excluded.updated_at''', (day, kind, status, basis, stamp))
        conn.execute('INSERT INTO operating_check_history(kind,identity,action,created_at) VALUES (?,?,?,?)',
                     ('daily', day + ':' + kind, status, stamp))
    return {'changed': True}


def _issue(key, title, project_id, basis, action, *, amount=0, priority=3, note=''):
    return {'key': key, 'title': title, 'project_id': project_id, 'basis': digest(basis),
            'action': action, 'amount_minor': amount, 'priority': priority, 'note': note}


def _issues(conn):
    result = []
    project_statuses = dict(conn.execute('SELECT id,status FROM projects'))
    for row in collection_service.list_project_cases():
        row['project_status'] = project_statuses.get(row['project_id'])
        finished = row['project_status'] in ('已完工', '已关闭')
        if not row['needs_action_today'] and not finished:
            continue
        label = row['attention_status'] if row['needs_action_today'] else '完工仍待收款'
        result.append(_issue('collection:' + str(row['project_id']),
            f"{row['project_name']} · {label}", row['project_id'],
            {key: row.get(key) for key in ('receivable_minor','promised_date','next_followup_date','due_date','next_action','project_status')},
            {'kind':'collection','id':row['project_id']}, amount=row['receivable_minor'],
            priority=0 if label=='承诺逾期' else 1,
            note='来自实际应收及跟进记录；未约定到期日不认定为付款逾期。'))
    for row in conn.execute("SELECT * FROM contracts WHERE status='active' ORDER BY id"):
        attachments = attachment_service.list_attachments('contract', row['id'])
        if any(item['file_exists'] for item in attachments):
            continue
        projects = [item[0] for item in conn.execute('''SELECT project_id FROM contract_project_allocations
            WHERE contract_id=? AND status='active' ORDER BY project_id''', (row['id'],))]
        result.append(_issue('contract_attachment:'+str(row['id']), row['name']+' · 合同附件待补',
            projects[0] if len(projects)==1 else None, [row['updated_at'], projects, [(item['id'],item['file_exists']) for item in attachments]],
            {'kind':'attachment','entity':'contract','id':row['id']}, note='检查真实文件是否可用，不只检查附件数量。'))
        result[-1]['project_ids'] = projects
    for row in conn.execute("SELECT * FROM sales_invoices WHERE status='active' ORDER BY id"):
        attachments = attachment_service.list_attachments('invoice', row['id'])
        if any(item['file_exists'] for item in attachments):
            continue
        result.append(_issue('invoice_attachment:'+str(row['id']), '发票 '+row['invoice_no']+' · 附件待补',
            row['project_id'], [row['updated_at'], [(item['id'],item['file_exists']) for item in attachments]],
            {'kind':'attachment','entity':'invoice','id':row['id']}, amount=row['amount_minor'], priority=4,
            note='已有发票事实不变，仅补原始票据。'))
    for row in conn.execute(f'SELECT * FROM ({COST_FACTS_SQL}) WHERE project_id IS NULL'):
        result.append(_issue('unassigned:'+str(row['id']),
            str(row['category'])+' · 成本待归集', None, dict(row),
            {'kind':'route','route':'governance'}, amount=row['amount_minor'], priority=2,
            note='未指定项目的真实成本，不按名称猜归属。'))
    return sorted(result, key=lambda item:(item['priority'], -item['amount_minor'], item['key']))


def overview(project_id=None):
    day = today()
    with db_read() as conn, use_read_snapshot(conn):
        checks = _checks(conn, day)
        previous = []
        days = [row[0] for row in conn.execute('SELECT DISTINCT day FROM daily_self_checks WHERE day<? ORDER BY day DESC', (day,))]
        for old_day in days:
            previous.extend(row for row in _checks(conn, old_day) if row['status'] in ('pending','later'))
        all_issues = _issues(conn)
        states = {row['issue_key']: dict(row) for row in conn.execute('SELECT * FROM operating_attention_states')}
        active, hidden = [], []
        for issue in all_issues:
            if project_id and issue['project_id'] != int(project_id) and int(project_id) not in issue.get('project_ids', []):
                continue
            saved = states.get(issue['key'])
            state = saved['status'] if saved and saved['basis']==issue['basis'] else 'pending'
            if state=='snoozed' and saved['until_day'] <= day:
                state='pending'
            issue['state'] = state
            issue['until_day'] = saved['until_day'] if saved else None
            (hidden if state in ('ignored','snoozed') else active).append(issue)
        existing = {issue['key'] for issue in all_issues}
        resolved = [row for key,row in states.items() if key not in existing and row['status']=='processing'
                    and (not project_id or row['project_id']==int(project_id))]
        return {'day':day, 'checks':checks, 'previous_checks':previous, 'attention':active,
                'hidden':hidden, 'resolved':resolved, 'scope':'指定项目' if project_id else '全公司',
                'daily_scope':'每日录入自检为全公司范围；业务日期口径，不自动认定遗漏。',
                'generated_at':clock().isoformat(timespec='seconds')}


def set_attention(key, action, expected, until_day=None):
    if action not in ('processing','ignored','snoozed','pending'):
        raise ValueError('请选择有效跟进操作')
    with db_transaction(immediate=True) as conn:
        # Nested services share this transaction's reads; financial facts remain untouched.
        from db.connection import use_write_transaction
        with use_write_transaction(conn):
            issue = next((row for row in _issues(conn) if row['key']==key), None)
        if issue is None or issue['basis'] != expected:
            raise ValueError('提醒依据已变化或问题已解决，请刷新后核对')
        if action=='snoozed':
            try:
                parsed = date.fromisoformat(until_day)
            except (ValueError,TypeError):
                raise ValueError('请选择稍后提醒日期') from None
            if not today() < parsed.isoformat() <= (clock().date()+timedelta(days=90)).isoformat():
                raise ValueError('提醒日期须在未来90天内')
        stamp = clock().isoformat(timespec='seconds')
        if action=='pending':
            conn.execute('DELETE FROM operating_attention_states WHERE issue_key=?', (key,))
        else:
            conn.execute('''INSERT INTO operating_attention_states VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(issue_key) DO UPDATE SET title=excluded.title,project_id=excluded.project_id,
                basis=excluded.basis,status=excluded.status,until_day=excluded.until_day,updated_at=excluded.updated_at''',
                (key,issue['project_id'],issue['title'],issue['basis'],action,until_day if action=='snoozed' else None,stamp))
        conn.execute('INSERT INTO operating_check_history(kind,identity,action,created_at) VALUES (?,?,?,?)',
                     ('attention',key,action,stamp))
    return {'action':issue['action'], 'changed':True}
