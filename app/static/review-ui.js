/* Local evidence review: stable queue, explicit transitions and one write path.
 * Saving a verdict does not rebuild the queue from its changed status. */
'use strict';

function currentAnnotation(evidence) { return evidence.annotations?.at(-1); }

function reviewEvidence() {
  const roster = new Map(S.project.body.roster.map(s => [s.id, s]));
  const query = (S.reviewSearch || '').trim().toLocaleLowerCase();
  const category = S.reviewCategory || '';
  return S.project.body.evidence.filter(evidence => {
    if (evidence.student_id === S.me.student_id) return false;
    const annotation = currentAnnotation(evidence), data = annotation?.data || {};
    const student = roster.get(evidence.student_id) || {};
    if (query && ![student.name, student.number, student.class_name, data.description,
      data.rule_item, evidence.id].join(' ').toLocaleLowerCase().includes(query)) return false;
    if (category && data.category_id !== category) return false;
    if (S.reviewFilter === 'all') return true;
    if (S.reviewFilter === 'unconfirmed') return !annotation || (data.status !== 'valid' && !annotation.reviewed);
    if (S.reviewFilter === 'sample') return data.status === 'valid' && parseInt(evidence.resource_sha.slice(0, 4), 16) % 10 === 0;
    return S.reviewStatuses.includes(data.status || 'unlabeled');
  });
}

function reviewQueue(reset = false) {
  const signature = JSON.stringify([pid(), S.reviewFilter, S.reviewStatuses, S.reviewSearch, S.reviewCategory]);
  if (reset || S.reviewSession?.signature !== signature) {
    const ids = reviewEvidence().map(e => e.id);
    S.reviewSession = {signature, ids, page: 0, selected: [], completed: new Set()};
    if (!ids.includes(S.reviewId)) S.reviewId = ids[0] || null;
  }
  return S.reviewSession;
}

function allowReviewNavigation() {
  if (S.reviewSaving) { toast('正在保存，请等待服务器确认。'); return false; }
  if (S.formDirty && !window.confirm('当前材料的修改尚未保存。放弃这些修改并切换材料？')) return false;
  clearDirty();
  S.reviewAttempt = null;
  return true;
}

async function applyReviewFilters(mode = S.reviewFilter) {
  if (!allowReviewNavigation()) {
    const selector = document.querySelector('[data-review-filter]');
    if (selector) selector.value = S.reviewFilter;
    for (const input of document.querySelectorAll('[data-review-status]'))
      input.checked = S.reviewStatuses.includes(input.dataset.reviewStatus);
    return;
  }
  S.reviewFilter = mode;
  S.reviewStatuses = [...document.querySelectorAll('[data-review-status]:checked')].map(e => e.dataset.reviewStatus);
  S.reviewSearch = get('review-search')?.value || '';
  S.reviewCategory = get('review-category')?.value || '';
  reviewQueue(true);
  await reviewView();
}

const reviewVerdicts = [
  ['valid', '有效', '1'], ['manual', '需人工判断', '2'], ['untrusted', '不可信', '3'],
  ['invalid_image', '图片无效', '4'], ['out_of_period', '不在加分时间', '5'],
  ['out_of_scope', '不属于加分情形', '6'], ['system_error', '系统异常', '7']
];
const REVIEW_PAGE_SIZE = 60;

