/* Shared interaction layer: strict imports, explicit bulk operations, screenshot
 * staging and drill-down. Persistent writes always use the existing API guards. */
'use strict';

function showUIError(error) {
  toast(error.message || '操作未完成，请重试。', true);
  if (!error.diagnostics) return;
  const report = error.diagnostics;
  const previous = document.activeElement;
  const dialog = document.createElement('dialog');
  dialog.className = 'wide import-errors';
  dialog.setAttribute('aria-label', '表格校验错误');
  dialog.innerHTML = `<div class="modal-head"><h2>表格校验错误</h2><button class="btn secondary" type="button" data-dismiss>关闭并修正</button></div>
    <div class="modal-body"><div class="callout amber">${esc(report.message)} 本次没有保存任何导入行。
    ${report.truncated ? '仅列出前200个问题，请先修正后再次校验。' : ''}</div><div class="row wrap" style="margin:12px 0">
    <button class="btn secondary" type="button" data-json>下载错误清单 JSON</button><button class="btn secondary" type="button" data-text>下载可交给AI的错误说明</button></div>
    <div class="table-wrap"><table><thead><tr><th>工作表</th><th>位置</th><th>字段</th><th>错误码</th><th>修正说明</th></tr></thead>
    <tbody>${report.errors.map(e => `<tr><td>${esc(e.sheet)}</td><td>${esc(e.column)}${e.row || '文件结构'}</td><td>${esc(e.field || '整行／结构')}</td><td class="mono small">${esc(e.code)}</td><td>${esc(e.message)}</td></tr>`).join('')}</tbody></table></div>
    <p>请保留材料ID、学生身份与原版本不变。遇到版本冲突或规则失效，应重新导出任务并重新判断，不能修改固定字段绕过检查。</p></div>`;
  document.body.append(dialog);
  const dismiss = () => { dialog.close(); dialog.remove(); previous?.isConnected && previous.focus(); };
  dialog.querySelector('[data-dismiss]').onclick = dismiss;
  dialog.addEventListener('cancel', e => { e.preventDefault(); dismiss(); });
  dialog.querySelector('[data-json]').onclick = () => saveBlob(new Blob([JSON.stringify(report, null, 2)], {type: 'application/json'}), '表格校验错误.json');
  dialog.querySelector('[data-text]').onclick = () => saveBlob(new Blob([
    '请根据以下位置修正原始回填表。不得修改任务身份字段或伪造版本。系统本次没有写入数据。\n\n' +
    report.errors.map(e => `${e.sheet} ${e.column}${e.row || ''} [${e.field || '整行/结构'}] ${e.code}：${e.message}`).join('\n')
  ], {type: 'text/plain;charset=utf-8'}), '表格校验修正说明.txt');
  dialog.showModal();
}
window.addEventListener('collection-error', event => showUIError(event.detail));
window.addEventListener('collection-download', event => saveBlob(event.detail.blob, event.detail.name));
window.addEventListener('collection-copy', event => modal('复制所选字段',
  `<p>浏览器未允许直接写入剪贴板。请选中下面文字后复制。</p>${area('copy-selection', '所选可见字段', event.detail, 'readonly rows="12"')}`));

function collectionIds(selector) { return BNUCollections.selected(document.querySelector(selector)); }
function requireSelection(ids, max = 2000) {
  if (!ids.length) throw new Error('请先勾选或框选要操作的记录。');
  if (ids.length > max) throw new Error(`每次最多处理${max}项，请缩小筛选范围或分批选择。`);
}
function snapshotStudent(student) {
  return {id: student.id, name: student.name, class_name: student.class_name, disabled: !!student.disabled};
}

