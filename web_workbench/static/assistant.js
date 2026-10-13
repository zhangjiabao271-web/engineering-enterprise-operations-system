'use strict';

const assistantView = {session: null, snapshot: null, timer: null, epoch: 0, signature: '', sending: false};
const assistantSessionKey = 'engineering-workbench-assistant';

async function assistantAPI(action, data) {
  const response = await fetch('/api/assistant/' + action, {
    method: data === undefined ? 'GET' : 'POST',
    headers: {'Content-Type': 'application/json', 'X-Workbench-Token': state.boot.csrf},
    ...(data === undefined ? {} : {body: JSON.stringify(data)}),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || '对话未完成，请核对连接状态。');
  return data === undefined ? result : result.result;
}

function answerInline(text) {
  return esc(text).replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>');
}

function answerHTML(value) {
  const lines = String(value || '').split('\n');
  const blocks = [];
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index].trim();
    if (!line) continue;
    if (line.startsWith('|') && /^\s*\|?[\s:|-]+\|\s*$/.test(lines[index + 1] || '')) {
      const cells = text => text.trim().replace(/^\||\|$/g, '').split('|').map(cell => cell.trim());
      const headers = cells(line);
      const rows = [];
      index += 2;
      while (index < lines.length && lines[index].trim().startsWith('|')) rows.push(cells(lines[index++]));
      index--;
      blocks.push(`<div class="table-wrap"><table><thead><tr>${headers.map(cell => `<th>${answerInline(cell)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(cell => `<td>${answerInline(cell)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
    } else if (/^#{1,6}\s/.test(line)) {
      blocks.push(`<h3>${answerInline(line.replace(/^#+\s*/, ''))}</h3>`);
    } else if (/^(?:[-*]|\d+[.)、])\s/.test(line)) {
      const rows = [line];
      while (/^(?:[-*]|\d+[.)、])\s/.test((lines[index + 1] || '').trim())) rows.push(lines[++index].trim());
      blocks.push(`<ul>${rows.map(row => `<li>${answerInline(row.replace(/^(?:[-*]|\d+[.)、])\s*/, ''))}</li>`).join('')}</ul>`);
    } else {
      const paragraph = [line];
      while (lines[index + 1]?.trim() && !/^(?:#|\||[-*]\s|\d+[.)、]\s)/.test(lines[index + 1].trim())) paragraph.push(lines[++index].trim());
      blocks.push(`<p>${paragraph.map(answerInline).join('<br>')}</p>`);
    }
  }
  return blocks.join('');
}

function assistantSourceHTML(source, index) {
  const facts = source.details || source.rows || [];
  const rows = Array.isArray(facts) ? facts : [];
  const names = {...columnLabels, source_id: '来源编号', record_id: '记录编号', invoice_no: '发票号码',
    receipt_date: '到账日期', supplier_name: '供应商', quantity: '数量', unit: '单位', label: '对象',
    date: '日期', total_minor: '金额合计', amount_minor: '金额', receipt_minor: '实际回款',
    customer: '客户', customer_name: '客户', historical_project: '历史旧账项目'};
  const columns = source.columns?.map(column => {
    const [key,label]=Array.isArray(column) ? column : [column.key,column.label];
    return [key,/[\u3400-\u9fff]/.test(label || '') ? label : names[key] || fieldLabel(key)];
  }) ||
    [...new Set(rows.flatMap(row => Object.keys(row)))].filter(key => names[key]).map(key => [key, names[key]]);
  let detail = rows.length && columns.length ? table(rows.slice(0, 30), columns) : '';
  if (source.kpis) detail = `<div class="source-summary">${source.kpis.map(([label, value]) => `<span>${esc(label)}：${esc(value)}</span>`).join('')}</div>` + detail;
  if (source.summary) detail += `<div class="source-summary">${Object.entries(source.summary).map(([key, value]) => `<span>${esc(names[key] || fieldLabel(key))}：${esc(display(value, key))}</span>`).join('')}</div>`;
  if (rows.length > 30) detail += '<p class="page-note">这里只展开前30条，完整记录请进入对应台账核对。</p>';
  const mapping = {purchase: 'purchases', workday: 'labor', finance: 'finance', cost: 'cost', project: 'projects', project_profit: 'profit', historical_receivable: 'finance'};
  const route = mapping[source.page_key] || source.page_key;
  const known = state.boot.navigation.some(([, items]) => items.some(([key]) => key === route));
  return `<details class="chat-source" data-source-index="${index}"><summary>${esc(source.label || source.module || '核对数据来源')}${source.record_count == null ? '' : ` · ${esc(source.record_count)}条`}</summary><p class="page-note">${esc(source.scope_label || source.module || '')}</p>${detail}${known ? `<a href="#${esc(route)}">打开对应台账核对</a>` : ''}</details>`;
}

function drawAssistant(snapshot) {
  const host = document.querySelector('#chat-messages');
  if (!host) return;
  assistantView.snapshot = snapshot;
  const signature = JSON.stringify(snapshot);
  if (signature === assistantView.signature) return;
  assistantView.signature = signature;
  const expanded = new Set([...host.querySelectorAll('details[open]')].map(item => item.dataset.sourceIndex));
  const pinned = host.scrollHeight - host.scrollTop - host.clientHeight < 80;
  const messages = snapshot.messages || [];
  host.innerHTML = messages.length ? messages.map((message, index) => {
    const sources = (message.sources || []).map((source, sourceIndex) => assistantSourceHTML(source, `${index}-${sourceIndex}`)).join('');
    const candidates = (message.candidates || []).map((candidate, candidateIndex) => `<button type="button" class="chat-candidate" data-candidate="${candidateIndex}" data-generation="${message.generation}" ${snapshot.busy || snapshot.generation !== message.generation ? 'disabled' : ''}><strong>${esc(candidate.label)}</strong><small>${esc(candidate.subtitle || '确认后继续查询')}</small></button>`).join('');
    const entry=message.entry_records&&!state.boot.read_only?`<button type="button" class="chat-candidate" data-entry-message="${index}" ${snapshot.busy||snapshot.generation!==message.generation?'disabled':''}>核对录入清单 · 尚未记账</button>`:'';
    const evidence=message.evidence_note?`<details class="chat-evidence-note"><summary>范围、依据与限制</summary><p>${esc(message.evidence_note.scope)} · 答复完成于 ${esc(message.evidence_note.completed_at)}</p><p>${esc(message.evidence_note.limits)}</p></details>`:'';
    return `<article class="chat-message ${message.role === 'user' ? 'chat-user' : 'chat-assistant'}"><small>${message.role === 'user' ? '' : '经营助手 · '}${message.project_id ? esc(state.boot.options.projects.find(item => String(item.value) === String(message.project_id))?.label || '指定项目') : '全公司'}</small><div class="chat-answer ${message.message_type === 'error' ? 'chat-error' : ''}">${message.role === 'user' ? `<p>${esc(message.content)}</p>` : answerHTML(message.content)}</div>${candidates ? `<div class="chat-candidates">${candidates}</div>` : ''}${entry}${evidence}${sources}</article>`;
  }).join('') : `<div class="chat-welcome"><span class="chat-mark" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"><path d="M4 4h16v13H9l-5 4zM8 9h8m-8 4h5"/></svg></span><h2>今天想了解哪项经营情况？</h2><p>收入、回款、项目成本、工人出勤。<br>查你的本机台账，每个结论都有来源。</p><div class="chat-examples"><button type="button" data-example="收入最多的客户是谁？">收入最多的客户是谁？</button><button type="button" data-example="今年实际回款是多少，包含历史旧账？">今年一共收了多少钱？</button><button type="button" data-example="这个项目的采购成本是多少？">这个项目的钱花在哪里？</button></div></div>`;
  if (snapshot.busy) host.insertAdjacentHTML('beforeend', `<article class="chat-message chat-assistant"><small>经营助手 · 正在查账</small><div class="chat-answer"><p>${esc(snapshot.stream || '正在理解条件并核对本机记录…')}</p></div></article>`);
  for (const detail of host.querySelectorAll('details')) detail.open = expanded.has(detail.dataset.sourceIndex);
  document.querySelector('#chat-send').disabled = snapshot.busy;
  document.querySelector('#chat-stop').hidden = !snapshot.busy;
  document.querySelector('#chat-state').textContent = snapshot.busy ? '查询中，可随时停止' : '本次对话仅保存在本机内存，重启服务后清除';
  document.querySelector('#chat-history-note').textContent = snapshot.dropped_messages ? `仅显示最近40条消息，较早的${snapshot.dropped_messages}条已清除。` : '';
  document.querySelector('#chat-scope').textContent = snapshot.project_id ? state.boot.options.projects.find(item => String(item.value) === String(snapshot.project_id))?.label || '指定项目' : '全公司经营数据';
  if (String(snapshot.project_id || '') !== state.project) {
    state.project = String(snapshot.project_id || '');
    document.querySelector('#project-scope').value = state.project;
    document.querySelector('#project-scope').dispatchEvent(new Event('choice-sync'));
  }
  host.querySelectorAll('[data-example]').forEach(button => button.onclick = () => {
    document.querySelector('#chat-question').value = button.dataset.example;
    document.querySelector('#chat-question').focus();
  });
  host.querySelectorAll('[data-candidate]').forEach(button => button.onclick = () => safe(async () => {
    await assistantAPI('confirm', {session_id: assistantView.session, generation: Number(button.dataset.generation),
      index: Number(button.dataset.candidate), allow_model: document.querySelector('#chat-model').checked});
    await pollAssistant(assistantView.epoch);
  }));
  if (pinned) host.scrollTop = host.scrollHeight;
  host.querySelectorAll('[data-entry-message]').forEach(button=>button.onclick=()=>safe(()=>{
    const message=snapshot.messages[Number(button.dataset.entryMessage)];
    return openEntryDraft(message.entry_records,message.generation,message.entry_questions||[],message.question);
  }));
}

async function pollAssistant(epoch) {
  clearTimeout(assistantView.timer);
  try {
    const snapshot = await assistantAPI('state?session_id=' + encodeURIComponent(assistantView.session));
    if (epoch !== assistantView.epoch || state.route !== 'assistant') return;
    drawAssistant(snapshot);
    if (snapshot.busy) assistantView.timer = setTimeout(() => pollAssistant(epoch), 600);
  } catch (error) {
    if (epoch === assistantView.epoch) {
      document.querySelector('#chat-state').textContent = error.message;
      document.querySelector('#chat-send').disabled = true;
      document.querySelector('#chat-stop').hidden = true;
      document.querySelector('#chat-reset').textContent = '重新连接';
      assistantView.session = null;
      sessionStorage.removeItem(assistantSessionKey);
    }
  }
}

async function suspendAssistant() {
  assistantView.epoch++;
  clearTimeout(assistantView.timer);
  if (assistantView.session && (assistantView.snapshot?.busy || assistantView.sending)) {
    try { assistantView.snapshot = await assistantAPI('stop', {session_id: assistantView.session}); }
    catch { assistantView.session = null; }
  }
}

async function renderAssistant() {
  const epoch = assistantView.epoch;
  let snapshot;
  assistantView.session ||= sessionStorage.getItem(assistantSessionKey);
  if (assistantView.session) {
    try { snapshot = await assistantAPI('state?session_id=' + encodeURIComponent(assistantView.session)); }
    catch { assistantView.session = null; sessionStorage.removeItem(assistantSessionKey); }
  }
  if (!assistantView.session) {
    snapshot = await assistantAPI('new', {});
    assistantView.session = snapshot.session_id;
    sessionStorage.setItem(assistantSessionKey, assistantView.session);
  }
  if (epoch !== assistantView.epoch || state.route !== 'assistant') return;
  if (String(snapshot.project_id || '') !== state.project) snapshot = await assistantAPI('scope', {session_id: assistantView.session, project_id: state.project});
  if (epoch !== assistantView.epoch || state.route !== 'assistant') return;
  main.innerHTML = `<section class="chat-layout"><header class="chat-head"><div><h1>经营助手</h1><p id="chat-scope"></p></div><button id="chat-reset" type="button">清空对话</button></header><div id="chat-messages" class="chat-messages" role="log" aria-label="经营对话"></div><p id="chat-history-note" class="page-note"></p><form id="chat-form" class="chat-composer"><label class="sr-only" for="chat-question">输入经营问题</label><textarea id="chat-question" maxlength="4000" rows="2" placeholder="例如：这个月谁的工天最多？"></textarea><div class="chat-compose-actions"><small id="chat-state" role="status"></small><button id="chat-stop" type="button" hidden>停止</button><button id="chat-send" class="primary" type="submit">发送</button></div><label class="chat-consent"><input id="chat-model" type="checkbox">允许模型理解问题（默认不发送）</label><p class="chat-privacy">勾选后仅将问题、已确认条件和工具目录发给桌面配置的模型，不发送原始账目或附件。查询和金额计算仍在本机完成。</p></form></section>`;
  assistantView.signature = '';
  document.querySelector('.chat-compose-actions').insertAdjacentHTML('afterbegin',`<label class="sr-only" for="chat-mode">助手模式</label><select id="chat-mode" class="chat-mode"><option value="query">查账与分析</option><option value="entry" ${state.boot.read_only?'disabled':''}>协助录入</option></select>`);
  document.querySelector('#chat-mode').onchange=event=>{
    document.querySelector('#chat-question').placeholder=event.target.value==='entry'?'一次说清多笔费用、采购、回款或工天，先整理清单再确认保存':'例如：这个月谁的工天最多？';
    document.querySelector('#chat-state').textContent=event.target.value==='entry'?'先整理草稿，不直接记账':'';
  };
  drawAssistant(snapshot);
  document.querySelector('#chat-form').onsubmit = event => {
    event.preventDefault();
    safe(async () => {
      const input = document.querySelector('#chat-question');
      const question = input.value.trim();
      if (!question) return input.focus();
      document.querySelector('#chat-send').disabled = true;
      assistantView.sending = true;
      try {
        await assistantAPI('ask', {session_id: assistantView.session, question, generation: assistantView.snapshot.generation,
          allow_model: document.querySelector('#chat-model').checked,mode:document.querySelector('#chat-mode').value});
        if (epoch !== assistantView.epoch) return;
        input.value = '';
        await pollAssistant(epoch);
      } catch (error) {
        if (epoch === assistantView.epoch) document.querySelector('#chat-send').disabled = false;
        throw error;
      } finally { assistantView.sending = false; }
    });
  };
  document.querySelector('#chat-question').onkeydown = event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      if (!document.querySelector('#chat-send').disabled) document.querySelector('#chat-form').requestSubmit();
    }
  };
  document.querySelector('#chat-stop').onclick = () => safe(async () => {
    drawAssistant(await assistantAPI('stop', {session_id: assistantView.session}));
    clearTimeout(assistantView.timer);
    document.querySelector('#chat-state').textContent = '已停止，未保存未完成回答';
  });
  document.querySelector('#chat-reset').onclick = () => safe(async () => {
    if (!assistantView.session) { await renderAssistant(); return; }
    drawAssistant(await assistantAPI('scope', {session_id: assistantView.session, project_id: state.project, clear: true}));
  });
  if (snapshot.busy) await pollAssistant(epoch);
}
