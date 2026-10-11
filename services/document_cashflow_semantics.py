"""Validate evidence-backed interpretation and calculate payment stages in cents."""

import re
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from services.document_cashflow_extract import KINDS, money_minor

SCHEMA_VERSION = 1


def _text(value, label, *, optional=False, limit=4000):
    if optional and value is None:
        return ''
    if not isinstance(value, str) or (not optional and not value.strip()) or len(value) > limit:
        raise ValueError(f'模型资料结果的{label}不完整或过长，未保存资料')
    return value.strip()


def _list(value, label, limit=100):
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f'模型资料结果的{label}格式不正确，未保存资料')
    return value


def _compact(text):
    return re.sub(r'\s+', '', text)


def _money_in_evidence(cents, proofs):
    for proof in proofs:
        for match in re.finditer(r'(?<![\d.])([0-9][\d,，]*(?:\.\d+)?)\s*(亿元|亿|万元|万|元)?(?![\d.%％])',
                                 proof['excerpt']):
            scale = {'亿元': 100_000_000, '亿': 100_000_000, '万元': 10_000, '万': 10_000}.get(match[2], 1)
            try:
                value = Decimal(match[1].replace(',', '').replace('，', '')) * scale * 100
                if value == cents:
                    return True
            except InvalidOperation:
                continue
    return False


