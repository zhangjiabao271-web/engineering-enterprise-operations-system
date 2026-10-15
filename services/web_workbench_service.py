"""Explicit browser adapters over existing business services; no alternative ledger."""

import json
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import uuid4

from services import (
    project_service as projects, master_data_service as partners,
    contract_service as contracts, finance_service as finance,
    procurement_service as procurement, labor_service as labor,
    construction_service as construction, cost_service as costs,
    project_profit_service as profit, operations_service as operations,
    data_governance_service as governance, collection_service as collection,
    funds_service as funds, historical_receivable_service as history,
    input_invoice_service as inputs, operating_entity_service as entities,
)
from services.document_cashflow_extract import digest
from services import project_ledger_service as project_ledger
from db.connection import db_read


def field(name, label, kind='text', *, required=False, source=None, choices=None, default=''):
    return dict(name=name, label=label, kind=kind, required=required, source=source,
                choices=choices, default=default)


def select(name, label, source, required=False):
    return field(name, label, 'select', source=source, required=required)


def choice(name, label, choices, default=''):
    return field(name, label, 'select', choices=choices, default=default)


PROJECT = select('project_id', '独立核算项目', 'projects', True)
AMOUNT = field('amount', '金额（人民币元）', 'money', required=True)
NOTES = field('notes', '说明 / 备注', 'textarea')
TODAY = date.today().isoformat()


