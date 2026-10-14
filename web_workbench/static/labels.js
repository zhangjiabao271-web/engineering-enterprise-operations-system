'use strict';

// Names for service-only facts that are not part of an editable form schema.
const factLabels = {
  materials:'材料名称', material_details:'材料 / 规格',
  ownership_status:'归属状态', ownership_label:'归属状态', ownership_source:'归属依据',
  ownership_reason:'归属说明', kind_label:'业务类型', assignable:'可直接确认', direct_entity_id:'原人工主体编号',
  before_entity:'原经营主体', after_entity:'确认后经营主体', change:'归属变化',
  contract_ids:'关联合同编号', contract_names:'关联合同', project_ids:'关联项目编号', updated_count:'本次确认笔数',
  year:'业务年度', month:'月份', date:'日期', period:'月份', period_start:'期间开始', period_end:'期间结束',
  yearly_business_minor:'年度业务额（已确认收入）', yearly_invoice_minor:'年度已开票',
  yearly_receipt_minor:'年度实际回款', current_receivable_minor:'当前未回款',
  total_business_minor:'累计已确认收入', total_receipt_minor:'累计已回款', latest_business_date:'最近业务日期',
  customer:'客户档案', projects:'项目明细', project:'项目资料', project_status:'项目状态',
  active_project_count:'在做项目数', accountable_project_count:'可核算项目数',
  summary:'汇总', totals:'合计', details:'业务明细', rows:'逐项明细', lines:'归集明细',
  items:'材料明细', history:'修改历史', record:'业务记录', before:'操作前事实', changes:'拟修改内容',
  title:'事项', reason:'操作依据', notice:'核对说明', warnings:'待核对事项', messages:'处理说明',
  basis:'核算口径', definition:'指标口径', north_star:'经营核心指标', drivers:'经营指标', gaps:'待完善事项',
  id:'记录编号', source_id:'来源编号', project_id:'项目编号', worker_id:'工人编号', site_id:'施工地点编号',
  contract_id:'合同编号', settlement_id:'收入确认编号', receipt_id:'回款编号', invoice_id:'发票记录编号',
  item_id:'材料明细编号', material_id:'材料编号', supplier_id:'供应商编号', supplier_offer_id:'材料报价编号',
  supplier_partner_id:'供应商客商编号', customer_partner_id:'客户客商编号', entity_id:'经营主体编号',
  suggested_entity_id:'建议经营主体编号', case_id:'回款跟进编号', project_site_id:'项目施工地点编号',
  parent_contract_id:'主合同编号', parent_contract_no:'主合同合同号',
  partner_code:'客商编码', legacy_supplier_id:'旧供应商编号', legacy_product_id:'旧材料编号',
  legacy_purchase_id:'旧采购编号', legacy_construction_site_id:'旧施工地点编号',
  legacy_status:'原系统状态', source_legacy_entry_id:'原系统来源编号', source_no:'来源单号',
  customer_key:'客户标识', roles:'客商角色', legal_name:'主体名称',
  contact_name:'联系人', contact_phone:'联系电话', phone:'电话',
  contact_email:'电子邮箱', contact_department:'联系人部门', contact_title:'联系人职务', contact_wechat:'微信',
  bank_name:'开户银行', bank_account:'银行账号', registered_address:'注册地址', business_address:'经营地址',
  customer_entity_type:'客户主体类型', supplier_category:'供应商类别',
  delivery_rating:'交付评价', quality_rating:'质量评价', export_capability:'出口能力',
  credit_limit_minor:'信用额度', default_tax_rate_bps:'默认税率', offer_tax_rate_bps:'报价税率',
  tax_rate:'识别税率（%）', tax_rate_bps:'税率', tax_identity:'纳税人身份',
  created_at:'创建时间', updated_at:'更新时间', created_by:'创建人', updated_by:'修改人',
  version:'版本', changed_at:'修改时间', before_json:'修改前记录', after_json:'修改后记录',
  entity_type:'业务类型', entity:'业务类型', action:'操作类型', attachment_id:'附件编号', photo_id:'照片编号',
  source_path:'本机原文件', sha256:'文件校验指纹', file_retained:'原文件保留', attachment_status:'附件状态',
  original_name:'原文件名', file_name:'文件名', file_path:'文件位置', file_size:'文件大小（字节）',
  attachment_count:'附件数量', photo_count:'照片数量', missing_attachment_count:'缺少附件数量',
  attachment_needs_attention:'附件待完善', attention_status:'待处理状态', record_status:'记录状态',
  record_count:'记录数', row_count:'文件业务行数', count:'数量', item_count:'材料条数',
  contract_count:'合同数', contract_attachment_count:'合同附件数', settlement_count:'收入确认笔数',
  invoice_count:'发票数量', order_count:'采购单数', purchase_order_count:'采购单数',
  worker_count:'工人数', site_count:'施工地点数', construction_record_count:'施工记录数',
  unassigned_labor_count:'待归集工天笔数', unassigned_purchase_count:'待归集采购笔数',
  pending_partner_count:'待确认客商数', cash_unpaid_count:'现金工程未收清项目数',
  cash_receipt_missing_voucher_count:'缺少凭证的现金回款笔数',
  candidate_count:'范围内记录数', affected_count:'受影响笔数', changed_count:'回算笔数',
  skipped_count:'跳过笔数', skipped_locked_count:'工资锁定跳过笔数', locked_count:'锁定跳过笔数',
  unchanged_count:'金额不变笔数', worklog_count:'工天笔数',
  amount:'金额（元）', total_minor:'总金额', book_minor:'账面余额', actual_minor:'实查余额',
  difference_minor:'差异金额', balance_minor:'账面余额', settled_minor:'期初已付',
  opening_settled_minor:'期初已付', paid_minor:'已登记付款', remaining_minor:'剩余金额',
  received_minor:'已回款', unreceived_minor:'未回款', invoiced_minor:'已开票',
  recorded_minor:'已登记金额', previous_total_minor:'调整前总额', allocated_amount_minor:'分配金额',
  net_amount_minor:'未税金额', tax_amount_minor:'税额', tax_inclusive_amount_minor:'含税金额',
  cash_agreed_amount_minor:'现金工程约定金额', cash_confirmed_minor:'现金工程已确认收入', cash_out_minor:'实际资金支出',
  accepted_minor:'已验收金额', control_limit_minor:'管控上限', control_overrun_minor:'超出管控金额',
  reference_overrun_minor:'超出参考金额', unlinked_receipt_minor:'待关联回款', unassigned_minor:'待归集金额',
  unassigned_labor_minor:'待归集人工成本', unassigned_purchase_minor:'待归集采购成本',
  purchase_material_minor:'采购材料未税金额', purchase_tax_inclusive_material_minor:'采购材料含税金额',
  purchase_tax_minor:'采购税额', purchase_freight_minor:'采购运费', purchase_paid_minor:'采购已付款',
  material_minor:'材料金额', freight_minor:'运费',
  material_amount_cents:'材料金额', freight_amount_cents:'运费', total_amount_cents:'采购总额',
  line_amount_cents:'材料明细金额', tax_amount_cents:'税额', project_cost_cents:'归入项目成本',
  material_unit_price_cents:'未税单价', tax_inclusive_unit_price_cents:'含税单价', unit_price_cents:'单价',
  tax_inclusive_price_minor:'含税报价', price_minor:'报价', quoted_price:'报价', quoted_price_minor:'报价',
  settlement_total_cents:'整批结算总额', work_amount_cents:'施工金额',
  old_amount_minor:'调整前金额', new_amount_minor:'调整后金额', old_minor:'调整前金额',
  new_minor:'调整后金额', delta_minor:'金额变化', old_rate_minor:'原日工资',
  current_rate_minor:'现日工资', new_rate_minor:'新日工资', daily_rate_minor:'日工资',
  worker:'工人档案', by_worker:'按工人汇总', by_site:'按施工地点汇总',
  labor:'人工明细', purchase:'采购明细', other:'其他费用明细',
  days:'工天', total_days:'合计工天', labor_work_days:'人工工天', overtime_days:'加班工天',
  overtime_record_count:'加班记录数', is_overtime:'加班标记', rate_locked:'工资已锁定',
  rate_lock_reason:'工资锁定依据', rate_locked_at:'工资锁定时间',
  scope_label:'影响范围', scope_mode:'回算范围', effective_from:'生效日期', range_end:'回算截止日期',
  is_active:'已启用', active:'已启用', is_accountable:'可完整核算', is_proxy:'代开发票',
  receipt_income_enabled:'按回款确认收入', invoice_policy:'开票规则', pricing_warning:'计价待核对事项',
  source_warning:'来源待核对事项', issue_type:'待处理问题', source_date:'业务日期',
  record_date:'记录日期', business_date:'业务日期', source_period:'来源月份', tracking_start:'资金启用日期',
  source_type:'来源类型', source_kind:'业务来源', label:'来源事项', kind:'类型',
  method:'归集方式', allocation_method:'归集方式', allocation_status:'归集状态', allocation_version:'归集版本',
  allocation_project_count:'分摊项目数', allocation_project_ids:'承担项目编号', allocation_project_names:'承担项目',
  purchase_categories:'采购成本分类', other_cost_categories:'其他费用分类', purchase_material_breakdown:'采购材料明细',
  purchase_attribution_percent:'采购归集进度', labor_attribution_percent:'人工归集进度',
  contract_coverage_percent:'合同覆盖率', settlement_coverage_percent:'收入确认覆盖率',
  settlement_progress_percent:'收入确认进度', gross_margin_percent:'已确认毛利率', percent:'占比',
  stage_code:'经营阶段', state:'核实状态', verified:'已核实',
  case_status:'回款跟进状态', case_status_label:'回款跟进状态', case_updated_at:'跟进更新时间',
  aging_bucket:'账龄区间', aging_days:'账龄（天）', oldest_unpaid_date:'最早未收日期',
  last_followup_date:'最近跟进日期', last_followup_content:'最近跟进内容',
  needs_action_today:'今天需要跟进',
  unassigned_labor:'待归集工天', unassigned_purchases:'待归集采购',
  tax_number:'纳税人识别号', subject:'业务事项', trade:'行业',
  manager_employee_id:'负责人档案编号', item_notes:'材料说明',
  allocations:'收入核销明细', automatic_income_allocation:'自动核销已确认收入',
  available_minor:'可用金额', contract_name:'合同名称', contract_status:'合同状态', cost_category:'成本类别',
  entity_name:'经营主体', file_sha256:'文件校验指纹', from_name:'转出账户', to_name:'转入账户',
  impact:'影响统计', project_impacts:'各项目调整影响',
  inspection_date:'验收日期', inspection_notes:'验收说明', inspector:'验收人',
  invoice_matched_minor:'已匹配发票金额', invoice_unmatched_minor:'未匹配发票金额',
  invoice_matches:'发票与回款抵扣明细', linked_minor:'已关联金额', links:'关联明细',
  net_minor:'未税金额', tax_minor:'税额', net_weight:'过磅净重',
  next_action:'下一步', next_followup_date:'下次跟进日期', overdue_reason:'逾期原因',
  owner_name:'跟进人', promised_date:'客户承诺日期', pending_allocation_minor:'待核销金额',
  product_id:'材料报价编号', purpose:'用途', quantity:'数量', settlement_mode:'采购结算方式',
  site:'施工地点', site_code:'施工地点编码', site_name:'施工地点名称', source_label:'来源事项',
  record_id:'业务记录编号', tax_inclusive_price:'含税报价', total_amount:'总金额（元）',
  void_reason:'作废依据', voided_at:'作废时间', warning:'待核对事项',
  weigh_ticket_no:'磅单编号', weight_unit:'过磅单位', weight_unit_price:'重量单价（元）',
  cost_entry_id:'费用记录编号', purchase_order_id:'采购单编号', work_log_id:'工天记录编号',
  adjustment_id:'调薪记录编号', invoice_revision_id:'发票修改记录编号', receipt_revision_id:'回款修改记录编号',
  account_id:'资金账户编号', account_name:'资金账户', balance_date:'核对日期',
  current_book_minor:'当前账面余额', needs_review:'需要重新核对',
  previous_values_json:'修改前记录', updated_values_json:'修改后记录', after:'修改后记录',
};