function enhanceCollections(container = document) {
  if (!container) return;
  const find = selector => container.matches?.(selector) ? [container] : [...container.querySelectorAll(selector)];
  // Native checklists use their existing input rather than two competing controls.
  for (const list of find('.check-list')) {
    if (!list.querySelector('.check-item input[type="checkbox"][value]')) continue;
    BNUCollections.mount(list, {selector: '.check-item', key: row => row.querySelector('input[type="checkbox"]').value,
      checkbox: 'input[type="checkbox"]', selectedKeys: [...list.querySelectorAll('input:checked')].map(x => x.value) });
  }
  for (const root of find('#project-list')) {
    BNUCollections.mount(root, {selector: '.project-card', key: row => row.dataset.recordId,
      actions: ids => S.me.role === 'teacher' && ids.length ? [
        ...(S.trash ? [{label: '恢复所选工程', run: () => projectBatchDialog(ids, 'restore')}] : [
          {label: '移动所选工程', run: () => projectBatchDialog(ids, 'move')},
          {label: '所选工程移入回收站', danger: true, run: () => projectBatchDialog(ids, 'trash')}])
      ] : []});
  }
  for (const root of find('#review-queue')) {
    const queue = reviewQueue();
    BNUCollections.mount(root, {selector: '.queue-entry', key: row => row.dataset.recordId,
      allKeys: () => queue.ids.slice(),
      selectedKeys: (queue.selected || []).filter(id => queue.ids.includes(id)),
      onChange: ids => { queue.selected = ids; },
      actions: ids => ids.length ? [
        {label: '依次阅览所选材料', run: async () => {
          requireSelection(ids); if (!allowReviewNavigation()) return;
          const q = reviewQueue(); q.ids = ids.slice(); q.page = 0; S.reviewId = ids[0]; await reviewView();
        }},
        ...(!['setup','closed','archived'].includes(S.project.body.state) ? [{label: '批量保存人工结论', run: () => reviewBatchDialog(ids)}] : []),
        ...(S.me.role === 'teacher' ? [{label: '所选材料导出网页AI任务', run: () => webTaskDialog(ids)}] : [])
      ] : []});
  }
  for (const root of find('#upload-queue')) {
    BNUCollections.mount(root, {selector: '.upload-item', key: row => row.dataset.recordId,
      actions: ids => ids.length ? [{label: '移除所选未提交图片', danger: true, run: async () => {
        const files = S.files.filter(f => ids.includes(f.key));
        if (S.uploading) throw new Error('正在上传，不能移除队列。');
        if (files.some(f => f.state === 'done')) throw new Error('所选项含已接收材料。原始材料不可删除，请只选择未接收项。');
        if (!window.confirm(`移除${files.length}张待上传图片？不会删除任何服务器原件。`)) return;
        for (const f of files) URL.revokeObjectURL(f.url);
        S.files = S.files.filter(f => !ids.includes(f.key)); await submissionView();
      }}] : []});
  }
  for (const root of find('tbody')) {
    const meaningful = row => row.cells.length > 1 && !row.querySelector('td[colspan]');
    const rows = [...root.children].filter(meaningful);
    if (!rows.length) continue;
    rows.forEach(row => { if (!row.dataset.recordId) row.dataset.recordId = row.querySelector('[data-id]')?.dataset.id || crypto.randomUUID(); });
    const config = {selector: 'tr[data-record-id]', key: row => row.dataset.recordId};
    if (root.id === 'student-table') config.actions = ids => ids.length ? [
      {label: '批量调整班级', run: () => studentBatchDialog(ids, 'class')},
      {label: '批量启用登录', run: () => studentBatchDialog(ids, 'enable')},
      {label: '批量停用登录', danger: true, run: () => studentBatchDialog(ids, 'disable')}
    ] : [];
    if (root.id === 'public-rows') config.actions = ids => {
      const editable = S.publicEdit?.owner_id === S.me.id && S.publicEdit.revision === (S.publicEdit.latest_revision || S.publicEdit.revision);
      return editable && ids.length ? [{label: '批量修改所选名单行（草稿）', run: () => publicBatchDialog(ids)},
        {label: '从草稿移出所选行', danger: true, run: () => {
          requireSelection(ids, 20000);
          if (!window.confirm(`从当前草稿移出${ids.length}行？保存后产生新版本，旧版和原件保持不变。`)) return;
          S.publicRows = S.publicRows.filter(r => !ids.includes(r.id)); refreshPublicRows();
        }}] : [];
    };
    if (root.id === 'appeal-table') config.actions = ids => S.project?.can_manage && ids.length ? [
      {label: '批量处理未处理申请', run: () => appealBatchDialog(ids)}
    ] : [];
    if (root.id === 'achievement-table') config.actions = ids => ids.length && S.me.role === 'teacher' ? [
      {label: '导出所选成果完整表', run: async () => {
        requireSelection(ids, 20000);
        const result = await api('/api/projects/' + pid() + '/export/selected-achievements', {method: 'POST', body: {ids}, binary: true});
        saveBlob(result.blob, '所选成果完整表.xlsx');
      }}
    ] : [];
    BNUCollections.mount(root, config);
    if(root.id==='public-rows'&&S.focusPublicRow){const target=root.querySelector('[data-record-id="'+CSS.escape(S.focusPublicRow)+'"]');if(target){root._collection.select([S.focusPublicRow]);target.tabIndex=0;setTimeout(()=>{target.scrollIntoView({block:'center'});target.focus();},0);}S.focusPublicRow=null;}
  }
  for (const root of find('.project-grid').filter(r => !r._collection)) {
    BNUCollections.mount(root, {selector: '.project-card', key: row => {
      if (!row.dataset.recordId) row.dataset.recordId = row.querySelector('a[href]')?.getAttribute('href') || crypto.randomUUID();
      return row.dataset.recordId;
    }});
  }
}