SPECS = {
    'projects': {'title': '项目台账', 'list': projects.list_projects, 'get': projects.get_project,
        'create': projects.create_project, 'update': projects.update_project,
        'scope': True,
        'columns': [('project_code','项目编号'),('name','项目名称'),('customer_name','客户'),('status','状态'),('business_mode','业务模式'),('settlement_minor','已确认收入'),('receipt_minor','已回款'),('invoice_minor','已开票'),('receivable_minor','未回款'),('planned_start_date','计划开工'),('planned_end_date','计划完工')],
        'fields': [field('name','项目名称',required=True), field('project_code','项目编号'), select('customer_partner_id','客户档案','customers'), field('manager','负责人'),
                   choice('status','项目状态', ['筹备中','进行中','已完工','已关闭'],'筹备中'), choice('business_mode','业务模式', {'contract':'正式合同工程','cash':'零星现金工程'},'contract'),
                   choice('invoice_policy','开票要求', {'required':'需要开票','not_required':'不要求开票','pending':'待确认'},'pending'), field('cash_agreed_amount','现金工程约定总额（可留空）','money'),
                   field('planned_start_date','计划开工','date'),field('planned_end_date','计划完工','date'),field('address','项目地址'),NOTES]},
    'contracts': {'title':'合同台账','list':contracts.list_contracts,'get':contracts.get_contract,'create':contracts.create_contract,'update':contracts.update_contract,
        'scope':True,'attachment':'contract','columns':[('contract_no','合同编号'),('name','合同名称'),('customer_name_snapshot','客户'),('entity_name','经营主体'),('ownership_source','归属依据'),('pricing_mode','计价方式'),('tax_inclusive_amount_minor','约定金额'),('settled_minor','已确认收入'),('status','合同状态')],
        'fields':[field('name','合同名称',required=True),field('contract_no','合同编号'),select('customer_partner_id','客户档案','customers'),select('entity_id','经营主体（项目自动沿用）','entities'),
                  choice('contract_type','合同类型',contracts.CONTRACT_TYPES,'project'),choice('pricing_mode','计价方式',contracts.PRICING_MODES,'fixed'),AMOUNT,
                  field('control_limit','控制上限（可留空）','money'),field('sign_date','签约日期','date',required=True,default=TODAY),field('start_date','合同开始','date'),field('end_date','合同结束','date'),
                  select('parent_contract_id','补充协议所属合同','contracts'),choice('status','合同状态',{'draft':'草稿','active':'履约中','completed':'已完成'},'draft'),NOTES]},
    'allocations': {'title':'项目关联 / 分配','list':contracts.list_allocations,'create':contracts.create_allocation,'scope':True,
        'columns':[('contract_no','合同'),('project_name','项目'),('allocated_amount_minor','分配金额'),('notes','关联说明')],
        'fields':[select('contract_id','合同','contracts',True),PROJECT,AMOUNT,NOTES]},
    'settlements': {'title':'收入确认','list':contracts.list_settlements,'get':contracts.get_settlement,'create':contracts.create_settlement,'update':contracts.update_settlement,
        'scope':True,'attachment':'settlement','columns':[('settlement_no','确认编号'),('settlement_date','确认日期'),('project_name','项目'),('contract_no','合同'),('amount_minor','确认金额'),('status','状态')],
        'fields':[PROJECT,select('contract_id','合同（现金工程可空）','contracts'),AMOUNT,field('settlement_date','确认日期','date',required=True,default=TODAY),field('basis','确认依据',required=True),NOTES]},
    'suppliers': {'title':'供应商','list':partners.list_suppliers,'get':partners.get_supplier,'create':partners.create_supplier,'update':partners.update_supplier,
        'columns':[('name','供应商'),('category','主营类别'),('supplier_kind','类型'),('contact','联系人'),('contact_phone','电话'),('status','状态')],
        'fields':[field('name','供应商名称',required=True),field('category','主营类别'),choice('supplier_kind','供应商类型',partners.SUPPLIER_KIND_LABELS,'unclassified'),field('contact','联系人'),field('contact_phone','联系电话'),NOTES]},
    'customers': {'title':'客户档案','list':partners.list_customers,'get':partners.get_business_partner,'create':partners.create_business_partner,'update':partners.update_business_partner,
        'columns':[('legal_name','客户名称'),('contact','联系人'),('contact_phone','电话'),('entity_type','主体类型'),('status','状态')],
        'fields':[field('legal_name','客户名称',required=True),field('short_name','简称'),field('contact_name','联系人'),field('contact_phone','联系电话'),
                  choice('entity_type','主体类型',{'enterprise':'企业','individual_business':'个体工商户','individual':'个人'},'enterprise'),
                  field('unified_credit_code','统一社会信用代码'),field('registered_address','注册地址'),field('bank_name','开户银行'),field('bank_account','银行账号'),NOTES]},
    'offers': {'title':'材料与报价','list':partners.list_supplier_offers,'get':partners.get_supplier_offer,'create':partners.create_supplier_offer,'update':partners.update_supplier_offer,
        'columns':[('supplier_name','供应商'),('name','材料名称'),('specification','规格'),('unit','单位'),('price_minor','未税单价'),('tax_inclusive_price_minor','含税单价'),('updated_at','更新日期')],
        'fields':[select('supplier_id','供应商','suppliers',True),field('name','材料名称',required=True),field('specification','规格型号'),field('unit','计量单位',required=True),field('price','报价（元）','money',required=True),
                  choice('price_basis','报价口径',{'exclusive':'未税价','inclusive':'含税价'},'exclusive'),field('tax_rate_percent','税率（%）','number',default='0'),NOTES]},
    'purchases': {'title':'采购管理','list':procurement.list_purchase_orders,'get':procurement.get_purchase_order,'scope':True,'attachment':'purchase','special':'purchase',
        'columns':[('purchase_date','采购日期'),('material_details','材料名称 / 规格'),('project_name','项目'),('supplier_name','供应商'),('total_amount_cents','含税总额'),('payment_status','付款状态'),('invoice_status','发票状态'),('order_no','采购单号')],
        'fields':[PROJECT,select('supplier_id','供应商','suppliers'),field('merchant_name_snapshot','零星商户名称'),field('purchase_date','采购日期','date',required=True,default=TODAY),
                  choice('purchase_type','采购类型',['正式采购','零星采购'],'正式采购'),field('purchaser','经办人'),field('freight_amount','运费（元）','money',default='0'),
                  choice('payment_method','付款方式',['银行转账','对公转账','现金','微信','支付宝','员工垫付','未记录','待确认'],'待确认'),choice('payment_status','付款状态',['未确认','未付款','部分付款','已付款'],'未确认'),
                  choice('invoice_status','票据状态',['未确认','有发票','收据','无发票','未开票','已开票','无需发票'],'未确认'),NOTES]},
    'costs': {'title':'费用登记','list':costs.list_cost_entries,'get':costs.get_cost_entry,'create':costs.create_cost,'update':costs.update_cost_details,'scope':True,'attachment':'cost',
        'columns':[('cost_no','费用编号'),('cost_date','发生日期'),('project_name','项目'),('category','类别'),('counterparty_name_snapshot','对方'),('amount_minor','费用金额'),('status','状态')],
        'fields':[select('project_id','项目（待归集可空）','projects'),choice('category','费用类别',costs.COST_CATEGORIES,'工程施工 / 材料辅料'),AMOUNT,field('cost_date','发生日期','date',required=True,default=TODAY),field('counterparty_name','对方名称'),field('vehicle_no','车牌（燃油费）'),NOTES]},
    'workers': {'title':'工人档案','list':labor.get_workers,'get':labor.get_worker_by_id,'create':labor.add_worker,'update':labor.update_worker,
        'columns':[('name','姓名'),('trade','工种'),('daily_rate','日工资（元）'),('phone','电话'),('status','在职状态')],
        'fields':[field('name','姓名',required=True),field('trade','工种'),field('phone','电话'),field('daily_rate','日工资（元）','money',required=True),choice('status','状态',['在职','离职'],'在职'),NOTES]},
    'worklogs': {'title':'人工与工天','list':labor.get_work_logs,'get':labor.get_work_log_by_id,'create':labor.add_work_log,'update':labor.update_work_log,
        'columns':[('work_date','日期'),('worker_name','工人'),('project_name','项目'),('construction_site','作业地点'),('work_days','工天'),('amount_minor','工资'),('is_overtime','加班')],
        'fields':[select('worker_id','工人','active_workers',True),PROJECT,field('construction_site','作业地点',required=True),field('work_date','日期','date',required=True,default=TODAY),
                  field('work_days','工天','number',required=True,default='1'),field('work_type','作业内容'),field('is_overtime','加班记录','checkbox',default=False),NOTES]},
    'construction': {'title':'施工与验收','list':construction.get_construction_records,'get':construction.get_construction_record,'create':construction.add_construction_record,'update':construction.update_construction_record,
        'scope':True,'attachment':'construction','columns':[('project_name','项目'),('work_area','作业区域'),('work_item','施工内容'),('start_date','开始日期'),('end_date','结束日期'),('inspection_status','验收结论'),('work_amount_cents','施工记录金额')],
        'fields':[PROJECT,field('work_area','作业区域'),field('work_details','施工内容与工程量','textarea',required=True),field('team_name','施工班组'),
                  field('start_date','开始日期','date',required=True,default=TODAY),field('end_date','结束日期','date',required=True,default=TODAY),field('work_amount','施工记录金额（元）','money',default='0'),field('description','补充说明','textarea')]},
    'invoices': {'title':'开票台账','list':finance.list_invoices,'get':finance.get_invoice,'create':finance.create_invoice,'update':finance.update_invoice,'scope':True,'attachment':'invoice',
        'columns':[('invoice_no','发票号码'),('invoice_date','开票日期'),('project_name','项目'),('entity_name','经营主体'),('buyer_name_snapshot','购买方'),('amount_minor','价税合计'),('received_minor','已回款'),('unreceived_minor','发票余额'),('status','状态')],
        'fields':[PROJECT,select('contract_id','合同','contracts'),select('settlement_id','收入确认','settlements'),select('entity_id','经营主体（未确认可留空）','entities'),field('invoice_no','发票号码',required=True),AMOUNT,
                  field('invoice_date','开票日期','date',required=True,default=TODAY),field('buyer_name','购买方'),field('tax_rate_percent','税率（%）','number',default='0'),NOTES]},
    'receipts': {'title':'回款台账','list':finance.list_receipts,'get':finance.get_receipt,'create':finance.create_receipt,'update':finance.update_receipt,'scope':True,'attachment':'receipt',
        'columns':[('receipt_no','回款编号'),('receipt_date','回款日期'),('project_name','项目'),('entity_name','经营主体'),('payer_name_snapshot','付款人'),('amount_minor','到账金额'),('payment_method','收款方式'),('status','状态')],
        'fields':[PROJECT,select('contract_id','合同','contracts'),select('settlement_id','对应收入确认','settlements'),select('invoice_id','对应发票（可空）','invoices'),select('entity_id','经营主体（未确认可留空）','entities'),AMOUNT,
                  field('receipt_date','实际到账日期','date',required=True,default=TODAY),field('payer_name','实际付款人'),choice('payment_method','收款方式',['银行转账','现金','微信','支付宝'],'银行转账'),
                  field('allow_advance','明确作为预收款（须人工核对）','checkbox',default=False),NOTES],
        'limitation':'回款更正和作废均保留历史；有关联资金流水或已结清的记录须先核对联动，不能直接绕过。'},
    'accounts': {'title':'资金账户','list':funds.list_accounts,'columns':[('name','账户名称'),('kind','类型'),('opening_date','期初日期'),('opening_minor','期初余额'),('balance_minor','当前账面余额'),('status','状态')],
        'fields':[field('name','账户名称',required=True),choice('kind','账户类型',funds.ACCOUNT_KINDS,'bank'),field('opening_date','期初日期','date',required=True,default=TODAY),field('opening_amount','期初余额（元）','money',default='0'),field('reason','建立/调整依据',required=True),NOTES]},
    'plans': {'title':'待收待付计划','list':funds.list_plans,'columns':[('title','计划事项'),('direction','收支'),('due_date','计划日期'),('counterparty','对方'),('amount_minor','计划金额'),('remaining_minor','剩余金额'),('state','状态')],
        'fields':[choice('direction','方向',{'in':'待收','out':'待付'},'in'),field('title','计划事项',required=True),AMOUNT,field('due_date','计划日期','date'),field('counterparty','对方'),NOTES]},
    'transactions': {'title':'真实资金流水','list':funds.list_transactions,'columns':[('transaction_date','实际日期'),('category','类别'),('counterparty','对方'),('amount_minor','金额'),('from_account_name','转出账户'),('to_account_name','转入账户'),('status','状态')],
        'fields':[choice('category','流水类别', {**funds.CATEGORY_LABELS},'other_in'),select('from_account_id','转出账户','accounts'),select('to_account_id','转入/到账账户','accounts'),AMOUNT,
                  field('transaction_date','实际发生日期','date',required=True,default=TODAY),field('counterparty','对方'),choice('source_kind','业务来源',{'':'无业务来源','receipt':'已登记回款','purchase':'采购付款','cost':'费用付款','wage':'工资发放'},''),
                  field('source_id','业务来源记录编号','integer'),field('source_period','工资月份（YYYY-MM）'),select('plan_id','对应计划','plans'),NOTES]},
    'historical': {'title':'历史应收账款','list':history.list_projects,'columns':[('customer_name','客户'),('name','历史项目'),('opening_minor','期初应收'),('receipt_minor','已收'),('remaining_minor','未收')],
        'fields':[select('customer_id','客户','customers',True),field('name','历史项目名称',required=True),field('opening_amount','期初应收（未知可空）','money'),NOTES]},
    'input_invoices': {'title':'进项发票','list':inputs.list_invoices,'columns':[('invoice_no','票号'),('invoice_date','日期'),('seller_name','销售方'),('buyer_name','购买方'),('gross_minor','价税合计'),('deduction_status','抵扣状态')],
        'fields':[select('entity_id','经营主体','entities',True),select('supplier_id','销售方供应商','suppliers',True),field('invoice_no','发票号码',required=True),field('invoice_date','日期','date',required=True,default=TODAY),field('seller_name','销售方',required=True),
                  field('buyer_name','购买方',required=True),field('amount','价税合计（元）','money',required=True),field('net_amount','未税金额（元）','money',required=True),field('tax_amount','税额（元）','money',required=True),field('tax_rate_label','税率标识',required=True),field('deduction_period','已抵扣月份（YYYY-MM）'),choice('deduction_status','抵扣状态',{'pending':'待核实','eligible':'可抵扣','deducted':'已抵扣','ineligible':'不可抵扣'},'pending'),NOTES]},
}



