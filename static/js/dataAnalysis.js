// Argos Data Analysis workspace: a persisted CSV dashboard, separate from chat.

const API_BASE = window.location.origin;
let modal = null;
let activeSessionId = null;
let activePayload = null;

const chartIcon = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 3v18h18"/><path d="m7 16 4-5 3 3 5-7"/><circle cx="7" cy="16" r="1"/><circle cx="11" cy="11" r="1"/><circle cx="14" cy="14" r="1"/><circle cx="19" cy="7" r="1"/></svg>`;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  }[char]));
}

function formatNumber(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—';
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: 3 }).format(Number(value));
}

function formatBytes(value) {
  const size = Number(value || 0);
  if (!size) return 'No dataset';
  const units = ['B', 'KB', 'MB', 'GB'];
  const index = Math.min(Math.floor(Math.log(size) / Math.log(1024)), units.length - 1);
  return `${(size / (1024 ** index)).toFixed(index ? 1 : 0)} ${units[index]}`;
}

async function api(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'same-origin',
    ...options,
  });
  const contentType = response.headers.get('content-type') || '';
  const payload = contentType.includes('application/json') ? await response.json() : null;
  if (!response.ok) throw new Error(payload?.detail || `Request failed (${response.status})`);
  return payload;
}

function ensureStyles() {
  if (document.getElementById('data-analysis-styles')) return;
  const style = document.createElement('style');
  style.id = 'data-analysis-styles';
  style.textContent = `
    .data-analysis-modal .modal-content { width:min(1180px, calc(100vw - 32px)); max-width:none; height:min(840px, calc(100vh - 32px)); display:flex; flex-direction:column; overflow:hidden; }
    .analysis-body { display:grid; grid-template-columns:minmax(0, 1.55fr) minmax(290px, .85fr); gap:14px; min-height:0; flex:1; padding:14px; overflow:hidden; }
    .analysis-main, .analysis-side { min-height:0; overflow:auto; display:flex; flex-direction:column; gap:12px; }
    .analysis-card { border:1px solid var(--border); border-radius:10px; background:color-mix(in srgb, var(--panel) 84%, transparent); padding:12px; }
    .analysis-upload { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .analysis-upload input { min-width:0; max-width:240px; }
    .analysis-upload button, .analysis-new-btn, .analysis-send-btn { border:1px solid var(--border); background:var(--panel); color:var(--fg); border-radius:7px; padding:7px 10px; cursor:pointer; }
    .analysis-upload button:hover, .analysis-new-btn:hover, .analysis-send-btn:hover { border-color:var(--accent, var(--red)); }
    .analysis-new-btn { display:inline-flex; gap:5px; align-items:center; white-space:nowrap; }
    .analysis-title-row { display:flex; gap:9px; align-items:center; min-width:0; }
    .analysis-title-row h4 { margin:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .analysis-title-row select { margin-left:auto; max-width:220px; min-width:120px; }
    .analysis-muted { opacity:.68; font-size:12px; margin:3px 0 0; }
    .analysis-metrics { display:grid; grid-template-columns:repeat(3, minmax(0, 1fr)); gap:8px; }
    .analysis-metric { padding:9px; border:1px solid color-mix(in srgb, var(--border) 70%, transparent); border-radius:8px; }
    .analysis-metric strong { display:block; font-size:18px; overflow:hidden; text-overflow:ellipsis; }
    .analysis-metric span { font-size:11px; opacity:.65; }
    .analysis-chart-wrap { min-height:245px; display:flex; align-items:center; justify-content:center; overflow:hidden; }
    .analysis-chart-wrap img { width:100%; max-height:430px; object-fit:contain; border-radius:6px; }
    .analysis-chart-empty { opacity:.65; text-align:center; font-size:13px; padding:30px; }
    .analysis-section-title { font-size:12px; font-weight:700; letter-spacing:.03em; text-transform:uppercase; opacity:.7; margin:0 0 8px; }
    .analysis-table-wrap { overflow:auto; max-height:265px; border:1px solid color-mix(in srgb, var(--border) 66%, transparent); border-radius:7px; }
    .analysis-table { border-collapse:collapse; width:100%; font-size:12px; }
    .analysis-table th, .analysis-table td { padding:6px 8px; text-align:left; border-bottom:1px solid color-mix(in srgb, var(--border) 55%, transparent); white-space:nowrap; max-width:220px; overflow:hidden; text-overflow:ellipsis; }
    .analysis-table th { position:sticky; top:0; background:var(--panel); z-index:1; }
    .analysis-columns { display:flex; flex-direction:column; gap:5px; }
    .analysis-column { display:flex; justify-content:space-between; gap:10px; padding:5px 0; border-bottom:1px solid color-mix(in srgb, var(--border) 50%, transparent); font-size:12px; }
    .analysis-column small { opacity:.62; white-space:nowrap; }
    .analysis-top-values { margin:0; padding-left:18px; font-size:12px; display:flex; flex-direction:column; gap:4px; }
    .analysis-chat { display:flex; flex-direction:column; min-height:250px; }
    .analysis-messages { min-height:142px; max-height:315px; overflow:auto; display:flex; flex-direction:column; gap:8px; }
    .analysis-message { border-radius:8px; padding:8px 9px; font-size:13px; line-height:1.4; border:1px solid color-mix(in srgb, var(--border) 66%, transparent); }
    .analysis-message.user { align-self:flex-end; background:color-mix(in srgb, var(--accent, var(--red)) 13%, var(--panel)); }
    .analysis-message.assistant { align-self:flex-start; background:color-mix(in srgb, var(--panel) 90%, transparent); }
    .analysis-message-role { display:block; font-size:10px; text-transform:uppercase; letter-spacing:.04em; opacity:.58; margin-bottom:3px; }
    .analysis-composer { display:flex; gap:7px; margin-top:10px; }
    .analysis-composer textarea { resize:vertical; flex:1; min-height:52px; max-height:120px; background:var(--bg); border:1px solid var(--border); color:var(--fg); border-radius:7px; padding:8px; font:inherit; }
    .analysis-status { min-height:17px; font-size:12px; margin-top:6px; opacity:.72; }
    .analysis-status.error { color:#e55; opacity:1; }
    .analysis-command-hint { margin-top:8px; font-size:11px; opacity:.62; }
    .data-analysis-launcher { gap:8px; }
    @media (max-width: 820px) { .data-analysis-modal .modal-content { width:100vw; height:100vh; max-height:none; border-radius:0; } .analysis-body { grid-template-columns:1fr; overflow:auto; } .analysis-main, .analysis-side { overflow:visible; } .analysis-side { padding-bottom:15px; } }
  `;
  document.head.appendChild(style);
}

