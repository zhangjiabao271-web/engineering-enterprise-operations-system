"""Local evidence lifecycle; only explicit confirmation may create fund plans."""

import json
from pathlib import Path

from db.connection import db_read, db_transaction
from services._common import now
from services._constraint_errors import translate_constraints
from services.document_cashflow_extract import (
    ENGINE_VERSION, KINDS, LIMIT_BYTES, digest, extract_rows,
    timeline, validate_fragments,
)
from services.document_cashflow_semantics import understanding_from_fragments

_write = translate_constraints(
    duplicate='资料或计划已存在，请刷新核对，避免重复确认',
    related='合同、项目或来源已变化，请刷新重新分析',
    invalid='资料分析记录不符合保存条件，请核对输入',
)


def _scope(conn, contract_id, project_id):
    if contract_id is None and project_id is None:
        raise ValueError('请选择现有合同或项目')
    context = {'contract': None, 'project': None, 'links': []}
    if contract_id is not None:
        row = conn.execute('SELECT * FROM contracts WHERE id=?', (contract_id,)).fetchone()
        if not row or row['status'] == 'void':
            raise ValueError('合同不存在或已作废')
        context['contract'] = dict(row)
        context['links'] = [dict(row) for row in conn.execute(
            'SELECT * FROM contract_project_allocations WHERE contract_id=? ORDER BY id',
            (contract_id,))]
    if project_id is not None:
        row = conn.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        if not row:
            raise ValueError('项目不存在')
        context['project'] = dict(row)
    if contract_id is not None and project_id is not None:
        linked = any(link['project_id'] == project_id and link['status'] == 'active'
                     for link in context['links'])
        settled = conn.execute('SELECT 1 FROM settlements WHERE contract_id=? AND project_id=? '
                               "AND status='active' LIMIT 1", (contract_id, project_id)).fetchone()
        if not linked and not settled:
            raise ValueError('合同与项目没有有效关系，不能将资料分配到该项目')
    return context


def scope_label(contract_id=None, project_id=None):
    with db_read() as conn:
        context = _scope(conn, contract_id, project_id)
    labels = []
    if context['contract']:
        labels.append('合同「' + context['contract']['contract_no'] + '」')
    if context['project']:
        labels.append('项目「' + context['project']['name'] + '」')
    else:
        labels.append('整份合同口径，未分配到某项目')
    return ' / '.join(labels)


def _understanding_context(conn, contract_id, project_id):
    scope = _scope(conn, contract_id, project_id)
    contract, project = scope['contract'], scope['project']
    linked = []
    if contract:
        linked = [dict(row) for row in conn.execute('''SELECT id,name FROM projects WHERE id IN (
            SELECT project_id FROM contract_project_allocations WHERE contract_id=? AND status='active'
            UNION SELECT project_id FROM settlements WHERE contract_id=? AND status='active') ORDER BY id''',
            (contract_id, contract_id))]
    entities = [row[0] for row in conn.execute('SELECT name FROM operating_entities WHERE active=1 ORDER BY id')]
    assigned = []
    for record_type, record_id in (('contract', contract_id), ('project', project_id)):
        if record_id:
            assigned.extend(row[0] for row in conn.execute('''SELECT e.name FROM record_entities r
                JOIN operating_entities e ON e.id=r.entity_id WHERE r.record_type=? AND r.record_id=?''',
                (record_type, record_id)))
    context = {
        'our_operating_entities': entities, 'assigned_entities': list(dict.fromkeys(assigned)),
        'contract': ({key: contract.get(key) for key in
                      ('contract_no', 'name', 'customer_name', 'contract_type', 'pricing_mode')} if contract else None),
        'project': ({key: project.get(key) for key in ('name', 'project_code', 'business_mode')} if project else None),
        'contract_projects': linked,
        'scope_rule': '整份合同口径' if not project else '只分析当前项目；整份合同金额不可直接套入多项目中的单一项目',
    }
    return context, digest({'scope': scope, 'understanding_context': context})