# The browser exposes the same detail fields used by the desktop services.
DETAIL_FIELDS = {
    'projects': [field('customer_name','客户名称（未建档时）')],
    'contracts': [field('customer_name','客户名称（未建档时）'),choice('income_mode','收入依据',{'manual':'人工确认收入','invoice':'以开票作为收入依据','receipt':'以实际回款作为收入依据'},'manual')],
    'settlements': [field('settlement_no','收入确认编号'),field('period_start','结算期间开始','date'),field('period_end','结算期间结束','date')],
    'suppliers': [field('short_name','简称'),field('unified_credit_code','统一社会信用代码'),field('registered_address','注册地址'),field('business_address','经营地址'),field('invoice_phone','开票电话'),field('bank_name','开户银行'),field('bank_account','银行账号'),field('price_level','价格评价'),field('delivery','交付评价'),field('quality','质量评价'),choice('export','出口能力',['是','否'],'否'),field('default_tax_rate_percent','默认税率（%）','number',default='0')],
    'customers': [field('business_address','经营地址'),field('invoice_phone','开票电话'),field('customer_category','客户类别'),field('settlement_terms','结算约定'),field('credit_limit','信用额度（元）','money',default='0')],
    'purchases': [field('order_no','采购单号')],
    'worklogs': [select('project_site_id','项目施工地点','sites'),field('daily_rate','本条日工资（空则按日期取工资版本）','money')],
    'invoices': [field('tax_rate_label','特殊税率标识（多税率 / 免税 / 不征税）'),field('net_amount','未税金额（可留空）','money'),field('tax_amount','税额（可留空）','money')],
    'receipts': [field('receipt_no','回款编号')],
    'plans': [choice('source_kind','计划业务来源',{'':'无具体业务来源','project':'项目待收','purchase':'采购待付','cost':'费用待付','wage':'工资待付'},''),field('source_id','来源记录编号','integer'),field('source_period','工资月份 YYYY-MM'),field('reason','建立 / 更正依据')],
    'input_invoices': [field('seller_tax_number','销售方税号'),field('buyer_tax_number','购买方税号')],
}
for module, additions in DETAIL_FIELDS.items():
    SPECS[module]['fields'].extend(additions)
