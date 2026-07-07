// Argos Venture — automatic CSV insight briefing workspace.
//
// An analysis tab is created from the Venture New Tab wizard with a CSV and an
// optional objective. The tab opens immediately, then each dashboard section
// shows a loading state while Argos profiles and visualizes the dataset.

const API_BASE = window.API_BASE || '';
let initialized = false;
let activeSessionId = null;
let activePayload = null;
let panel = null;
let wizardObserver = null;
const knownAnalysisSessions = new Set();

const chartIcon = `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="m7 16 4-5 3 3 5-7"/><circle cx="7" cy="16" r="1"/><circle cx="11" cy="11" r="1"/><circle cx="14" cy="14" r="1"/><circle cx="19" cy="7" r="1"/></svg>`;

function esc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[char]));
}

function number(value, digits = 2) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(numeric)
    : '—';
}

function percentage(value, digits = 1) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${number(numeric * 100, digits)}%` : '—';
}

function bytes(value) {
  const size = Number(value || 0);
  if (!size) return 'No dataset yet';
  const units = ['B', 'KB', 'MB', 'GB'];
  const index = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1);
  return `${(size / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, { credentials: 'same-origin', ...options });
  const data = (response.headers.get('content-type') || '').includes('application/json')
    ? await response.json()
    : null;
  if (!response.ok) throw new Error(data?.detail || `Request failed (${response.status})`);
  return data;
}