A['check-all'] = button => {
  const root = get(button.dataset.target), collection = root?._collection;
  if (collection) collection.select([...collection.selected, ...collection.visibleRows().map(row => row.dataset.pick)]);
  else root?.querySelectorAll('[data-search]').forEach(row => { if (!row.hidden) row.querySelector('input[type=checkbox]').checked = true; });
};

function studentBatchDialog(ids, action) {
  requireSelection(ids);
  const selected = S.students.filter(s => ids.includes(s.id));
  if (selected.length !== ids.length) throw new Error('选择的学生已不在当前名册中，请刷新后重试。');
  S.batchOperation = {kind: 'students', action, items: selected.map(snapshotStudent)};
  const title = {class: '批量调整班级', enable: '批量启用登录', disable: '批量停用登录'}[action];
  modal(title, `<form data-form="batch-operation"><p>实际选择${ids.length}名学生，包括当前筛选隐藏的已选项。该批次全部校验通过才保存。</p>
    ${action === 'class' ? field('class_name', '目标班级', '', 'text', 'required maxlength="100"') : '<p>只改变登录状态，不删除学生、原件、历史分数；停用会使现有登录会话失效。</p>'}
    <details><summary>核对所选学生</summary><p>${selected.map(s => esc(s.name + ' · ' + s.number)).join('<br>')}</p></details>${actions('确认' + title)}</form>`);
}
async function projectBatchDialog(ids, action) {
  requireSelection(ids);
  const projects = S.projects.filter(p => ids.includes(p.id));
  if (projects.length !== ids.length || projects.some(p => !p.can_manage)) throw new Error('仅能批量操作本人负责的工程，请检查所选项。');
  S.batchOperation = {kind: 'projects', action, items: projects.map(p => ({id: p.id, revision: p.revision}))};
  modal('批量工程管理', `<form data-form="batch-operation"><p>所选${ids.length}个工程将一起${{move: '移动', trash: '移入回收站', restore: '恢复到根目录'}[action]}。</p>
    ${action === 'move' ? select('folder_id', '目标目录', [['', '根目录'], ...S.folders.filter(f => !f.trashed).map(f => [f.id, f.name])]) : ''}
    <p>原始材料和审计记录不会被物理删除。任何一项权限或版本冲突都会取消整个批次。</p>${actions('确认处理所选工程')}</form>`);
}
function reviewBatchDialog(ids) {
  requireSelection(ids);
  if (S.formDirty) throw new Error('当前材料有未保存修改，请先保存或明确放弃，再进行批量核对。');
  const selected = S.project.body.evidence.filter(e => ids.includes(e.id));
  if (selected.length !== ids.length) throw new Error('队列已变化，请重新选择。');
  S.batchOperation = {kind: 'review', pid: pid(), revision: S.project.revision,
    items: selected.map(e => ({id: e.id, version: currentAnnotation(e)?.version || 0})), operation_id: crypto.randomUUID()};
  modal('批量保存人工结论', `<form data-form="batch-operation"><p>所选${ids.length}张材料。只有已逐张核对、可使用同一处理理由时才使用批量操作。</p>
    ${select('status', '处理状态', [['manual', '需人工判断'], ['untrusted', '不可信'], ['invalid_image', '图片无效'], ['out_of_period', '不在加分时间'], ['out_of_scope', '不属于加分情形'], ['system_error', '系统异常'], ['valid', '确认已有完整有效结论']])}
    ${area('note', '共同人工核对依据（内部）', '', 'required maxlength="4000"')}
    <p class="small muted">“确认已有完整有效结论”不会编造分值，任一材料没有完整的有效提案就整体拒绝。其余状态新版本记0分，旧分值保留。</p>
    <label class="checkbox"><input type="checkbox" required>我已核对全部所选材料及共同依据</label>${actions('确认保存整个批次')}</form>`);
}
function appealBatchDialog(ids) {
  requireSelection(ids);
  const appeals = S.project.body.appeals.filter(a => ids.includes(a.id));
  if (appeals.length !== ids.length || appeals.some(a => a.history.length)) throw new Error('批量处理仅适用于尚未处理的申请，请筛选“待处理”并重新选择。');
  S.batchOperation = {kind: 'appeals', pid: pid(), revision: S.project.revision, ids};
  modal('批量处理未处理申请', `<form data-form="batch-operation"><p>所选${ids.length}份申请使用同一结论。处理历史将分别保留。</p>
    ${select('decision', '处理结论', [['approved', '批准补交'], ['rejected', '不予批准']])}
    ${field('until', '批准补交截止时间', localDate(new Date(Date.now() + 86400000).toISOString()), 'datetime-local')}
    ${area('note', '统一处理依据', '', 'required maxlength="4000"')}${actions('确认保存处理记录')}</form>`);
}
F['batch-operation'] = async form => {
  const operation = S.batchOperation;
  if (!operation) throw new Error('批量操作已失效，请重新选择。');
  const data = formData(form), {kind, ...payload} = operation;
  let path;
  if (kind === 'students') { path = '/api/students/batch'; payload.class_name = data.class_name || ''; }
  if (kind === 'projects') { path = '/api/projects/batch'; payload.folder_id = data.folder_id || null; }
  if (kind === 'review') { path = '/api/projects/' + payload.pid + '/review-batch'; delete payload.pid; payload.status = data.status; payload.note = data.note; }
  if (kind === 'appeals') {
    path = '/api/projects/' + payload.pid + '/appeals/batch'; delete payload.pid;
    Object.assign(payload, {decision: data.decision, note: data.note, until: data.decision === 'approved' && data.until ? new Date(data.until).toISOString() : null});
  }
  const result = await api(path, {method: 'POST', body: payload});
  S.batchOperation = null; A.close(); toast(`已保存${result.count}项。`);
  if (kind === 'students') await studentsView();
  else if (kind === 'projects') await workspace();
  else { await loadProject(operation.pid); if (kind === 'review') await reviewView(); else await publicationView(); }
};

