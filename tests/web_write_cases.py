"""Synthetic fixtures and independent persisted outcomes for HTTP and browser QA."""
from datetime import date
from decimal import Decimal


def project(b, suffix):
    return b.save_record('projects', {'name':'合成收口 '+suffix,'project_code':'CLOSE-'+suffix,'customer_partner_id':1})


def purchase(b, suffix, *, unassigned=False, tools=False):
    order=b.save_record('purchases', {'project_id':1,'supplier_id':2,'order_no':'CLOSE-'+suffix,
        'purchase_date':'2026-09-30','allocation_method':'direct',
        'items':[{'material_name_snapshot':'合成工具' if tools else '合成材料','quantity':'1','unit_snapshot':'台',
                  'unit_price':'100.01','tax_percent':'0','cost_category':'工具和设备' if tools else '材料费','price_basis':'exclusive'}]})
    if unassigned:
        current=b.procurement.get_purchase_order(order)
        b.procurement.update_purchase_order(order,{**current,'project_id':None,'allocation_method':'unassigned','project_ids':[]},current['items'])
    return order


def input_invoice(b, suffix):
    entity=next(row for row in b.entities.list_entities() if row['tax_identity']=='general')
    supplier=b.partners.get_supplier(2)
    number='88990000112233445566'
    return b.save_record('input_invoices', {'entity_id':entity['id'],'supplier_id':2,'seller_name':supplier['name'],
        'buyer_name':entity['name'],'invoice_no':number,'invoice_date':'2026-10-03','amount':'113.01','net_amount':'100.01',
        'tax_amount':'13','tax_rate_label':'13%','deduction_status':'pending','notes':'合成收口 '+suffix})