function installStyles() {
  if (document.getElementById('venture-analysis-style')) return;
  const style = document.createElement('style');
  style.id = 'venture-analysis-style';
  style.textContent = `
    .venture-analysis-workspace { position:fixed; inset:var(--workspace-shell-h, 0px) 0 0 max(var(--icon-rail-w, 0px), var(--sidebar-w, 0px)); z-index:19; background:var(--bg); color:var(--fg); display:flex; flex-direction:column; overflow:hidden; }
    .venture-analysis-workspace[hidden] { display:none!important; }
    .venture-analysis-topbar { display:flex; align-items:center; gap:10px; padding:12px 16px; border-bottom:1px solid var(--border); background:var(--panel); flex:0 0 auto; }
    .venture-analysis-topbar h2 { margin:0; font-size:16px; display:flex; align-items:center; gap:8px; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .venture-analysis-sub { margin-left:auto; font-size:12px; opacity:.66; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
    .venture-analysis-close, .venture-analysis-button { border:1px solid var(--border); color:var(--fg); background:var(--bg); border-radius:7px; padding:7px 10px; font:inherit; font-size:12px; cursor:pointer; }
    .venture-analysis-button:hover, .venture-analysis-close:hover { border-color:var(--accent, var(--red)); }
    .venture-analysis-button:disabled { opacity:.55; cursor:progress; }
    .venture-analysis-button.primary { background:var(--fg); color:var(--bg); }
    .venture-analysis-layout { display:grid; grid-template-columns:minmax(0, 1.65fr) minmax(286px, .75fr); gap:14px; flex:1; min-height:0; padding:14px; overflow:hidden; }
    .venture-analysis-main, .venture-analysis-side { display:flex; flex-direction:column; gap:12px; min-height:0; overflow:auto; padding-right:2px; }
    .venture-analysis-card { border:1px solid var(--border); border-radius:10px; background:color-mix(in srgb, var(--panel) 88%, transparent); padding:12px; }
    .venture-analysis-card h3 { margin:0 0 8px; font-size:12px; letter-spacing:.05em; text-transform:uppercase; opacity:.72; }
    .venture-analysis-upload { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .venture-analysis-upload input { max-width:280px; min-width:0; }
    .venture-analysis-note { font-size:12px; opacity:.68; line-height:1.45; }
    .venture-analysis-status { min-height:18px; margin-top:7px; font-size:12px; opacity:.75; }
    .venture-analysis-status.error { color:#e55; opacity:1; }
    .venture-analysis-section-label { margin:0 0 8px; font-size:12px; letter-spacing:.05em; text-transform:uppercase; opacity:.72; }
    .venture-analysis-metrics { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:8px; }
    .venture-analysis-metric { border:1px solid color-mix(in srgb, var(--border) 66%, transparent); border-radius:8px; padding:9px; min-height:58px; }
    .venture-analysis-metric strong { display:block; font-size:18px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .venture-analysis-metric span { font-size:11px; opacity:.65; }
    .venture-analysis-insight-grid, .venture-analysis-visual-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:9px; }
    .venture-analysis-insight { border:1px solid color-mix(in srgb, var(--border) 62%, transparent); border-radius:8px; padding:10px; min-height:94px; }
    .venture-analysis-insight.warning { border-style:dashed; }
    .venture-analysis-insight.goal { background:color-mix(in srgb, var(--fg) 5%, var(--panel)); }
    .venture-analysis-insight h4 { margin:0 0 5px; font-size:12px; }
    .venture-analysis-insight strong { display:block; font-size:16px; margin-bottom:5px; }
    .venture-analysis-insight p { margin:0; font-size:12px; line-height:1.38; opacity:.78; }
    .venture-analysis-insight small { display:block; margin-top:7px; font-size:11px; opacity:.62; }
    .venture-analysis-summary { margin:10px 0 0; padding-left:18px; display:flex; flex-direction:column; gap:6px; font-size:12px; line-height:1.45; }
    .venture-analysis-visual { min-height:220px; display:flex; flex-direction:column; }
    .venture-analysis-visual h4 { margin:0 0 7px; font-size:13px; }
    .venture-analysis-visual-image { min-height:185px; display:flex; align-items:center; justify-content:center; overflow:hidden; }
    .venture-analysis-visual-image img { width:100%; max-height:290px; object-fit:contain; border-radius:6px; }
    .venture-analysis-empty { text-align:center; opacity:.68; font-size:13px; padding:26px; line-height:1.45; }
    .venture-analysis-table-wrap { overflow:auto; max-height:280px; border:1px solid color-mix(in srgb, var(--border) 66%, transparent); border-radius:7px; }
    .venture-analysis-table { width:100%; border-collapse:collapse; font-size:12px; }
    .venture-analysis-table th, .venture-analysis-table td { padding:7px 8px; text-align:left; border-bottom:1px solid color-mix(in srgb, var(--border) 55%, transparent); white-space:nowrap; max-width:220px; overflow:hidden; text-overflow:ellipsis; }
    .venture-analysis-table th { position:sticky; top:0; background:var(--panel); z-index:1; }
    .venture-analysis-columns { display:flex; flex-direction:column; gap:5px; }
    .venture-analysis-column { display:flex; justify-content:space-between; gap:8px; padding:5px 0; border-bottom:1px solid color-mix(in srgb, var(--border) 50%, transparent); font-size:12px; }
    .venture-analysis-column > span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .venture-analysis-column small { opacity:.62; white-space:nowrap; }
    .venture-analysis-quality-list, .venture-analysis-categories { margin:0; padding-left:18px; font-size:12px; display:flex; flex-direction:column; gap:5px; }
    .venture-analysis-next { font-size:13px; line-height:1.45; margin:0; }
    .venture-analysis-goal { margin:0 0 10px; font-size:12px; line-height:1.4; opacity:.8; }
    .venture-analysis-skeleton { position:relative; overflow:hidden; border-radius:7px; background:color-mix(in srgb, var(--border) 35%, transparent); min-height:15px; }
    .venture-analysis-skeleton::after { content:''; position:absolute; inset:0; transform:translateX(-100%); background:linear-gradient(90deg, transparent, color-mix(in srgb, var(--fg) 12%, transparent), transparent); animation:venture-analysis-shimmer 1.2s infinite; }
    .venture-analysis-skeleton.metric { min-height:58px; }
    .venture-analysis-skeleton.card { min-height:104px; }
    .venture-analysis-skeleton.visual { min-height:220px; }
    .venture-choice.venture-analysis-choice strong { display:flex; align-items:center; gap:6px; }
    .venture-analysis-fields { margin-top:10px; }
    .venture-analysis-file-field { display:flex; flex-direction:column; gap:6px; }
    .venture-analysis-file-field textarea { min-height:72px; resize:vertical; background:var(--bg); color:var(--fg); border:1px solid var(--border); border-radius:7px; padding:8px; font:inherit; }
    @keyframes venture-analysis-shimmer { 100% { transform:translateX(100%); } }
    @media (max-width:960px) { .venture-analysis-layout { grid-template-columns:1fr; overflow:auto; } .venture-analysis-main,.venture-analysis-side { overflow:visible; } }
    @media (max-width:620px) { .venture-analysis-workspace { left:0; top:0; } .venture-analysis-topbar { padding:10px; } .venture-analysis-sub { display:none; } .venture-analysis-metrics { grid-template-columns:repeat(2, minmax(0, 1fr)); } .venture-analysis-insight-grid, .venture-analysis-visual-grid { grid-template-columns:1fr; } }
  `;
  document.head.appendChild(style);
}