const factValueLabels = {
  income_mode:{manual:'人工确认收入',invoice:'按开票确认收入',receipt:'按实际回款确认收入'},
  method:{direct:'单项目归集',equal:'多项目均摊',manual:'手工金额分摊',unassigned:'待归集'},
  allocation_method:{direct:'单项目归集',equal:'多项目均摊',manual:'手工金额分摊',unassigned:'待归集'},
  allocation_status:{assigned:'已归集',unassigned:'待归集',partial:'部分归集'},
  settlement_mode:{quantity:'按数量',weight:'按过磅重量',total:'按整批总额'},
  kind:{purchase_orders:'采购单',cost_entries:'费用记录'},
  roles:{customer:'客户',supplier:'供应商',subcontractor:'分包商'},
  source_type:{purchase:'采购',labor:'人工',manual:'其他费用'},
  source_kind:{receipt:'工程回款',historical_receipt:'历史回款',purchase:'采购付款',cost:'费用付款',wage:'工资发放',project:'项目'},
  entity_type:{offer:'材料报价',purchase:'采购',purchase_order:'采购单',purchase_orders:'采购单',cost:'费用',costs:'费用',worklog:'工天',worklogs:'工天',receipt:'回款',receipts:'回款',invoice:'发票',invoices:'发票'},
  attachment_status:{active:'有效',void:'已移出'},
  tax_identity:{general:'一般纳税人',small:'小规模纳税人'},
};