def understanding_context(contract_id=None, project_id=None):
    """Minimal purpose context only: no ledger rows, account numbers or credentials."""
    with db_read() as conn:
        return _understanding_context(conn, contract_id, project_id)


def _documents(conn, contract_id, project_id, *, include_history=False):
    # Exact scope avoids treating a multi-project contract total as one project's cash flow.
    sql = 'SELECT * FROM cashflow_documents WHERE contract_id IS ? AND project_id IS ?'
    if not include_history:
        sql += ' AND active=1'
    return [dict(row) for row in conn.execute(sql + ' ORDER BY name,kind,version,id',
                                             (contract_id, project_id))]


def list_documents(contract_id=None, project_id=None, *, include_history=False):
    with db_read() as conn:
        _scope(conn, contract_id, project_id)
        return _documents(conn, contract_id, project_id, include_history=include_history)


def _file_state(document):
    if not document['source_path']:
        return 'manual-snapshot'
    source = Path(document['source_path'])
    try:
        if not source.is_file():
            return 'missing'
        if source.stat().st_size > LIMIT_BYTES:
            return 'oversized'
        return digest(source.read_bytes())
    except OSError:
        return 'unreadable'


def _fingerprint(conn, contract_id, project_id):
    context = _scope(conn, contract_id, project_id)
    documents = _documents(conn, contract_id, project_id)
    sources = []
    for document in documents:
        state = _file_state(document)
        if document['source_path'] and state != document['source_file_sha256']:
            raise ValueError(f'来源「{document["name"]}」已修改、缺失或不可读，请重新关联资料后分析')
        attachment = None
        if document['attachment_id']:
            row = conn.execute('SELECT id,status,file_path,updated_at FROM business_attachments WHERE id=?',
                               (document['attachment_id'],)).fetchone()
            if not row or row['status'] != 'active':
                raise ValueError('关联的原业务附件已作废，请核对来源')
            attachment = dict(row)
        sources.append([document['id'], document['sha256'], document['version'], state, attachment])
    _, purpose_fingerprint = _understanding_context(conn, contract_id, project_id)
    for document in documents:
        fragments = json.loads(document['fragments_json'])
        packet = fragments[0].get('understanding') if fragments else None
        if packet and packet.get('scope_fingerprint') != purpose_fingerprint:
            raise ValueError('资料理解时的合同、项目或经营主体已变化，请重新关联该资料后分析')
    return digest({'context': context, 'purpose': purpose_fingerprint,
                   'sources': sources, 'engine': ENGINE_VERSION}), documents


