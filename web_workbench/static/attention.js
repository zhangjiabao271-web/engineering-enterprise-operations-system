'use strict';

const attentionLabels={pending:'待核对',done:'已录完',none:'今日无业务',later:'稍后处理'};
let attentionRefreshTimer;

function dailyCheckHTML(row,readOnly){
  const completed=['done','none'].includes(row.status);
  const summary=row.kind==='worklogs'?`已登记 ${row.count} 人 · ${row.record_count} 条工天`:`已登记 ${row.count} 张采购单`;
  return `<article class="daily-check ${completed?'is-complete':''}" data-check-kind="${esc(row.kind)}" data-check-day="${row.day}">
    <div><strong>${esc(row.label)}</strong><span>${row.day} · ${summary}</span>${row.changed_after_check?'<small class="attention-change">确认后记录有变化，请重新核对</small>':''}</div>
    <div class="daily-actions"><span class="daily-status">${attentionLabels[row.status]}</span>
    <button type="button" data-check-entry="${row.kind}">${row.kind==='worklogs'?'去录工天':'去录采购'}</button>
    ${readOnly?'':completed?'<button type="button" data-check-state="pending">撤销完成</button>':`<button type="button" data-check-state="done">已录完</button><button type="button" data-check-state="none" ${row.count?'disabled':''}>今日无业务</button><button type="button" data-check-state="later">稍后处理</button>`}</div></article>`;
}

function attentionIssueHTML(row,readOnly){
  const hidden=['ignored','snoozed'].includes(row.state);
  return `<article class="attention-issue" data-issue-key="${esc(row.key)}"><div><strong>${esc(row.title)}</strong>
    <p>${esc(row.note)}</p><small>${row.amount_minor?esc(money(row.amount_minor))+' · ':''}${row.state==='processing'?'已进入处理，仍需按源记录复查':hidden?(row.state==='snoozed'?'提醒日期 '+esc(row.until_day):'本次已忽略'):'待处理'}</small></div>
    <div class="attention-actions"><button type="button" data-issue-go>去处理</button>
    ${row.action.kind==='collection'?'<button type="button" data-issue-message>催款内容</button>':''}
    ${readOnly?'':hidden?'<button type="button" data-issue-state="pending">恢复提醒</button>':'<button type="button" data-issue-state="snoozed">明天再提醒</button><button type="button" data-issue-state="ignored">忽略这次</button>'}</div></article>`;
}

