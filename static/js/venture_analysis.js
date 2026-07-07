// Argos Venture — stateful CSV Data Analysis workspace.
//
// This module augments the existing Venture New Tab wizard without duplicating
// its Quest/Chat flows. Data Analysis sessions are ordinary workspace tabs with
// mode="analysis"; their dashboard state and messages use the dedicated API.

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

function number(value, digits = 3) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(numeric)
    : '—';
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
  const isJson = (response.headers.get('content-type') || '').includes('application/json');
  const data = isJson ? await response.json() : null;
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
    .venture-analysis-button.primary { background:var(--fg); color:var(--bg); }
    .venture-analysis-layout { display:grid; grid-template-columns:minmax(0, 1.55fr) minmax(300px, .85fr); gap:14px; flex:1; min-height:0; padding:14px; overflow:hidden; }
    .venture-analysis-main, .venture-analysis-side { display:flex; flex-direction:column; gap:12px; min-height:0; overflow:auto; }
    .venture-analysis-card { border:1px solid var(--border); border-radius:10px; background:color-mix(in srgb, var(--panel) 88%, transparent); padding:12px; }
    .venture-analysis-card h3 { margin:0 0 8px; font-size:12px; letter-spacing:.05em; text-transform:uppercase; opacity:.72; }
    .venture-analysis-upload { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .venture-analysis-upload input { max-width:260px; min-width:0; }
    .venture-analysis-note { font-size:12px; opacity:.68; }
    .venture-analysis-status { min-height:18px; margin-top:7px; font-size:12px; opacity:.75; }
    .venture-analysis-status.error { color:#e55; opacity:1; }
    .venture-analysis-metrics { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:8px; }
    .venture-analysis-metric { border:1px solid color-mix(in srgb, var(--border) 66%, transparent); border-radius:8px; padding:9px; }
    .venture-analysis-metric strong { display:block; font-size:19px; overflow:hidden; text-overflow:ellipsis; }
    .venture-analysis-metric span { font-size:11px; opacity:.65; }
    .venture-analysis-chart { min-height:270px; display:flex; align-items:center; justify-content:center; overflow:hidden; }
    .venture-analysis-chart img { width:100%; max-height:430px; object-fit:contain; border-radius:7px; }
    .venture-analysis-empty { text-align:center; opacity:.68; font-size:13px; padding:26px; line-height:1.4; }
    .venture-analysis-table-wrap { overflow:auto; max-height:280px; border:1px solid color-mix(in srgb, var(--border) 66%, transparent); border-radius:7px; }
    .venture-analysis-table { width:100%; border-collapse:collapse; font-size:12px; }
    .venture-analysis-table th, .venture-analysis-table td { padding:7px 8px; text-align:left; border-bottom:1px solid color-mix(in srgb, var(--border) 55%, transparent); white-space:nowrap; max-width:220px; overflow:hidden; text-overflow:ellipsis; }
    .venture-analysis-table th { position:sticky; top:0; background:var(--panel); z-index:1; }
    .venture-analysis-columns { display:flex; flex-direction:column; gap:5px; }
    .venture-analysis-column { display:flex; justify-content:space-between; gap:8px; padding:5px 0; border-bottom:1px solid color-mix(in srgb, var(--border) 50%, transparent); font-size:12px; }
    .venture-analysis-column > span { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .venture-analysis-column small { opacity:.62; white-space:nowrap; }
    .venture-analysis-categories { margin:0; padding-left:18px; font-size:12px; display:flex; flex-direction:column; gap:4px; }
    .venture-analysis-chat { min-height:260px; display:flex; flex-direction:column; }
    .venture-analysis-messages { min-height:130px; max-height:295px; overflow:auto; display:flex; flex-direction:column; gap:8px; }
    .venture-analysis-message { padding:8px 9px; border:1px solid color-mix(in srgb, var(--border) 66%, transparent); border-radius:8px; font-size:13px; line-height:1.4; white-space:pre-wrap; overflow-wrap:anywhere; }
    .venture-analysis-message.user { align-self:flex-end; background:color-mix(in srgb, var(--fg) 8%, var(--panel)); }
    .venture-analysis-message.assistant { align-self:flex-start; background:color-mix(in srgb, var(--panel) 92%, transparent); }
    .venture-analysis-message-role { display:block; font-size:10px; opacity:.58; letter-spacing:.04em; text-transform:uppercase; margin-bottom:3px; }
    .venture-analysis-composer { display:flex; gap:8px; margin-top:9px; }
    .venture-analysis-composer textarea { min-height:56px; resize:vertical; flex:1; background:var(--bg); color:var(--fg); border:1px solid var(--border); border-radius:7px; padding:8px; font:inherit; }
    .venture-analysis-hint { margin-top:8px; font-size:11px; opacity:.62; line-height:1.4; }
    .venture-choice.venture-analysis-choice strong { display:flex; align-items:center; gap:6px; }
    .venture-analysis-fields { margin-top:10px; }
    @media (max-width:820px) { .venture-analysis-workspace { left:0; top:0; } .venture-analysis-layout { grid-template-columns:1fr; overflow:auto; } .venture-analysis-main,.venture-analysis-side { overflow:visible; } .venture-analysis-topbar { padding:10px; } .venture-analysis-sub { display:none; } }
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
  panel.querySelector('#venture-analysis-close').addEventListener('click', () => {
    panel.hidden = true;
    document.body.classList.remove('venture-analysis-active');
  });
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

function render(payload) {
  activePayload = payload;
  activeSessionId = payload?.session?.id || activeSessionId;
  if (activeSessionId) knownAnalysisSessions.add(String(activeSessionId));
  const root = getPanel();
  const layout = root.querySelector('#venture-analysis-layout');
  const session = payload?.session || {};
  const overview = payload?.insights?.overview || {};
  const chart = payload?.chart;
  const image = chart?.image_png_base64 ? `data:image/png;base64,${chart.image_png_base64}` : '';
  const columns = payload?.schema?.columns || [];
  const rows = payload?.insights?.sample_rows || [];
  const messages = payload?.messages || [];
  root.querySelector('#venture-analysis-title').textContent = session.name || 'Data Analysis';
  root.querySelector('#venture-analysis-file-meta').textContent = session.dataset_filename ? `${session.dataset_filename} · ${bytes(session.dataset_size_bytes)}` : 'CSV analysis workspace';

  layout.innerHTML = `
    <main class="venture-analysis-main">
      <section class="venture-analysis-card">
        <h3>Dataset</h3>
        <div class="venture-analysis-upload">
          <input id="venture-analysis-file" type="file" accept=".csv,text/csv" aria-label="Choose CSV dataset">
          <button type="button" class="venture-analysis-button primary" id="venture-analysis-upload">Upload & analyze</button>
          <span class="venture-analysis-note">CSV only · up to 200 MB · processed with Polars</span>
        </div>
        <div class="venture-analysis-status" id="venture-analysis-status"></div>
      </section>
      <div class="venture-analysis-metrics">
        <div class="venture-analysis-metric"><strong>${number(overview.rows, 0)}</strong><span>Rows</span></div>
        <div class="venture-analysis-metric"><strong>${number(overview.columns, 0)}</strong><span>Columns</span></div>
        <div class="venture-analysis-metric"><strong>${number((overview.missing_rate || 0) * 100, 2)}%</strong><span>Missing values</span></div>
      </div>
      <section class="venture-analysis-card">
        <h3>Dashboard · ${esc(payload?.dashboard?.chart?.kind || 'preview')}</h3>
        <div class="venture-analysis-chart">${image ? `<img alt="${esc(chart?.summary || 'Dataset chart')}" src="${image}">` : `<div class="venture-analysis-empty">${esc(chart?.summary || (session.dataset_filename ? 'The chart image could not be rendered. The saved Plotly figure remains available through the API.' : 'Upload a CSV to generate insights and a chart.'))}</div>`}</div>
        ${chart?.summary ? `<div class="venture-analysis-note">${esc(chart.summary)}</div>` : ''}
      </section>
      <section class="venture-analysis-card"><h3>Dataset preview</h3>${table(rows)}</section>
    </main>
    <aside class="venture-analysis-side">
      <section class="venture-analysis-card"><h3>Columns</h3><div class="venture-analysis-columns">${columns.slice(0, 20).map(column => `<div class="venture-analysis-column"><span title="${esc(column.name)}">${esc(column.name)}</span><small>${esc(column.dtype)} · ${number(column.null_count, 0)} null</small></div>`).join('') || '<div class="venture-analysis-note">Column details appear after uploading a CSV.</div>'}</div></section>
      <section class="venture-analysis-card"><h3>Top categories</h3>${Object.entries(payload?.insights?.top_categories || {}).slice(0, 3).map(([column, values]) => `<div style="margin-bottom:10px"><strong style="font-size:12px">${esc(column)}</strong><ol class="venture-analysis-categories">${(values || []).slice(0, 5).map(value => `<li>${esc(value.value)} <span style="opacity:.58">${number(value.count, 0)}</span></li>`).join('')}</ol></div>`).join('') || '<div class="venture-analysis-note">Categorical summaries appear after upload.</div>'}</section>
      <section class="venture-analysis-card venture-analysis-chat">
        <h3>Dashboard conversation</h3>
        <div class="venture-analysis-messages" id="venture-analysis-messages">${messages.map(item => `<div class="venture-analysis-message ${esc(item.role)}"><span class="venture-analysis-message-role">${esc(item.role)}</span>${esc(item.content)}</div>`).join('') || '<div class="venture-analysis-note">Ask the dashboard to transform a view after uploading a CSV.</div>'}</div>
        <div class="venture-analysis-composer"><textarea id="venture-analysis-prompt" placeholder="e.g. histogram revenue, top 10 region, filter region = East"></textarea><button type="button" class="venture-analysis-button primary" id="venture-analysis-send">Send</button></div>
        <div class="venture-analysis-hint">State is persisted every turn. Try: histogram, scatter X vs Y, top N column, filter column = value, preview, reset.</div>
      </section>
    </aside>`;

  layout.querySelector('#venture-analysis-upload')?.addEventListener('click', uploadDataset);
  layout.querySelector('#venture-analysis-send')?.addEventListener('click', sendPrompt);
  layout.querySelector('#venture-analysis-prompt')?.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      sendPrompt();
    }
  });
  const messagesNode = layout.querySelector('#venture-analysis-messages');
  if (messagesNode) messagesNode.scrollTop = messagesNode.scrollHeight;
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

async function uploadDataset() {
  const input = panel?.querySelector('#venture-analysis-file');
  const file = input?.files?.[0];
  if (!file) {
    setStatus('Choose a CSV file first.', true);
    return;
  }
  if (!activeSessionId) return;
  const form = new FormData();
  form.append('file', file);
  setStatus(`Analyzing ${file.name}…`);
  try {
    const data = await api(`/api/venture/analysis/sessions/${encodeURIComponent(activeSessionId)}/dataset`, { method: 'POST', body: form });
    render(data);
    setStatus('Dataset analyzed and dashboard state saved.');
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function sendPrompt() {
  const input = panel?.querySelector('#venture-analysis-prompt');
  const content = input?.value?.trim();
  if (!content || !activeSessionId) return;
  input.value = '';
  setStatus('Updating dashboard…');
  try {
    const data = await api(`/api/venture/analysis/sessions/${encodeURIComponent(activeSessionId)}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    render(data);
    setStatus('Dashboard state saved.');
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function createFromWizard(modal, backdrop, status) {
  const title = String(modal.querySelector('[name="analysis_title"]')?.value || '').trim() || 'Data Analysis';
  const data = await api('/api/venture/analysis/sessions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: title }),
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

  const choice = document.createElement('label');
  choice.className = 'venture-choice venture-analysis-choice';
  choice.innerHTML = `<input type="radio" name="session_type" value="analysis"><strong>${chartIcon} Data Analysis</strong><span class="venture-muted">Upload a CSV, build visual dashboards, and keep a state-aware analysis conversation.</span>`;
  grid.appendChild(choice);

  const fields = document.createElement('div');
  fields.className = 'venture-analysis-fields';
  fields.dataset.analysisFields = 'true';
  fields.hidden = true;
  fields.innerHTML = `<div class="venture-form-grid"><div class="venture-full-span"><label>Analysis title</label><input name="analysis_title" autocomplete="off" placeholder="e.g. Quarterly sales exploration"></div></div><div class="venture-muted" style="margin-top:8px">Create the workspace first, then upload a CSV. Argos will retain your cleaned schema, insights, visual state, and dashboard conversation separately from standard chat history.</div>`;
  questFields.insertAdjacentElement('afterend', fields);

  const sync = () => {
    const selected = modal.querySelector('[name="session_type"]:checked')?.value || 'chat';
    const analysis = selected === 'analysis';
    fields.hidden = !analysis;
    if (analysis) {
      chatFields.hidden = true;
      questFields.hidden = true;
      submit.textContent = 'Create analysis workspace';
    }
  };
  modal.querySelectorAll('[name="session_type"]').forEach(input => input.addEventListener('change', sync));
  modal.addEventListener('submit', async event => {
    const selected = modal.querySelector('[name="session_type"]:checked')?.value;
    if (selected !== 'analysis') return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const backdrop = document.querySelector('.venture-modal-backdrop[data-venture-session-wizard]');
    const status = modal.querySelector('[aria-live]');
    submit.disabled = true;
    if (status) status.textContent = 'Creating analysis workspace…';
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