def operation_fixture(name, b):
    values={'reason':'合成收口 '+name}
    entity=next(row for row in b.entities.list_entities() if row['tax_identity']=='general')
    if name=='collection_followup':values.update(id=1,owner_name='合成跟进人',followup_date='2026-10-03',followup_content='合成取消与重复核对',status='pending')
    elif name=='labor_batch':
        worker=b.save_record('workers',{'name':'合成批量工人乙','daily_rate':'300'})
        values['entries']=[{'worker_id':worker_id,'project_id':1,'construction_site':'合成整批','work_date':'2026-10-03','work_days':'0.5'} for worker_id in (1,worker)]
    elif name=='cost_allocate':values.update(id=1,method='equal',project_ids=[1,project(b,name)])
    elif name=='cost_void':values['id']=1
    elif name=='purchase_allocate':values.update(id=purchase(b,name,tools=True),method='equal',project_ids=[1,project(b,name)])
    elif name=='purchase_void':values['id']=purchase(b,name)
    elif name=='purchase_status':values.update(id=1,payment_method='银行转账',payment_status='部分付款',invoice_status='已开票')
    elif name=='labor_adjust':
        today=date.today().isoformat()
        b.save_record('worklogs',{'worker_id':1,'project_id':1,'construction_site':'合成回算','work_date':today,'work_days':'0.5'})
        values.update(worker_id=1,new_daily_rate='410.01',effective_from=today,scope_mode='through_today')
    elif name in ('labor_lock','labor_overtime','labor_void'):
        values['ids']=[1]
        if name=='labor_lock':values['locked']=True
        if name=='labor_overtime':values['is_overtime']=True
    elif name=='worker_delete':values['id']=b.save_record('workers',{'name':'合成空档案','daily_rate':'100'})
    elif name=='project_close':values['id']=project(b,name)
    elif name in ('site_update','site_deactivate'):
        p=project(b,name);site=b.projects.create_project_site(p,{'site_code':'CLOSE-SITE','site_name':'合成原地点'})
        values.update(id=p,site_id=site)
        if name=='site_update':values.update(site_code='CLOSE-SITE-EDIT',site_name='合成修改地点',address='合成地点地址')
    elif name=='contract_void':values['id']=b.save_record('contracts',{'name':'合成无业务合同','contract_no':'CLOSE-EMPTY','amount':'1000.01','customer_partner_id':1,'sign_date':'2026-10-03'})
    elif name=='allocation_update':values.update(id=1,amount='250000.01')
    elif name=='allocation_void':
        p=project(b,name);contract=b.save_record('contracts',{'name':'合成可取消分配合同','contract_no':'CLOSE-ALLOCATION','amount':'1000.01','customer_partner_id':1,'sign_date':'2026-10-03'})
        values['id']=b.save_record('allocations',{'contract_id':contract,'project_id':p,'amount':'1000.01'})
    elif name=='settlement_void':values['id']=b.save_record('settlements',{'project_id':1,'contract_id':1,'amount':'100.01','settlement_date':'2026-10-03','basis':'合成可作废结算'})
    elif name=='settlement_contract':
        b.entities.assign_records('contracts',[1],entity['id'],'合成确认主体')
        values['id']=b.save_record('settlements',{'project_id':1,'amount':'100.01','settlement_date':'2026-10-03','basis':'合成合同待补业务'})
        values['contract_id']=1
    elif name=='invoice_void':values['id']=1
    elif name=='receipt_allocation':
        values['id']=b.save_record('receipts',{'project_id':1,'contract_id':1,'settlement_id':1,'amount':'100.01','receipt_date':'2026-10-03'})
        values.update(allocations=[{'settlement_id':1,'amount':'100.01'}],allow_advance=False)
    elif name=='receipt_void':
        values['id']=b.save_record('receipts',{'project_id':1,'contract_id':1,'settlement_id':1,'amount':'100.01','receipt_date':'2026-10-03'})
    elif name in ('fund_verify','fund_void_transaction','fund_link_plan'):
        order=purchase(b,name)
        if name=='fund_verify':values.update(kind='purchase',source_id=order,source_period='',settled_amount='10.01',payee='合成收款单位')
        else:
            b.funds.verify_source('purchase',order,'',{'settled_amount':'0','payee':'合成供应商','reason':'合成期初核对'})
            cash=b.save_record('transactions',{'category':'purchase','source_id':order,'from_account_id':1,'amount':'10.01',
                                             'transaction_date':'2026-10-03','request_key':'CLOSE-'+name})
            values['id']=cash
            if name=='fund_link_plan':values['plan_id']=b.save_record('plans',{'direction':'out','title':'合成付款计划','source_kind':'purchase','source_id':order,'amount':'10.01','due_date':'2026-10-03'})
    elif name=='fund_reconcile':values.update(account_id=1,balance_date='2026-10-03',actual_amount='249999.99')
    elif name=='fund_archive':values.update(id=b.save_record('accounts',{'name':'合成零余额账户','kind':'bank','opening_date':'2026-10-01','opening_amount':'0','reason':'合成空账户'}),archived=True)
    elif name in ('input_links','input_deduction','input_void'):
        values['id']=input_invoice(b,name)
        if name=='input_links':
            b.entities.assign_records('purchase_orders',[1],entity['id'],'合成主体核对')
            values['links']=[{'kind':'purchase_orders','record_id':1,'amount':'113.01'}]
        elif name=='input_deduction':values.update(deduction_status='deducted',deduction_period='2026-10')
    elif name=='entity_assign':values.update(kind='purchase_orders',ids=[1],entity_id=entity['id'])
    elif name=='entity_tax':values.update(entity_id=entity['id'],tax_number='91331000SYNTHETIC01')
    elif name=='entity_setup':values.update(entity_id=entity['id'],name='合成首次设置主体',tax_number='SYNTHETIC-SETUP')
    elif name=='historical_receipt':values.update(id=b.save_record('historical',{'customer_id':1,'name':'合成历史工程','opening_amount':'200.01'}),receipt_date='2026-10-03',amount='100.01',payer_name='合成历史付款人',payment_method='银行转账')
    elif name=='supplier_deactivate':values['id']=b.save_record('suppliers',{'name':'合成可停用供应商'})
    elif name=='customer_deactivate':values['id']=b.save_record('customers',{'legal_name':'合成可停用客户'})
    elif name=='offer_deactivate':values['id']=b.save_record('offers',{'supplier_id':2,'name':'合成可停用报价','unit':'台','price':'100.01'})
    elif name=='construction_void':values['id']=1
    elif name=='attachment_void':
        from pathlib import Path
        import tempfile
        from services.attachment_service import add_attachment
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'synthetic.txt';path.write_text('合成保留附件',encoding='utf-8')
            values['ids']=[add_attachment('contract',1,path)]
    elif name=='governance_customer':values.update(project_id=1,customer_name='合成收口客户确认',update_contracts=False)
    elif name=='governance_labor':
        log=b.labor.add_work_log({'worker_id':1,'construction_site':'合成待归集','allow_unassigned':True,'work_date':'2026-10-03','work_days':'0.5'})
        site=b.projects.list_project_sites(1)[0]
        values.update(ids=[log],project_id=1,project_site_id=site['id'])
    elif name=='governance_purchase':values.update(ids=[purchase(b,name,unassigned=True)],project_id=1)
    else:raise AssertionError('Missing synthetic operation fixture: '+name)
    return values