async function loadOperatingAttention(){
  clearTimeout(attentionRefreshTimer);
  const version=loadingVersion,project=state.project;
  if(state.route!=='home')return;
  if(!state.boot.read_only)await api('attention/day_open',{});
  const data=await api('attention?'+query({project_id:project}));
  if(version!==loadingVersion||state.route!=='home')return;
  const host=document.querySelector('#operating-attention');if(!host)return;
  const pending=data.checks.filter(row=>!['done','none'].includes(row.status));
  const completed=data.checks.filter(row=>['done','none'].includes(row.status));
  host.innerHTML=`<section class="card daily-checklist"><div class="section-head"><div><h2>今日自检</h2><p class="cockpit-footnote">${data.day} · ${esc(data.daily_scope)}</p></div><span>${completed.length} / ${data.checks.length} 已核对</span></div>
    ${pending.length?pending.map(row=>dailyCheckHTML(row,state.boot.read_only)).join(''):'<p class="attention-clear">今天的自检已完成。不会因新增一条记录自动认定全部录完。</p>'}
    ${completed.length?`<details><summary>已完成 ${completed.length} 项 · 可查看和撤销</summary>${completed.map(row=>dailyCheckHTML(row,state.boot.read_only)).join('')}</details>`:''}
    ${data.previous_checks.length?`<details><summary>以前未核对 ${data.previous_checks.length} 项</summary>${data.previous_checks.map(row=>dailyCheckHTML(row,state.boot.read_only)).join('')}</details>`:''}</section>
    <section class="card operating-priorities"><div class="section-head"><div><h2>今日经营关注</h2><p class="cockpit-footnote">${esc(data.scope)} · 优先显示最值得处理的3件事，不自动更改账目。</p></div><span>${data.attention.length} 项待处理</span></div>
    ${data.attention.length?data.attention.slice(0,3).map(row=>attentionIssueHTML(row,state.boot.read_only)).join(''):'<p class="attention-clear">当前没有触发这些经营提醒；不代表所有业务已经登记完整。</p>'}
    ${data.attention.length>3?`<details><summary>查看其他 ${data.attention.length-3} 项</summary>${data.attention.slice(3).map(row=>attentionIssueHTML(row,state.boot.read_only)).join('')}</details>`:''}
    ${data.hidden.length?`<details><summary>已收起 ${data.hidden.length} 项 · 暂缓或忽略</summary>${data.hidden.map(row=>attentionIssueHTML(row,state.boot.read_only)).join('')}</details>`:''}
    ${data.resolved.length?`<details><summary>处理后复查 ${data.resolved.length} 项</summary>${data.resolved.map(row=>`<p class="attention-resolved"><strong>${esc(row.title)}</strong><span>源业务已不再触发这条提醒，保留跟进历史。</span></p>`).join('')}</details>`:''}</section>`;
  host.querySelectorAll('[data-check-kind]').forEach(article=>{
    const row=[...data.checks,...data.previous_checks].find(row=>row.kind===article.dataset.checkKind&&row.day===article.dataset.checkDay);
    article.querySelector('[data-check-entry]').onclick=()=>safe(async()=>{
      if(state.boot.read_only){location.hash=row.kind==='worklogs'?'labor?tab=worklogs':'purchases';return;}
      if(row.kind==='worklogs')await openWorkdayBatch({work_date:row.day});
      else await editRecord('purchases',null,{purchase_date:row.day});
    });
    article.querySelectorAll('[data-check-state]').forEach(button=>button.onclick=()=>safe(async()=>{
      button.disabled=true;
      try{await api('attention/check',{kind:row.kind,day:row.day,status:button.dataset.checkState,basis:row.basis,confirmed:true});await loadOperatingAttention();}
      finally{if(button.isConnected)button.disabled=false;}
    }));
  });
  host.querySelectorAll('[data-issue-key]').forEach(article=>{
    const row=[...data.attention,...data.hidden].find(row=>row.key===article.dataset.issueKey);
    article.querySelector('[data-issue-go]').onclick=()=>safe(async()=>{
      if(!state.boot.read_only)await api('attention/manage',{key:row.key,action:'processing',basis:row.basis,confirmed:true});
      if(row.action.kind==='collection'){
        if(state.boot.read_only)return openProjectLedger(row.action.id,'outstanding');
        return openOperation('collection_followup',{id:row.action.id});
      }
      if(row.action.kind==='attachment')return attachments(row.action.entity==='contract'?'contracts':'invoices',row.action.id);
      location.hash=row.action.route;
    });
    article.querySelector('[data-issue-message]')?.addEventListener('click',()=>{
      const text=`您好，想核对一下“${row.title.split(' · ')[0]}”的付款安排。按目前台账，尚有${money(row.amount_minor)}未收。方便确认预计付款日期吗？如双方结算或付款记录有差异，我们先核对明细。`;
      openEditor('催款内容草稿',`<p class="cockpit-footnote">仅供人工核对和复制，不自动联系客户，也不记录客户已经承诺付款。</p><textarea class="collection-copy-text" readonly>${esc(text)}</textarea>`,null,{note:'依据当前应收 · 沟通草稿',cancelLabel:'返回经营关注'});
    });
    article.querySelectorAll('[data-issue-state]').forEach(button=>button.onclick=()=>safe(async()=>{
      button.disabled=true;const action=button.dataset.issueState;
      const tomorrow=new Date(data.day+'T00:00:00+08:00');tomorrow.setDate(tomorrow.getDate()+1);
      try{await api('attention/manage',{key:row.key,action,basis:row.basis,
        until_day:action==='snoozed'?tomorrow.toLocaleDateString('sv-SE',{timeZone:'Asia/Shanghai'}):null,confirmed:true});await loadOperatingAttention();}
      finally{if(button.isConnected)button.disabled=false;}
    }));
  });
  attentionRefreshTimer=setTimeout(()=>{if(state.route==='home'&&!editor.open&&!document.hidden)safe(()=>loadOperatingAttention());},60000);
}