def normalize_understanding(packet, fragments):
    """Revalidate persisted raw JSON; derived amounts are never accepted from callers."""
    if not isinstance(packet, dict) or type(packet.get('schema')) is not int or packet['schema'] != SCHEMA_VERSION:
        raise ValueError('资料理解版本不支持，请重新读取资料')
    mode = packet.get('source_mode')
    if mode not in ('vision', 'text'):
        raise ValueError('资料理解来源方式不正确')
    model = _text(packet.get('model'), '模型名称', limit=200)
    scope = packet.get('scope')
    if not isinstance(scope, dict) or any(type(scope.get(key)) is not bool for key in
                                         ('project_scope', 'multi_project_contract')):
        raise ValueError('资料理解的范围信息不正确')
    result = packet.get('result')
    if not isinstance(result, dict) or result.get('kind') not in KINDS:
        raise ValueError('模型未明确资料类型，未保存资料')
    source_text = {fragment['locator']: fragment['text'] for fragment in fragments}
    if len(source_text) != len(fragments):
        raise ValueError('资料来源位置重复，无法核对完整性')
    covered = _list(result.get('covered_sources'), '完整来源', 10000)
    unreadable = _list(result.get('unreadable_sources'), '不可读来源', 10000)
    if (any(not isinstance(item, str) for item in covered + unreadable)
            or len(set(covered)) != len(covered) or set(covered) != set(source_text)
            or len(set(unreadable)) != len(unreadable) or not set(unreadable) <= set(covered)):
        raise ValueError('模型未覆盖整份资料或返回了无效页码，未保存资料')

    def evidence(items):
        entries = _list(items, '条款依据', 20)
        if not entries:
            raise ValueError('模型结果缺少原文与来源，未保存资料')
        checked = []
        for item in entries:
            if not isinstance(item, dict):
                raise ValueError('模型条款依据格式不正确')
            locator = _text(item.get('locator'), '条款位置', limit=300)
            quote = _text(item.get('quote'), '原文摘录', limit=3000)
            if locator not in source_text or locator in unreadable:
                raise ValueError('模型引用了不存在或不可读的来源，未保存资料')
            if mode == 'text' and _compact(quote) not in _compact(source_text[locator]):
                raise ValueError('模型摘录与原文不符，未保存资料')
            checked.append({'locator': locator, 'excerpt': quote})
        return checked

    bases = {}
    for item in _list(result.get('bases'), '金额基数'):
        if not isinstance(item, dict):
            raise ValueError('模型金额基数格式不正确')
        identity = _text(item.get('id'), '基数编号', limit=80)
        if identity in bases:
            raise ValueError('模型金额基数编号重复')
        base_amount = money_minor(item.get('amount'))
        base_proofs = evidence(item.get('evidence'))
        bases[identity] = {'amount_minor': base_amount,
                           'label': _text(item.get('label'), '基数名称', limit=180),
                           'evidence': base_proofs, 'verified': _money_in_evidence(base_amount, base_proofs)}

    accumulated = {}
    uncertain_groups = set()
    nodes = []
    for item in _list(result.get('nodes'), '收付节点'):
        if not isinstance(item, dict):
            raise ValueError('模型收付节点格式不正确')
        title = _text(item.get('title'), '节点名称', limit=180)
        direction = item.get('direction')
        attribution = item.get('scope')
        if direction not in ('in', 'out', 'unknown') or attribution not in ('current', 'whole_contract', 'uncertain'):
            raise ValueError('模型未明确收支方向或项目归属')
        issues = [_text(value, '待核对项', limit=500) for value in _list(item.get('issues'), '待核对项', 20)]
        proofs = evidence(item.get('evidence'))
        stage_proofs = list(proofs)
        condition = _text(item.get('condition'), '付款条件', optional=True)
        timing = _text(item.get('timing'), '时间条件', optional=True, limit=1000)
        counterparty = _text(item.get('counterparty'), '对方名称', optional=True, limit=300)
        amount_spec = item.get('amount')
        if not isinstance(amount_spec, dict) or amount_spec.get('type') not in ('fixed', 'percentage', 'unknown'):
            raise ValueError('模型金额表达不正确')
        amount = None
        calculation = ''
        basis_id = amount_spec.get('base_id')
        if basis_id is not None and (not isinstance(basis_id, str) or basis_id not in bases):
            raise ValueError('模型引用了不存在的金额基数')
        group = (direction, attribution, basis_id)
        if amount_spec['type'] == 'fixed':
            amount = money_minor(amount_spec.get('value'))
            if not _money_in_evidence(amount, proofs):
                amount = None
                issues.append('固定金额与原文数字不符或无法复核，不能计入计划')
            if basis_id:
                if amount is not None:
                    accumulated[group] = accumulated.get(group, 0) + amount
        elif amount_spec['type'] == 'percentage':
            if not basis_id or amount_spec.get('percentage_mode') not in ('incremental', 'cumulative'):
                raise ValueError('模型比例缺少基数或累计/本期口径')
            try:
                percentage = Decimal(str(amount_spec.get('value')))
                if not percentage.is_finite() or not 0 < percentage <= 100:
                    raise ValueError
            except (InvalidOperation, ValueError):
                raise ValueError('模型付款比例须为大于零且不超过100%') from None
            base = bases[basis_id]['amount_minor']
            target = int((Decimal(base) * percentage / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            prior = accumulated.get(group, 0)
            cumulative = amount_spec['percentage_mode'] == 'cumulative'
            if cumulative and group in uncertain_groups:
                issues.append('前序付款金额未确定，累计款本期差额待核对')
            amount = target - prior if cumulative else target
            calculation = f"{bases[basis_id]['label']} × {percentage}%" + (' − 前序各期金额' if cumulative else '')
            if amount <= 0:
                amount = None
                issues.append('累计比例不高于前序金额，节点顺序/重复付款待核对')
            else:
                accumulated[group] = prior + amount
            proofs += bases[basis_id]['evidence']
            if not bases[basis_id]['verified']:
                issues.append('计算基数与原文数字不符或无法复核，不能计入计划')
            percentage_pattern = rf'(?<![\d.]){re.escape(format(percentage.normalize(), "f"))}(?:\.0+)?\s*[%％]'
            if not any(re.search(percentage_pattern, proof['excerpt']) for proof in stage_proofs):
                issues.append('比例未找到可复核的原文数字，不能计入计划')
        if basis_id and accumulated.get(group, 0) > bases[basis_id]['amount_minor']:
            issues.append('该组分期超过引用基数，付款比例或重复节点待核对')
        if amount is None:
            issues.append('金额或可核对计算基数待补充')
            if basis_id:
                uncertain_groups.add(group)
        due = item.get('due_date')
        if due is not None:
            try:
                due = date.fromisoformat(due).isoformat()
            except (TypeError, ValueError):
                raise ValueError('模型返回了无效日期') from None
            year, month, day = map(int, due.split('-'))
            date_pattern = rf'{year}\s*(?:年|[-/\.])\s*0?{month}\s*(?:月|[-/\.])\s*0?{day}(?:日|\b)'
            if not any(re.search(date_pattern, proof['excerpt']) for proof in stage_proofs):
                raise ValueError('模型给出的日历日期没有原文依据，未保存资料')
        else:
            issues.append('付款触发日期待补充；相对期限不推定为实际日期')
        if not condition:
            condition = '前置条件待核对'
            issues.append('开票、验收、审批等前置条件待核对')
        if direction == 'unknown':
            issues.append('我方收款/付款方向待核对')
        if attribution == 'uncertain' or (scope['project_scope'] and attribution == 'whole_contract'):
            issues.append('属于整份合同或归属不明，不能计入当前项目')
        if scope['project_scope'] and scope['multi_project_contract'] and basis_id:
            issues.append('多项目合同的金额基数须另行核对项目份额')
        nodes.append({'title': title, 'direction': None if direction == 'unknown' else direction,
                      'amount_minor': amount, 'due_date': due, 'timing': timing, 'condition': condition,
                      'counterparty': counterparty, 'calculation': calculation,
                      'issues': list(dict.fromkeys(issues)), 'conflict': False, 'evidence': proofs})
    # Every stage in an overcommitted series is unsafe, not only the final stage.
    if any('该组分期超过引用基数，付款比例或重复节点待核对' in node['issues'] for node in nodes):
        for node in nodes:
            node['issues'].append('分期总额存在异常，本资料节点均待核对')
    risks = []
    for item in _list(result.get('risks'), '风险提示', 30):
        if not isinstance(item, dict):
            raise ValueError('模型风险提示格式不正确')
        risks.append({'text': _text(item.get('text'), '风险说明'), 'evidence': evidence(item.get('evidence'))})
    return {'summary': _text(result.get('summary'), '理解摘要', limit=6000), 'kind': result['kind'],
            'model': model, 'source_mode': mode, 'nodes': nodes, 'risks': risks,
            'covered_sources': covered, 'unreadable_sources': unreadable}


def understanding_from_fragments(fragments):
    packet = fragments[0].get('understanding') if fragments else None
    return normalize_understanding(packet, fragments) if packet is not None else None