function reviewControls(evidence, index, queue) {
  return `<div class="review-toolbar" role="toolbar" aria-label="连续阅卷操作">
    <div class="row between wrap"><div class="row wrap">
      ${btn('review-prev', '上一个', 'secondary small-btn', {}, 'back')}
      <span class="small">第 ${index + 1} / ${queue.ids.length} 项${queue.completed.has(evidence.id) ? ' · 本轮已处理' : ''}</span>
      ${btn('review-next', '下一个', 'secondary small-btn', {}, 'arrow')}
      ${btn('review-note', '填写备注', 'secondary small-btn', {}, 'edit')}
    </div>${btn('review-shortcuts', '快捷键说明', 'ghost small-btn')}</div>
    <div class="row wrap verdict-buttons">${reviewVerdicts.map(([value, label, key]) =>
      btn('review-verdict', label + '并下一项', value === 'valid' ? 'small-btn' : 'secondary small-btn', {status: value, shortcut: key})).join('')}</div>
    <p class="small muted">按钮同时保存当前表单及备注，服务器保存成功后才切换。非有效结论记0分，旧分值留在历史中。无效／待判断结论必须填写人工原因。</p>
  </div>`;
}

async function reviewView() {
  const queue = reviewQueue();
  const byId = new Map(S.project.body.evidence.map(e => [e.id, e]));
  queue.ids = queue.ids.filter(id => byId.has(id));
  if (!queue.ids.includes(S.reviewId)) S.reviewId = queue.ids[0] || null;
  const evidence = byId.get(S.reviewId), annotation = evidence ? currentAnnotation(evidence) : null;
  const data = annotation?.data || {}, student = evidence ? studentById(evidence.student_id) : null;
  const index = queue.ids.indexOf(S.reviewId);
  const pages = Math.max(1, Math.ceil(queue.ids.length / REVIEW_PAGE_SIZE));
  queue.page = Math.min(queue.page, pages - 1);
  const pageIds = queue.ids.slice(queue.page * REVIEW_PAGE_SIZE, (queue.page + 1) * REVIEW_PAGE_SIZE);
  const related = evidence ? S.project.body.evidence.filter(e => e.student_id === evidence.student_id && e.id !== evidence.id) : [];
  const filters = [['attention', '按下方材料状态筛选'], ['unconfirmed', '全部待人工确认的无效结论'],
    ['all', '全部材料'], ['sample', '有效材料抽样约10%']];
  const tools = S.me.role === 'teacher' ? btn('review-export', '导出本轮队列表格', 'secondary small-btn', {}, 'download') +
    btn('web-ai-export', '导出网页AI任务', 'secondary small-btn', {}, 'download') +
    btn('web-ai-import', '网页AI表格回填', 'secondary small-btn', {}, 'upload') : '';
  paintProject('review', `<div class="section-heading"><div><h2>局部材料标注</h2>
      <p>只认定本项材料，不在此处封顶、求和。队列在应用筛选时固定，处理后不会跳项；点击“重新生成队列”接收新提交。</p></div>
      <div class="row wrap">${tools}</div></div>
    <div class="review-filter-panel"><div class="row wrap">
      ${select('review-filter', '显示范围', filters, S.reviewFilter, 'data-review-filter')}
      <label>学生／材料搜索<input id="review-search" type="search" value="${esc(S.reviewSearch || '')}" placeholder="姓名、学号、班级、描述或材料ID"></label>
      <label>具体分类<select id="review-category"><option value="">全部分类</option>${leafOptions().map(([id, label]) =>
        `<option value="${esc(id)}" ${S.reviewCategory === id ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select></label>
      ${btn('review-apply', '应用筛选', 'secondary small-btn')}${btn('review-rebuild', '重新生成队列', 'secondary small-btn')}
    </div><div class="row wrap"><span>材料状态：</span>${Object.entries(statuses).map(([value, label]) =>
      `<label class="checkbox"><input type="checkbox" data-review-status="${value}" ${S.reviewStatuses.includes(value) ? 'checked' : ''}>${label}</label>`).join('')}</div></div>
    <div class="review-layout"><aside class="card review-queue"><div class="card-head"><h3>材料队列 ${queue.ids.length}</h3></div>
      <div class="queue-list" id="review-queue">${pageIds.map(id => {
        const e = byId.get(id), a = currentAnnotation(e), s = studentById(e.student_id);
        return `<div class="queue-entry ${id === S.reviewId ? 'active' : ''}" data-record-id="${id}" data-search="${esc(s.name + ' ' + s.number + ' ' + (a?.data.description || ''))}">
          <button type="button" class="queue-item ${id === S.reviewId ? 'active' : ''}" data-action="select-evidence" data-id="${id}">
          <img loading="lazy" src="${evidenceImage(id)}" alt="材料缩略图"><div><strong>${esc(s.name)}</strong>
          <span>${esc(a?.data.description || '尚未标注')}</span>${statusTag(a?.data.status || 'unlabeled')}${queue.completed.has(id) ? tag('本轮已处理', 'gray') : ''}</div></button></div>`;
      }).join('') || '<p class="pad muted">此筛选条件下没有材料。</p>'}</div>
      <div class="row between wrap pad"><span class="small">队列第 ${queue.page + 1} / ${pages} 页</span>
        ${btn('review-page', '上一页', 'secondary small-btn', {delta: -1})}${btn('review-page', '下一页', 'secondary small-btn', {delta: 1})}</div>
    </aside>${evidence ? `<div class="review-main">${reviewControls(evidence, index, queue)}
      <section class="card"><div class="card-head"><div><h3>${esc(student.name)} <span class="muted small">${esc(student.number)}</span></h3>
        <p>${fmt(evidence.submitted_at)} · 原件 ${esc(evidence.resource_sha.slice(0, 12))}</p></div>
        <div class="row">${btn('image-rotate', '旋转', 'ghost small-btn', {}, 'refresh')}${link('查看原图', evidenceImage(evidence.id, true), 'btn secondary small-btn', 'eye')}</div></div>
        <div class="review-image"><img id="review-image" src="${evidenceImage(evidence.id)}" alt="待标注的原始材料，可放大查看"></div></section>
      <section class="card pad" style="margin-top:16px"><form data-form="annotation" data-id="${evidence.id}">
        <div class="row between"><h3>认定与依据</h3><div>${statusTag(data.status || 'unlabeled')}${tag(annotation ? '标注版本 ' + annotation.version : '尚未标注', annotation?.reviewed ? '' : 'gray')}</div></div>
        <div class="field-grid" style="margin-top:16px">${field('material_date', '材料有效日期（年 / 年月 / 日期）', data.material_date || '', 'text', 'placeholder="2026-08-31"')}
          ${stampFields(data)}<div class="span2">${area('description', '图片中的关键内容', data.description || '')}</div></div>
        <div style="margin:16px 0"><h4>逐级选择具体分类</h4>${categoryPicker(data.category_id)}</div>
        <div class="field-grid">${area('rule_item', '具体加分项（细则原话）', data.rule_item || '')}${area('scenario', '具体加分情形', data.scenario || '')}
          ${field('rule_source', '规则出处 / 页码 / 条款', data.rule_source || '')}${field('score', '本项最终认定分值', data.score || '0', 'text', 'required inputmode="decimal"')}
          ${field('outcome_key', '成果键（用于发现重复，建议填写）', data.outcome_key || '')}
          ${area('human_note', '人工核对备注 / 无效原因（内部）', data.human_note || '')}
          <div class="span2">${area('student_note', '向学生说明（公示后本人可见，可留空）', data.student_note || '', 'maxlength="4000"')}</div></div>
        <details style="margin-top:16px"><summary>局部系数、共同作者与材料关联</summary><div class="field-grid">
          ${field('base_score', '基础分（填写后系统校验计算）', data.base_score || '')}${field('factors', '局部系数（逗号分隔）', (data.factors || []).join(','))}
          ${field('denominator', '共同作者 / 分母', data.denominator || '1')}
          ${select('relation', '与其他材料的关系', [['', '独立成果'], ['support_for', '同成果补充佐证（不独立计分）'], ['duplicate_of', '重复材料（不独立计分）'],
            ...(S.me.role === 'teacher' ? [['replaces', '替代旧材料（仅教师确认）']] : [])], data.support_for ? 'support_for' : data.duplicate_of ? 'duplicate_of' : data.replaces ? 'replaces' : '')}
          ${select('related_id', '关联同一学生的哪份材料', [['', '不关联'], ...related.map(e => [e.id, (currentAnnotation(e)?.data.description || '未标注材料') + ' · ' + e.id.slice(0, 8)])], data.support_for || data.duplicate_of || data.replaces || '')}
          ${S.me.role === 'teacher' ? area('date_override_reason', '日期特殊认定依据（内部；留空严格校验参评期）', data.date_override_reason || '') : ''}</div>
          <p class="small muted">佐证与重复材料的分值必须为0。替代保留旧件，只在新件经教师认定有效后改变计算。</p></details>
        ${data.ai_note ? `<details><summary>外部标注备注（仅审核端可见）</summary><p class="pre-wrap">${esc(data.ai_note)}</p></details>` : ''}
        <div class="form-actions">${btn('annotation-history', '查看修改历史', 'secondary', {id: evidence.id}, 'clock')}
          <button type="submit" class="btn secondary">保存当前结论并停留</button></div>
      </form></section></div>` : empty('没有待阅材料', '调整筛选，或在外部标注完成后重新生成队列。')}</div>`);
  const locked = ['setup', 'closed', 'archived'].includes(S.project.body.state);
  if (locked) {
    document.querySelectorAll('form[data-form="annotation"] input, form[data-form="annotation"] textarea, form[data-form="annotation"] select, form[data-form="annotation"] [type="submit"], [data-action="review-verdict"], [data-action="review-note"]').forEach(control => control.disabled = true);
    const note = document.createElement('p'); note.className = 'small muted'; note.textContent = '当前阶段只读，可查看材料、历史及切换队列，不能保存标注。';
    document.querySelector('.review-toolbar')?.append(note);
  }
  const prev = document.querySelector('[data-action="review-prev"]'), next = document.querySelector('[data-action="review-next"]');
  if (prev) prev.disabled = index <= 0;
  if (next) next.disabled = index < 0 || index >= queue.ids.length - 1;
  document.querySelectorAll('[data-action="review-page"]').forEach(b => b.disabled =
    Number(b.dataset.delta) < 0 ? queue.page === 0 : queue.page >= pages - 1);
}

function annotationFromForm(form, state) {
  const value = formData(form), evidence = S.project.body.evidence.find(e => e.id === form.dataset.id);
  const latest = currentAnnotation(evidence), status = state || latest?.data.status || 'unlabeled';
  if (status === 'unlabeled') throw new Error('请使用上方结论快捷按钮选择明确状态；未处理材料不需要保存为空标注。');
  if (status !== 'valid' && !value.human_note.trim()) {
    form.elements.human_note.focus();
    throw new Error('请填写人工核对备注／无效原因，再点击相应结论按钮。');
  }
  const data = {...stampData(value), status, material_date: value.material_date || null,
    description: value.description, category_id: value.category_id || null, rule_item: value.rule_item,
    scenario: value.scenario, rule_source: value.rule_source, score: status === 'valid' ? value.score : '0',
    base_score: status === 'valid' ? value.base_score || null : null,
    factors: status === 'valid' && value.factors ? value.factors.split(/[,，]/).map(s => s.trim()).filter(Boolean) : [],
    denominator: status === 'valid' ? value.denominator || '1' : '1', outcome_key: value.outcome_key || null,
    human_note: value.human_note || null, student_note: value.student_note || null, ai_note: latest?.data.ai_note || null,
    support_for: null, duplicate_of: null, replaces: null, date_override_reason: value.date_override_reason || null};
  if (value.relation) {
    if (!value.related_id) throw new Error('请选择关联材料。');
    data[value.relation] = value.related_id;
  }
  return {data, evidence, latest};
}

async function saveReview(state, advance) {
  if (S.reviewSaving) return;
  if (['setup', 'closed', 'archived'].includes(S.project.body.state)) throw new Error('当前阶段只读，不能保存标注。');
  const form = document.querySelector('form[data-form="annotation"]');
  if (!form || !form.reportValidity()) return;
  const {data, evidence, latest} = annotationFromForm(form, state);
  const projectId = pid(), signature = JSON.stringify([projectId, evidence.id, data]);
  if (S.reviewAttempt?.signature !== signature) S.reviewAttempt = {signature, key: crypto.randomUUID(),
    revision: S.project.revision, version: latest?.version || 0};
  const attempt = S.reviewAttempt;
  S.reviewSaving = true;
  const controls = [...document.querySelectorAll('.review-toolbar button,form[data-form="annotation"] button')];
  const priorDisabled = controls.map(b => b.disabled);
  controls.forEach(b => b.disabled = true);
  try {
    const response = await api('/api/projects/' + projectId + '/evidence/' + evidence.id + '/annotation', {
      method: 'PUT', body: {revision: attempt.revision, expected_annotation_version: attempt.version,
        annotation: data, idempotency_key: attempt.key}});
    if (S.project?.id !== projectId) { toast('材料标注已保存到原工程。'); return; }
    S.project.revision = response.revision;
    Object.assign(evidence, response.evidence);
    clearDirty();
    S.reviewAttempt = null;
    const queue = reviewQueue(), index = queue.ids.indexOf(evidence.id);
    queue.completed.add(evidence.id);
    if (advance && index >= 0 && index + 1 < queue.ids.length) {
      S.reviewId = queue.ids[index + 1];
      queue.page = Math.floor((index + 1) / REVIEW_PAGE_SIZE);
      toast('已保存，已打开下一项。');
    } else toast(advance ? '已保存。这是队列最后一项，已停留在此；不会回到首项。' : '已保存当前结论。');
    if (location.hash.split('/').at(-1) === 'review') await reviewView();
  } finally {
    S.reviewSaving = false;
    controls.forEach((b, i) => { if (b.isConnected) b.disabled = priorDisabled[i]; });
  }
}

async function selectReview(id) {
  if (!allowReviewNavigation()) return;
  const queue = reviewQueue(), index = queue.ids.indexOf(id);
  if (index < 0) return;
  S.reviewId = id;
  queue.page = Math.floor(index / REVIEW_PAGE_SIZE);
  await reviewView();
}
A['select-evidence'] = button => selectReview(button.dataset.id);
A['review-prev'] = () => { const q = reviewQueue(), i = q.ids.indexOf(S.reviewId); return i > 0 ? selectReview(q.ids[i - 1]) : undefined; };
A['review-next'] = () => { const q = reviewQueue(), i = q.ids.indexOf(S.reviewId); return i >= 0 && i + 1 < q.ids.length ? selectReview(q.ids[i + 1]) : undefined; };
A['review-page'] = async button => {
  if (!allowReviewNavigation()) return;
  const queue = reviewQueue();
  queue.page = Math.max(0, Math.min(Math.ceil(queue.ids.length / REVIEW_PAGE_SIZE) - 1, queue.page + Number(button.dataset.delta)));
  await reviewView();
};
A['review-verdict'] = button => saveReview(button.dataset.status, true);
A['review-note'] = () => document.querySelector('form[data-form="annotation"] [name="human_note"]')?.focus();
A['review-apply'] = () => applyReviewFilters();
A['review-rebuild'] = async () => {
  if (!allowReviewNavigation()) return;
  await loadProject(pid());
  reviewQueue(true);
  await reviewView();
};
A['review-shortcuts'] = () => modal('连续阅卷快捷键', `<p>快捷键仅在材料标注页生效。弹窗、输入法组合输入及长按重复期间不触发评分。</p>
  <table><thead><tr><th>按键</th><th>操作</th></tr></thead><tbody><tr><td>Alt + ← / →</td><td>上一项／下一项（不保存，未保存时先确认）</td></tr>
  <tr><td>Alt + M</td><td>定位到人工备注</td></tr>${reviewVerdicts.map(([, label, key]) => `<tr><td>Alt + ${key}</td><td>保存表单、标为${label}，成功后下一项</td></tr>`).join('')}
  <tr><td>Ctrl + Enter / ⌘ + Enter</td><td>保存现有明确结论并停留</td></tr></tbody></table><p>快捷键不替代按钮。若系统或浏览器占用组合键，请直接使用悬浮操作区按钮。</p>`);
F.annotation = () => saveReview(null, false);
A['jump-evidence'] = button => {
  if (!allowReviewNavigation()) return;
  S.reviewFilter = 'all'; S.reviewSearch = ''; S.reviewCategory = ''; S.reviewId = button.dataset.id;
  S.reviewSession = null; A.close();
  if (location.hash === purl('review')) reviewView(); else location.hash = purl('review');
};

document.addEventListener('keydown', event => {
  if (event.defaultPrevented || event.isComposing || event.repeat || document.querySelector('dialog[open]') ||
      !document.querySelector('form[data-form="annotation"]')) return;
  let action;
  if (event.altKey && !event.ctrlKey && !event.metaKey) {
    if (event.key === 'ArrowLeft') action = A['review-prev'];
    else if (event.key === 'ArrowRight') action = A['review-next'];
    else if (event.code === 'KeyM') action = A['review-note'];
    else { const verdict = reviewVerdicts.find(v => event.code === 'Digit' + v[2]); if (verdict) action = () => saveReview(verdict[0], true); }
  }
  if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key === 'Enter') action = () => saveReview(null, false);
  if (action) { event.preventDefault(); Promise.resolve().then(action).catch(showUIError); }
});
function categoryPicker(value){const nodes=latestRules().nodes,root=nodes.find(n=>!n.parent);let path=[],cur=nodes.find(n=>n.id===value);while(cur){path.unshift(cur.id);cur=nodes.find(n=>n.id===cur.parent);}let html='',parent=root?.id,depth=1;while(parent){const children=nodes.filter(n=>n.parent===parent);if(!children.length)break;const v=path[depth]||'';html+=select('category_level_'+depth,'第 '+depth+' 级分类',[['','请选择'],...children.map(n=>[n.id,n.name])],v,'data-category-level="'+depth+'"');parent=v;depth++;}const leaf=value&&!nodes.some(n=>n.parent===value);return `<div id="category-picker" class="field-grid">${html}<input type="hidden" name="category_id" value="${leaf?esc(value):''}"></div>`;}
A['image-rotate']=()=>{const im=get('review-image'),r=(Number(im.dataset.rotation||0)+90)%360;im.dataset.rotation=r;im.style.transform='rotate('+r+'deg)';};
A['annotation-history']=b=>{const e=S.project.body.evidence.find(e=>e.id===b.dataset.id);modal('标注修改历史',`<div class="timeline">${(e.annotations||[]).slice().reverse().map(a=>`<div class="timeline-item"><strong>版本 ${a.version} · ${a.reviewed?'人工核对':'外部服务标注'}</strong><p>${fmt(a.created_at)} · ${esc(a.actor_id)}</p><p>${statusTag(a.data.status)} ${esc(a.data.description)} · ${esc(a.data.score)} 分</p><p class="small muted">${esc(a.data.human_note||a.data.ai_note||'')}</p><details><summary>完整结构化记录</summary><pre>${esc(JSON.stringify(a,null,2))}</pre></details></div>`).join('')||'<p class="muted">尚无标注记录。</p>'}</div>`,true);};

A['review-export']=async()=>{
  const ids=reviewQueue().ids.slice();requireSelection(ids,20000);
  const result=await api('/api/projects/'+pid()+'/export/selected-achievements',{method:'POST',body:{ids},binary:true});
  saveBlob(result.blob,'本轮审核队列成果.xlsx');
};