function publicBatchDialog(ids) {
  requireSelection(ids, 20000);
  S.publicBatchIds = ids.slice();
  modal('批量修改名单行草稿', `<form data-form="public-batch"><p>所选${ids.length}行。只有勾选“应用”的字段会改变；未勾选字段保留原值。</p>
    <div class="field-grid">${[['category_id', '具体分类', select('category_id', '', publicNodeOptions())],
      ['scenario', '具体情形', field('scenario', '', '')], ['material_date', '材料日期', field('material_date', '', '')],
      ['rule_source', '细则出处', field('rule_source', '', '')], ['note', '内部备注', area('note', '', '')],
      ['score', '相同局部分值', field('score', '', '0', 'text', 'inputmode="decimal"')]].map(([key, label, html]) =>
      `<div><label class="checkbox"><input type="checkbox" name="apply_${key}">应用：${label}</label>${html}</div>`).join('')}</div>
    <p class="small muted">此操作只更新草稿，不改变已保存版本。分值与基础分、系数不一致时，最终保存会拒绝；有不同依据的行应分别编辑。</p>${actions('应用到所选草稿行')}</form>`, true);
}
F['public-batch'] = async form => {
  const data = formData(form), keys = Object.keys(data).filter(k => k.startsWith('apply_')).map(k => k.slice(6));
  if (!keys.length) throw new Error('请至少勾选一个要应用的字段。');
  const ids = new Set(S.publicBatchIds || []);
  S.publicRows = S.publicRows.map(row => ids.has(row.id) ? {...row, ...Object.fromEntries(keys.map(k => [k, k === 'material_date' ? data[k] || null : data[k]]))} : row);
  S.publicBatchIds = null; A.close(); refreshPublicRows(); toast('已更新草稿；请核对并保存新的名单版本。');
};

