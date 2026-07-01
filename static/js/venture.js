// Argos Venture UI shell and capability gates.

const API_BASE = window.API_BASE || '';
let caps = null;
let currentQuestId = null;

const captainOnlySelectors = [
  '#rail-new-session', '#new-session-btn', '#overflow-attach-btn', '#overflow-doc-btn',
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
    .venture-modal { position:fixed; inset:7vh auto auto 50%; transform:translateX(-50%); width:min(720px, 94vw); max-height:86vh; overflow:auto; z-index:10000; background:var(--bg); color:var(--fg); border:1px solid var(--border); border-radius:8px; padding:16px; box-shadow:0 18px 60px rgba(0,0,0,.35); }
    .venture-modal label { display:block; font-size:12px; margin:10px 0 4px; opacity:.8; }
    .venture-modal input, .venture-modal textarea, .venture-modal select { width:100%; box-sizing:border-box; border:1px solid var(--border); background:var(--panel); color:var(--fg); border-radius:6px; padding:8px; font:inherit; }
    .venture-modal textarea { min-height:72px; resize:vertical; }
    .venture-modal-actions { display:flex; justify-content:flex-end; gap:8px; margin-top:12px; }
    @media (max-width: 900px) { .venture-right-rail { width:min(86vw, 360px); } }
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
  let rail = document.getElementById('venture-right-rail');
  if (!rail) {
    rail = h('aside', { id: 'venture-right-rail', class: 'venture-right-rail', 'aria-label': 'Quest context' });
    document.body.appendChild(rail);
  }
  const qid = selectedQuestId();
  rail.innerHTML = '';
  const toggle = h('button', { class: 'venture-rail-toggle', type: 'button', title: 'Collapse Quest context', text: '◂', onclick: () => rail.classList.toggle('collapsed') });
  rail.appendChild(toggle);
  rail.appendChild(h('div', { style: 'clear:both' }));
  if (!qid) {
    rail.appendChild(h('div', { class: 'venture-card' }, [
      h('h3', { text: 'Quests' }),
      h('div', { class: 'venture-muted', text: caps.can_create_quest ? 'Create or open a Quest to see its Current Bearing.' : 'Open an accepted Quest to see its Current Bearing.' }),
      caps.can_create_quest ? h('button', { class: 'venture-btn primary', type: 'button', text: 'New Quest', onclick: openQuestWizard }) : null,
    ]));
    return;
  }
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

function openQuestWizard() {
  const modal = h('form', { class: 'venture-modal QuestCreationWizard' });
  modal.innerHTML = `
    <h2>Create Quest</h2>
    <label>Quest title</label><input name="title" required>
    <label>Exploration goal</label><textarea name="exploration_goal" required></textarea>
    <label>Initial question or hypothesis</label><textarea name="initial_question"></textarea>
    <label>Desired outcome</label><textarea name="desired_outcome"></textarea>
    <label>Primary Quest Source name</label><input name="source_name" required>
    <label>Source type</label><select name="source_type"><option value="website">Website</option><option value="file">File</option><option value="document">Document</option><option value="database">Database</option><option value="email">Email</option></select>
    <label>Source reference or query</label><input name="source_ref" required>
    <label>Shipmates (comma-separated, optional)</label><input name="shipmates">
    <div class="venture-modal-actions"><button type="button" class="venture-btn" data-cancel>Cancel</button><button type="submit" class="venture-btn primary">Launch Quest</button></div>
    <div class="venture-muted" aria-live="polite"></div>
  `;
  modal.querySelector('[data-cancel]').addEventListener('click', () => modal.remove());
  modal.addEventListener('submit', async e => {
    e.preventDefault();
    const fd = new FormData(modal);
    const type = fd.get('source_type');
    const ref = fd.get('source_ref');
    const config = type === 'email'
      ? { account_id: ref, scope: { mailbox: 'INBOX', query: ref } }
      : { reference: ref, url: type === 'website' ? ref : undefined, summary: ref };
    const payload = {
      title: fd.get('title'),
      exploration_goal: fd.get('exploration_goal'),
      initial_question: fd.get('initial_question'),
      desired_outcome: fd.get('desired_outcome'),
      source: { source_type: type, display_name: fd.get('source_name'), configuration: config },
      shipmates: String(fd.get('shipmates') || '').split(',').map(s => s.trim()).filter(Boolean),
    };
    const res = await fetch(`${API_BASE}/api/quests`, { method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    if (!res.ok) {
      modal.querySelector('[aria-live]').textContent = (await res.json().catch(() => ({}))).detail || 'Quest creation failed.';
      return;
    }
    const data = await res.json();
    modal.remove();
    currentQuestId = data.quest.id;
    history.replaceState(null, '', `#${data.quest.id}`);
    await renderRightRail();
  });
  document.body.appendChild(modal);
}

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
  installShipmateCaptureGuards();
  hardDisableShipmateControls();
  setInterval(hardDisableShipmateControls, 1000);
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

