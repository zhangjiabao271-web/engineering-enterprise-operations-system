// Allocation belongs to the order; item prices and categories remain purchase facts.
function purchaseAllocationHTML(record, options) {
  if (!state.boot.schema.purchases.fields.some(field => field.name === 'allocation_method')) {
    return notice('采购均摊已更新。请先保存当前输入，方便时重新打开工作台启用；当前仍可正常登记单项目采购。');
  }
  const selected = new Set((record.allocation_method === 'equal' ? record.project_ids || [] : options.active_projects.map(row => row.value)).map(String));
  const active = new Set(options.active_projects.map(row => String(row.value)));
  const projects = [...options.active_projects, ...options.projects.filter(row => selected.has(String(row.value)) && !active.has(String(row.value)))];
  return `<section id="purchase-allocation" class="purchase-allocation" hidden aria-labelledby="purchase-allocation-title">
    <h3 id="purchase-allocation-title">均摊到哪些项目</h3>
    <p>本单全部材料和运费一起均摊。默认选择未完工项目，可取消不参与的项目；专用材料请另开单项目采购。</p>
    <div class="purchase-allocation-tools"><button type="button" data-allocation-select="active">选中未完工项目</button><button type="button" data-allocation-select="none">清空选择</button></div>
    <div class="purchase-allocation-projects">${projects.map(row => `<label><input type="checkbox" name="purchase-project" value="${esc(row.value)}" data-active="${active.has(String(row.value))}" ${selected.has(String(row.value)) ? 'checked' : ''}><span>${esc(row.label)}${active.has(String(row.value)) ? '' : '（历史归集项目）'}</span></label>`).join('') || '<p>暂无未完工项目。请先建项目，或选择单项目归集。</p>'}</div>
    <output id="purchase-allocation-preview" aria-live="polite"></output>
  </section>`;
}

function collectPurchaseAllocation() {
  if (!form.elements.allocation_method) return {};
  const method = form.elements.allocation_method.value;
  return {
    allocation_method: method,
    project_ids: method === 'equal' ? [...form.querySelectorAll('[name="purchase-project"]:checked')].map(input => input.value) : [],
    project_id: method === 'direct' ? form.elements.project_id.value : null,
  };
}

function bindPurchaseAllocation() {
  const section = form.querySelector('#purchase-allocation');
  const project = form.elements.project_id;
  const mode = form.elements.allocation_method;
  if (!mode) return;
  const sync = () => {
    section.hidden = mode.value !== 'equal';
    project.closest('.form-field').hidden = mode.value !== 'direct';
    project.disabled = mode.value !== 'direct';
    project.dispatchEvent(new Event('choice-sync'));
  };
  section.querySelectorAll('[data-allocation-select]').forEach(button => button.onclick = () => {
    section.querySelectorAll('[name="purchase-project"]').forEach(input => {
      input.checked = button.dataset.allocationSelect === 'active' && input.dataset.active === 'true';
    });
    section.dispatchEvent(new Event('change', {bubbles:true}));
  });
  mode.addEventListener('change', sync);
  sync();
}

function renderPurchaseAllocationPreview(result) {
  const output = form.querySelector('#purchase-allocation-preview');
  if (!output) return;
  const rows = result.allocations || [];
  output.innerHTML = rows.length ? `<p>已选 ${rows.length} 个项目 · 合计 ${money(result.total_amount_cents)} · 尾差自动分配到分</p><ul>${rows.map(row => `<li><span>${esc(row.project_name)} · ${esc(row.project_code)}</span><strong>${money(row.amount_minor)}</strong></li>`).join('')}</ul>` : '';
}
