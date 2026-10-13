'use strict';

const entryNames={project:'项目名称',date:'业务日期',amount:'金额（元）',category:'费用分类',description:'用途 / 说明',paid:'付款情况',
  supplier:'供应商名称',payment_method:'付款方式',payment_status:'付款状态',invoice_status:'票据情况',
  contract:'合同名称（现金工程留空）',payer:'付款人（可空）',workers:'工人名单',days:'每人工天',site:'作业地点',is_overtime:'加班记录',
  name:'材料名称',specification:'规格',quantity:'数量',unit:'单位',unit_price:'单价（元）',price_basis:'金额口径',tax_percent:'税率（%）',total_amount:'整批金额（元）'};
const entryFields={costs:['project','date','amount','category','description','paid'],
  purchases:['project','supplier','date','description','payment_method','payment_status','invoice_status'],
  receipts:['project','contract','date','amount','payment_method','payer','description'],
  worklogs:['project','date','workers','days','site','is_overtime']};

function entryField(key,value,prefix){
  let field={name:key,label:entryNames[key]||key,kind:key==='date'?'date':'text'};
  if(key==='category')field={...field,kind:'select',choices:state.boot.schema.costs.fields.find(item=>item.name==='category').choices};
  if(key==='paid'){field={...field,kind:'select',choices:{'':'待确认','true':'已付款','false':'未付款'}};value=value===true?'true':value===false?'false':'';}
  if(key==='is_overtime')field={...field,kind:'checkbox'};
  if(key==='workers')value=Array.isArray(value)?value.join('、'):value;
  if(key==='price_basis')field={...field,kind:'select',choices:{inclusive:'含税',exclusive:'未税'}};
  for(const [name,choices] of Object.entries({payment_method:['银行转账','现金','微信','支付宝','其他'],payment_status:['已付款','未付款','部分付款','未确认'],invoice_status:['有发票','无发票','收据','未确认']})){
    if(key===name)field={...field,kind:'select',choices};
  }
  return fieldHTML(field,value??'',{},prefix);
}

function collectEntryRecords(records){
  return records.map((row,index)=>{
    const data={};
    for(const key of entryFields[row.module]){
      const input=form.elements.namedItem(`entry-${index}-${key}`);
      let value=input.type==='checkbox'?input.checked:input.value.trim();
      if(key==='paid')value=value==='true'?true:value==='false'?false:null;
      if(key==='workers')value=value.split(/[、,，\n]+/).map(item=>item.trim()).filter(Boolean);
      data[key]=value===''?null:value;
    }
    if(row.module==='purchases')data.items=(row.data.items||[]).map((item,itemIndex)=>{
      const value={};
      for(const key of ['name','specification','quantity','unit','unit_price','price_basis','tax_percent','total_amount']){
        const input=form.elements.namedItem(`entry-${index}-item-${itemIndex}-${key}`);
        value[key]=input.value.trim()||null;
      }
      return value;
    });
    return {module:row.module,data};
  });
}

async function openEntryDraft(records,generation,modelQuestions=[],originalText=''){
  if(state.boot.read_only)throw new Error('只读工作台不能保存助手录入');
  if(assistantView.snapshot.generation!==generation)throw new Error('对话已变化，请使用最新录入草稿');
  if(!Array.isArray(records)||records.some(row=>!entryFields[row.module]))throw new Error('草稿不包含可支持的新增记录，请重新说明业务');
  openEditor('正在核对录入清单',notice('正在核对项目、供应商、金额、日期和业务边界；尚未记账。'),null,{note:'人工确认前不写账'});
  const request=beginEditorRequest();
  let review=await api('entry/preview',{records,request_key:crypto.randomUUID(),project_id:state.project,model_questions:modelQuestions});
  try{request.check();}catch(error){await api('entry/discard',{token:review.token});throw error;}
  records=review.records;
  function draw(){
    let dirty=false;
    const problems=review.questions.map(text=>`<p>${esc(text)}</p>`).join('');
    const body=notice('先核对全部项目、日期及金额。没有提供的条件保持待补；付款标记不自动生成独立资金流水。')+
      (originalText?`<details><summary>对照本次原话，检查是否遗漏</summary><p>${esc(originalText)}</p></details>`:'')+
      (modelQuestions.length?`<div class="entry-review-warning"><strong>模型待核对事项</strong>${modelQuestions.map(text=>`<p>${esc(text)}</p>`).join('')}<label class="confirm-check"><input name="entry-model-reviewed" type="checkbox" ${review.model_reviewed?'checked':''}>这些提示已逐项核对并处理；本次仅保存下方清单中的业务。</label></div>`:'')+
      (problems?`<div class="inline-error" role="status">${problems}</div>`:'')+
      records.map((row,index)=>`<section class="entry-review-row"><h3>第 ${index+1} 项 · ${esc({costs:'费用',purchases:'采购',receipts:'回款',worklogs:'工天'}[row.module])}</h3><div class="form-grid">${entryFields[row.module].map(key=>entryField(key,row.data[key],`entry-${index}-`)).join('')}</div>
      ${row.module==='purchases'?(row.data.items||[]).map((item,itemIndex)=>`<h3>材料 ${itemIndex+1}</h3><p class="entry-review-warning">数量、单位和单价只在逐件计价时填写；按整批金额结算时留空，不猜测数量。</p><div class="form-grid">${['name','specification','quantity','unit','unit_price','price_basis','tax_percent','total_amount'].map(key=>entryField(key,item[key],`entry-${index}-item-${itemIndex}-`)).join('')}</div>`).join(''):''}</section>`).join('')+
      `<div class="entry-review-warning">${review.prepared.map(row=>`<p>${esc(row.day)} · ${esc(row.label)}</p>${(row.effects||[]).map(text=>`<p>${esc(text)}</p>`).join('')}`).join('')}</div>`+
      '<label class="confirm-check"><input name="entry-confirmed" type="checkbox">清单完整覆盖本次要保存的业务；我已核对项目、日期、金额及预收或结算影响，确认保存。</label>';
    openEditor('助手录入确认清单',body,async()=>{
      if(dirty||!review.can_apply){
        const updated=collectEntryRecords(records);
        const next=await api('entry/preview',{records:updated,request_key:crypto.randomUUID(),project_id:state.project,
          model_questions:modelQuestions,model_reviewed:form.elements.namedItem('entry-model-reviewed')?.checked||false});
        await api('entry/discard',{token:review.token});
        records=next.records;review=next;draw();return;
      }
      if(!form.elements['entry-confirmed'].checked)throw new Error('请核对清单并勾选明确确认');
      const result=await api('entry/apply',{token:review.token,confirmed:true});
      cancelEditor=null;editor.close();
      await refreshOptions();
      try{drawAssistant(await assistantAPI('entry_completed',{session_id:assistantView.session,generation,token:review.token}));}
      catch(error){toast('记录已保存，请从业务台账核对；对话已变化，不重复记账。');return;}
      toast(`已保存并核对 ${result.length} 笔记录。`);
    },{label:review.can_apply?'确认保存全部':'重新核对清单',note:'结构化草稿 · 明确确认后整笔保存'});
    cancelEditor=()=>api('entry/discard',{token:review.token});
    const session=editorSession;
    document.querySelector('#editor-body').oninput=event=>{
      if(editorSession!==session)return;
      if(event.target.name==='entry-confirmed')return;
      const confirmation=form.elements.namedItem('entry-confirmed');if(!confirmation)return;
      dirty=true;confirmation.checked=false;
      editorSaveLabel='重新核对清单';document.querySelector('#editor-save').textContent=editorSaveLabel;
    };
  }
  draw();
}