// Screenshot and clipboard input share the existing upload endpoints/constraints.
A['capture-evidence'] = async () => { const file = await BNUCapture.take(); if (file) await addFiles([file]); };
function clearFeedbackDraft() {
  for (const item of S.feedbackFiles || []) if (item.url) URL.revokeObjectURL(item.url);
  S.feedbackFiles = []; S.feedbackText = '';
}
function paintFeedbackFiles() {
  const list = get('feedback-files'); if (!list) return;
  list.innerHTML = (S.feedbackFiles || []).map(item => `<div class="feedback-file" data-record-id="${item.id}">
    ${item.url ? `<img src="${item.url}" alt="待提交反馈图片">` : icon('file')}
    <div class="grow"><strong>${esc(item.file.name)}</strong><div class="small muted">${size(item.file.size)}</div></div>
    ${btn('feedback-remove', '移除', 'secondary small-btn', {id: item.id})}</div>`).join('') || '<p class="muted small">尚未添加附件。</p>';
}
function addFeedbackFiles(files) {
  const incoming = [...files], current = S.feedbackFiles || [];
  if (current.length + incoming.length > 5) throw new Error('反馈最多5个附件，请先移除不需要的文件。');
  if ([...current.map(x => x.file), ...incoming].reduce((n, file) => n + file.size, 0) > 100 * 1024 * 1024)
    throw new Error('反馈附件合计不能超过100MB，本次选择没有加入。');
  if (incoming.some(f => !['image/jpeg','image/png','image/webp','video/mp4','video/webm','application/pdf'].includes(f.type) && !/\.(jpe?g|png|webp|mp4|webm|pdf)$/i.test(f.name)))
    throw new Error('反馈附件仅支持JPEG、PNG、WebP、MP4、WebM或PDF，本次选择没有加入。');
  S.feedbackFiles = [...current, ...incoming.map(file => ({file, id: crypto.randomUUID(), url: file.type.startsWith('image/') ? URL.createObjectURL(file) : null}))];
  paintFeedbackFiles();
}
A.feedback = () => {
  modal('提交意见反馈', `<form data-form="feedback" id="feedback-form">
    ${area('text', '问题、操作步骤或建议', S.feedbackText || '', 'required maxlength="10000" placeholder="请说明所在页面、操作步骤与看到的问题。"')}
    <div class="row wrap">${btn('capture-feedback', '截屏并添加', 'secondary desktop-capture', {}, 'camera')}
      <label class="btn secondary">选择截图或录屏<input class="visually-hidden" name="files" type="file" data-feedback-files multiple accept="image/jpeg,image/png,image/webp,video/mp4,video/webm,.pdf"></label></div>
    <p class="paste-hint small muted">也可使用系统截图后在此弹窗按 Ctrl+V / ⌘V 粘贴。截图确认后先加入附件，不会立即发送。</p>
    <div id="feedback-files"></div><p class="small muted">最多5个附件，合计100MB。仅唯一系统管理员能查看反馈和附件。请勿上传无关个人隐私。</p>
    ${actions('发送给系统管理员')}</form>`);
  paintFeedbackFiles();
};
A['capture-feedback'] = async () => { const file = await BNUCapture.take(); if (file) addFeedbackFiles([file]); };
A['feedback-remove'] = button => {
  const item = S.feedbackFiles?.find(f => f.id === button.dataset.id); if (item?.url) URL.revokeObjectURL(item.url);
  S.feedbackFiles = (S.feedbackFiles || []).filter(f => f !== item); paintFeedbackFiles();
};
F.feedback = async form => {
  const data = new FormData(); data.append('text', form.elements.text.value);
  for (const item of S.feedbackFiles || []) data.append('files', item.file);
  const result = await api('/api/feedback', {method: 'POST', body: data, raw: true});
  clearFeedbackDraft(); A.close(); toast(result.message);
};
document.addEventListener('input', event => {
  if (event.target.closest('#feedback-form') && event.target.name === 'text') S.feedbackText = event.target.value;
});
document.addEventListener('change', event => {
  if (!event.target.hasAttribute('data-feedback-files')) return;
  try { addFeedbackFiles(event.target.files); } catch (error) { showUIError(error); }
  event.target.value = '';
});
document.addEventListener('paste', event => {
  const feedback = event.target.closest('#feedback-form');
  const evidence = event.target.closest('#evidence-paste-area');
  if (!feedback && !evidence) return;
  const files = [...(event.clipboardData?.items || [])].filter(i => i.kind === 'file' && i.type.startsWith('image/')).map(i => i.getAsFile()).filter(Boolean);
  if (!files.length) return;
  event.preventDefault();
  Promise.resolve().then(() => feedback ? addFeedbackFiles(files) : addFiles(files)).catch(showUIError);
});

/* Preflight -> explicit confirmation. The server revalidates on commit; neither
 * the preview nor client-side defaults are an authority for stored data. */