@_write
def save_document(data, contract_id=None, project_id=None):
    name = str(data.get('name') or '').strip()
    kind = data.get('kind')
    if not name or kind not in KINDS:
        raise ValueError('请填写资料名称并选择资料类型')
    fragments = validate_fragments(data.get('fragments'))
    path = str(data.get('source_path') or '')
    file_hash = str(data.get('source_file_sha256') or '')
    if path and _file_state({'source_path': path}) != file_hash:
        raise ValueError('资料文件在读取后已变化，请重新选择')
    content_hash = digest({'fragments': fragments, 'file': file_hash})
    with db_transaction(immediate=True) as conn:
        _scope(conn, contract_id, project_id)
        packet = fragments[0].get('understanding')
        if packet:
            _, purpose_fingerprint = _understanding_context(conn, contract_id, project_id)
            if packet.get('scope_fingerprint') != purpose_fingerprint:
                raise ValueError('资料理解后合同、项目或经营主体已变化，请重新读取核对')
        existing = conn.execute('''SELECT * FROM cashflow_documents
            WHERE contract_id IS ? AND project_id IS ? AND name=? AND kind=? AND active=1''',
            (contract_id, project_id, name, kind)).fetchone()
        if existing and existing['sha256'] == content_hash and _file_state(dict(existing)) not in ('missing', 'unreadable'):
            return {'id': existing['id'], 'version': existing['version'], 'duplicate': True}
        reason = str(data.get('change_reason') or '').strip()
        if existing and not reason:
            raise ValueError('同名资料发生变化，请填写换版原因；旧版本将保留')
        attachment_id = data.get('attachment_id')
        if attachment_id:
            allowed = {item['id'] for item in _available_attachments(conn, contract_id, project_id)}
            if attachment_id not in allowed:
                raise ValueError('该附件不属于当前合同/项目范围或已作废')
        version = conn.execute('''SELECT COALESCE(MAX(version),0)+1 FROM cashflow_documents
            WHERE contract_id IS ? AND project_id IS ? AND name=? AND kind=?''',
            (contract_id, project_id, name, kind)).fetchone()[0]
        conn.execute('''UPDATE cashflow_documents SET active=0
            WHERE contract_id IS ? AND project_id IS ? AND name=? AND kind=? AND active=1''',
            (contract_id, project_id, name, kind))
        document_id = conn.execute('''INSERT INTO cashflow_documents
            (contract_id,project_id,name,kind,version,sha256,fragments_json,source_path,
             source_file_sha256,attachment_id,change_reason,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
            (contract_id, project_id, name, kind, version, content_hash,
             json.dumps(fragments, ensure_ascii=False), path, file_hash, attachment_id, reason, now())).lastrowid
        return {'id': document_id, 'version': version, 'duplicate': False}


@_write
def retire_document(document_id, reason):
    if not str(reason or '').strip():
        raise ValueError('请填写移出分析的原因')
    with db_transaction(immediate=True) as conn:
        conn.execute('UPDATE cashflow_documents SET active=0,change_reason=? WHERE id=? AND active=1',
                     (str(reason).strip(), document_id))


def _available_attachments(conn, contract_id, project_id):
    rows = []
    if contract_id:
        rows.extend(dict(row) for row in conn.execute(
            "SELECT * FROM business_attachments WHERE contract_id=? AND status='active'", (contract_id,)))
    elif project_id:
        rows.extend(dict(row) for row in conn.execute('''SELECT a.* FROM business_attachments a
            JOIN contracts c ON c.id=a.contract_id AND c.status!='void'
            WHERE a.status='active' AND (EXISTS (
                SELECT 1 FROM contract_project_allocations ca WHERE ca.contract_id=c.id
                AND ca.project_id=? AND ca.status='active') OR EXISTS (
                SELECT 1 FROM settlements s WHERE s.contract_id=c.id
                AND s.project_id=? AND s.status='active'))''', (project_id, project_id)))
    if project_id:
        rows.extend(dict(row) for row in conn.execute('''SELECT a.* FROM business_attachments a
            LEFT JOIN purchase_orders po ON po.id=a.purchase_order_id
            LEFT JOIN construction_records cr ON cr.id=a.construction_record_id
            LEFT JOIN construction_sites cs ON cs.id=cr.site_id
            WHERE a.status='active' AND (po.project_id=? OR cs.project_id=?)''', (project_id, project_id)))
    return sorted({row['id']: row for row in rows}.values(), key=lambda row: row['id'])


def available_attachments(contract_id=None, project_id=None):
    from services.attachment_service import attachment_path
    with db_read() as conn:
        _scope(conn, contract_id, project_id)
        rows = _available_attachments(conn, contract_id, project_id)
        linked = {row['attachment_id'] for row in _documents(conn, contract_id, project_id)}
    for row in rows:
        path = attachment_path(row['file_path'])
        row.update(absolute_path=str(path), file_exists=path.is_file(), already_linked=row['id'] in linked)
    return rows


@_write
def create_analysis(contract_id=None, project_id=None, *, opening_minor=0,
                    income_delay_days=0, expense_increase_percent=0):
    with db_transaction(immediate=True) as conn:
        fingerprint, documents = _fingerprint(conn, contract_id, project_id)
        if not documents:
            raise ValueError('请先关联合同正文或其他资料')
        rows = extract_rows(documents)
        base = timeline(rows, opening_minor=opening_minor)
        scenario = timeline(rows, opening_minor=opening_minor, income_delay_days=income_delay_days,
                            expense_increase_percent=expense_increase_percent)
        notes = []
        interpretations = []
        for document in documents:
            fragments = json.loads(document['fragments_json'])
            understanding = understanding_from_fragments(fragments)
            if understanding is not None:
                source = {key: document[key] for key in ('id', 'name', 'version', 'sha256', 'kind')}
                interpretations.append({'summary': understanding['summary'], 'model': understanding['model'],
                                        'unreadable_sources': understanding['unreadable_sources'], 'source': source})
                for risk in understanding['risks']:
                    notes.extend({'text': risk['text'], 'source': {**source, **proof}}
                                 for proof in risk['evidence'])
                if understanding['unreadable_sources']:
                    notes.append({'text': '存在未读清来源，资金分析并不完整，请核对原件。',
                                  'source': {**source, 'locator': '、'.join(understanding['unreadable_sources'])}})
                continue
            parsed = [row for row in rows if any(s['id'] == document['id'] for s in row['sources'])]
            if not parsed:
                notes.append({'text': '未识别到明确收付节点；材料/预算不自动等同实际付款。请人工核对摘录。',
                              'source': {'id': document['id'], 'name': document['name'],
                                         'version': document['version'], 'sha256': document['sha256'],
                                         'locator': '全文'}})
            for fragment in fragments:
                if any(word in fragment['text'] for word in ('无可读文字', '验收', '开票', '审批', '质保', '审计')):
                    notes.append({'text': '核对该条的触发日期、证明材料及审核期限；可作为协商预付款或明确付款期限的依据。',
                                  'source': {'id': document['id'], 'name': document['name'],
                                             'version': document['version'], 'sha256': document['sha256'],
                                             **fragment}})
        limitations = ['模型理解依据所选原始资料与最小项目/合同上下文，仅供核对；不是实际收入、回款或支出事实。',
                       '金额按整数分复算；累计付款只取本期差额，相对期限保留触发条件，不编造日历日期。',
                       '待补项、归属不明、缺页与冲突不进入时间表；模型也可能漏读或误解，须核对原件与总分项重复。',
                       '合同总额、材料预算、发票和已发生账目不能自动充当未来现金流。']
        if len(interpretations) < len(documents):
            limitations.append('部分历史资料/人工摘录仍使用本地明确字段解析，尚无模型语义理解；百分比和相对日期须补充核对。')
        payload = {'rows': rows, 'base': base, 'scenario': scenario, 'notes': notes,
                   'interpretations': interpretations,
                   'scope': {'contract_id': contract_id, 'project_id': project_id},
                   'limitations': limitations}
        return conn.execute('''INSERT INTO cashflow_analyses
            (contract_id,project_id,fingerprint,engine_version,payload_json,created_at)
            VALUES (?,?,?,?,?,?)''', (contract_id, project_id, fingerprint, ENGINE_VERSION,
                                     json.dumps(payload, ensure_ascii=False), now())).lastrowid


def _analysis(conn, analysis_id):
    row = conn.execute('SELECT * FROM cashflow_analyses WHERE id=?', (analysis_id,)).fetchone()
    if not row:
        raise ValueError('分析草稿不存在')
    result = dict(row)
    result['payload'] = json.loads(result['payload_json'])
    result['stale_reason'] = ''
    try:
        fingerprint, _ = _fingerprint(conn, result['contract_id'], result['project_id'])
        if fingerprint != result['fingerprint'] or result['engine_version'] != ENGINE_VERSION:
            result['stale_reason'] = '资料、合同/项目关系或分析规则已变化，请重新分析；既有正式计划需另行核对'
    except ValueError as error:
        result['stale_reason'] = str(error)
    result['plan_ids'] = [row[0] for row in conn.execute(
        'SELECT plan_id FROM cashflow_plan_links WHERE analysis_id=? ORDER BY plan_id', (analysis_id,))]
    return result


def get_analysis(analysis_id):
    with db_read() as conn:
        return _analysis(conn, analysis_id)


def list_analyses(contract_id=None, project_id=None):
    with db_read() as conn:
        return [dict(row) for row in conn.execute('''SELECT id,status,created_at FROM cashflow_analyses
            WHERE contract_id IS ? AND project_id IS ? ORDER BY id DESC''', (contract_id, project_id))]


@_write
def cancel_analysis(analysis_id):
    with db_transaction(immediate=True) as conn:
        result = conn.execute("UPDATE cashflow_analyses SET status='cancelled' WHERE id=? AND status='draft'",
                              (analysis_id,))
        if result.rowcount != 1:
            raise ValueError('只能取消未确认草稿；已确认计划须在正式计划中另行取消')


@_write
def confirm_analysis(analysis_id, row_keys, *, acknowledged=False, reason=''):
    if acknowledged is not True or not str(reason or '').strip():
        raise ValueError('请明确核对节点、来源、前置条件及重复计划，并填写确认依据')
    if not isinstance(row_keys, list) or not row_keys or len(set(row_keys)) != len(row_keys):
        raise ValueError('请选择不重复的收付节点')
    with db_transaction(immediate=True) as conn:
        analysis = _analysis(conn, analysis_id)
        if analysis['status'] == 'confirmed':
            existing = [row[0] for row in conn.execute(
                'SELECT row_key FROM cashflow_plan_links WHERE analysis_id=?', (analysis_id,))]
            if set(existing) != set(row_keys):
                raise ValueError('该草稿已确认；不能追加或改变节点，请核对已有计划')
            return analysis['plan_ids']
        if analysis['status'] != 'draft' or analysis['stale_reason']:
            raise ValueError(analysis['stale_reason'] or '草稿已取消，不能确认')
        rows = {row['key']: row for row in analysis['payload']['rows']}
        selected = []
        for key in row_keys:
            row = rows.get(key)
            if not row or row['conflict'] or row['issues'] or not row['direction'] or not row['due_date'] or not row['amount_minor']:
                raise ValueError('所选节点存在冲突、金额/日期/方向或前置条件待补充，请核对资料并重新分析')
            selected.append(row)
        for row in selected:
            # Prevent regenerating the same scope/node as a second formal plan.
            previous = conn.execute('''SELECT l.plan_id,l.row_key,a.payload_json FROM cashflow_plan_links l
                JOIN cashflow_analyses a ON a.id=l.analysis_id
                JOIN fund_plans p ON p.id=l.plan_id
                WHERE a.contract_id IS ? AND a.project_id IS ? AND p.status='planned' ''',
                (analysis['contract_id'], analysis['project_id'])).fetchall()
            for prior in previous:
                duplicate_node = any(old['key'] == prior['row_key'] and old['direction'] == row['direction'] and old['title'] == row['title']
                                     for old in json.loads(prior['payload_json'])['rows'])
                if duplicate_node:
                    raise ValueError(f'节点「{row["title"]}」已有正式计划#{prior["plan_id"]}，请先核对或取消已有计划')
        plan_ids = []
        timestamp = now()
        for row in selected:
            trace = json.dumps({'analysis_id': analysis_id, 'scope': analysis['payload']['scope'],
                                'sources': row['sources'], 'condition': row['condition'],
                                'confirmation_reason': reason}, ensure_ascii=False)
            plan_id = conn.execute('''INSERT INTO fund_plans
                (direction,title,counterparty,amount_minor,due_date,notes,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)''', (row['direction'], row['title'], row['counterparty'],
                                           row['amount_minor'], row['due_date'], trace, timestamp, timestamp)).lastrowid
            conn.execute('INSERT INTO cashflow_plan_links(analysis_id,row_key,plan_id) VALUES (?,?,?)',
                         (analysis_id, row['key'], plan_id))
            conn.execute('''INSERT INTO fund_audit
                (entity_type,entity_id,action,before_json,after_json,reason,created_at)
                VALUES ('plan',?,'cashflow_confirm','{}',?,?,?)''',
                (str(plan_id), trace, str(reason).strip(), timestamp))
            plan_ids.append(plan_id)
        conn.execute("UPDATE cashflow_analyses SET status='confirmed',confirmed_at=?,confirmation_reason=? WHERE id=?",
                     (timestamp, str(reason).strip(), analysis_id))
        return plan_ids
