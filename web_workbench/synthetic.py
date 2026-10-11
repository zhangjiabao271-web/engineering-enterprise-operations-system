"""Synthetic demonstration records, clearly labelled and generated locally."""


def seed():
    from services import web_workbench_service as business
    from services import document_cashflow_service as cashflow
    customer = business.save_record('customers', {'legal_name':'合成演示 · 建设单位','contact_name':'演示联系人'})
    supplier = business.save_record('suppliers', {'name':'合成演示 · 钢材供应商','category':'钢材'})
    project = business.save_record('projects', {'name':'合成演示 · 厂房钢结构工程','project_code':'DEMO-001',
        'customer_partner_id':customer,'status':'进行中','planned_start_date':'2026-10-01','planned_end_date':'2026-12-31'})
    contract = business.save_record('contracts', {'name':'合成演示 · 钢结构施工合同','contract_no':'DEMO-HT-001',
        'customer_partner_id':customer,'amount':'500000','status':'active','sign_date':'2026-10-01'})
    business.save_record('allocations',{'contract_id':contract,'project_id':project,'amount':'500000'})
    settlement = business.save_record('settlements',{'project_id':project,'contract_id':contract,'amount':'100000',
        'settlement_date':'2026-10-02','basis':'合成演示阶段验收'})
    business.save_record('invoices',{'project_id':project,'contract_id':contract,'settlement_id':settlement,
        'invoice_no':'DEMO-INV-001','amount':'100000','invoice_date':'2026-10-02'})
    offer = business.save_record('offers',{'supplier_id':supplier,'name':'合成演示 · H型钢','specification':'H300',
        'unit':'吨','price':'4500','tax_rate_percent':'13'})
    business.save_record('purchases',{'project_id':project,'supplier_id':supplier,'purchase_date':'2026-10-02',
        'items':[{'product_id':offer,'material_name_snapshot':'合成演示 · H型钢','quantity':'20','unit_snapshot':'吨',
                  'unit_price':'4500','tax_percent':'13','price_basis':'exclusive'}]})
    worker = business.save_record('workers',{'name':'合成演示 · 安装工','trade':'安装','daily_rate':'400'})
    business.save_record('worklogs',{'worker_id':worker,'project_id':project,'construction_site':'合成演示 · 厂房钢结构工程',
        'work_date':'2026-10-02','work_days':'1'})
    business.save_record('construction',{'project_id':project,'work_area':'一层','work_details':'合成演示：钢柱安装、焊缝检查',
        'start_date':'2026-10-01','end_date':'2026-10-02','work_amount':'100000'})
    business.save_record('costs',{'project_id':project,'category':'工程施工 / 工人伙食','amount':'2000','cost_date':'2026-10-02','notes':'合成演示费用'})
    business.save_record('accounts',{'name':'合成演示 · 公司账户','kind':'bank','opening_date':'2026-10-01','opening_amount':'250000','reason':'合成演示期初'})
    business.save_record('plans',{'direction':'out','title':'合成演示 · 钢材付款计划','amount':'10000','due_date':'2026-11-01'})
    scope = {'contract_id':contract,'project_id':project}
    cashflow.save_document({'name':'合成演示合同摘录','kind':'合同正文','fragments':[
        {'locator':'第4.1条 / 第2页','text':'收款 预付款；金额：100000元；日期：2026-11-01；条件：签约并开票'},
        {'locator':'第4.2条 / 第2页','text':'收款 进度款；金额：220000元；日期：2026-12-15；条件：验收及审批'},
        {'locator':'第4.3条 / 第3页','text':'收款 质保金；金额：合同额5%；日期：验收后两年；条件：质保期满'}]},**scope)
    cashflow.save_document({'name':'合成演示采购安排','kind':'采购付款安排','fragments':[
        {'locator':'第1行','text':'付款 钢材定金；金额：180000元；日期：2026-11-05；条件：下单确认'},
        {'locator':'第2行','text':'付款 安装人工；金额：70000元；日期：2026-12-05；条件：进场确认'}]},**scope)
    cashflow.create_analysis(**scope,income_delay_days=30,expense_increase_percent=10)