def operation_facts(name, b, values, conn):
    """Expected literals are independent of the preview and mutation return values."""
    record_id=values.get('id');ids=values.get('ids') or [record_id]
    def scalar(sql, parameters=()):return conn.execute(sql,parameters).fetchone()[0]
    if name=='collection_followup':return scalar('SELECT COUNT(*) FROM collection_followup_logs WHERE project_id=1'),1
    if name=='labor_batch':return scalar("SELECT COUNT(*) FROM work_logs WHERE construction_site='合成整批'"),2
    if name=='cost_allocate':return [row[0] for row in conn.execute("SELECT amount_minor FROM cost_allocation_lines WHERE cost_entry_id=1 AND status='active' ORDER BY project_id")],[100000,100000]
    if name=='purchase_allocate':return [row[0] for row in conn.execute("SELECT amount_minor FROM purchase_cost_allocation_lines WHERE purchase_order_id=? AND status='active' ORDER BY project_id",(record_id,))],[5001,5000]
    if name=='purchase_status':
        row=b.get_record('purchases',record_id);return [row['payment_method'],row['payment_status'],row['invoice_status'],row['total_amount_cents']],['银行转账','部分付款','已开票',10170000]
    if name=='labor_adjust':return scalar("SELECT amount_minor FROM work_logs WHERE construction_site='合成回算'"),20501
    if name=='labor_lock':return scalar('SELECT rate_locked FROM work_logs WHERE id=?',(ids[0],)),1
    if name=='labor_overtime':return scalar('SELECT is_overtime FROM work_logs WHERE id=?',(ids[0],)),1
    if name=='worker_delete':return scalar('SELECT COUNT(*) FROM workers WHERE id=?',(record_id,)),0
    if name=='project_close':return scalar('SELECT status FROM projects WHERE id=?',(record_id,)),'已关闭'
    if name=='site_update':return scalar('SELECT name FROM project_sites WHERE id=?',(values['site_id'],)),'合成修改地点'
    if name=='site_deactivate':return scalar('SELECT is_active FROM project_sites WHERE id=?',(values['site_id'],)),0
    if name=='allocation_update':return scalar('SELECT allocated_amount_minor FROM contract_project_allocations WHERE id=?',(record_id,)),25000001
    if name=='settlement_contract':return scalar('SELECT contract_id FROM settlements WHERE id=?',(record_id,)),values['contract_id']
    if name=='receipt_allocation':return scalar("SELECT SUM(allocated_amount_minor) FROM receipt_allocations WHERE receipt_id=? AND status='active'",(record_id,)),10001
    if name=='fund_verify':
        row=next(r for r in b.funds.list_sources('purchase',include_closed=True) if r['id']==values['source_id']);return [row['verified'],row['opening_settled_minor']],[True,1001]
    if name=='fund_reconcile':return scalar('SELECT actual_minor-book_minor FROM fund_reconciliations ORDER BY id DESC LIMIT 1'),-1
    if name=='fund_archive':return scalar('SELECT status FROM fund_accounts WHERE id=?',(record_id,)),'archived'
    if name=='fund_link_plan':return scalar('SELECT plan_id FROM fund_transactions WHERE id=?',(record_id,)),values['plan_id']
    if name=='input_links':return scalar('SELECT SUM(amount_minor) FROM input_invoice_links WHERE invoice_id=?',(record_id,)),11301
    if name=='input_deduction':return scalar('SELECT deduction_status FROM input_invoices WHERE id=?',(record_id,)),'deducted'
    if name=='entity_assign':return scalar('SELECT entity_id FROM record_entities WHERE record_type=? AND record_id=?',(values['kind'],ids[0])),values['entity_id']
    if name=='entity_tax':return scalar('SELECT tax_number FROM operating_entities WHERE id=?',(values['entity_id'],)),values['tax_number']
    if name=='entity_setup':return scalar('SELECT name FROM operating_entities WHERE id=?',(values['entity_id'],)),values['name']
    if name=='historical_receipt':return scalar('SELECT SUM(amount_minor) FROM historical_receipts WHERE project_id=?',(record_id,)),10001
    if name=='attachment_void':
        from services.attachment_service import attachment_path
        row=conn.execute('SELECT status,file_path FROM business_attachments WHERE id=?',(ids[0],)).fetchone()
        return [row['status'],attachment_path(row['file_path']).is_file()],['void',True]
    if name=='governance_customer':return scalar('SELECT customer_name FROM projects WHERE id=1'),'合成收口客户确认'
    if name=='governance_labor':return scalar('SELECT project_id FROM work_logs WHERE id=?',(ids[0],)),1
    if name=='governance_purchase':return scalar('SELECT project_id FROM purchase_orders WHERE id=?',(ids[0],)),1
    tables={'cost_void':'cost_entries','purchase_void':'purchase_orders','labor_void':'work_logs','contract_void':'contracts',
            'allocation_void':'contract_project_allocations','settlement_void':'settlements','invoice_void':'sales_invoices','receipt_void':'receipts',
            'fund_void_transaction':'fund_transactions','input_void':'input_invoices','supplier_deactivate':'business_partners',
            'customer_deactivate':'business_partners','offer_deactivate':'supplier_offers','construction_void':'construction_records'}
    table=tables[name];column='record_status' if name=='construction_void' else 'status'
    expected='作废' if name=='construction_void' else 'inactive' if name.endswith('deactivate') else 'void'
    return scalar(f'SELECT {column} FROM {table} WHERE id=?',(ids[0],)),expected