function previewTable(headers, rows, limit = 100) {
  return `<div class="import-preview table-wrap"><table><thead><tr>${headers.map(x => '<th>' + esc(x) + '</th>').join('')}</tr></thead>
    <tbody>${rows.slice(0, limit).map(row => '<tr>' + row.map(value => '<td>' + esc(value) + '</td>').join('') + '</tr>').join('')}</tbody></table></div>
    ${rows.length > limit ? `<p class="small muted">预览展示前${limit}行；完整批次共${rows.length}行，全部行均已校验。</p>` : ''}`;
}
function showImportPreview(operation, title, message, headers, rows) {
  S.importOperation = operation;
  modal(title, `<div class="callout">${esc(message)}</div>${previewTable(headers, rows)}<div class="form-actions">
    ${btn('close', '暂不应用', 'secondary')}${btn('confirm-import', '确认应用本次导入', '')}</div>
    <p class="small muted">出现版本冲突时不会继续写入，请重新校验。名单和课程应用到草稿后还须保存新版本。</p>`, true);
}
F['roster-import'] = async form => {
  if (!form.elements.file.files.length) {
    const values = formData(form), response = await api('/api/students/import', {method: 'POST', body: {rows: [{number: values.number, name: values.name, class_name: values.class_name}]}});
    A.close(); await studentsView();
    if (response.credentials.length) credentialsModal(response.credentials); else toast('学生资料已保存。');
    return;
  }
  const file = form.elements.file.files[0], body = new FormData(); body.append('file', file);
  const result = await api('/api/tables/validate/students', {method: 'POST', body, raw: true});
  showImportPreview({kind: 'students', file, preview: result}, '学生名册导入预览',
    `已校验${result.rows.length}行；新增${result.new_count}人，匹配现有${result.matched_count}人。确认后才创建账户或更新班级。`,
    ['学号', '姓名', '班级'], result.rows.map(r => [r.number, r.name, r.class_name]));
};
F['public-import'] = async form => {
  const file = form.elements.file.files[0], body = new FormData(); body.append('file', file);
  if (S.publicContextProject) body.append('project_id', S.publicContextProject);
  const result = await api('/api/public-lists/import-rows', {method: 'POST', body, raw: true});
  const ids = new Set(S.publicRows.map(r => r.id));
  const collision = result.rows.find(r => ids.has(r.id));
  if (collision) throw new Error('导入行ID与当前名单草稿重复：' + collision.id + '。该文件没有追加，请核对是否重复导入。');
  showImportPreview({kind: 'public', file, preview: result, listId: S.publicEdit.id}, '名单类奖项导入预览',
    `全部${result.count}行已通过格式、身份和局部评分校验。确认后追加到当前名单草稿，不会覆盖已保存版本。`,
    ['学号', '姓名', '分类ID', '描述', '局部分数', '状态'], result.rows.map(r => {
      const student = S.students.find(s => s.id === r.student_id); return [student?.number, student?.name, r.category_id, r.description, r.score, statuses[r.status]];
    }));
};
F['academic-import'] = async form => {
  const file = form.elements.file.files[0], body = new FormData(); body.append('file', file);
  const result = await api('/api/projects/' + pid() + '/academic/import-rows', {method: 'POST', body, raw: true});
  showImportPreview({kind: 'academic', file, preview: result, projectId: pid()}, '课程成绩导入预览',
    `已校验${result.courses.length}门课程、${result.students.length}名学生。确认后替换当前未保存草稿，原始来源文件和旧版本仍保留；需重新核对确认。`,
    ['学号', '姓名', '课程记录数'], result.students.map(r => { const student = studentById(r.student_id); return [student.number, student.name, r.entries.length]; }));
};
A['confirm-import'] = async () => {
  const operation = S.importOperation;
  if (!operation) throw new Error('没有待确认的导入，请重新选择文件。');
  if (operation.kind === 'students') {
    const body = new FormData(); body.append('file', operation.file);
    if (operation.preview.fingerprint) body.append('fingerprint', operation.preview.fingerprint);
    const result = await api('/api/students/import-file', {method: 'POST', body, raw: true});
    S.importOperation = null; A.close(); await studentsView();
    if (result.credentials.length) credentialsModal(result.credentials); else toast('名册已更新，没有新增账户。');
  } else if (operation.kind === 'public') {
    if (S.publicEdit?.id !== operation.listId) throw new Error('已切换名单，请在目标名单重新导入。');
    const ids = new Set(S.publicRows.map(r => r.id));
    if (operation.preview.rows.some(r => ids.has(r.id))) throw new Error('草稿已变化，部分行重复，请重新导入校验。');
    S.publicRows.push(...operation.preview.rows); S.importOperation = null; A.close(); refreshPublicRows(); toast('已追加至草稿，请核对后保存新版本。');
  } else if (operation.kind === 'academic') {
    if (pid() !== operation.projectId || S.project.revision !== operation.preview.revision) throw new Error('工程已变化，请重新校验课程表。');
    S.academic.courses = operation.preview.courses; S.academic.students = operation.preview.students;
    S.academic.confirmed = false; S.academicStudent = S.academic.students[0]?.student_id || '';
    S.importOperation = null; markDirty(); A.close(); await academicView(); toast('已替换课程草稿，请核对并保存新版本。');
  }
};