function getModal() {
  if (modal) return modal;
  ensureStyles();
  modal = document.createElement('div');
  modal.id = 'data-analysis-modal';
  modal.className = 'modal data-analysis-modal hidden';
  modal.innerHTML = `
    <div class="modal-content" role="dialog" aria-modal="true" aria-label="Data analysis workspace">
      <div class="modal-header">
        <h4>${chartIcon} Data Analysis</h4>
        <button class="close-btn" type="button" data-analysis-close aria-label="Close data analysis">✖</button>
      </div>
      <div class="analysis-body" id="analysis-body"></div>
    </div>`;
  document.body.appendChild(modal);
  modal.querySelector('[data-analysis-close]').addEventListener('click', closeDataAnalysis);
  return modal;
}

function setStatus(message, isError = false) {
  const node = modal?.querySelector('#analysis-status');
  if (!node) return;
  node.textContent = message || '';
  node.classList.toggle('error', !!isError);
}

function renderTable(rows = []) {
  if (!rows.length) return '<div class="analysis-chart-empty">No rows to preview.</div>';
  const columns = Object.keys(rows[0]).slice(0, 10);
  return `<div class="analysis-table-wrap"><table class="analysis-table"><thead><tr>${columns.map(column => `<th title="${escapeHtml(column)}">${escapeHtml(column)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${columns.map(column => `<td title="${escapeHtml(row[column])}">${escapeHtml(row[column])}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function renderPayload(payload) {
  activePayload = payload;
  activeSessionId = payload?.session?.id || activeSessionId;
  const body = modal?.querySelector('#analysis-body');
  if (!body) return;

  const session = payload?.session || {};
  const overview = payload?.insights?.overview || {};
  const chart = payload?.chart;
  const schemaColumns = payload?.schema?.columns || [];
  const sampleRows = payload?.insights?.sample_rows || [];
  const messages = payload?.messages || [];
  const selectedState = payload?.dashboard?.chart || {};
  const image = chart?.image_png_base64 ? `data:image/png;base64,${chart.image_png_base64}` : '';
  const options = (window.__analysisSessionList || []).map(item => `<option value="${escapeHtml(item.id)}" ${item.id === session.id ? 'selected' : ''}>${escapeHtml(item.name)}${item.dataset_filename ? ` · ${escapeHtml(item.dataset_filename)}` : ''}</option>`).join('');

  body.innerHTML = `
    <section class="analysis-main">
      <div class="analysis-card">
        <div class="analysis-title-row">
          <div style="min-width:0"><h4>${escapeHtml(session.name || 'Data analysis')}</h4><p class="analysis-muted">${session.dataset_filename ? `${escapeHtml(session.dataset_filename)} · ${formatBytes(session.dataset_size_bytes)}` : 'Create an analysis session, then upload a CSV.'}</p></div>
          <select id="analysis-session-select" aria-label="Analysis sessions"><option value="">Current session</option>${options}</select>
          <button class="analysis-new-btn" id="analysis-new-session" title="New data analysis session">${chartIcon}<span>New</span></button>
        </div>
        <div class="analysis-upload" style="margin-top:12px">
          <input id="analysis-file" type="file" accept=".csv,text/csv" aria-label="CSV dataset" />
          <button id="analysis-upload" type="button">Upload & analyze</button>
          <span class="analysis-muted">CSV only · up to 200 MB</span>
        </div>
        <div id="analysis-status" class="analysis-status"></div>
      </div>
      <div class="analysis-metrics">
        <div class="analysis-metric"><strong>${formatNumber(overview.rows)}</strong><span>Rows</span></div>
        <div class="analysis-metric"><strong>${formatNumber(overview.columns)}</strong><span>Columns</span></div>
        <div class="analysis-metric"><strong>${formatNumber((overview.missing_rate || 0) * 100)}%</strong><span>Missing values</span></div>
      </div>
      <div class="analysis-card">
        <p class="analysis-section-title">Dashboard · ${escapeHtml(selectedState.kind || 'preview')}</p>
        <div class="analysis-chart-wrap">${image ? `<img src="${image}" alt="${escapeHtml(chart?.summary || 'Analysis chart')}" />` : `<div class="analysis-chart-empty">${escapeHtml(chart?.summary || (session.dataset_filename ? 'Chart rendering is unavailable; the Plotly JSON response is still stored by the API.' : 'Upload a CSV to create a dashboard.'))}</div>`}</div>
        ${chart?.summary ? `<p class="analysis-muted">${escapeHtml(chart.summary)}</p>` : ''}
      </div>
      <div class="analysis-card">
        <p class="analysis-section-title">Dataset preview</p>
        ${renderTable(sampleRows)}
      </div>
    </section>
    <aside class="analysis-side">
      <div class="analysis-card">
        <p class="analysis-section-title">Columns</p>
        <div class="analysis-columns">${schemaColumns.slice(0, 18).map(column => `<div class="analysis-column"><span title="${escapeHtml(column.name)}">${escapeHtml(column.name)}</span><small>${escapeHtml(column.dtype)} · ${formatNumber(column.null_count)} null</small></div>`).join('') || '<div class="analysis-muted">No CSV loaded.</div>'}</div>
      </div>
      <div class="analysis-card">
        <p class="analysis-section-title">Top categories</p>
        ${Object.entries(payload?.insights?.top_categories || {}).slice(0, 3).map(([column, values]) => `<div style="margin-bottom:10px"><strong style="font-size:12px">${escapeHtml(column)}</strong><ol class="analysis-top-values">${(values || []).slice(0, 5).map(item => `<li>${escapeHtml(item.value)} <span style="opacity:.58">${formatNumber(item.count)}</span></li>`).join('')}</ol></div>`).join('') || '<div class="analysis-muted">Categorical insights appear after upload.</div>'}
      </div>
      <div class="analysis-card analysis-chat">
        <p class="analysis-section-title">Dashboard conversation</p>
        <div class="analysis-messages" id="analysis-messages">${messages.map(message => `<div class="analysis-message ${escapeHtml(message.role)}"><span class="analysis-message-role">${escapeHtml(message.role)}</span>${escapeHtml(message.content)}</div>`).join('') || '<div class="analysis-muted">Ask the dashboard to change the view after uploading a CSV.</div>'}</div>
        <div class="analysis-composer"><textarea id="analysis-prompt" placeholder="e.g. histogram revenue, top 10 region, filter region = East"></textarea><button id="analysis-send" class="analysis-send-btn" type="button">Send</button></div>
        <div class="analysis-command-hint">State is persisted with every turn. Commands: histogram, scatter X vs Y, top N column, filter column = value, preview, reset.</div>
      </div>
    </aside>`;

  body.querySelector('#analysis-upload')?.addEventListener('click', uploadDataset);
  body.querySelector('#analysis-send')?.addEventListener('click', sendMessage);
  body.querySelector('#analysis-prompt')?.addEventListener('keydown', event => {
    if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
      event.preventDefault();
      sendMessage();
    }
  });
  body.querySelector('#analysis-new-session')?.addEventListener('click', createAndOpenSession);
  body.querySelector('#analysis-session-select')?.addEventListener('change', event => {
    if (event.target.value) loadSession(event.target.value);
  });
}

async function refreshSessionList() {
  const data = await api('/api/workspace/analysis/sessions');
  window.__analysisSessionList = data.sessions || [];
}

async function loadSession(sessionId) {
  setStatus('Loading dashboard…');
  try {
    const payload = await api(`/api/workspace/analysis/sessions/${encodeURIComponent(sessionId)}`);
    renderPayload(payload);
    setStatus('');
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function createAndOpenSession() {
  setStatus('Creating analysis session…');
  try {
    const payload = await api('/api/workspace/analysis/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: 'Data analysis' }),
    });
    await refreshSessionList();
    renderPayload(payload);
    setStatus('Upload a CSV to begin.');
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function uploadDataset() {
  const file = modal?.querySelector('#analysis-file')?.files?.[0];
  if (!file) {
    setStatus('Choose a CSV file first.', true);
    return;
  }
  if (!activeSessionId) {
    setStatus('Creating a session first…');
    await createAndOpenSession();
  }
  const payload = new FormData();
  payload.append('file', file);
  setStatus(`Analyzing ${file.name}…`);
  try {
    const result = await api(`/api/workspace/analysis/sessions/${encodeURIComponent(activeSessionId)}/dataset`, {
      method: 'POST', body: payload,
    });
    await refreshSessionList();
    renderPayload(result);
    setStatus('Dataset analyzed and dashboard state saved.');
  } catch (error) {
    setStatus(error.message, true);
  }
}

async function sendMessage() {
  const input = modal?.querySelector('#analysis-prompt');
  const content = input?.value?.trim();
  if (!content || !activeSessionId) return;
  input.value = '';
  setStatus('Updating dashboard…');
  try {
    const result = await api(`/api/workspace/analysis/sessions/${encodeURIComponent(activeSessionId)}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    renderPayload(result);
    const messages = modal?.querySelector('#analysis-messages');
    if (messages) messages.scrollTop = messages.scrollHeight;
    setStatus('Dashboard state saved.');
  } catch (error) {
    setStatus(error.message, true);
  }
}

function insertLaunchers() {
  const overflow = document.getElementById('overflow-menu');
  if (overflow && !document.getElementById('overflow-data-analysis-btn')) {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'overflow-menu-item';
    item.id = 'overflow-data-analysis-btn';
    item.innerHTML = `${chartIcon}<span>Data Analysis</span>`;
    item.title = 'Start a persisted CSV data analysis session';
    item.addEventListener('click', () => {
      document.getElementById('overflow-menu')?.classList.add('hidden');
      document.getElementById('overflow-plus-btn')?.classList.remove('expanded');
      openDataAnalysis();
    });
    // The existing + menu holds the current workspace starters/tools. Appending
    // this preserves their order and exposes Data Analysis as the fourth session workflow.
    overflow.appendChild(item);
  }

  const sidebarNewChat = document.getElementById('sidebar-new-chat-btn');
  if (sidebarNewChat && !document.getElementById('sidebar-data-analysis-btn')) {
    const item = document.createElement('div');
    item.className = 'list-item data-analysis-launcher';
    item.id = 'sidebar-data-analysis-btn';
    item.title = 'New data analysis';
    item.innerHTML = `${chartIcon}<span class="grow">Data Analysis</span>`;
    item.addEventListener('click', openDataAnalysis);
    sidebarNewChat.insertAdjacentElement('afterend', item);
  }

  const railNew = document.getElementById('rail-new-session');
  if (railNew && !document.getElementById('rail-data-analysis-btn')) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'icon-rail-btn';
    button.id = 'rail-data-analysis-btn';
    button.title = 'Data Analysis';
    button.setAttribute('aria-label', 'Start data analysis');
    button.innerHTML = chartIcon;
    button.addEventListener('click', openDataAnalysis);
    railNew.insertAdjacentElement('afterend', button);
  }
}

export async function openDataAnalysis() {
  const pane = getModal();
  pane.classList.remove('hidden');
  try {
    await refreshSessionList();
    if (activeSessionId) {
      await loadSession(activeSessionId);
    } else {
      await createAndOpenSession();
    }
  } catch (error) {
    setStatus(error.message, true);
  }
}

export function closeDataAnalysis() {
  modal?.classList.add('hidden');
}

export function initDataAnalysis() {
  ensureStyles();
  insertLaunchers();
}

export default { initDataAnalysis, openDataAnalysis, closeDataAnalysis };
