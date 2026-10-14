"""Reviewable browser operations over the existing business services."""
import json
from datetime import date

from db.connection import db_read, use_read_snapshot
from services import web_workbench_service as b
from services import attachment_service as attachments
from services import project_cost_detail_service as cost_details
from services import ai_operating_query_service as local_query
from services.document_cashflow_extract import digest

F=b.field
C=b.choice
S=b.select
REASON=F('reason','操作依据 / 原因','textarea',required=True)
IDS=F('ids','选择记录','multi',required=True)
PROJECTS=F('project_ids','承担项目','multi',source='projects')


def spec(module,title,fields=(),*,read=None):
    return {'module':module,'title':title,'fields':list(fields),'read':read}


OPERATIONS={
 'collection_followup':spec('collection','更新回款跟进',[F('owner_name','跟进人'),F('due_date','应收到期日','date'),F('promised_date','客户承诺日','date'),F('next_followup_date','下次跟进日','date'),C('status','跟进状态',b.collection.CASE_STATUSES,'pending'),F('overdue_reason','逾期原因'),F('next_action','下一步'),F('notes','备注','textarea'),F('followup_date','本次跟进日期','date'),F('followup_content','本次跟进内容','textarea'),REASON]),
 'collection_history':spec('collection','回款跟进历史',read='collection_history'),
 'customer_business':spec('customers','客户业务与年度贡献',read='customer_business'),
 'offer_history':spec('offers','报价修改历史',read='offer_history'),
 'labor_batch':spec('workers','批量登记工天',[F('entries','工天明细','worklog_rows',required=True),REASON]),
 'cost_allocate':spec('costs','费用归集与分摊',[C('method','归集方式',{'direct':'单项目','equal':'多项目均摊','manual':'手工金额','unassigned':'暂不归集'},'direct'),PROJECTS,F('allocations','手工分摊明细','allocation_rows'),REASON]),
 'cost_void':spec('costs','作废手工费用',[REASON]),
 'cost_history':spec('costs','费用修改历史',read='cost_history'),
 'cost_allocations':spec('costs','费用当前归集',read='cost_allocations'),
 'purchase_allocate':spec('purchases','采购项目归集',[C('method','归集方式',{'direct':'单项目','equal':'工具设备均摊','unassigned':'暂不归集'},'direct'),PROJECTS,REASON]),
 'purchase_void':spec('purchases','作废采购单',[REASON]),
 'purchase_status':spec('purchases','采购支付与票据状态',[F('payment_method','支付方式'),F('payment_status','支付状态'),F('invoice_status','票据状态'),REASON]),
 'purchase_history':spec('purchases','采购价格修改历史',read='purchase_history'),
 'labor_adjust':spec('workers','调薪与批量回算',[S('worker_id','工人','workers',True),F('new_daily_rate','新日工资（元）','money',required=True),F('effective_from','生效日期','date',required=True),C('scope_mode','影响范围',b.labor.RATE_ADJUSTMENT_MODES,'future_only'),F('range_end','自定义截止日期','date'),S('project_id','自定义项目范围','projects'),REASON]),
 'labor_adjust_history':spec('workers','工资版本与调薪历史',read='labor_adjust_history'),
 'labor_lock':spec('worklogs','锁定或解锁工资',[IDS,F('locked','锁定工资','checkbox',default=True),REASON]),
 'labor_overtime':spec('worklogs','设置加班标记',[IDS,F('is_overtime','加班','checkbox',default=True),REASON]),
 'labor_void':spec('worklogs','作废工天记录',[IDS,REASON]),
 'worker_delete':spec('workers','删除无业务记录的工人',[REASON]),
 'project_close':spec('projects','关闭项目',[REASON]),
 'project_cost_details':spec('projects','项目成本明细',read='project_cost_details'),
 'site_update':spec('projects','修改施工地点',[S('site_id','施工地点','sites',True),F('site_code','地点编码',required=True),F('site_name','地点名称',required=True),F('address','地址'),REASON]),
 'site_deactivate':spec('projects','停用施工地点',[S('site_id','施工地点','sites',True),REASON]),
 'contract_void':spec('contracts','作废合同',[REASON]),
 'allocation_update':spec('allocations','调整合同项目分配',[F('amount','分配金额（元）','money',required=True),REASON]),
 'allocation_void':spec('allocations','作废项目分配',[REASON]),
 'settlement_void':spec('settlements','作废收入确认',[REASON]),
 'invoice_void':spec('invoices','作废销项发票',[REASON]),
 'receipt_allocation':spec('receipts','调整收入核销明细',[F('allocations','收入确认核销金额','settlement_rows'),F('allow_advance','余额明确作为预收款','checkbox'),REASON]),
 'receipt_void':spec('receipts','作废录错的回款（保留记录）',[REASON]),
 'receipt_history':spec('receipts','查看回款修改历史',read='receipt_history'),
 'fund_verify':spec('funds','核实业务期初付款',[C('kind','业务来源',{'purchase':'采购','cost':'费用','wage':'工资'},'purchase'),S('source_id','业务记录','fund_sources',True),F('source_period','工资月份（YYYY-MM）'),F('settled_amount','资金启用前已付款（元）','money',required=True),F('payee','收款单位 / 人员',required=True),REASON]),
 'fund_reconcile':spec('accounts','核对账户余额',[S('account_id','资金账户','accounts',True),F('balance_date','核对日期','date',required=True),F('actual_amount','实查余额（元）','money',required=True),REASON]),
 'fund_archive':spec('accounts','停用或启用账户',[F('archived','停用账户','checkbox',default=True),REASON]),
 'fund_void_transaction':spec('transactions','作废录错的资金流水',[REASON]),
 'fund_link_plan':spec('transactions','关联或调整对应收付计划',[S('plan_id','正式计划','plans',True),REASON]),
 'input_links':spec('input_invoices','关联进项票成本',[F('links','采购 / 费用关联明细','invoice_links'),REASON]),
 'input_deduction':spec('input_invoices','核对进项税抵扣状态',[C('deduction_status','抵扣状态',{'pending':'待核实','eligible':'可抵扣','deducted':'已抵扣','ineligible':'不可抵扣'},'pending'),F('deduction_period','已抵扣月份（YYYY-MM）'),REASON]),
 'input_void':spec('input_invoices','作废进项发票',[REASON]),
 'entity_assign':spec('entities','确认业务经营主体',[C('kind','业务类型',{k:v[0] for k,v in b.entities.RECORDS.items()},'contracts'),F('ids','业务记录','multi',source='entity_records',required=True),S('entity_id','经营主体','entities',True),REASON]),
 'entity_tax':spec('entities','维护主体税号',[S('entity_id','经营主体','entities',True),F('tax_number','税号'),REASON]),
 'historical_receipt':spec('historical','登记历史项目回款',[F('receipt_date','实际回款日期','date',required=True),F('amount','金额（元）','money',required=True),F('payer_name','付款方'),F('payment_method','支付方式',default='待确认'),F('notes','备注','textarea'),REASON]),
 'supplier_deactivate':spec('suppliers','停用供应商',[REASON]),
 'customer_deactivate':spec('customers','停用客户档案',[REASON]),
 'offer_deactivate':spec('offers','停用报价',[REASON]),
 'construction_void':spec('construction','作废施工记录',[REASON]),
 'attachment_void':spec('attachments','移出业务附件',[IDS,REASON]),
 'governance_customer':spec('governance','确认项目客户',[S('project_id','项目','projects',True),F('customer_name','客户名称',required=True),F('update_contracts','同时核对关联合同客户','checkbox'),REASON]),
 'governance_labor':spec('governance','批量归集工天',[F('ids','工天记录','multi',source='unassigned_labor',required=True),S('project_id','归属项目','projects',True),S('project_site_id','施工地点','sites'),REASON]),
 'governance_purchase':spec('governance','批量归集采购',[F('ids','采购记录','multi',source='unassigned_purchases',required=True),S('project_id','归属项目','projects',True),REASON]),
}