SPECS['settlements']['fields']=[item for item in SPECS['settlements']['fields'] if item['name']!='notes']
for module in ('suppliers','customers'):
    SPECS[module]['fields'].extend([field('contact_department','联系人部门'),field('contact_title','联系人职务'),field('contact_email','联系人邮箱'),field('contact_wechat','联系人微信')])

NAVIGATION = [
    ('经营决策',[('home','经营驾驶舱'),('profit','项目经营核算'),('governance','数据治理'),('entities','经营主体')]),
    ('合同资金',[('contracts','合同与结算'),('cashflow','资料现金流分析'),('finance','开票与回款'),('funds','资金与计划'),('cost','成本台账')]),
    ('项目履约',[('projects','项目台账'),('construction','施工与验收'),('purchases','采购管理'),('labor','人工与工天')]),
    ('供应链资料',[('suppliers','供应商'),('customers','客户档案'),('offers','材料与报价'),('compare','报价对比')]),
    ('数据工具',[('assistant','经营助手'),('tools','数据导入导出')]),
]


def schema():
    result = {}
    today = date.today().isoformat()
    special_create = {'purchases','accounts','plans','transactions','historical','input_invoices'}
    special_update = {'purchases','accounts','plans','historical','input_invoices'}
    for key, spec in SPECS.items():
        result[key] = {name:value for name,value in spec.items() if not callable(value)}
        result[key]['fields'] = [
            {**item, 'default': today, 'default_today': True}
            if item['kind'] == 'date' and item['default'] == TODAY else dict(item)
            for item in spec['fields']
        ]
        result[key]['can_create'] = 'create' in spec or key in special_create
        result[key]['can_update'] = 'update' in spec or key in special_update
    return result


