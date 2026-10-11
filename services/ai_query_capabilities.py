"""Typed adapters for proven operating queries; no model-generated code or SQL."""

from datetime import date

from db.connection import use_read_snapshot
from services import ai_operating_query_service as operating


def _tool(label, handler, *, scope='global', note='', argument=None):
    dimensions = []
    parameters = ['dataset', 'filters']
    if scope in ('optional_project', 'required_project'):
        dimensions = ['project']
        parameters.append('project_id')
    elif scope == 'customer':
        dimensions = ['customer']
        parameters.append('year')
    return {'label': label, 'handler': handler, 'scope': scope, 'note': note, 'argument': argument,
            'dimensions': dimensions, 'parameters': parameters}


TOOLS = {
    'invoice_balance': _tool('当前未结清发票余额', '_invoice_balance', scope='optional_project',
        note='按财务服务实际发票抵扣计算当前剩余余额；不是开票总额，也不是项目应收。'),
    'receipt_matching': _tool('当前回款待抵发票', '_receipt_matching', scope='optional_project',
        note='当前尚未匹配现有或后续发票的回款；无需重新按开票日期猜测。'),
    'project_finance': _tool('项目当前开票回款概况', '_project_finance', scope='required_project',
        note='项目累计确认收入、已开票、已回款、应收、未开票。必须指定唯一项目。'),
    'project_profit': _tool('项目累计经营核算', '_project_profit', scope='required_project',
        note='复用正式项目经营核算口径。期间利润、项目比较或排名使用profit。'),
    'monthly_cost_breakdown': _tool('本月成本分类概况', '_cost_breakdown', scope='optional_project',
        note='仅本月，复用成本看板；其他期间按costs按category分组查询。'),
    'funds_overview': _tool('已登记账户余额与本月实收支', '_funds_overview',
        note='公司账户层面，余额依赖资金登记完整性；不是工程收入，不提供预算或未来预测。'),
    'collection_actions': _tool('今天催款行动', '_collection_actions', argument='今天该催谁',
        note='全公司当前待催款、逾期和应收优先级。'),
    'collection_promises_overdue': _tool('付款承诺已逾期', '_collection_actions', argument='承诺付款逾期',
        note='全公司当前客户承诺已逾期的跟进项目。'),
    'business_action_plan': _tool('今日经营待办', '_business_action_plan',
        note='全公司当前催款、履约、附件和资料待办。'),
    'business_gaps': _tool('项目业务数据缺口', '_business_risks', argument='数据缺口',
        note='全公司当前缺合同、结算、开票、成本等资料的项目。'),
    'profit_risks': _tool('当前负毛利项目', '_business_risks', argument='负毛利',
        note='全公司当前累计核算为负的项目；期间比较或最小对象使用profit。'),
    'receivable_risks': _tool('当前未回款重点项目', '_business_risks', argument='应收最多',
        note='全公司当前未回款重点项目；客户占比或精确排名使用receivables。'),
    'purchase_cash_gaps': _tool('回款少于已付采购标记的项目', '_business_risks', argument='负现金',
        note='不是账户余额或真实现金风险；未扣工资、费用，也不含期初。'),
    'purchase_year_comparison': _tool('今年与去年采购比较', '_purchase_year_comparison',
        note='按现有年度采购看板比较今年与去年已登记全年采购，不是同期比较。其他期间使用purchases。'),
    'customer_business': _tool('客户年度业务与当前应收档案', '_customer_business', scope='customer',
        note='年度收入贡献与当前累计应收分开取事实；year可指定年份，默认为今年。'),
}


def catalog():
    return {name: {
        'label': spec['label'], 'read_only': True, 'dated': False,
        'dimensions': list(spec['dimensions']), 'metrics': [], 'parameters': list(spec['parameters']),
        'note': spec['note'] + ' 当前业务快照，不接受日期、分组、指标或排名参数；不得忽略额外条件。',
    } for name, spec in TOOLS.items()}


def validate_query(query):
    from services.ai_query_tools import QueryValidationError

    spec = TOOLS[query['dataset']]
    allowed = set(spec['parameters'])
    if set(query) - allowed:
        raise QueryValidationError(f"{spec['label']}不支持这些参数，不能忽略范围或日期条件。")
    filters = query.get('filters', {})
    dimensions = spec['dimensions']
    if not isinstance(filters, dict) or set(filters) - set(dimensions):
        raise QueryValidationError('此经营查询不支持该筛选条件。')
    if any(not isinstance(value, str) or not value.strip() or len(value) > 100 for value in filters.values()):
        raise QueryValidationError('筛选条件须为不超过100字的名称。')
    if 'project_id' in query and (type(query['project_id']) is not int or query['project_id'] <= 0):
        raise QueryValidationError('项目编号须为有效的正整数。')
    if spec['scope'] == 'required_project' and not (query.get('project_id') or filters.get('project')):
        raise QueryValidationError('此查询必须选择唯一项目。')
    if 'year' in query and (type(query['year']) is not int or not 1900 <= query['year'] <= date.today().year):
        raise QueryValidationError('客户业务年份无效。')


def execute(conn, query):
    from services.ai_query_tools import QueryValidationError, resolve_project

    spec = TOOLS[query['dataset']]
    filters = query.get('filters', {})
    project = None
    resolved = {}
    handler = getattr(operating, spec['handler'])
    with use_read_snapshot(conn):
        if 'project' in spec['dimensions']:
            project = resolve_project(conn, filters.get('project'), query.get('project_id'))
            if project:
                resolved['project'] = project['name']
            result = handler(project)
        elif spec['scope'] == 'customer':
            year = query.get('year', date.today().year)
            term = filters.get('customer')
            if term:
                customer, _candidates = operating._customer_scope(term, year)
                if not customer:
                    raise QueryValidationError('客户没有唯一匹配，请确认完整客户名称；不能按全公司查询。')
                resolved['customer'] = customer['legal_name']
            result = handler(resolved.get('customer', '客户业务'), year)
        elif spec['argument'] is not None:
            result = handler(spec['argument'])
        else:
            result = handler()
    sources = result.get('sources') or []
    return {'answer': result['answer'], 'sources': sources, 'totals': {}, 'groups': [],
            'resolved_filters': resolved, 'project_id': project['id'] if project else None}


def plan_for_local_result(result):
    """Bridge offline operating answers into the same validated follow-up format."""
    aliases = {'cost_breakdown': 'monthly_cost_breakdown',
               'profit_risk': 'profit_risks', 'cash_risk': 'purchase_cash_gaps',
               'receivable_risk': 'receivable_risks'}
    name = aliases.get(result.get('intent'), result.get('intent'))
    if name not in TOOLS:
        return None
    query = {'dataset': name}
    updates = result.get('context_updates') or {}
    if 'project' in TOOLS[name]['dimensions'] and updates.get('project_id'):
        query['project_id'] = int(updates['project_id'])
    if name == 'customer_business':
        # The legacy text route does not expose the resolved customer identity.
        # Do not turn a single-customer result into a whole-company follow-up.
        return None
    if name == 'collection_actions' and any(
        source.get('scope_label') == '客户承诺已逾期' for source in result.get('sources', [])
    ):
        query['dataset'] = 'collection_promises_overdue'
    return {'queries': [query]}