function knownFieldLabel(key, module='') {
  if (/[\u3400-\u9fff]/.test(key)) return key;
  const schema=state.boot?.schema || {};
  const fromSchema = definition => definition?.fields?.find(field=>field.name===key)?.label ||
    definition?.columns?.find(column=>column[0]===key)?.[1];
  const moduleLabel=fromSchema(schema[module]);
  if (moduleLabel) return moduleLabel;
  if (Object.hasOwn(factLabels,key)) return factLabels[key];
  if (Object.hasOwn(columnLabels,key)) return columnLabels[key];
  for (const definition of Object.values(schema)) {
    const label=fromSchema(definition);
    if (label) return label;
  }
  if (key.endsWith('_snapshot')) return knownFieldLabel(key.slice(0,-9),module);
  for (const [prefix,label] of [['previous_','修改前'],['updated_','修改后'],['old_','调整前'],['new_','调整后']]) {
    if (!key.startsWith(prefix)) continue;
    const original=knownFieldLabel(key.slice(prefix.length),module);
    if (original) return label+' · '+original;
  }
}

function fieldLabel(key, module='') {
  // An unmapped future field must not expose an internal database identifier.
  return knownFieldLabel(key,module) || '补充字段（名称待完善）';
}

function valueLabel(value, key, module='') {
  const schema=state.boot?.schema || {};
  const moduleChoices=schema[module]?.fields?.find(field=>field.name===key)?.choices;
  if (moduleChoices && !Array.isArray(moduleChoices) && Object.hasOwn(moduleChoices,value)) return moduleChoices[value];
  const specific=factValueLabels[key];
  if (specific && Object.hasOwn(specific,value)) return specific[value];
  const candidates=new Set();
  // "active" means different things for a contract and a customer archive.
  for (const definition of key==='status' ? [] : Object.values(schema)) {
    const choices=definition.fields?.find(field=>field.name===key)?.choices;
    if (choices && !Array.isArray(choices) && Object.hasOwn(choices,value)) candidates.add(choices[value]);
  }
  if (candidates.size===1) return [...candidates][0];
  return Object.hasOwn(labels,value) ? labels[value] : String(value);
}

function parseEvidenceJSON(text) {
  return JSON.parse(text,(key,value,context)=>{
    if (typeof value==='number' && (key.endsWith('_minor') || key.endsWith('_cents'))) {
      if (context?.source) return context.source;
      return Number.isSafeInteger(value) ? String(value) : '金额待核对';
    }
    return value;
  });
}