BLOCKED=[
 {'operation':'外部模型对话 / 远程OCR','reason':'本任务明确禁止上传真实项目资料到外部服务；未发现已配置的本地语义模型或OCR。'},
 {'operation':'数据库自动升级','reason':'启动不自动升级账本；缺失迁移须单独演练。'},
]


def database_fingerprint(conn):
    tables=[row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    snapshot={}
    for table in tables:
        if table in {'daily_self_checks','operating_attention_states','operating_check_history','assistant_entry_drafts'}:
            continue
        quoted='"'+table.replace('"','""')+'"'
        snapshot[table]=[dict(row) for row in conn.execute(f'SELECT * FROM {quoted} ORDER BY rowid')]
    return digest(json.loads(json.dumps(snapshot,ensure_ascii=False,default=str)))


def prepare(name,data):
    if name not in OPERATIONS or OPERATIONS[name]['read']:
        raise ValueError('不支持的业务操作；请核对受阻清单')
    definition=OPERATIONS[name]
    values=dict(data)
    values['id']=int(values.get('id') or 0)
    values['ids']=list(dict.fromkeys(int(v) for v in values.get('ids',[]) if v))
    values['project_ids']=[int(v) for v in values.get('project_ids',[]) if v]
    for field in definition['fields']:
        key=field['name']
        value=values.setdefault(key,field['default'])
        if field['required'] and (value in ('',None) or value==[]):
            raise ValueError('请填写或选择'+field['label'])
        if field['kind']=='money' and value not in ('',None):
            b.money_input(value)
        if field.get('choices'):
            if value not in field['choices']:
                raise ValueError(field['label']+'选项无效')
        if field['kind']=='checkbox' and not isinstance(value,bool):
            raise ValueError(field['label']+'必须明确选择')
        if field['kind']=='date' and value:
            try:
                if date.fromisoformat(str(value)).isoformat()!=value:
                    raise ValueError
            except ValueError:
                raise ValueError(field['label']+'须为 YYYY-MM-DD') from None
        if field.get('source') and field['kind']=='select':
            values[key]=int(value) if value else None
    record_id=values['id']
    module=definition['module']
    before=b.get_record(module,record_id) if record_id and module in b.SPECS else None
    review={'title':definition['title'],'before':before,'reason':values.get('reason'),'rows':[]}
    if name=='collection_followup':
        review['before']=b.collection.get_project_case(record_id)
    elif name=='labor_batch':
        if (not isinstance(values.get('entries'),list) or not 1 <= len(values['entries']) <= 200
                or any(not isinstance(row,dict) for row in values['entries'])):
            raise ValueError('请选择1至200条工天明细')
        review['rows']=b.labor.preview_work_logs_batch(values['entries'],active_only=True)
        review['total_minor']=sum(row['amount_minor'] for row in review['rows'])
    elif name=='cost_allocate':
        review['rows']=b.costs.build_allocation_plan(before['amount'],values['method'],values['project_ids'],values.get('allocations'))
        review['total_minor']=before['amount_minor']
    elif name=='purchase_allocate':
        method=values['method']
        if method=='equal':
            if before['items'][0]['cost_category']!=b.procurement.TOOL_EQUIPMENT_CATEGORY:
                raise ValueError('仅工具和设备采购支持多项目均摊')
            review['rows']=b.procurement.build_equal_allocation_plan(before['total_amount_cents'],values['project_ids'])
        elif method=='direct' and len(values['project_ids'])!=1:
            raise ValueError('单项目归集请选择且只选一个项目')
        elif method not in ('direct','equal','unassigned'):
            raise ValueError('采购归集方式无效')
        review['total_minor']=before['total_amount_cents']
    elif name=='labor_adjust':
        review['impact']=b.labor.preview_rate_adjustment(values)
        review['rows']=review['impact']['project_impacts']
    elif name=='receipt_allocation':
        receipt=dict(before,allow_advance=bool(values.get('allow_advance')),
                     settlement_allocations=values.get('allocations',[]))
        review['rows']=b.finance.preview_receipt_allocations(receipt,exclude_receipt_id=record_id)
    elif name=='receipt_void':
        validate_receipt_void(record_id)
        review['notice']='只作废录错的到账事实，保留原回款与修改历史，重新计算发票余额和未回款；不撤销已确认收入，也不删除附件。'
    elif name=='fund_verify':
        source=next((r for r in b.funds.list_sources(values['kind'],include_closed=True)
                     if r['id']==values['source_id'] and r.get('period','')==values.get('source_period','')),None)
        if not source:
            raise ValueError('业务来源不存在或工资月份不匹配')
        review['before']=source
        review['settled_minor']=b.money_input(values['settled_amount'])
    elif name=='fund_reconcile':
        account=next((r for r in b.funds.list_accounts(values['balance_date']) if r['id']==values['account_id']),None)
        if not account:
            raise ValueError('资金账户不存在')
        review['book_minor']=account['balance_minor']
        review['actual_minor']=b.money_input(values['actual_amount'])
        review['difference_minor']=review['actual_minor']-review['book_minor']
        review['notice']='核对只记录差异；不会创建调整流水或强行抹平余额。'
    elif name=='input_links':
        review['rows']=values.get('links',[])
        for row in review['rows']:
            b.money_input(row['amount'],zero=False)
    elif name=='entity_assign':
        impact = b.entities.preview_assignment(values['kind'], values['ids'], values['entity_id'])
        review.update(rows=impact['rows'], notice=impact['notice'], ownership_projects=impact['projects'])
    elif name in ('governance_purchase', 'governance_labor'):
        purchases = name == 'governance_purchase'
        candidates = b.governance.list_unassigned_purchases() if purchases else b.governance.list_unassigned_labor()
        selected = set(values['ids'])
        chosen = [row for row in candidates if row['id'] in selected]
        if len(chosen) != len(selected):
            raise ValueError('部分记录已归集、作废或不存在，请刷新后核对')
        target = b.projects.get_project(values['project_id'])
        if not target or target['status'] == '已关闭':
            raise ValueError('目标项目不存在或已关闭')
        keys = ('id','order_no','purchase_date','supplier_name','material_details','amount_minor','notes','items','freight_amount_cents','payment_status') if purchases else (
            'id','work_date','worker_name','construction_site','work_type','work_days','amount_minor','notes')
        review['rows'] = [{key: row[key] for key in keys} for row in chosen]
        review['source_kind'] = 'purchases' if purchases else 'worklogs'
        review['total_minor'] = sum(row['amount_minor'] for row in chosen)
        review['notice'] = f"归集到：{target['name']} · {target['project_code']}；仅改变明确选中记录的项目归属，不改原金额。"
    elif name in ('labor_lock','labor_overtime','labor_void'):
        review['rows']=[b.get_record('worklogs',record_id) for record_id in values['ids']]
    elif name in ('site_update','site_deactivate'):
        review['before']=next((r for r in b.projects.list_project_sites(record_id,include_inactive=True) if r['id']==values['site_id']),None)
        if not review['before']:
            raise ValueError('施工地点不属于当前项目')
    review['changes']={field['label']:values[field['name']] for field in definition['fields'] if field['name'] not in ('reason','allocations','links')}
    with db_read() as conn,use_read_snapshot(conn):
        fingerprint=database_fingerprint(conn)
    return values,review,fingerprint


def apply(name,values,request_key):
    i=values['id'];reason=values.get('reason','');ids=values.get('ids') or [i]
    if name=='collection_followup':return b.collection.save_project_case(i,values)
    if name=='labor_batch':return b.labor.add_work_logs_batch(values['entries'],active_only=True)
    if name=='cost_allocate':return b.costs.allocate_cost(i,values['method'],values['project_ids'],values.get('allocations'))
    if name=='cost_void':return b.costs.void_costs([i])
    if name=='purchase_allocate':
        record=b.get_record('purchases',i)
        record.update(allocation_method=values['method'],project_ids=values['project_ids'],
                      project_id=values['project_ids'][0] if values['method']=='direct' else None)
        return b.procurement.update_purchase_order(i,record,record['items'])
    if name=='purchase_void':return b.procurement.void_purchase_orders([i])
    if name=='purchase_status':return b.procurement.update_purchase_order_status([i],values['payment_method'],values['payment_status'],values['invoice_status'])
    if name=='labor_adjust':return b.labor.apply_rate_adjustment(values)
    if name=='labor_lock':return b.labor.set_work_logs_rate_locked(ids,bool(values['locked']),reason)
    if name=='labor_overtime':return b.labor.set_work_logs_overtime(ids,bool(values['is_overtime']))
    if name=='labor_void':return b.labor.delete_work_logs(ids)
    if name=='worker_delete':return b.labor.delete_workers([i])
    if name=='project_close':return b.projects.close_projects([i])
    if name=='site_update':return b.projects.update_project_site(values['site_id'],values)
    if name=='site_deactivate':return b.projects.deactivate_project_sites([values['site_id']])
    if name=='contract_void':return b.contracts.void_contracts([i])
    if name=='allocation_update':return b.contracts.update_allocation_amount(i,values['amount'],reason)
    if name=='allocation_void':return b.contracts.void_allocations([i])
    if name=='settlement_void':return b.contracts.void_settlements([i])
    if name=='invoice_void':return b.finance.void_invoices([i])
    if name=='receipt_allocation':
        record=b.get_record('receipts',i)
        record.update(settlement_allocations=values['allocations'],allow_advance=bool(values.get('allow_advance')))
        return b.finance.update_receipt(i,record)
    if name=='receipt_void':
        validate_receipt_void(i)
        b.finance.void_receipts([i])
        from db.connection import db_transaction
        with db_transaction() as conn:
            conn.execute("UPDATE receipts SET notes=COALESCE(notes,'') || ? WHERE id=? AND status='void'",
                         ('\n[作废原因] ' + reason, i))
        return i
    if name=='fund_verify':return b.funds.verify_source(values['kind'],values['source_id'],values.get('source_period',''),values)
    if name=='fund_reconcile':return b.funds.reconcile_account(values['account_id'],values['balance_date'],values['actual_amount'],reason)
    if name=='fund_archive':return b.funds.set_account_archived(i,bool(values['archived']))
    if name=='fund_void_transaction':return b.funds.void_transaction(i,reason)
    if name=='fund_link_plan':return b.funds.link_transaction_plan(i,values['plan_id'],reason)
    if name=='input_links':return b.inputs.update_links(i,values['links'])
    if name=='input_deduction':return b.inputs.set_deduction(i,values['deduction_status'],values.get('deduction_period',''),reason)
    if name=='input_void':return b.inputs.void_invoice(i,reason)
    if name=='entity_assign':return b.entities.assign_records(values['kind'],ids,values['entity_id'],reason)
    if name=='entity_tax':return b.entities.update_tax_number(values['entity_id'],values['tax_number'])
    if name=='historical_receipt':return b.history.record_receipt(i,values['receipt_date'],values['amount'],request_key,
        values.get('payment_method') or '待确认',values.get('notes',''),payer_name=values.get('payer_name'))
    if name=='supplier_deactivate':return b.partners.deactivate_suppliers([i])
    if name=='customer_deactivate':return b.partners.deactivate_business_partners([i])
    if name=='offer_deactivate':return b.partners.deactivate_supplier_offers([i])
    if name=='construction_void':return b.construction.void_construction_records([i])
    if name=='attachment_void':return attachments.void_attachments(ids)
    if name=='governance_customer':return b.governance.confirm_project_customer(values['project_id'],values['customer_name'],update_contracts=bool(values.get('update_contracts')))
    if name=='governance_labor':return b.governance.assign_labor_records(ids,values['project_id'],values.get('project_site_id'))
    if name=='governance_purchase':return b.governance.assign_purchase_orders(ids,values['project_id'])
    raise ValueError('不支持的业务操作')


def validate_receipt_void(receipt_id):
    from services.cash_closure_service import guard_receipt_change
    with db_read() as conn:
        row = conn.execute("SELECT ra.project_id FROM receipts r JOIN receipt_allocations ra ON ra.receipt_id=r.id WHERE r.id=? AND r.status='active' AND ra.status='active' LIMIT 1", (receipt_id,)).fetchone()
        if not row:
            raise ValueError('有效回款记录不存在，请刷新后核对')
        if conn.execute("SELECT 1 FROM fund_transactions WHERE source_kind='receipt' AND source_id=? AND status='active'", (receipt_id,)).fetchone():
            raise ValueError('该回款已有到账资金流水，请先核对并更正对应资金流水；本次未作废回款')
        guard_receipt_change(conn, row['project_id'])


def read(name,query):
    i=int(query.get('id') or 0)
    if name=='receipt_history':
        with db_read() as conn:
            if i:
                return {'record':dict(conn.execute('SELECT * FROM receipts WHERE id=?',(i,)).fetchone() or {}),
                        'history':[dict(row) for row in conn.execute('SELECT * FROM receipt_revisions WHERE receipt_id=? ORDER BY id DESC',(i,))]}
            return [dict(row) for row in conn.execute("SELECT id,receipt_no,receipt_date,payer_name_snapshot,amount_minor,payment_method,notes,status FROM receipts WHERE status='void' ORDER BY receipt_date DESC,id DESC")]
    if name=='cost_history':return b.costs.list_cost_revisions(i)
    if name=='cost_allocations':return b.costs.get_cost_allocations(i)
    if name in ('purchase_history','offer_history'):
        with db_read() as conn:
            kind='purchase' if name=='purchase_history' else 'offer'
            return [dict(r) for r in conn.execute('SELECT * FROM purchase_price_revisions WHERE entity_type=? AND entity_id=? ORDER BY id DESC',(kind,i))]
    if name=='labor_adjust_history':return b.labor.list_rate_adjustments(i)
    if name=='project_cost_details':return cost_details.get_project_cost_details(i)
    if name=='sites':return b.projects.list_project_sites(i or int(query.get('project_id') or 0),include_inactive=True)
    if name=='fund_sources':return b.funds.list_sources(query.get('kind','purchase'),include_closed=True)
    if name=='reconciliations':return b.funds.list_reconciliations()
    if name=='entities':return b.entities.list_entities()
    if name=='entity_records':return b.entities.list_records(query.get('kind','contracts'),pending_only=query.get('pending')=='1',
        assignable_only=query.get('assignable')=='1', keyword=query.get('keyword',''),
        project_id=int(query['project_id']) if query.get('project_id') else None)
    if name in ('entity_overview','entity_detail'):
        from services import operating_entity_dashboard
        project_id = int(query['project_id']) if query.get('project_id') else None
        if name=='entity_overview':return operating_entity_dashboard.get_overview(project_id)
        entity_id = 'all' if query.get('entity_id')=='all' else (int(query['entity_id']) if query.get('entity_id') else None)
        return operating_entity_dashboard.get_detail(query['metric'],entity_id,project_id)
    if name=='entity_history':return b.entities.ownership_history(query['kind'],i)
    if name=='input_candidates':return b.inputs.cost_candidates(int(query['entity_id']),int(query['supplier_id']) if query.get('supplier_id') else None,query.get('kind','purchase_orders'))
    if name=='input_links':
        with db_read() as conn:return [dict(r) for r in conn.execute('SELECT * FROM input_invoice_links WHERE invoice_id=? ORDER BY id',(i,))]
    if name=='historical_receipts':return b.history.list_receipts(i)
    if name=='collection_history':return b.collection.list_followup_logs(i)
    if name=='collection_case':return b.collection.get_project_case(i)
    if name=='unassigned_labor':return b.governance.list_unassigned_labor()
    if name=='unassigned_purchases':return b.governance.list_unassigned_purchases()
    if name=='fulfillment_gaps':return b.governance.list_fulfillment_gaps()
    if name=='labor_month':return b.labor.get_work_dashboard(query.get('month') or date.today().strftime('%Y-%m'))
    if name=='worker_month':return b.labor.get_worker_month_work_logs(i,query['month'],query['start'],query['end'])
    if name=='customer_business':return b.partners.get_customer_business_detail(i,int(query['year']) if query.get('year') else None)
    if name=='local_query':return local_query.retrieve_operating_query(query.get('question',''),int(query['project_id']) if query.get('project_id') else None)
    raise ValueError('不支持的查询')


def run_worker(action,payload):
    """Keep review and mutation snapshots coherent even with another local writer."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    from db.connection import DB_PATH
    environment=os.environ.copy()
    environment.update(SUPPLY_CHAIN_DB_PATH=str(DB_PATH),PYTHONIOENCODING='utf-8')
    completed=subprocess.run([sys.executable,'-B','-m','services.web_workbench_operations',action],
        input=json.dumps(payload,ensure_ascii=False),capture_output=True,text=True,encoding='utf-8',
        env=environment,cwd=Path(__file__).resolve().parents[1],timeout=60)
    try:
        result=json.loads(completed.stdout.splitlines()[-1])
    except (ValueError,IndexError):
        raise ValueError('本机业务操作未完成；请核对状态') from None
    if 'error' in result:
        raise ValueError(result['error'])
    return result['result']


if __name__=='__main__':
    import sqlite3
    import sys
    from db.connection import DB_PATH
    from web_workbench.transfer import bind_business_connections
    conn=None
    try:
        payload=json.load(sys.stdin)
        conn=sqlite3.connect(DB_PATH);conn.row_factory=sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        writing=sys.argv[1]=='apply'
        conn.execute('BEGIN IMMEDIATE' if writing else 'BEGIN')
        with bind_business_connections(conn):
            if writing:
                if database_fingerprint(conn)!=payload['fingerprint']:
                    raise ValueError('业务资料在预览后已变化，请重新预览；本次操作未提交')
                result=apply(payload['name'],payload['values'],payload['token'])
                conn.commit()
            else:
                result=prepare(payload['name'],payload['record'])
                conn.rollback()
        print(json.dumps({'result':result},ensure_ascii=False,default=lambda value:sorted(value) if isinstance(value,set) else str(value)))
    except Exception as error:
        if conn:conn.rollback()
        print(json.dumps({'error':str(error)},ensure_ascii=False))
        raise SystemExit(1)
    finally:
        if conn:conn.close()