def money_input(value, *, zero=True):
    try:
        amount = Decimal(str(value)) * 100
        if not amount.is_finite() or amount < 0 or (not zero and amount == 0) or amount != amount.to_integral_value() or amount > 9_000_000_000_000_000:
            raise ValueError
        return int(amount)
    except (ValueError, InvalidOperation):
        raise ValueError('金额须为非负且最多两位小数的人民币金额') from None


def money_form_value(value):
    cents = int(value)
    return f'{cents // 100}.{cents % 100:02d}'


def offer_quoted_minor(row):
    # Older exclusive offers predate the explicit quoted-price column.
    if row['quoted_price_minor'] is not None:
        return row['quoted_price_minor']
    return row['price_minor']


def offer_option_label(row):
    basis = '含税' if row['price_basis'] == 'inclusive' else '未税'
    price = f'{basis} ¥{money_form_value(offer_quoted_minor(row))}/{row["unit"] or "单位未填"}'
    return ' · '.join([row['name'], row['specification'] or '规格未填',
                       row['supplier_name'], price, f'报价#{row["id"]}'])


def options(project_id=None):
    def items(rows, label, id_key='id'):
        return [{'value':row[id_key], 'label':label(row)} for row in rows]
    project_rows = projects.list_projects()
    contract_rows = contracts.list_contracts(project_id=project_id)
    worker_rows = labor.get_workers()
    settlement_rows = contracts.list_settlements(project_id=project_id)
    invoice_rows = finance.list_invoices(project_id=project_id)
    invoice_settlements = {}
    with db_read() as conn:
        for row in conn.execute('''SELECT a.invoice_id,a.settlement_id FROM invoice_settlement_allocations a
            JOIN settlements s ON s.id=a.settlement_id WHERE s.status='active' '''):
            invoice_settlements.setdefault(row['invoice_id'], []).append(row['settlement_id'])
    return {
        'projects':[{'value':row['id'],'label':f'{row["name"]} · {row["project_code"]}',
                     'business_mode':row['business_mode']} for row in project_rows],
        'active_projects':items([r for r in project_rows if r['status'] in ('进行中','筹备中')], lambda r:f'{r["name"]} · {r["project_code"]}'),
        'contracts':[{'value':row['id'],'label':f'{row["contract_no"]} · {row["name"]}',
                      'income_mode':row['income_mode']} for row in contract_rows],
        'customers':items(partners.list_customers(), lambda r:r.get('legal_name') or r.get('name','')),
        'suppliers':items(partners.list_suppliers(), lambda r:r['name']),
        'offers':[{'value':row['id'], 'label':offer_option_label(row), 'supplier_id':row['supplier_id']}
                  for row in partners.list_supplier_offers()],
        'workers':items(worker_rows, lambda r:r['name']),
        'active_workers':items([r for r in worker_rows if r['status']=='在职'], lambda r:r['name']),
        'settlements':[{'value':row['id'],'label':row['settlement_no'],
                        'project_id':row['project_id'],'contract_id':row['contract_id']}
                       for row in settlement_rows],
        'invoices':[{'value':row['id'],'label':row['invoice_no'],
                     'project_id':row['project_id'],'contract_id':row['contract_id'],
                     'settlement_ids':invoice_settlements.get(row['id'],[])} for row in invoice_rows],
        'accounts':items(funds.list_accounts(), lambda r:r['name']),
        'plans':items(funds.list_plans(), lambda r:r['title']),
        'entities':items(entities.list_entities(), lambda r:r['name']),
        'sites':items(projects.list_project_sites(project_id), lambda r:r['site_name']) if project_id else [],
    }


