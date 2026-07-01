// Argos Venture UI shell and capability gates.

const API_BASE = window.API_BASE || '';
let caps = null;
let currentQuestId = null;

const captainOnlySelectors = [
  '#workspace-new-tab', '#rail-new-session', '#new-session-btn', '#overflow-attach-btn', '#overflow-doc-btn',
  '#overflow-rag-btn', '#overflow-workspace-btn', '#overflow-preset-btn',
  '#web-toggle-btn', '#bash-toggle-btn', '#model-picker-btn', '#model-picker-add-models-btn',
  '#mode-agent-btn', '#research-toggle-btn', '#tool-research-btn', '#tool-memory-btn',
  '#rail-calendar', '#rail-compare', '#rail-cookbook', '#rail-research', '#rail-email',
  '#incognito-btn', '#custom-preset-modal', '#workspace-indicator-btn',
  'input[type="file"]', '.attachment-strip', '#attach-strip',
];

function h(tag, attrs = {}, children = []) {
  const el = document.createElement(tag);
  Object.entries(attrs || {}).forEach(([k, v]) => {
    if (k === 'class') el.className = v;
    else if (k === 'text') el.textContent = v;
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  });
  (Array.isArray(children) ? children : [children]).forEach(c => {
    if (c == null) return;
    el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
  });
  return el;
}

async function loadCapabilities() {
  try {
    const res = await fetch(`${API_BASE}/api/venture/capabilities`, { credentials: 'same-origin' });
    if (!res.ok) return null;
    return await res.json();
  } catch (_) {
    return null;
  }
}

function installStyles() {
  if (document.getElementById('venture-style')) return;
  const style = document.createElement('style');
  style.id = 'venture-style';
  style.textContent = `
    body.argos-venture #current-meta::before { content: "Voyage Log · "; opacity: .75; }
    .venture-right-rail { position: fixed; right: 0; top: 0; bottom: 0; width: min(360px, 34vw); min-width: 280px; z-index: 20; background: var(--panel, #151515); border-left: 1px solid var(--border); overflow:auto; padding: 12px; box-sizing: border-box; }
    .venture-right-rail.collapsed { transform: translateX(calc(100% - 38px)); overflow:hidden; }
    .venture-rail-toggle { position: sticky; top: 0; float: left; width: 28px; height: 28px; border: 1px solid var(--border); background: var(--bg); color: var(--fg); border-radius: 6px; cursor:pointer; }
    .venture-card { border: 1px solid var(--border); border-radius: 8px; padding: 10px; margin: 10px 0; background: color-mix(in srgb, var(--panel) 88%, var(--fg) 4%); }
    .venture-card h3 { font-size: 13px; margin: 0 0 8px; letter-spacing: 0; }
    .venture-muted { opacity: .65; font-size: 12px; }
    .venture-list { display:flex; flex-direction:column; gap:6px; }
    .venture-row { display:flex; justify-content:space-between; gap:8px; align-items:center; font-size:12px; }
    .venture-btn { border:1px solid var(--border); background:var(--bg); color:var(--fg); border-radius:6px; padding:5px 8px; cursor:pointer; font:inherit; font-size:12px; }
    .venture-btn.primary { background:var(--fg); color:var(--bg); }
    .venture-modal-backdrop { position:fixed; inset:0; z-index:9999; background:rgba(0,0,0,.45); }
    .venture-modal { position:fixed; inset:7vh auto auto 50%; transform:translateX(-50%); width:min(760px, 94vw); max-height:86vh; overflow:auto; z-index:10000; background:var(--bg); color:var(--fg); border:1px solid var(--border); border-radius:8px; padding:16px; box-shadow:0 18px 60px rgba(0,0,0,.35); }
    .venture-modal h2 { margin:0 0 12px; font-size:18px; letter-spacing:0; }
    .venture-modal label { display:block; font-size:12px; margin:10px 0 4px; opacity:.8; }
    .venture-modal input, .venture-modal textarea, .venture-modal select { width:100%; box-sizing:border-box; border:1px solid var(--border); background:var(--panel); color:var(--fg); border-radius:6px; padding:8px; font:inherit; }
    .venture-modal textarea { min-height:72px; resize:vertical; }
    .venture-modal input[type="radio"], .venture-modal input[type="checkbox"] { width:auto; }
    .venture-modal-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:12px; }
    .venture-choice-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:8px; margin:10px 0 14px; }
    .venture-choice { border:1px solid var(--border); border-radius:8px; padding:10px; background:var(--panel); cursor:pointer; display:grid; gap:4px; }
    .venture-choice input { position:absolute; opacity:0; pointer-events:none; }
    .venture-choice:has(input:checked) { outline:2px solid color-mix(in srgb, var(--fg) 38%, transparent); }
    .venture-choice strong { font-size:13px; letter-spacing:0; }
    .venture-form-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; }
    .venture-source-panel { border:1px solid var(--border); border-radius:8px; padding:10px; margin-top:10px; background:color-mix(in srgb, var(--panel) 86%, var(--fg) 3%); }
    .venture-check-list { display:grid; gap:6px; max-height:160px; overflow:auto; border:1px solid var(--border); border-radius:8px; padding:8px; background:var(--panel); }
    .venture-check-row { display:flex!important; align-items:center; gap:8px; margin:0!important; opacity:1!important; }
    .venture-file-list { margin-top:6px; display:grid; gap:4px; }
    .venture-full-span { grid-column:1 / -1; }
    @media (max-width: 900px) { .venture-right-rail { width:min(86vw, 360px); } }
    @media (max-width: 720px) { .venture-choice-grid, .venture-form-grid { grid-template-columns:1fr; } }
  `;
  document.head.appendChild(style);
}