def form_fixture(module, b, *, edit=False):
    values={'notes':'合成收口表单 '+module}
    defaults={
        'projects':{'name':'合成表单项目','project_code':'CLOSE-FORM'},
        'contracts':{'name':'合成表单合同','contract_no':'CLOSE-FORM-HT','amount':'1000.01','customer_partner_id':1,'sign_date':'2026-10-03'},
        'allocations':{'project_id':1,'contract_id':1,'amount':'0.01'},
        'settlements':{'project_id':1,'contract_id':1,'amount':'100.01','settlement_date':'2026-10-03','basis':'合成表单收入依据'},
        'suppliers':{'name':'合成表单供应商','contact':'合成联系人','contact_phone':'13800000000'},
        'customers':{'legal_name':'合成表单客户','credit_limit':'123456789012.34'},
        'offers':{'supplier_id':2,'name':'合成表单报价','specification':'S1','unit':'台','price':'100.01','tax_rate_percent':'13'},
        'purchases':{'project_id':1,'supplier_id':2,'order_no':'CLOSE-FORM-PO','purchase_date':'2026-10-03','items':[{'material_name_snapshot':'合成表单材料','quantity':'1','unit_snapshot':'台','unit_price':'100.01','tax_percent':'13','price_basis':'exclusive'}]},
        'costs':{'project_id':1,'category':'管理与财务 / 代理记账','amount':'100.01','cost_date':'2026-10-03'},
        'workers':{'name':'合成表单工人','daily_rate':'100.01'},
        'worklogs':{'worker_id':1,'project_id':1,'construction_site':'合成表单工地','work_date':'2026-10-03','work_days':'0.5'},
        'construction':{'project_id':1,'work_details':'合成表单工程量','start_date':'2026-10-03','end_date':'2026-10-03','work_amount':'100.01'},
        'receipts':{'project_id':1,'contract_id':1,'settlement_id':1,'amount':'100.01','receipt_date':'2026-10-03','payer_name':'合成付款人'},
        'accounts':{'name':'合成表单账户','kind':'bank','opening_amount':'100.01','opening_date':'2026-10-01','reason':'合成表单期初'},
        'plans':{'direction':'out','title':'合成表单付款计划','amount':'100.01','due_date':'2026-10-03','reason':'合成表单依据'},
        'transactions':{'category':'other_in','to_account_id':1,'amount':'100.01','transaction_date':'2026-10-03','counterparty':'合成表单付款人','request_key':'CLOSE-FORM-CASH'},
        'historical':{'customer_id':1,'name':'合成表单历史项目','opening_amount':'100.01'},
    }
    if module=='invoices':
        settlement=b.save_record('settlements',{'project_id':1,'contract_id':1,'amount':'113.01','settlement_date':'2026-10-03','basis':'合成开票基础'})
        values.update(project_id=1,contract_id=1,settlement_id=settlement,invoice_no='99112233445566778800',amount='113.01',net_amount='100.01',tax_amount='13',tax_rate_percent='13',invoice_date='2026-10-03')
    elif module=='input_invoices':
        entity=next(row for row in b.entities.list_entities() if row['tax_identity']=='general')
        values.update(entity_id=entity['id'],supplier_id=2,seller_name=b.partners.get_supplier(2)['name'],buyer_name=entity['name'],
            invoice_no='88776655443322110099',invoice_date='2026-10-03',amount='113.01',net_amount='100.01',tax_amount='13',tax_rate_label='13%',deduction_status='pending')
    else:values.update(defaults[module])
    if module=='allocations':
        contract=b.save_record('contracts',{'name':'合成分配新合同','contract_no':'CLOSE-FORM-ALLOC','amount':'100.01','sign_date':'2026-10-03','customer_partner_id':1})
        values['contract_id']=contract;values['amount']='100.01'
    record_id=None
    if edit:
        record_id=b.save_record(module,values)
        values={**b.get_record(module,record_id),'notes':'合成收口修改已保存 '+module}
        if module=='construction':values['description']='合成收口修改已保存 '+module
        if module=='settlements':values['basis']='合成收口修改已保存 '+module
        if module=='accounts':values['reason']='合成收口修改依据'
        if module=='purchases':
            values['items']=[dict(item,unit_price=b.money_form_value(item['tax_inclusive_unit_price_cents'] if item['price_basis']=='inclusive' else item['material_unit_price_cents']),tax_percent=b.money_form_value(item['tax_rate_bps'])) for item in values['items']]
        for key in ('roles',):values.pop(key,None)
    return {'module':module,'id':record_id,'record':values}