def prepare_purchase_items(items):
    if not isinstance(items, list) or not 0 < len(items) <= 200:
        raise ValueError('请添加1至200条采购材料明细')
    prepared = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError('材料明细格式无效')
        item = dict(item)
        for key in ('product_id', 'item_id'):
            value = item.get(key)
            if value in ('', None):
                item[key] = None
                continue
            identity = str(value).strip()
            if not identity.isascii() or not identity.isdigit() or int(identity) <= 0:
                raise ValueError('材料报价或明细编号无效，请重新打开采购单核对')
            item[key] = int(identity)
        item['material_unit_price_cents'] = money_input(item.pop('unit_price', '0'))
        item['tax_inclusive_unit_price_cents'] = item['material_unit_price_cents']
        item['tax_rate_bps'] = money_input(item.pop('tax_percent', '0'))
        if item.get('settlement_mode') == 'total':
            item['settlement_total_cents'] = money_input(item.pop('settlement_total', '0'))
        prepared.append(item)
    return prepared


def purchase_preview(items, freight='0'):
    """Reuse authoritative item math without creating or updating a ledger row."""
    amounts = [procurement.calculate_purchase_item_amounts({}, item)
               for item in prepare_purchase_items(items)]
    freight_cents = money_input(freight)
    material = sum(row['line_amount_cents'] for row in amounts)
    return {'items': amounts, 'material_amount_cents': material,
            'freight_amount_cents': freight_cents, 'total_amount_cents': material + freight_cents}


def rows(module, project_id=None, keyword=''):
    if module not in SPECS:
        raise ValueError('未知业务模块')
    spec = SPECS[module]
    if module == 'projects':
        result = project_ledger.list_projects(project_id, keyword)
    elif module == 'purchases':
        result = procurement.list_purchase_orders(project_id=project_id, keyword=keyword)
        for row in result:
            row['material_details'] = '\n'.join(
                ' · '.join(value for value in (
                    item.get('material_name_snapshot'), item.get('specification_snapshot'),
                ) if value) or '材料名称未记录'
                for item in row.get('items') or [row]
            )
    elif spec.get('scope'):
        result = spec['list'](project_id=project_id)
    else:
        result = spec['list']()
    if module == 'suppliers':
        for row in result:
            partner = partners.get_business_partner(row['id'])
            row['contact_phone'] = partner['contact_phone']
    if module in ('projects','contracts','settlements','invoices','receipts'):
        kind = 'sales_invoices' if module == 'invoices' else module
        with db_read() as conn:
            from services.entity_ownership import OwnershipResolver
            resolver = OwnershipResolver(conn)
            originals = {r['id']: r for r in conn.execute(f'SELECT * FROM {kind}')}
            for row in result:
                row.update(resolver.record(kind, originals[row['id']]))
    if project_id and module == 'worklogs':
        result = [r for r in result if r.get('project_id') == project_id]
    if keyword and module != 'purchases':
        result = [r for r in result if keyword.casefold() in json.dumps(r, ensure_ascii=False, default=str).casefold()]
    return result