async function webTaskDialog(ids = null) {
  if (S.formDirty) throw new Error('请先保存或放弃当前材料的未保存修改，再导出任务。');
  const queue = reviewQueue(), all = S.project.body.evidence;
  let omitted = 0;
  if (!ids) {
    ids = queue.ids.filter(id => !currentAnnotation(all.find(e => e.id === id))?.reviewed);
    omitted = queue.ids.length - ids.length;
  }
  requireSelection(ids);
  const selected = all.filter(e => ids.includes(e.id));
  if (selected.length !== ids.length || selected.some(e => currentAnnotation(e)?.reviewed))
    throw new Error('所选材料已有人工作出结论或不在当前工程。网页AI不能覆盖人工结论，请重新选择。');
  S.taskOperation = {projectId: pid(), revision: S.project.revision, ids: ids.slice()};
  modal('导出网页AI标注任务', `<form data-form="web-ai-export"><p>本次导出${ids.length}张材料，含原图、材料索引、规则原文、学院辅助说明、提示词、回填模板和任务凭据。</p>
    ${omitted ? `<p>已明确排除本队列中${omitted}张有人工作出结论的材料。</p>` : ''}
    <div class="callout amber">任务包含学生个人资料。只能使用学院准许的AI服务，不得将登录密码、服务令牌或整班无关资料交给网页AI。</div>
    <p>任务凭据有效7日；规则或学院说明变更后失效。任务表导入只生成外部标注，不等于人工核对。每批最多2000张，请按班级或筛选范围分批。</p>
    <label class="checkbox"><input type="checkbox" required>我已确认所选材料范围和使用服务符合学院要求</label>${actions('生成并下载任务包')}</form>`);
}
A['web-ai-export'] = () => { const selected = collectionIds('#review-queue'); return webTaskDialog(selected.length ? selected : null); };
F['web-ai-export'] = async () => {
  const operation = S.taskOperation;
  if (!operation) throw new Error('请选择要导出的材料。');
  const result = await api('/api/projects/' + operation.projectId + '/web-ai/export', {method: 'POST',
    body: {revision: operation.revision, evidence_ids: operation.ids}, binary: true});
  saveBlob(result.blob, '网页AI标注任务.zip'); S.taskOperation = null; A.close(); toast('任务包已生成。解压后先阅读“请先阅读.txt”。');
};
A['web-ai-import'] = () => {
  if (S.formDirty) throw new Error('请先保存或明确放弃当前材料的未保存修改，再回填表格。');
  S.webImport = null;
  modal('网页AI表格回填', `<form data-form="web-ai-import">
    <p>先选择系统导出任务包中的原始“任务凭据.json”，再选择网页AI完成的CSV或XLSX。两者须属于同一任务。</p>
    ${field('credential', '原始任务凭据.json', '', 'file', 'required accept=".json"')}
    ${field('file', 'AI完成的回填表', '', 'file', 'required accept=".csv,.xlsx"')}
    <p class="small muted">只导入图片的局部标注，不导入总分。存在任何校验错误都不会保存；错误清单可下载后交给网页AI修正。</p>${actions('校验并预览，不保存')}</form>`);
};
F['web-ai-import'] = async form => {
  const operation = {projectId: pid(), credential: form.elements.credential.files[0], file: form.elements.file.files[0]};
  const body = new FormData(); body.append('credential', operation.credential); body.append('file', operation.file);
  const result = await api('/api/projects/' + operation.projectId + '/web-ai/import', {method: 'POST', body, raw: true});
  S.webImport = {...operation, revision: result.revision, preview: result};
  modal('网页AI回填校验预览', `<div class="callout">全部${result.count}行校验通过，准备新增${result.would_write}条标注；${result.already_applied}条已按同一任务保存，无需重复写入。尚未保存本次新增。</div>
    ${previewTable(['材料ID', '内容', '结论', '局部分', '人工核对'], result.preview.map(r => [r.id, r.description, statuses[r.status], r.score, '否']))}
    <p>非有效材料进入人工复核。网页AI不能填写内部人工意见、向学生说明、日期豁免、负分或替代结论。</p>
    <div class="form-actions">${btn('web-ai-import', '重新选择文件', 'secondary')}${btn('web-ai-commit', '确认保存全部校验结果')}</div>`, true);
};
A['web-ai-commit'] = async () => {
  const operation = S.webImport;
  if (!operation) throw new Error('请先校验回填文件。');
  const body = new FormData(); body.append('credential', operation.credential); body.append('file', operation.file);
  body.append('commit', 'true'); body.append('expected_revision', String(operation.revision));
  const result = await api('/api/projects/' + operation.projectId + '/web-ai/import', {method: 'POST', body, raw: true});
  S.webImport = null; A.close(); toast(`已保存${result.written}条外部标注，人工核对状态为“否”。`);
  await loadProject(operation.projectId); reviewQueue(true); await reviewView();
};