function isShipmate() {
  return caps && caps.role === 'shipmate';
}

function applyTerminology() {
  const meta = document.getElementById('current-meta');
  if (meta && /Odysseus Chat|Chat/.test(meta.textContent || '')) meta.textContent = 'Quest';
  const search = document.getElementById('search-input');
  if (search) search.placeholder = 'Search Quests and Artifacts...';
}

function hardDisableShipmateControls() {
  if (!isShipmate()) return;
  captainOnlySelectors.forEach(sel => {
    document.querySelectorAll(sel).forEach(el => {
      el.setAttribute('hidden', '');
      el.setAttribute('aria-hidden', 'true');
      el.style.display = 'none';
      if ('disabled' in el) el.disabled = true;
    });
  });
  document.querySelectorAll('.settings-nav-item').forEach(btn => {
    const tab = btn.getAttribute('data-settings-tab');
    if (tab && tab !== 'account') {
      btn.setAttribute('hidden', '');
      btn.style.display = 'none';
    }
  });
}

function hideVentureNewChatShortcuts() {
  if (!caps?.is_venture) return;
  document.querySelectorAll('#rail-new-session, #new-session-btn, #chat-new-btn').forEach(el => {
    el.setAttribute('hidden', '');
    el.setAttribute('aria-hidden', 'true');
    el.style.display = 'none';
    if ('disabled' in el) el.disabled = true;
  });
  const brand = document.getElementById('sidebar-brand-btn');
  if (brand) {
    brand.removeAttribute('title');
    brand.style.cursor = 'default';
  }
  const workspacePlus = document.getElementById('workspace-new-tab');
  if (workspacePlus && !isShipmate()) {
    workspacePlus.removeAttribute('hidden');
    workspacePlus.removeAttribute('aria-hidden');
    workspacePlus.style.display = '';
    workspacePlus.disabled = false;
    workspacePlus.title = 'Create session';
    workspacePlus.setAttribute('aria-label', 'Create session');
  }
}