def get_record(module, record_id):
    if module not in SPECS:
        raise ValueError('未知业务模块')
    spec = SPECS[module]
    if 'get' in spec:
        row = spec['get'](record_id)
    else:
        row = next((r for r in rows(module) if r['id'] == record_id), None)
    if not row:
        raise ValueError('记录不存在，请刷新后核对')
    if module == 'suppliers':
        row = {**partners.get_business_partner(record_id), **row}
    if module in ('contracts','invoices', 'receipts'):
        with db_read() as conn:
            row = {**row, 'entity_id': entities.entity_for(
                conn, 'sales_invoices' if module == 'invoices' else module, record_id)}
            if module == 'receipts':
                row['_receipt_income_owned'] = bool(conn.execute('''SELECT 1
                    FROM receipt_confirmed_settlements l JOIN settlements s ON s.id=l.settlement_id
                    WHERE l.receipt_id=? AND s.status='active' ''', (record_id,)).fetchone())
    result = dict(row)
    result['_version'] = digest(json.loads(json.dumps(row, ensure_ascii=False, default=lambda value: sorted(value) if isinstance(value,set) else str(value))))
    aliases = {'allocated_amount_minor':'amount','amount_minor':'amount','tax_inclusive_amount_minor':'amount','control_limit_minor':'control_limit',
               'price_minor':'price','opening_minor':'opening_amount','freight_amount_cents':'freight_amount',
               'work_amount_cents':'work_amount','cash_agreed_amount_minor':'cash_agreed_amount','credit_limit_minor':'credit_limit','net_amount_minor':'net_amount','tax_amount_minor':'tax_amount','daily_rate_minor':'daily_rate'}
    for key,target in aliases.items():
        if key in row and row[key] is not None:
            result[target] = money_form_value(row[key])
    for key,target in [('customer_partner_id','customer_partner_id'),('supplier_partner_id','supplier_id'),('counterparty_name_snapshot','counterparty_name'),('payer_name_snapshot','payer_name'),('buyer_name_snapshot','buyer_name')]:
        if key in row:
            result[target] = row[key]
    if module == 'offers':
        result['price'] = money_form_value(offer_quoted_minor(row))
        result['tax_rate_percent'] = str(row['tax_rate_bps']/100)
    if module == 'invoices':
        result['tax_rate_percent'] = str(row.get('tax_rate_bps',0)/100)
    if module == 'input_invoices':
        for source, target in [('gross_minor','amount'),('net_minor','net_amount'),('tax_minor','tax_amount')]:
            result[target] = money_form_value(row[source])
    if module == 'purchases':
        result['supplier_id'] = row.get('supplier_partner_id')
    if module == 'costs':
        allocation=costs.get_cost_allocations(record_id)
        if allocation['method']=='direct' and len(allocation['lines'])==1:
            result['project_id']=allocation['lines'][0]['project_id']
    return result


def save_record(module, data, record_id=None, expected=None):
    if module not in SPECS:
        raise ValueError('未知业务模块')
    spec = SPECS[module]
    previous = {}
    if record_id:
        previous = get_record(module, record_id)
        if not expected or previous['_version'] != expected:
            raise ValueError('记录版本已变化，请刷新并核对后再修改；原输入未提交')
    values = {**previous, **data}
    if module=='costs' and record_id and 'project_id' in data and (int(data['project_id']) if data['project_id'] else None) != previous.get('project_id'):
        raise ValueError('费用项目分摊请通过成本归集操作调整；本次未改变费用或项目关联')
    for item in schema()[module]['fields']:
        key = item['name']
        if key not in values:
            values[key] = item['default']
        if item['required'] and values[key] in ('', None):
            raise ValueError('请填写' + item['label'])
        if module == 'purchases' and item['choices'] is not None and values[key] not in ('', None) and values[key] not in item['choices']:
            if not record_id or values[key] != previous.get(key):
                raise ValueError(item['label'] + '选项无效，请重新选择')
        if (item['kind'] == 'select' and item.get('source')) or item['kind'] == 'integer':
            if key == 'entity_id' and values[key] not in ('', None):
                if not str(values[key]).strip().isascii() or not str(values[key]).strip().isdigit():
                    raise ValueError('请选择有效的经营主体')
            values[key] = int(values[key]) if values[key] not in ('', None) else None
        if item['kind'] == 'date' and values[key] not in ('', None):
            try:
                parsed_date = date.fromisoformat(str(values[key]))
            except ValueError:
                raise ValueError(item['label'] + '须为有效的 YYYY-MM-DD 日期') from None
            if parsed_date.isoformat() != values[key]:
                raise ValueError(item['label'] + '须为 YYYY-MM-DD 日期')
        if item['kind'] == 'money' and values[key] not in ('', None):
            money_input(values[key])
    if module == 'worklogs':
        if not record_id or values['worker_id'] != previous['worker_id']:
            labor.validate_active_work_log_workers([values['worker_id']])
        for key in ('daily_rate_minor','amount','amount_minor'):
            values.pop(key, None)
    if module == 'customers':
        values['roles'] = list(set(previous.get('roles', [])) | {'customer'})
    if module in ('suppliers','customers'):
        contact=values.get('contact') if module=='suppliers' else values.get('contact_name')
        if not str(contact or '').strip() and any(str(values.get(key) or '').strip() for key in ('contact_phone','contact_email','contact_wechat','contact_department','contact_title')):
            raise ValueError('填写电话、邮箱或其他联系人资料时，请同时填写联系人姓名；原服务需要姓名才能保存联系资料')
    if module == 'invoices':
        values['tax_rate'] = values['tax_rate_percent']
    if module == 'construction':
        if values['end_date'] < values['start_date']:
            raise ValueError('施工结束日期不能早于开始日期')
        values['work_amount_cents'] = money_input(values['work_amount'])
    if module == 'purchases':
        values['freight_amount_cents'] = money_input(values['freight_amount'])
        prepared = prepare_purchase_items(data.get('items'))
        if record_id:
            procurement.update_purchase_order(record_id, values, prepared)
            return record_id
        return procurement.add_purchase_order(values, prepared)
    if module == 'accounts':
        return funds.save_account(values, record_id)
    if module == 'plans':
        return funds.save_plan(values, record_id)
    if module == 'transactions':
        if record_id:
            raise ValueError('实际资金流水不直接编辑，请按现有业务更正流程处理')
        values['request_key'] = data.get('request_key') or str(uuid4())
        return funds.record_transaction(values)
    if module == 'historical':
        return history.save_project(values['customer_id'], values['name'], values.get('opening_amount') or None,
                                    values.get('notes',''), record_id)
    if module == 'input_invoices':
        if record_id:
            inputs.update_invoice(record_id, values)
            return record_id
        return inputs.create_invoice(values)
    if record_id:
        if 'update' not in spec:
            raise ValueError('该记录的调整入口尚未迁移，请使用保留的桌面入口')
        spec['update'](record_id, values)
        return record_id
    return spec['create'](values)