function getPanel() {
  if (panel) return panel;
  panel = document.createElement('section');
  panel.id = 'venture-analysis-workspace';
  panel.className = 'venture-analysis-workspace';
  panel.hidden = true;
  panel.innerHTML = `
    <header class="venture-analysis-topbar">
      <h2>${chartIcon}<span id="venture-analysis-title">Data Analysis</span></h2>
      <span class="venture-analysis-sub" id="venture-analysis-file-meta"></span>
      <button type="button" class="venture-analysis-close" id="venture-analysis-close">Close dashboard</button>
    </header>
    <div class="venture-analysis-layout" id="venture-analysis-layout"></div>`;
  document.body.appendChild(panel);
  panel.querySelector('#venture-analysis-close').addEventListener('click', closeForNavigation);
  return panel;
}

function setStatus(text, error = false) {
  const node = panel?.querySelector('#venture-analysis-status');
  if (!node) return;
  node.textContent = text || '';
  node.classList.toggle('error', !!error);
}

function table(rows = []) {
  if (!rows.length) return '<div class="venture-analysis-empty">No rows available for preview.</div>';
  const columns = Object.keys(rows[0]).slice(0, 10);
  return `<div class="venture-analysis-table-wrap"><table class="venture-analysis-table"><thead><tr>${columns.map(column => `<th title="${esc(column)}">${esc(column)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${columns.map(column => `<td title="${esc(row[column])}">${esc(row[column])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function skeletons(count, kind = 'card') {
  return Array.from({ length: count }, () => `<div class="venture-analysis-skeleton ${kind}"></div>`).join('');
}

function metric(value, label) {
  return `<div class="venture-analysis-metric"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`;
}

function insightCard(card) {
  return `<article class="venture-analysis-insight ${esc(card.tone || 'neutral')}"><h4>${esc(card.title)}</h4><strong>${esc(card.metric)}</strong><p>${esc(card.detail)}</p><small>${esc(card.action || '')}</small></article>`;
}

function visualCard(visual) {
  const image = visual?.image_png_base64 ? `data:image/png;base64,${visual.image_png_base64}` : '';
  return `<article class="venture-analysis-card venture-analysis-visual">
    <h4>${esc(visual?.title || 'Visual summary')}</h4>
    <div class="venture-analysis-visual-image">${image
      ? `<img alt="${esc(visual?.summary || visual?.title || 'Dataset visualization')}" src="${image}">`
      : `<div class="venture-analysis-empty">${esc(visual?.summary || 'The visual could not be rendered, but its source data remains available through the analysis API.')}</div>`}</div>
    ${visual?.summary ? `<div class="venture-analysis-note">${esc(visual.summary)}</div>` : ''}
  </article>`;
}

function render(payload, { loading = false, loadingFile = '' } = {}) {
  activePayload = payload || activePayload;
  activeSessionId = payload?.session?.id || activeSessionId;
  if (activeSessionId) knownAnalysisSessions.add(String(activeSessionId));
  const root = getPanel();
  const layout = root.querySelector('#venture-analysis-layout');
  const session = payload?.session || {};
  const overview = payload?.insights?.overview || {};
  const briefing = payload?.briefing || {};
  const columns = payload?.schema?.columns || [];
  const visuals = payload?.visuals || [];
  const rows = payload?.insights?.sample_rows || [];
  const complete = 1 - Number(overview.missing_rate || 0);
  const duplicate = overview.duplicate_checked ? number(overview.duplicate_rows || 0, 0) : 'Not sampled';
  const goal = session.analysis_goal || briefing.analysis_goal || '';

  root.querySelector('#venture-analysis-title').textContent = session.name || 'Data Analysis';
  root.querySelector('#venture-analysis-file-meta').textContent = loading
    ? `${loadingFile || session.dataset_filename || 'CSV'} · Profiling dataset…`
    : session.dataset_filename
      ? `${session.dataset_filename} · ${bytes(session.dataset_size_bytes)}`
      : 'CSV insight workspace';

  const health = loading ? skeletons(4, 'metric') : session.dataset_filename
    ? [
      metric(number(overview.rows, 0), 'Rows'),
      metric(number(overview.columns, 0), 'Columns'),
      metric(percentage(complete), 'Complete cells'),
      metric(duplicate, overview.duplicate_checked ? 'Duplicate rows' : 'Duplicate scan'),
    ].join('')
    : '<div class="venture-analysis-empty">The selected CSV will be profiled here.</div>';
  const generalCards = loading ? skeletons(4) : briefing.cards?.length
    ? briefing.cards.map(insightCard).join('')
    : '<div class="venture-analysis-note">Insight cards appear after profiling completes.</div>';
  const goalCards = loading ? skeletons(2) : briefing.goal_cards?.length
    ? briefing.goal_cards.map(insightCard).join('')
    : '';
  const qualityColumns = [...columns]
    .filter(column => Number(column.null_count || 0) > 0)
    .sort((left, right) => Number(right.null_rate || 0) - Number(left.null_rate || 0))
    .slice(0, 8);

  layout.innerHTML = `
    <main class="venture-analysis-main">
      <section class="venture-analysis-card">
        <h3>Dataset</h3>
        <div class="venture-analysis-upload">
          <input id="venture-analysis-file" type="file" accept=".csv,text/csv" aria-label="Choose replacement CSV dataset" ${loading ? 'disabled' : ''}>
          <button type="button" class="venture-analysis-button primary" id="venture-analysis-upload" ${loading ? 'disabled' : ''}>${session.dataset_filename ? 'Replace & analyze' : 'Analyze CSV'}</button>
          <span class="venture-analysis-note">CSV only · up to 200 MB · Polars profiling</span>
        </div>
        <div class="venture-analysis-status" id="venture-analysis-status">${loading ? `Profiling ${esc(loadingFile || 'the dataset')} and preparing each briefing section…` : ''}</div>
      </section>
      <section>
        <h3 class="venture-analysis-section-label">Dataset health</h3>
        <div class="venture-analysis-metrics">${health}</div>
      </section>
      <section class="venture-analysis-card">
        <h3>${esc(briefing.headline || 'What Argo found')}</h3>
        ${goal ? `<p class="venture-analysis-goal"><strong>Analysis objective:</strong> ${esc(goal)}</p>` : ''}
        <div class="venture-analysis-insight-grid">${generalCards}</div>
        ${!loading && briefing.summary?.length ? `<ul class="venture-analysis-summary">${briefing.summary.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : ''}
      </section>
      ${loading || goalCards ? `<section class="venture-analysis-card"><h3>Goal-specific findings</h3><div class="venture-analysis-insight-grid">${goalCards || skeletons(2)}</div></section>` : ''}
      <section>
        <h3 class="venture-analysis-section-label">Visual analysis</h3>
        <div class="venture-analysis-visual-grid">${loading ? skeletons(4, 'visual') : visuals.length ? visuals.map(visualCard).join('') : '<div class="venture-analysis-card venture-analysis-empty">Upload a CSV to generate the automatic chart set.</div>'}</div>
      </section>
      <section class="venture-analysis-card"><h3>Dataset preview</h3>${loading ? '<div class="venture-analysis-skeleton visual"></div>' : table(rows)}</section>
    </main>
    <aside class="venture-analysis-side">
      <section class="venture-analysis-card">
        <h3>Recommended next step</h3>
        <p class="venture-analysis-next">${loading ? 'Argos is calculating the next most useful exploration after profiling completes.' : esc(briefing.recommended_next_step || 'Upload a CSV to receive a recommended next step.')}</p>
      </section>
      <section class="venture-analysis-card">
        <h3>Data quality</h3>
        ${loading ? '<div class="venture-analysis-skeleton visual"></div>' : qualityColumns.length ? `<ul class="venture-analysis-quality-list">${qualityColumns.map(column => `<li><strong>${esc(column.name)}</strong> · ${percentage(column.null_rate)} missing</li>`).join('')}</ul>` : '<div class="venture-analysis-note">No missing values were detected in the profiled columns.</div>'}
      </section>
      <section class="venture-analysis-card">
        <h3>Columns</h3>
        <div class="venture-analysis-columns">${loading ? skeletons(8) : columns.slice(0, 20).map(column => `<div class="venture-analysis-column"><span title="${esc(column.name)}">${esc(column.name)}</span><small>${esc(column.dtype)} · ${number(column.null_count, 0)} null</small></div>`).join('') || '<div class="venture-analysis-note">Column details appear after profiling.</div>'}</div>
      </section>
      <section class="venture-analysis-card">
        <h3>Top categories</h3>
        ${loading ? skeletons(4) : Object.entries(payload?.insights?.top_categories || {}).slice(0, 3).map(([column, values]) => `<div style="margin-bottom:10px"><strong style="font-size:12px">${esc(column)}</strong><ol class="venture-analysis-categories">${(values || []).slice(0, 5).map(value => `<li>${esc(value.value)} <span style="opacity:.58">${number(value.count, 0)}</span></li>`).join('')}</ol></div>`).join('') || '<div class="venture-analysis-note">Category summaries appear after profiling.</div>'}
      </section>
    </aside>`;

  layout.querySelector('#venture-analysis-upload')?.addEventListener('click', uploadDataset);
}

async function openForSession(sessionId) {
  const root = getPanel();
  root.hidden = false;
  document.body.classList.add('venture-analysis-active');
  activeSessionId = String(sessionId);
  knownAnalysisSessions.add(activeSessionId);
  try {
    const data = await api(`/api/venture/analysis/sessions/${encodeURIComponent(activeSessionId)}`);
    if (String(window.sessionModule?.getCurrentSessionId?.() || '') !== activeSessionId) return;
    render(data);
  } catch (error) {
    root.querySelector('#venture-analysis-layout').innerHTML = `<div class="venture-analysis-empty">${esc(error.message)}</div>`;
  }
}

function closeForNavigation() {
  if (panel) panel.hidden = true;
  document.body.classList.remove('venture-analysis-active');
  activePayload = null;
}

async function uploadDatasetFile(file) {
  if (!file || !activeSessionId) return;
  const snapshot = activePayload || { session: { id: activeSessionId, name: 'Data Analysis' } };
  render(snapshot, { loading: true, loadingFile: file.name });
  const form = new FormData();
  form.append('file', file);
  try {
    const data = await api(`/api/venture/analysis/sessions/${encodeURIComponent(activeSessionId)}/dataset`, { method: 'POST', body: form });
    render(data);
    setStatus('Dataset profiled. Insight briefing and visual summaries are ready.');
  } catch (error) {
    render(snapshot);
    setStatus(error.message, true);
  }
}

async function uploadDataset() {
  const file = panel?.querySelector('#venture-analysis-file')?.files?.[0];
  if (!file) {
    setStatus('Choose a CSV file first.', true);
    return;
  }
  await uploadDatasetFile(file);
}

async function createFromWizard(modal, backdrop, status) {
  const title = String(modal.querySelector('[name="analysis_title"]')?.value || '').trim() || 'Data Analysis';
  const goal = String(modal.querySelector('[name="analysis_goal"]')?.value || '').trim();
  const file = modal.querySelector('[name="analysis_file"]')?.files?.[0];
  if (!file) throw new Error('Choose a CSV file before creating the workspace.');
  const data = await api('/api/venture/analysis/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: title, goal }),
  });
  const sessionId = data?.session?.id;
  if (!sessionId) throw new Error('Data analysis session did not return an ID.');
  knownAnalysisSessions.add(String(sessionId));
  modal.remove();
  backdrop?.remove();
  await window.sessionModule?.loadSessions?.();
  await window.sessionModule?.selectSession?.(sessionId);
  window.sessionControlModule?.viewFullConversation?.(sessionId);
  history.replaceState(null, '', `#${sessionId}`);
  await openForSession(sessionId);
  await uploadDatasetFile(file);
  if (status) status.textContent = '';
}

function extendWizard(modal) {
  if (!modal || modal.dataset.analysisExtended === 'true') return;
  const grid = modal.querySelector('.venture-choice-grid');
  const submit = modal.querySelector('[data-submit]');
  const chatFields = modal.querySelector('[data-chat-fields]');
  const questFields = modal.querySelector('[data-quest-fields]');
  if (!grid || !submit || !chatFields || !questFields) return;
  modal.dataset.analysisExtended = 'true';
  submit.dataset.analysisOriginalText = submit.textContent || 'Create';

  const choice = document.createElement('label');
  choice.className = 'venture-choice venture-analysis-choice';
  choice.innerHTML = `<input type="radio" name="session_type" value="analysis"><strong>${chartIcon} Data Analysis</strong><span class="venture-muted">Choose a CSV and open an automatic visual insight briefing.</span>`;
  grid.appendChild(choice);

  const fields = document.createElement('div');
  fields.className = 'venture-analysis-fields';
  fields.dataset.analysisFields = 'true';
  fields.hidden = true;
  fields.innerHTML = `<div class="venture-form-grid">
    <div class="venture-full-span"><label>Analysis title</label><input name="analysis_title" autocomplete="off" placeholder="e.g. Quarterly sales exploration"></div>
    <div class="venture-full-span venture-analysis-file-field"><label>CSV dataset</label><input name="analysis_file" type="file" accept=".csv,text/csv"><span class="venture-muted">CSV only, up to 200 MB. The workspace opens immediately while Argos profiles the data.</span></div>
    <div class="venture-full-span venture-analysis-file-field"><label>What do you want to learn? <span class="venture-muted">(optional)</span></label><textarea name="analysis_goal" maxlength="600" placeholder="e.g. Identify revenue drivers, regional changes, and unusual orders."></textarea><span class="venture-muted">Argos uses this objective and relevant column names to generate additional insight cards.</span></div>
  </div>`;
  questFields.insertAdjacentElement('afterend', fields);

  const sync = () => {
    const analysis = modal.querySelector('[name="session_type"]:checked')?.value === 'analysis';
    fields.hidden = !analysis;
    chatFields.hidden = analysis;
    questFields.hidden = analysis;
    fields.querySelector('[name="analysis_file"]').required = analysis;
    submit.textContent = analysis ? 'Create analysis workspace' : submit.dataset.analysisOriginalText;
  };
  modal.querySelectorAll('[name="session_type"]').forEach(input => input.addEventListener('change', sync));
  modal.addEventListener('submit', async event => {
    if (modal.querySelector('[name="session_type"]:checked')?.value !== 'analysis') return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const backdrop = document.querySelector('.venture-modal-backdrop[data-venture-session-wizard]');
    const status = modal.querySelector('[aria-live]');
    submit.disabled = true;
    if (status) status.textContent = 'Creating workspace and preparing the data briefing…';
    try {
      await createFromWizard(modal, backdrop, status);
    } catch (error) {
      if (status) status.textContent = error.message || 'Could not create data analysis session.';
    } finally {
      submit.disabled = false;
    }
  }, true);
  sync();
}

function observeWizard() {
  if (wizardObserver) return;
  wizardObserver = new MutationObserver(records => {
    for (const record of records) {
      for (const node of record.addedNodes) {
        if (node.nodeType !== Node.ELEMENT_NODE) continue;
        const modal = node.matches?.('.QuestCreationWizard') ? node : node.querySelector?.('.QuestCreationWizard');
        if (modal) extendWizard(modal);
      }
    }
  });
  wizardObserver.observe(document.body, { childList: true, subtree: true });
  document.querySelectorAll('.QuestCreationWizard').forEach(extendWizard);
}

function selectedSessionIsAnalysis() {
  const sessionId = window.sessionModule?.getCurrentSessionId?.();
  if (!sessionId) return false;
  const sessions = window.sessionModule?.getSessions?.() || [];
  const row = sessions.find(session => String(session.id) === String(sessionId));
  return row?.mode === 'analysis' || knownAnalysisSessions.has(String(sessionId));
}

function syncSelectedWorkspace() {
  const sessionId = window.sessionModule?.getCurrentSessionId?.();
  if (sessionId && selectedSessionIsAnalysis()) openForSession(String(sessionId));
  else closeForNavigation();
}

async function isVentureRuntime() {
  try {
    const response = await fetch(`${API_BASE}/api/venture/capabilities`, { credentials: 'same-origin' });
    if (!response.ok) return false;
    return !!(await response.json())?.is_venture;
  } catch (_) {
    return false;
  }
}

export async function initVentureDataAnalysis() {
  if (initialized) return;
  initialized = true;
  if (!await isVentureRuntime()) return;
  installStyles();
  getPanel();
  observeWizard();
  window.argosVentureOpenDataAnalysis = openForSession;
  document.addEventListener('odysseus:session-selected', () => setTimeout(syncSelectedWorkspace, 0));
  document.addEventListener('odysseus:new-chat-shown', closeForNavigation);
  document.addEventListener('odysseus:workspace-tab-activated', () => setTimeout(syncSelectedWorkspace, 0));
  setTimeout(syncSelectedWorkspace, 100);
}

export default { initVentureDataAnalysis, openForSession };
