'use strict';

function backupSummary(review) {
  return `<dl class="backup-summary"><div><dt>数据库版本</dt><dd>${esc(review.schema_version)}</dd></div><div><dt>项目记录</dt><dd>${esc(review.project_count)}</dd></div><div><dt>工程 / 历史回款记录</dt><dd>${esc(review.receipt_count)} / ${esc(review.historical_receipt_count)}</dd></div><div><dt>已打包附件</dt><dd>${esc(review.file_count)}</dd></div><div><dt>原文件缺失</dt><dd>${esc(review.missing_count)}</dd></div></dl>`;
}

function installBackupControls() {
  main.insertAdjacentHTML('afterbegin', `<section class="card"><div class="section-head"><div><h2>账本与附件备份</h2><p class="page-note">完整 ZIP 保留账本、原附件、施工照片与资料来源；报表导出不能代替它。</p></div><button id="create-backup" class="primary">生成完整备份</button></div><div id="backup-result" role="status"></div><details><summary>从备份恢复账本</summary><p>恢复将覆盖当前账本及备份中对应附件。先隔离预检，再保全当前资料；失败会尝试回滚。缺失原文件仍单独报告，不删除其他附件。</p><label class="form-field">选择本机 ZIP（最多512MB）<input id="restore-file" type="file" accept=".zip"></label><button id="preview-restore" ${state.boot.read_only?'disabled':''}>上传并预检恢复范围</button><p id="restore-progress" role="status"></p></details></section>`);
  document.querySelector('#create-backup').onclick = () => safe(async () => {
    const button = document.querySelector('#create-backup');
    button.disabled = true;
    try {
      const result = await api('backup/create', {});
      document.querySelector('#backup-result').innerHTML = backupSummary(result.review) +
        `<a class="quick-link" href="/api/backup/download?${query({token:result.token})}" download>保存完整备份 ZIP</a>` +
        notice(result.review.missing_count ? '备份已生成，但有原附件缺失；请核对，不应视为附件齐全。' : '数据库、文件指纹、完整性和外键检查通过。请保存到另外一个安全位置。');
    } finally { button.disabled = false; }
  });
  document.querySelector('#preview-restore').onclick = () => safe(previewRestore);
}

async function previewRestore() {
  const generation = loadingVersion;
  const file = document.querySelector('#restore-file').files[0];
  if (!file || file.size > 512 * 1024**2) throw new Error('请选择不超过512MB的备份 ZIP');
  const button = document.querySelector('#preview-restore');
  const progress = document.querySelector('#restore-progress');
  button.disabled = true;
  let token;
  try {
    const started = await api('backup/restore_start', {name:file.name, size:file.size});
    token = started.token;
    for (let offset = 0; offset < file.size; offset += started.chunk_size) {
      const part = file.slice(offset, offset + started.chunk_size);
      const content = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(',')[1]);
        reader.onerror = reject;
        reader.readAsDataURL(part);
      });
      await api('backup/restore_chunk', {token, offset, content});
      if (generation !== loadingVersion) throw new SupersededView();
      progress.textContent = `已上传 ${Math.min(100, Math.round((offset + part.size) / file.size * 100))}% · 尚未恢复账本`;
    }
    progress.textContent = '正在隔离预检数据库与附件，尚未恢复账本…';
    const result = await api('backup/restore_preview', {token});
    if (generation !== loadingVersion) throw new SupersededView();
    progress.textContent = '预检完成，尚未恢复账本。';
    openEditor('确认覆盖当前账本', notice('这不是普通导入。确认后将用该备份替换当前账本，旧窗口、对话和未保存表单将失效；恢复前自动保全数据库及被覆盖附件。','warning') + backupSummary(result.review) +
      `<label class="form-field">输入“恢复该账本”<input name="restore-confirmation" required autocomplete="off"></label><label class="confirm-check"><input type="checkbox" name="restore-confirmed" required>已核对备份日期、账本、缺失附件及覆盖范围，确认恢复。</label>`, async () => {
        const restored = await api('backup/restore_apply', {token, confirmed:form.elements.namedItem('restore-confirmed').checked, confirmation:form.elements.namedItem('restore-confirmation').value});
        cancelEditor = null;
        openEditor('账本已恢复', notice('恢复前安全材料已保留：' + restored.safety_backup) + notice('请重新打开工作台，使用恢复后的账本。') + '<button type="button" id="reload-restored">重新载入工作台</button>', null);
        document.querySelector('#reload-restored').onclick = () => location.reload();
      }, {label:'确认恢复账本', cancel:() => api('backup/restore_discard', {token})});
  } catch (error) {
    if (token) { try { await api('backup/restore_discard', {token}); } catch {} }
    throw error;
  } finally { button.disabled = false; }
}