def form_facts(module, b, record_id):
    row=b.get_record(module,record_id)
    expected={'projects':('project_code','CLOSE-FORM'),'contracts':('tax_inclusive_amount_minor',100001),
        'allocations':('allocated_amount_minor',10001),'settlements':('amount_minor',10001),'suppliers':('contact_phone','13800000000'),
        'customers':('credit_limit_minor',12345678901234),'offers':('price_minor',10001),'purchases':('total_amount_cents',11301),
        'costs':('amount_minor',10001),'workers':('daily_rate','100.01'),'worklogs':('amount_minor',20000),
        'construction':('work_amount_cents',10001),'invoices':('amount_minor',11301),'receipts':('amount_minor',10001),
        'accounts':('opening_minor',10001),'plans':('amount_minor',10001),'transactions':('amount_minor',10001),
        'historical':('opening_minor',10001),'input_invoices':('gross_minor',11301)}
    key,value=expected[module]
    actual=row[key]
    if module=='workers':actual=str(Decimal(str(actual)))
    expected_fields={key:value}
    dates={'settlements':'settlement_date','purchases':'purchase_date','costs':'cost_date','worklogs':'work_date',
           'invoices':'invoice_date','receipts':'receipt_date','transactions':'transaction_date','input_invoices':'invoice_date'}
    if module in dates:expected_fields[dates[module]]='2026-10-03'
    if module in ('allocations','settlements','purchases','worklogs','construction','invoices','receipts'):expected_fields['project_id']=1
    if module in ('settlements','invoices','receipts'):expected_fields['contract_id']=1
    if module in ('offers','purchases','input_invoices'):expected_fields['supplier_id']=2
    if module=='worklogs':expected_fields.update(worker_id=1,daily_rate_minor=40000)
    if module=='invoices':expected_fields.update(tax_rate_bps=1300,net_amount_minor=10001,tax_amount_minor=1300)
    if module=='input_invoices':expected_fields.update(deduction_status='pending',net_minor=10001,tax_minor=1300)
    if module=='transactions':expected_fields.update(to_account_id=1,category='other_in')
    actual_fields={field:row.get(field) for field in expected_fields}
    actual_fields[key]=actual
    if module=='costs':
        allocation=b.costs.get_cost_allocations(record_id)
        actual_fields['allocation']=[(line['project_id'],line['amount_minor']) for line in allocation['lines']]
        expected_fields['allocation']=[(1,10001)]
    return actual_fields,expected_fields