def dashboards(view, project_id=None, month=None):
    if view == 'home':
        from services.cockpit_service import get_overview
        return get_overview(project_id, month)
    if view == 'profit':
        if project_id:
            result = profit.get_project_summary(project_id)
            return {'projects':[{'id': project_id, 'project_name':result['project']['name'], **result}], 'details':result}
        result = profit.get_portfolio_summary()
        result['projects'] = [{'id':r['project']['id'], 'project_name':r['project']['name'], **r} for r in result['projects']]
        return result
    if view == 'finance':
        return finance.get_finance_dashboard(project_id)
    if view == 'governance':
        projects = governance.list_project_completeness()
        if project_id:
            projects = [row for row in projects if row['id'] == int(project_id)]
        return {'summary':governance.get_governance_summary(), 'unassigned_labor':governance.list_unassigned_labor(),
                'unassigned_purchases':governance.list_unassigned_purchases(), 'projects':projects}
    if view == 'cost':
        from services.web_workbench_costs import ledger_dashboard
        return ledger_dashboard(project_id, month)
    if view == 'assistant':
        return {'summary':finance.get_finance_dashboard(project_id)['summary'], 'projects':finance.get_finance_dashboard(project_id)['projects'],
                'mode':'本地确定性经营查询，复用现有服务；未调用外部模型，不模拟AI生成。'}
    if view == 'collection':
        return {'rows':collection.list_project_cases(project_id,include_settled=True)}
    raise ValueError('未知分析页面')


def action(name, data):
    record_id = int(data.get('id') or 0)
    reason = str(data.get('reason') or '').strip()
    if name == 'plan_cancel':
        return funds.cancel_plan(record_id, reason)
    if name == 'inspection':
        values = dict(data)
        values['inspection_date'] = values.get('inspection_date') or date.today().isoformat()
        return construction.update_construction_inspection(record_id, values)
    if name == 'assign_labor':
        return governance.assign_labor_records(data['ids'], int(data['project_id']))
    if name == 'assign_purchase':
        return governance.assign_purchase_orders(data['ids'], int(data['project_id']))
    if name == 'collection':
        return collection.save_project_case(record_id, data)
    if name == 'project_close':
        return projects.close_projects([record_id])
    if name == 'project_site':
        return projects.create_project_site(int(data['project_id']), data)
    if name == 'historical_receipt':
        return history.record_receipt(int(data['project_id']), data['receipt_date'], data['amount'],
                                      data['request_key'], payer_name=data.get('payer_name'))
    raise ValueError('此操作尚未迁移或被明确禁止；请核对迁移清单')