function installShipmateCaptureGuards() {
  document.addEventListener('click', e => {
    if (!isShipmate()) return;
    const blocked = e.target.closest(captainOnlySelectors.join(','));
    if (blocked) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener('drop', e => {
    if (isShipmate()) { e.preventDefault(); e.stopImmediatePropagation(); }
  }, true);
  document.addEventListener('paste', e => {
    if (!isShipmate()) return;
    const items = Array.from(e.clipboardData?.items || []);
    if (items.some(i => i.kind === 'file')) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);
  document.addEventListener('submit', e => {
    if (!isShipmate()) return;
    const form = e.target;
    if (!form || form.id !== 'chat-form') return;
    ['attachments', 'use_web', 'use_research', 'allow_bash', 'allow_web_search', 'use_rag', 'workspace', 'preset_id', 'mode'].forEach(name => {
      form.querySelectorAll(`[name="${name}"]`).forEach(el => { el.disabled = true; });
    });
  }, true);
}

async function loadQuest(id) {
  if (!id || !caps?.is_venture) return;
  currentQuestId = id;
  await renderRightRail();
}

function selectedQuestId() {
  const hash = (window.location.hash || '').replace(/^#/, '');
  return hash || currentQuestId || localStorage.getItem('lastSessionId') || '';
}

async function getJson(url) {
  const res = await fetch(`${API_BASE}${url}`, { credentials: 'same-origin' });
  if (!res.ok) return null;
  return await res.json().catch(() => null);
}

async function renderRightRail() {
  if (!caps?.is_venture) return;
  installStyles();
  const qid = selectedQuestId();
  let rail = document.getElementById('venture-right-rail');
  if (!qid) {
    rail?.remove();
    return;
  }
  if (!rail) {
    rail = h('aside', { id: 'venture-right-rail', class: 'venture-right-rail', 'aria-label': 'Quest context' });
    document.body.appendChild(rail);
  }
  rail.innerHTML = '';
  const toggle = h('button', { class: 'venture-rail-toggle', type: 'button', title: 'Collapse Quest context', text: '◂', onclick: () => rail.classList.toggle('collapsed') });
  rail.appendChild(toggle);
  rail.appendChild(h('div', { style: 'clear:both' }));
  const [bearing, roster, sources, artifacts, memory] = await Promise.all([
    getJson(`/api/quests/${encodeURIComponent(qid)}/bearing`),
    getJson(`/api/quests/${encodeURIComponent(qid)}/roster`),
    getJson(`/api/quests/${encodeURIComponent(qid)}/sources`),
    getJson(`/api/quests/${encodeURIComponent(qid)}/artifacts`),
    caps.can_view_quest_memory ? getJson(`/api/quests/${encodeURIComponent(qid)}/memory`) : Promise.resolve(null),
  ]);
  rail.appendChild(currentBearingCard(bearing?.bearing || {}));
  rail.appendChild(crewRoster(roster?.members || []));
  if (caps.can_manage_sources || (sources?.sources || []).length) rail.appendChild(sourceCard(sources?.sources || [], qid));
  if (caps.can_review_artifacts) rail.appendChild(artifactReviewQueue(qid));
  rail.appendChild(artifactShelf(artifacts || { documents: [], gallery: [] }));
  rail.appendChild(argoStatusCard(qid, memory?.memory || []));
}

function currentBearingCard(b) {
  return h('section', { class: 'venture-card CurrentBearingCard' }, [
    h('h3', { text: 'Current Bearing' }),
    h('div', { text: b.title || 'Untitled Quest' }),
    h('p', { class: 'venture-muted', text: b.exploration_goal || 'No exploration goal set.' }),
    b.next_bearing ? h('div', { class: 'venture-muted', text: `Next: ${b.next_bearing}` }) : null,
  ]);
}

function crewRoster(members) {
  return h('section', { class: 'venture-card CrewRoster' }, [
    h('h3', { text: 'Crew' }),
    h('div', { class: 'venture-list' }, members.map(m => h('div', { class: 'venture-row' }, [
      h('span', { text: m.username }),
      h('span', { class: 'venture-muted', text: m.role === 'captain' ? 'Captain' : 'Shipmate' }),
    ]))),
  ]);
}

function sourceCard(sources, qid) {
  return h('section', { class: 'venture-card QuestSourceCard' }, [
    h('h3', { text: 'Quest Sources' }),
    h('div', { class: 'venture-list' }, sources.map(s => h('div', { class: 'venture-row' }, [
      h('span', { text: s.display_name }),
      h('span', { class: 'venture-muted', text: s.access_mode }),
    ]))),
    caps.can_manage_sources ? h('button', { class: 'venture-btn', type: 'button', text: 'Refresh Sources', onclick: async () => {
      for (const s of sources) await fetch(`${API_BASE}/api/quests/${encodeURIComponent(qid)}/sources/${encodeURIComponent(s.id)}/refresh`, { method: 'POST', credentials: 'same-origin' });
      await renderRightRail();
    } }) : null,
  ]);
}

function artifactReviewQueue(qid) {
  const box = h('section', { class: 'venture-card ArtifactReviewQueue' }, [h('h3', { text: 'Artifact Review' }), h('div', { class: 'venture-muted', text: 'Loading...' })]);
  getJson(`/api/quests/${encodeURIComponent(qid)}/artifact-proposals`).then(data => {
    const proposals = data?.proposals || [];
    box.innerHTML = '<h3>Artifact Review</h3>';
    if (!proposals.length) box.appendChild(h('div', { class: 'venture-muted', text: 'No pending drafts.' }));
    proposals.filter(p => p.status === 'pending_review').forEach(p => {
      box.appendChild(h('div', { class: 'venture-row' }, [
        h('span', { text: p.title }),
        h('button', { class: 'venture-btn', type: 'button', text: 'Review', onclick: () => openArtifactDraft(p.id) }),
      ]));
    });
  });
  return box;
}

function artifactShelf(artifacts) {
  const docs = artifacts.documents || [];
  const gallery = artifacts.gallery || [];
  return h('section', { class: 'venture-card ArtifactShelf' }, [
    h('h3', { text: 'Quest Artifacts' }),
    ...docs.map(d => h('div', { class: 'venture-row ArtifactCard' }, [
      h('span', { text: d.title }),
      h('button', { class: 'venture-btn', type: 'button', text: 'Open', onclick: () => window.dispatchEvent(new CustomEvent('odysseus:open-document', { detail: { id: d.id } })) }),
    ])),
    ...gallery.map(g => h('div', { class: 'venture-row ArtifactCard' }, [h('span', { text: g.prompt || g.filename })])),
    (!docs.length && !gallery.length) ? h('div', { class: 'venture-muted', text: 'No published Artifacts yet.' }) : null,
  ]);
}

function argoStatusCard(qid, memory) {
  return h('section', { class: 'venture-card ArgoStatusCard' }, [
    h('h3', { text: 'Argo Status' }),
    h('div', { class: 'venture-muted', text: `${memory.length} Voyage Memory entries visible.` }),
    caps.can_review_artifacts ? h('button', { class: 'venture-btn', type: 'button', text: 'Run Synthesis', onclick: async () => {
      await fetch(`${API_BASE}/api/quests/${encodeURIComponent(qid)}/argo-synthesis/run`, { method: 'POST', credentials: 'same-origin' });
      await renderRightRail();
    } }) : null,
  ]);
}

function closeVentureModal(modal, backdrop) {
  modal?.remove();
  backdrop?.remove();
}

async function launchRegularChatFromWizard(modal, backdrop, status) {
  let created = false;
  if (window.createDirectChatFromPreferredModel) {
    created = !!await window.createDirectChatFromPreferredModel();
  } else if (window.sessionModule?.createDirectChat) {
    const res = await fetch(`${API_BASE}/api/default-chat`, { credentials: 'same-origin' });
    if (res.ok) {
      const dc = await res.json().catch(() => null);
      if (dc?.endpoint_url && dc?.model) {
        await window.sessionModule.createDirectChat(dc.endpoint_url, dc.model, dc.endpoint_id, { source: 'venture-session-wizard' });
        created = true;
      }
    }
  }
  if (created && window.sessionModule?.hasPendingChat?.() && window.sessionModule?.materializePendingSession) {
    await window.sessionModule.materializePendingSession({ source: 'venture-session-wizard', openInFullView: true });
  }
  if (created || window.sessionModule?.getCurrentSessionId?.()) {
    closeVentureModal(modal, backdrop);
  } else if (status) {
    status.textContent = 'Unable to start a regular chat.';
  }
}

function selectedCheckboxValues(root, name) {
  return Array.from(root.querySelectorAll(`input[name="${name}"]:checked`))
    .map(input => input.value)
    .filter(Boolean);
}

function sourceDisplayName(type, config, emailAccountsById) {
  if (type === 'website') return config.urls?.length === 1 ? config.urls[0] : 'Website evidence';
  if (type === 'file') return config.files?.length === 1 ? config.files[0].name : 'Selected files';
  if (type === 'email') {
    const names = (config.account_ids || [config.account_id])
      .map(id => emailAccountsById.get(id)?.name || emailAccountsById.get(id)?.from_address || id)
      .filter(Boolean);
    return names.length === 1 ? names[0] : 'Email evidence';
  }
  return 'Quest Source';
}

function buildSourcePayload(modal, emailAccountsById) {
  const type = modal.querySelector('[name="source_type"]')?.value || 'website';
  if (type === 'website') {
    const urls = String(modal.querySelector('[name="source_urls"]')?.value || '')
      .split(/\n+/)
      .map(v => v.trim())
      .filter(Boolean);
    if (!urls.length) throw new Error('Add at least one website URL.');
    const config = { urls, url: urls[0], summary: urls.join('\n') };
    return { type, config, extraSources: [] };
  }
  if (type === 'file') {
    const files = Array.from(modal.querySelector('[name="source_files"]')?.files || []);
    if (!files.length) throw new Error('Select at least one file.');
    const fileRefs = files.map(file => ({
      name: file.name,
      size: file.size,
      type: file.type || 'application/octet-stream',
      last_modified: file.lastModified || null,
    }));
    const config = {
      files: fileRefs,
      references: fileRefs.map(file => file.name),
      summary: fileRefs.map(file => file.name).join(', '),
    };
    return { type, config, extraSources: [] };
  }
  if (type === 'email') {
    const accountIds = selectedCheckboxValues(modal, 'email_accounts');
    if (!accountIds.length) throw new Error('Select at least one configured email account.');
    const baseConfig = id => ({
      account_id: id,
      account_ids: accountIds,
      scope: { mailbox: 'INBOX', query: 'in:anywhere' },
    });
    return {
      type,
      config: baseConfig(accountIds[0]),
      extraSources: accountIds.slice(1).map(id => ({
        source_type: 'email',
        source_mode: 'dynamic',
        access_mode: 'captain_only',
        display_name: sourceDisplayName('email', { account_id: id }, emailAccountsById),
        configuration: baseConfig(id),
      })),
    };
  }
  throw new Error('Choose a Quest Source type.');
}

function renderCheckboxList(container, items, name, emptyText, labelFor) {
  container.innerHTML = '';
  if (!items.length) {
    container.appendChild(h('div', { class: 'venture-muted', text: emptyText }));
    return;
  }
  items.forEach(item => {
    const value = String(item.id || item.username || '');
    container.appendChild(h('label', { class: 'venture-check-row' }, [
      h('input', { type: 'checkbox', name, value }),
      h('span', { text: labelFor(item) }),
    ]));
  });
}

async function loadWizardOptions(modal) {
  const [emailData, userData] = await Promise.all([
    getJson('/api/email/accounts'),
    getJson('/api/auth/users'),
  ]);
  const emails = (emailData?.accounts || []).filter(account => account.enabled !== false);
  const users = (userData?.users || []).filter(user => !user.is_admin);
  const emailList = modal.querySelector('[data-email-list]');
  const shipmateList = modal.querySelector('[data-shipmate-list]');
  renderCheckboxList(emailList, emails, 'email_accounts', 'No configured email accounts available.', account => account.name || account.from_address || account.imap_user || account.id);
  renderCheckboxList(shipmateList, users, 'shipmates', 'No regular users available.', user => user.username);
  return new Map(emails.map(account => [String(account.id), account]));
}

function syncQuestWizardSourcePanel(modal) {
  const type = modal.querySelector('[name="source_type"]')?.value || 'website';
  modal.querySelectorAll('[data-source-panel]').forEach(panel => {
    panel.hidden = panel.dataset.sourcePanel !== type;
  });
}

function syncSelectedFiles(modal) {
  const list = modal.querySelector('[data-file-list]');
  if (!list) return;
  const files = Array.from(modal.querySelector('[name="source_files"]')?.files || []);
  list.innerHTML = '';
  files.forEach(file => list.appendChild(h('div', { class: 'venture-muted', text: `${file.name} · ${Math.ceil(file.size / 1024)} KB` })));
}

async function createQuestFromWizard(modal, status, emailAccountsById) {
  const title = String(modal.querySelector('[name="title"]')?.value || '').trim();
  const goal = String(modal.querySelector('[name="exploration_goal"]')?.value || '').trim();
  if (!title) throw new Error('Quest title is required.');
  if (!goal) throw new Error('Goal is required.');
  const { type, config, extraSources } = buildSourcePayload(modal, emailAccountsById);
  const displayName = sourceDisplayName(type, config, emailAccountsById);
  const payload = {
    title,
    exploration_goal: goal,
    source: {
      source_type: type,
      source_mode: type === 'email' ? 'dynamic' : 'static',
      access_mode: type === 'email' ? 'captain_only' : 'shared_read',
      display_name: displayName,
      configuration: config,
    },
    shipmates: selectedCheckboxValues(modal, 'shipmates'),
  };
  const res = await fetch(`${API_BASE}/api/quests`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || 'Quest creation failed.');
  const data = await res.json();
  const questId = data.quest?.id;
  if (!questId) throw new Error('Quest creation did not return a Quest ID.');
  for (const source of extraSources) {
    const sourceRes = await fetch(`${API_BASE}/api/quests/${encodeURIComponent(questId)}/sources`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(source),
    });
    if (!sourceRes.ok && status) {
      status.textContent = 'Quest created, but one additional email source could not be added.';
    }
  }
  currentQuestId = questId;
  history.replaceState(null, '', `#${questId}`);
  await window.sessionModule?.loadSessions?.();
  await window.sessionModule?.selectSession?.(questId);
  window.dispatchEvent(new CustomEvent('odysseus:session-materialized', {
    detail: { sessionId: questId, name: title, openInFullView: true },
  }));
  await renderRightRail();
}

function openSessionWizard() {
  if (!caps?.can_create_quest) return;
  installStyles();
  document.querySelectorAll('.QuestCreationWizard, .venture-modal-backdrop[data-venture-session-wizard]').forEach(el => el.remove());
  const backdrop = h('div', { class: 'venture-modal-backdrop', 'data-venture-session-wizard': 'true' });
  const modal = h('form', { class: 'venture-modal QuestCreationWizard', role: 'dialog', 'aria-modal': 'true', 'aria-label': 'Create session' });
  modal.innerHTML = `
    <h2>Create Session</h2>
    <div class="venture-choice-grid" role="radiogroup" aria-label="Session type">
      <label class="venture-choice"><input type="radio" name="session_type" value="chat" checked><strong>Regular chat</strong><span class="venture-muted">Start a chat session without Quest configuration.</span></label>
      <label class="venture-choice"><input type="radio" name="session_type" value="quest"><strong>Quest</strong><span class="venture-muted">Launch an evidence-guided Venture Quest.</span></label>
    </div>
    <div data-chat-fields>
      <div class="venture-muted">Regular chats use the existing chat workspace and do not require Quest setup.</div>
    </div>
    <div data-quest-fields hidden>
      <div class="venture-form-grid">
        <div>
          <label>Quest title</label>
          <input name="title" autocomplete="off">
        </div>
        <div>
          <label>Predefined source</label>
          <select name="source_type">
            <option value="website">Website</option>
            <option value="file">File</option>
            <option value="email">Email</option>
          </select>
        </div>
        <div class="venture-full-span">
          <label>Goal</label>
          <textarea name="exploration_goal"></textarea>
        </div>
      </div>
      <div class="venture-source-panel" data-source-panel="website">
        <label>Website URLs</label>
        <textarea name="source_urls" placeholder="https://example.com/research&#10;https://example.com/context"></textarea>
      </div>
      <div class="venture-source-panel" data-source-panel="file" hidden>
        <label>Files from disk</label>
        <input type="file" name="source_files" multiple>
        <div class="venture-file-list" data-file-list></div>
      </div>
      <div class="venture-source-panel" data-source-panel="email" hidden>
        <label>Configured emails</label>
        <div class="venture-check-list" data-email-list><div class="venture-muted">Loading email accounts...</div></div>
      </div>
      <label>Shipmates</label>
      <div class="venture-check-list" data-shipmate-list><div class="venture-muted">Loading regular users...</div></div>
    </div>
    <div class="venture-modal-actions">
      <button type="button" class="venture-btn" data-cancel>Cancel</button>
      <button type="submit" class="venture-btn primary" data-submit>Create</button>
    </div>
    <div class="venture-muted" aria-live="polite"></div>
  `;
  document.body.appendChild(backdrop);
  document.body.appendChild(modal);
  let emailAccountsById = new Map();
  const status = modal.querySelector('[aria-live]');
  const submit = modal.querySelector('[data-submit]');
  const syncType = () => {
    const isQuest = modal.querySelector('[name="session_type"]:checked')?.value === 'quest';
    modal.querySelector('[data-chat-fields]').hidden = isQuest;
    modal.querySelector('[data-quest-fields]').hidden = !isQuest;
    submit.textContent = isQuest ? 'Launch Quest' : 'Start Chat';
  };
  modal.querySelectorAll('[name="session_type"]').forEach(input => input.addEventListener('change', syncType));
  modal.querySelector('[name="source_type"]').addEventListener('change', () => syncQuestWizardSourcePanel(modal));
  modal.querySelector('[name="source_files"]').addEventListener('change', () => syncSelectedFiles(modal));
  modal.querySelector('[data-cancel]').addEventListener('click', () => closeVentureModal(modal, backdrop));
  backdrop.addEventListener('click', () => closeVentureModal(modal, backdrop));
  modal.addEventListener('submit', async e => {
    e.preventDefault();
    submit.disabled = true;
    status.textContent = '';
    try {
      const type = modal.querySelector('[name="session_type"]:checked')?.value || 'chat';
      if (type === 'chat') {
        await launchRegularChatFromWizard(modal, backdrop, status);
      } else {
        await createQuestFromWizard(modal, status, emailAccountsById);
        closeVentureModal(modal, backdrop);
      }
    } catch (err) {
      status.textContent = err?.message || 'Session creation failed.';
    } finally {
      submit.disabled = false;
    }
  });
  syncType();
  syncQuestWizardSourcePanel(modal);
  loadWizardOptions(modal).then(map => { emailAccountsById = map; }).catch(() => {
    status.textContent = 'Some setup options could not be loaded.';
  });
  setTimeout(() => modal.querySelector('[name="session_type"]')?.focus(), 0);
}

window.argosVentureOpenSessionWizard = openSessionWizard;

async function openArtifactDraft(proposalId) {
  const data = await getJson(`/api/library/artifact-drafts/${encodeURIComponent(proposalId)}`);
  const draft = data?.draft;
  if (!draft) return;
  const modal = h('div', { class: 'venture-modal ArtifactDraftPreview' });
  modal.innerHTML = `<h2></h2><pre style="white-space:pre-wrap;max-height:52vh;overflow:auto;"></pre><div class="venture-modal-actions"><button class="venture-btn" data-close>Close</button><button class="venture-btn" data-decline>Decline</button><button class="venture-btn primary" data-publish>Publish to Quest</button></div><div class="venture-muted" aria-live="polite"></div>`;
  modal.querySelector('h2').textContent = draft.title;
  modal.querySelector('pre').textContent = draft.document?.content || draft.summary || '';
  modal.querySelector('[data-close]').addEventListener('click', () => modal.remove());
  modal.querySelector('[data-decline]').addEventListener('click', async () => {
    await fetch(`${API_BASE}/api/quests/${encodeURIComponent(draft.session_id)}/artifact-proposals/${encodeURIComponent(draft.id)}/decline`, { method: 'POST', credentials: 'same-origin' });
    modal.remove(); renderRightRail();
  });
  modal.querySelector('[data-publish]').addEventListener('click', async () => {
    await fetch(`${API_BASE}/api/quests/${encodeURIComponent(draft.session_id)}/artifact-proposals/${encodeURIComponent(draft.id)}/publish`, { method: 'POST', credentials: 'same-origin' });
    modal.remove(); renderRightRail();
  });
  document.body.appendChild(modal);
}

async function initVenture() {
  caps = await loadCapabilities();
  window.argosVentureCapabilities = caps;
  if (!caps?.is_venture) return;
  document.body.classList.add('argos-venture', caps.role === 'captain' ? 'venture-captain' : 'venture-shipmate');
  installStyles();
  applyTerminology();
  hideVentureNewChatShortcuts();
  installShipmateCaptureGuards();
  hardDisableShipmateControls();
  setInterval(() => {
    hideVentureNewChatShortcuts();
    hardDisableShipmateControls();
  }, 1000);
  await renderRightRail();
  window.addEventListener('hashchange', () => loadQuest(selectedQuestId()));
  window.addEventListener('odysseus:session-selected', e => loadQuest(e.detail?.id || selectedQuestId()));
  window.addEventListener('argos-venture:review-artifact-draft', e => openArtifactDraft(e.detail?.proposalId));
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initVenture, { once: true });
} else {
  initVenture();
}

export default { initVenture };
