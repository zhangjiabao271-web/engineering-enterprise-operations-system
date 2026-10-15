'use strict';
Object.assign(labels,{retain:'保留真实结算',reverse:'撤销误补收入',receipt_income_retain:'确认保留收入',receipt_income_reverse:'确认撤销误补收入'});

function receiptIncomeFields() {
  return [{name:'receipt_income_action',label:'自动补足收入如何处理',kind:'select',default:'',
    choices:{retain:'仅更正回款，保留真实结算',reverse:'同步撤销录错的自动补足收入'}},
    {name:'receipt_income_reason',label:'回款及收入更正说明',kind:'textarea',default:''}];
}

function receiptIncomeHTML(impact) {
  if (!impact.requires_decision) return notice('本次修改没有减少关联自动补足收入，无需选择撤销。');
  return notice(impact.notice) + notice('若选择同步撤销，将减少自动补足收入 '+money(impact.reduction_minor)+
    '；保留真实结算则收入不变。'+(impact.can_reverse?'':'当前有关联保护，不能同步撤销。')) +
    table(impact.rows,[['settlement_no','关联收入确认'],['before_minor','原收入'],['after_minor','拟保留收入'],
      ['invoiced_minor','关联开票'],['other_received_minor','其他回款占用'],['restriction','限制说明']],null,'receipt-income-sources');
}

function bindReceiptIncomePreview(record) {
  const request=beginEditorRequest();
  const host=document.querySelector('#receipt-income-impact');
  const amount=form.elements.namedItem('amount');
  const action=form.elements.namedItem('receipt_income_action');
  const reason=form.elements.namedItem('receipt_income_reason');
  let version=0;
  const refresh=async()=>{
    const current=++version;
    const impact=await request.read('receipt_income_preview?'+query({id:record.id,amount:amount.value}));
    request.check();
    if (current!==version || !editor.open) throw new SupersededView();
    host.innerHTML=receiptIncomeHTML(impact);
    for(const input of [action,reason]) {
      input.disabled=!impact.requires_decision;
      input.required=impact.requires_decision;
      input.closest('label').hidden=!impact.requires_decision;
    }
    return impact;
  };
  form._receiptIncomeRefresh=refresh;
  amount.addEventListener('input',()=>{
    version++;
    action.value='';
    form.elements.confirmed.checked=false;
    host.innerHTML=notice('金额已改变，离开输入框后将重新核对收入影响。');
  });
  amount.addEventListener('change',()=>safe(refresh));
  safe(refresh);
}