// Calculated records open their source; no cell in this view can import a total.
async function achievementDialog(studentId = '', categoryId = '') {
  const params = new URLSearchParams(); if (studentId) params.set('student_id', studentId); if (categoryId) params.set('category_id', categoryId);
  const response = await api('/api/projects/' + pid() + '/achievement-records?' + params);
  S.achievementRecords = response.records;
  modal('成果明细与来源', `<p>共${response.records.length}项；工程修订r${response.revision}。点击“打开来源”定位原件、固定名单版本或课程计算。此表只读。</p>
    <div class="row wrap">${search('achievement-table', '搜索学生、完整分类、奖项、日期或状态')}${S.me.role === 'teacher' ? projectTable('achievements', '导出工程全部成果') : ''}</div>
    <div class="table-wrap"><table><thead><tr><th>学生／学号</th><th>来源与日期</th><th>完整分类／具体成果</th><th>状态</th><th>局部分</th><th>计算处理</th><th>操作</th></tr></thead>
    <tbody id="achievement-table">${response.records.map(record => `<tr data-record-id="${esc(record.id)}" data-search="${esc([record.name, record.number, record.category_path, record.description, record.material_date, statuses[record.status]].join(' '))}">
      <td>${esc(record.name)}<div class="small mono">${esc(record.number)}</div></td><td>${esc(record.source)}<div class="small">${esc(record.material_date || '日期待确认')}</div></td>
      <td>${esc(record.category_path)}<p>${esc(record.description)}</p><p class="small muted">${esc(record.scenario)}</p></td>
      <td>${statusTag(record.status)}</td><td>${esc(record.score)}</td><td>${esc(record.calculation_status)}<p class="small muted">${esc(record.calculation_note)}</p></td>
      <td>${btn('achievement-source', '打开来源', 'secondary small-btn', {id: record.id})}${btn('achievement-data', '结构化记录', 'ghost small-btn', {id: record.id})}</td></tr>`).join('')}</tbody></table></div>`, true);
}
A['all-achievements'] = () => achievementDialog();
A['open-achievements'] = button => achievementDialog(button.dataset.sid || '', button.dataset.category || '');
A['achievement-data'] = button => {
  const record = S.achievementRecords?.find(r => r.id === button.dataset.id);
  if (!record) throw new Error('当前成果选择已失效，请重新打开明细。');
  modal('成果结构化记录', `<p>以下为已保存数据和系统计算说明，不能在此修改。</p><pre>${esc(JSON.stringify(record, null, 2))}</pre>`, true);
};
A['achievement-source'] = async button => {
  const record = S.achievementRecords?.find(r => r.id === button.dataset.id);
  if (!record) throw new Error('当前成果选择已失效，请重新打开明细。');
  if (record.source === '个人图片') return A['jump-evidence']({dataset: {id: record.id}});
  if (record.list_id && S.me.role === 'teacher') {
    const target = '#/public-lists/' + record.list_id + '/' + record.list_revision;
    S.focusPublicRow = record.id.split(':').at(-1); A.close(); location.hash = target; return;
  }
  if (record.source === '课程计算' && S.project.can_manage) {
    A.close(); S.academic = null; academicInit(); S.academicStudent = record.student_id;
    location.hash = purl('academic'); return;
  }
  A['achievement-data'](button);
};
A['result-columns'] = async () => { S.resultColumns = S.resultColumns === 'all' ? 'top' : 'all'; await calculationView(); };
