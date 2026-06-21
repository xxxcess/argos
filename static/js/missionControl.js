// static/js/missionControl.js
// Session Control dashboard layered over the existing chat/session pipeline.

import uiModule from './ui.js';
import sessionModule from './sessions.js';
import chatModule from './chat.js';
import voiceRecorderModule from './voiceRecorder.js';
import markdownModule from './markdown.js';

const STATUS_LABELS = {
  idle: 'Ready',
  listening: 'Listening',
  transcribing: 'Transcribing',
  streaming: 'Responding',
  speaking: 'Speaking',
  complete: 'Complete',
  stopped: 'Stopped',
  error: 'Needs Attention',
  archived: 'Archived',
};

const state = {
  mode: 'new-mission',
  activePanelSessionId: null,
  isActiveMissionOpen: false,
  dashboardVisible: true,
  filter: 'all',
  sort: 'active',
  query: '',
  chatsPage: 0,
  operationsOpen: false,
  snapshots: new Map(),
};

const els = {};
let _composerHome = null;
let _deckResizeTimer = null;
let _activePanelRenderRaf = null;

function esc(value) {
  return uiModule.esc(String(value == null ? '' : value));
}

function icon(name) {
  const icons = {
    plus: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>',
    close: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    stop: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="6" width="12" height="12" rx="2"/></svg>',
    open: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>',
    chevronLeft: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="15 18 9 12 15 6"/></svg>',
    chevronRight: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="9 18 15 12 9 6"/></svg>',
    menu: '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>',
    search: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><circle cx="10" cy="10" r="7"/><path d="M21 21l-4.35-4.35"/></svg>',
  };
  return icons[name] || '';
}

function sessionTitle(sessionId) {
  if (!sessionId) return '';
  const s = sessionModule.getSessions().find(item => String(item.id) === String(sessionId));
  const snap = snapshotFor(sessionId);
  return s && s.name || snap && snap.title || '';
}

function sessionMeta(sessionId) {
  return sessionModule.getSessions().find(item => String(item.id) === String(sessionId)) || null;
}

function labelForStatus(status) {
  return STATUS_LABELS[status || 'idle'] || STATUS_LABELS.idle;
}

function statusClass(statusLabel, snap) {
  const label = String(statusLabel || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  return [
    'mission-status-pill',
    label ? `status-${label}` : '',
    snap?.needsAttention ? 'attention' : '',
  ].filter(Boolean).join(' ');
}

function snapshotFor(sessionId) {
  return state.snapshots.get(String(sessionId)) || chatModule.getSessionStreamSnapshot?.(sessionId) || null;
}

function renderBubbleContent(element, text) {
  if (!element) return;
  const raw = markdownModule.renderContent(String(text == null ? '' : text));
  element.dataset.raw = raw;
  element.innerHTML = markdownModule.processWithThinking(markdownModule.squashOutsideCode(raw));
  if (window.hljs) {
    element.querySelectorAll('pre code:not(.hljs)').forEach(block => window.hljs.highlightElement(block));
  }
  if (markdownModule.renderMermaid) {
    try { markdownModule.renderMermaid(element); } catch (_) {}
  }
}

function stripIds(root, options = {}) {
  if (!root || !root.querySelectorAll) return root;
  root.removeAttribute?.('id');
  root.querySelectorAll('[id]').forEach(el => el.removeAttribute('id'));
  root.querySelectorAll('.msg-footer, .ctx-popup, .msg-overflow-menu, .memory-used-detail').forEach(el => el.remove());
  if (options.terminal) {
    root.querySelectorAll('.agent-thinking-dots, .live-think-spinner-slot').forEach(el => el.remove());
    root.querySelectorAll('.live-think-header-text').forEach(el => {
      if ((el.textContent || '').trim().toLowerCase().startsWith('thinking')) {
        el.textContent = 'View thinking process';
      }
    });
  }
  root.querySelectorAll('.thinking-section').forEach((section, index) => {
    const header = section.querySelector('.thinking-header');
    const content = section.querySelector('.thinking-content');
    const toggle = section.querySelector('.thinking-toggle');
    if (!header || !content || !toggle) return;
    const id = `mission-thinking-${Date.now()}-${index}-${Math.random().toString(36).slice(2, 8)}`;
    header.dataset.thinkingId = id;
    content.id = id;
    toggle.id = `${id}-toggle`;
  });
  return root;
}

function isTerminalStatus(status) {
  return ['complete', 'stopped', 'error'].includes(String(status || '').toLowerCase());
}

function isMeaningfulMissionNode(node) {
  if (!node || !node.classList) return false;
  if (node.classList.contains('msg-user')) return false;
  if (node.classList.contains('agent-thread')) return true;
  if (node.classList.contains('agent-thinking-dots')) return true;
  if (!node.classList.contains('msg-ai')) return false;
  const body = node.querySelector('.body');
  if (!body) return false;
  if ((body.textContent || '').trim()) return true;
  return !!body.querySelector('.thinking-section, .agent-thinking-dots, .loading, .spinner, pre, code, details');
}

function mirrorLatestChatTurn(target, prompt, response, status) {
  if (!target) return false;
  const terminal = isTerminalStatus(status);
  const history = document.getElementById('chat-history');
  const nodes = history ? Array.from(history.children).filter(node =>
    node.nodeType === 1 &&
    (node.classList.contains('msg') || node.classList.contains('agent-thread'))
  ) : [];
  let start = -1;
  for (let i = nodes.length - 1; i >= 0; i -= 1) {
    if (nodes[i].classList.contains('msg-user')) {
      start = i;
      break;
    }
  }
  if (start >= 0) {
    const turn = [];
    for (let i = start + 1; i < nodes.length; i += 1) {
      if (i > start && nodes[i].classList.contains('msg-user')) break;
      if (terminal && nodes[i].classList.contains('agent-thinking-dots')) continue;
      if (isMeaningfulMissionNode(nodes[i])) turn.push(nodes[i]);
    }
    if (turn.length) {
      target.innerHTML = '';
      turn.forEach(node => {
        const clone = stripIds(node.cloneNode(true), { terminal });
        clone.classList.add('mission-panel-msg');
        clone.querySelectorAll('button, a, input, textarea, select').forEach(el => {
          if (el.closest('.agent-thread-header, details, summary')) return;
          el.setAttribute('tabindex', '-1');
        });
        target.appendChild(clone);
      });
      if (window.hljs) target.querySelectorAll('pre code:not(.hljs)').forEach(block => window.hljs.highlightElement(block));
      if (markdownModule.renderMermaid) {
        try { markdownModule.renderMermaid(target); } catch (_) {}
      }
      return true;
    }
  }

  target.innerHTML = `
    <div class="msg msg-ai mission-panel-msg">
      <div class="role">Assistant</div>
      <div class="body" data-mission-body="assistant"></div>
    </div>
  `;
  renderBubbleContent(target.querySelector('[data-mission-body="assistant"]'), response);
  return false;
}

function hideFullChatWelcome() {
  document.getElementById('welcome-screen')?.classList.add('hidden');
  document.getElementById('chat-container')?.classList.remove('welcome-active');
}

function setDashboardVisible(visible) {
  state.dashboardVisible = !!visible;
  if (state.dashboardVisible) moveComposerIntoCommandBar();
  else restoreComposerToChat();
  document.body.classList.toggle('mission-dashboard-visible', state.dashboardVisible);
  if (els.root) els.root.hidden = !state.dashboardVisible;
  if (els.returnBtn) els.returnBtn.hidden = state.dashboardVisible;
  const history = document.getElementById('chat-history');
  const welcome = document.getElementById('welcome-screen');
  if (history) history.hidden = state.dashboardVisible;
  if (welcome) welcome.classList.toggle('hidden', state.dashboardVisible);
}

function moveComposerIntoCommandBar() {
  const bar = document.querySelector('.chat-input-bar');
  if (!bar || !els.composerSlot || els.composerSlot.contains(bar)) return;
  if (!_composerHome && bar.parentNode) {
    _composerHome = {
      parent: bar.parentNode,
      nextSibling: bar.nextSibling,
    };
  }
  els.composerSlot.appendChild(bar);
}

function restoreComposerToChat() {
  const bar = document.querySelector('.chat-input-bar');
  if (!bar || !_composerHome || !_composerHome.parent || bar.parentNode === _composerHome.parent) return;
  if (_composerHome.nextSibling && _composerHome.nextSibling.parentNode === _composerHome.parent) {
    _composerHome.parent.insertBefore(bar, _composerHome.nextSibling);
  } else {
    _composerHome.parent.appendChild(bar);
  }
  const input = document.getElementById('message');
  if (input) input.placeholder = 'Message Odysseus...';
  if (window._updateSendBtnIcon) setTimeout(window._updateSendBtnIcon, 0);
}

function buildShell() {
  const container = document.getElementById('chat-container');
  const topBar = container?.querySelector('.chat-top-bar');
  if (!container || !topBar || document.getElementById('mission-control')) return false;

  const root = document.createElement('section');
  root.id = 'mission-control';
  root.className = 'mission-control';
  root.setAttribute('aria-label', 'Session Control dashboard');
  root.innerHTML = `
    <header class="mission-dashboard-header">
      <div class="mission-dashboard-title"><svg class="welcome-boat" viewBox="0 0 32 32" aria-hidden="true"><path d="M16 4L16 22L6 22Z" fill="currentColor"/><path d="M16 8L16 22L24 22Z" fill="currentColor" opacity="0.6"/><path d="M4 24Q10 20 16 24Q22 28 28 24" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round"/></svg>Odysseus</div>
      <div class="mission-dashboard-subtitle">Session Control</div>
    </header>
    <div class="mission-command-bar" aria-label="Session command bar">
      <div class="mission-command-meta">
        <div>
          <div class="mission-mode-line">
            <span id="mission-mode-label" class="mission-status-pill">New Session</span>
            <span id="mission-target-title" class="mission-target-title"></span>
          </div>
          <div id="mission-draft-note" class="mission-draft-note" role="status" aria-live="polite"></div>
        </div>
      </div>
      <div id="mission-composer-slot"></div>
    </div>
    <section id="mission-active-panel" class="mission-active-panel" aria-labelledby="mission-active-title" aria-live="polite"></section>
    <section class="mission-section" aria-labelledby="mission-recent-title">
      <div class="mission-section-header">
        <h2 id="mission-recent-title">Recent Chats</h2>
        <div class="mission-recent-controls">
          <select id="mission-chat-filter" class="memory-sort-select" aria-label="Filter chats">
            <option value="all">All</option>
            <option value="streaming">Streaming</option>
            <option value="complete">Complete</option>
            <option value="attention">Needs Attention</option>
            <option value="pinned">Pinned</option>
            <option value="archived">Archived</option>
          </select>
          <select id="mission-chat-sort" class="memory-sort-select" aria-label="Sort chats">
            <option value="active">Last Active</option>
            <option value="newest">Newest</option>
            <option value="manual">Manual</option>
          </select>
          <button type="button" class="input-icon-btn" id="mission-chats-prev" aria-label="Previous chats">${icon('chevronLeft')}</button>
          <button type="button" class="input-icon-btn" id="mission-chats-next" aria-label="Next chats">${icon('chevronRight')}</button>
        </div>
      </div>
      <div id="mission-chats-deck" class="mission-card-deck"></div>
    </section>
    <div id="mission-operations-drawer" class="mission-operations-drawer" hidden role="dialog" aria-label="Operations"></div>
    <div id="mission-command-palette" class="mission-command-palette" hidden role="dialog" aria-label="Session commands"></div>
  `;
  topBar.insertAdjacentElement('afterend', root);

  els.root = root;
  els.modeLabel = root.querySelector('#mission-mode-label');
  els.targetTitle = root.querySelector('#mission-target-title');
  els.draftNote = root.querySelector('#mission-draft-note');
  els.composerSlot = root.querySelector('#mission-composer-slot');
  els.activePanel = root.querySelector('#mission-active-panel');
  els.chatsDeck = root.querySelector('#mission-chats-deck');
  els.operationsBtn = root.querySelector('#mission-operations-btn');
  els.opsCount = root.querySelector('#mission-ops-count');
  els.operationsDrawer = root.querySelector('#mission-operations-drawer');
  els.palette = root.querySelector('#mission-command-palette');
  els.search = root.querySelector('#mission-chat-search');
  els.filter = root.querySelector('#mission-chat-filter');
  els.sort = root.querySelector('#mission-chat-sort');
  ensureReturnButton(topBar);
  if (els.search) {
    els.search.value = '';
    els.search.defaultValue = '';
  }
  moveComposerIntoCommandBar();
  return true;
}

function ensureReturnButton(topBar) {
  const meta = topBar?.querySelector('.chat-meta-overlay');
  if (!topBar || !meta) return;
  if (document.getElementById('mission-return-btn')) {
    els.returnBtn = document.getElementById('mission-return-btn');
    if (els.returnBtn.parentElement !== meta) meta.insertBefore(els.returnBtn, meta.firstChild);
    return;
  }
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.id = 'mission-return-btn';
  btn.className = 'memory-toolbar-btn mission-return-btn';
  btn.setAttribute('aria-label', 'Back to Session Control');
  btn.title = 'Back to Session Control';
  btn.hidden = true;
  btn.innerHTML = `${icon('chevronLeft')} Session Control`;
  btn.addEventListener('click', () => {
    setDashboardVisible(true);
    updateAll();
    document.getElementById('message')?.focus();
  });
  meta.insertBefore(btn, meta.firstChild);
  els.returnBtn = btn;
}

function updateComposerMode() {
  if (!state.dashboardVisible) return;
  const input = document.getElementById('message');
  const activeSessionId = state.activePanelSessionId;
  const active = state.mode === 'active-mission' && activeSessionId && (sessionMeta(activeSessionId) || snapshotFor(activeSessionId));
  if (els.modeLabel) els.modeLabel.textContent = active ? 'Active Session' : 'New Session';
  if (els.targetTitle) els.targetTitle.textContent = active ? sessionTitle(activeSessionId) : '';
  if (input) input.placeholder = active ? 'Continue the active session...' : 'Start a new session...';
  if (window.aiTTSManager?.setActiveMissionSession) {
    window.aiTTSManager.setActiveMissionSession(active ? activeSessionId : null, active);
  }
}

function clearSessionChrome() {
  const metaEl = document.getElementById('current-meta');
  if (metaEl) metaEl.textContent = 'New Chat';
  const metaCountEl = document.getElementById('current-meta-count');
  if (metaCountEl) metaCountEl.textContent = '';
  const costEl = document.getElementById('session-cost-display');
  if (costEl) {
    costEl.textContent = '';
    costEl.style.display = 'none';
  }
}

function resetToNewSessionUi() {
  state.mode = 'new-mission';
  state.activePanelSessionId = null;
  state.isActiveMissionOpen = false;
  if (els.modeLabel) els.modeLabel.textContent = 'New Session';
  if (els.targetTitle) els.targetTitle.textContent = '';
  if (els.activePanel) {
    els.activePanel.hidden = true;
    els.activePanel.innerHTML = '';
    delete els.activePanel.dataset.shellKey;
    delete els.activePanel.dataset.sessionId;
  }
  clearSessionChrome();
  const input = document.getElementById('message');
  if (input) input.placeholder = 'Start a new session...';
  if (window.aiTTSManager?.setActiveMissionSession) {
    window.aiTTSManager.setActiveMissionSession(null, false);
  }
}

function startNewMissionDraft() {
  resetToNewSessionUi();
  setDashboardVisible(true);
  sessionModule.setCurrentSessionId(null);
  const history = document.getElementById('chat-history');
  if (history) history.innerHTML = '';
  updateAll();
  document.getElementById('message')?.focus();
}

function focusMission(sessionId) {
  if (!sessionId) return;
  state.activePanelSessionId = sessionId;
  state.isActiveMissionOpen = true;
  state.mode = 'active-mission';
  sessionModule.setCurrentSessionId(sessionId);
  setDashboardVisible(true);
  updateAll();
  document.getElementById('message')?.focus();
}

function viewFullConversation(sessionId) {
  if (!sessionId) return;
  setDashboardVisible(false);
  hideFullChatWelcome();
  if (String(sessionModule.getCurrentSessionId?.() || '') === String(sessionId)) return;
  Promise.resolve(sessionModule.selectSession(sessionId)).finally(hideFullChatWelcome);
}

function closeActiveMission(options = {}) {
  const sessionId = state.activePanelSessionId;
  if (!sessionId) return;
  const silent = !!options.silent;
  const input = document.getElementById('message');
  const hadDraft = !!(input && input.value.trim());
  const snap = snapshotFor(sessionId);
  if (chatModule.detachCurrentStream && snap?.status === 'streaming' && String(sessionModule.getCurrentSessionId?.() || '') === String(sessionId)) {
    chatModule.detachCurrentStream(sessionId);
  }
  if (window.aiTTSManager?.stopForMissionClose) window.aiTTSManager.stopForMissionClose(sessionId);
  else if (window.aiTTSManager?.stop) window.aiTTSManager.stop();
  if (voiceRecorderModule.cancelForMissionClose) voiceRecorderModule.cancelForMissionClose(sessionId);
  else {
    try { voiceRecorderModule.stopConversationLoop?.('silent'); } catch (_) {}
    try { voiceRecorderModule.stopRecording?.(); } catch (_) {}
  }
  state.activePanelSessionId = null;
  state.isActiveMissionOpen = false;
  state.mode = 'new-mission';
  sessionModule.setCurrentSessionId(null);
  clearSessionChrome();
  if (els.draftNote) els.draftNote.textContent = hadDraft ? 'Draft is ready for a new session.' : '';
  setTimeout(() => { if (els.draftNote) els.draftNote.textContent = ''; }, 3500);
  if (!silent) uiModule.showToast('Session closed. Voice stopped. Background response continues.', 3200);
  updateAll();
  input?.focus();
}

function stopGeneration(sessionId) {
  if (!sessionId) return;
  if (chatModule.stopSessionGeneration) chatModule.stopSessionGeneration(sessionId);
  else chatModule.abortCurrentRequest(true);
  const snap = snapshotFor(sessionId);
  if (snap) {
    state.snapshots.set(String(sessionId), Object.assign({}, snap, { status: 'stopped', needsAttention: false }));
  }
  updateAll();
}

function endVoiceLoop(sessionId) {
  if (voiceRecorderModule.cancelForMissionClose) voiceRecorderModule.cancelForMissionClose(sessionId);
  else voiceRecorderModule.stopConversationLoop?.('manual');
  uiModule.showToast('Voice loop ended');
  updateAll();
}

function visibleCount(kind) {
  if (window.innerWidth <= 700) return 1;
  if (window.innerWidth <= 1050) return 2;
  return 3;
}

function sessionType(s) {
  if (s.mode === 'agent') return 'Agent';
  if (s.mode === 'research') return 'Research';
  if (s.has_documents) return 'Document';
  if (s.has_images) return 'Image';
  return 'Chat';
}

function cardStatus(s) {
  const snap = snapshotFor(s.id);
  if (snap?.status === 'streaming') return snap.isBackground ? 'Responding in Background' : 'Responding';
  if (snap?.status === 'listening') return 'Listening';
  if (snap?.status === 'transcribing') return 'Transcribing';
  if (snap?.status === 'speaking') return 'Speaking';
  if (snap?.status === 'error') return 'Needs Attention';
  if (snap?.status === 'stopped') return 'Stopped';
  if (s.archived) return 'Archived';
  if (snap?.status === 'complete') return 'Complete';
  if (state.isActiveMissionOpen && state.activePanelSessionId && String(state.activePanelSessionId) === String(s.id)) return 'Active Session';
  return 'Idle';
}

function filterSessions(sessions) {
  const q = state.query.trim().toLowerCase();
  let rows = sessions.filter(s => s && s.id && (state.filter === 'archived' ? s.archived : !s.archived));
  rows = rows.filter(s => {
    const snap = snapshotFor(s.id);
    const status = snap?.status || (s.archived ? 'archived' : 'idle');
    if (state.filter === 'streaming' && status !== 'streaming') return false;
    if (state.filter === 'complete' && status !== 'complete') return false;
    if (state.filter === 'attention' && !(snap?.needsAttention || status === 'error')) return false;
    if (state.filter === 'pinned' && !(s.is_important || s.favorite || s.pinned)) return false;
    return true;
  });
  if (q) {
    rows = rows.filter(s => {
      const snap = snapshotFor(s.id);
      return [s.name, s.model, sessionType(s), snap?.latestPrompt, snap?.latestResponse]
        .some(v => String(v || '').toLowerCase().includes(q));
    });
  }
  if (state.sort === 'newest') rows.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0));
  else if (state.sort === 'manual') rows.sort((a, b) => Number(!!(b.is_important || b.favorite)) - Number(!!(a.is_important || a.favorite)));
  else rows.sort((a, b) => new Date(b.last_message_at || b.updated_at || b.created_at || 0) - new Date(a.last_message_at || a.updated_at || a.created_at || 0));
  return rows;
}

function renderChats() {
  if (!els.chatsDeck) return;
  if (els.search && state.query === '' && els.search.value !== '') {
    els.search.value = '';
  }
  const rows = filterSessions(sessionModule.getSessions());
  const count = visibleCount('chats');
  const maxPage = Math.max(0, Math.ceil(rows.length / count) - 1);
  state.chatsPage = Math.min(state.chatsPage, maxPage);
  const page = rows.slice(state.chatsPage * count, state.chatsPage * count + count);
  if (!page.length) {
    els.chatsDeck.innerHTML = '<div class="mission-empty">No chats match this view.</div>';
  } else {
    els.chatsDeck.innerHTML = page.map(s => {
      const snap = snapshotFor(s.id);
      const status = cardStatus(s);
      const prompt = snap?.latestPrompt || s.lastUserExcerpt || '';
      return `
        <article class="mission-chat-card" data-session-id="${esc(s.id)}">
          <div class="mission-card-top">
            <span class="mission-card-title">${esc(s.name || 'Untitled')}</span>
          </div>
          <div class="mission-card-meta">${esc((s.model || '').split('/').pop() || 'Model')} · ${esc(sessionType(s))}</div>
          <div class="mission-card-preview">${esc(prompt || 'No prompt preview')}</div>
          <div class="mission-card-actions">
            <span class="${esc(statusClass(status, snap))}">${esc(status)}</span>
            <button type="button" class="memory-toolbar-btn mission-open-chat">${icon('open')} Open Chat</button>
          </div>
        </article>
      `;
    }).join('');
  }
  els.chatsDeck.querySelectorAll('.mission-open-chat').forEach(btn => btn.addEventListener('click', e => viewFullConversation(e.target.closest('[data-session-id]').dataset.sessionId)));
  const prev = document.getElementById('mission-chats-prev');
  const next = document.getElementById('mission-chats-next');
  if (prev) prev.disabled = state.chatsPage <= 0;
  if (next) next.disabled = state.chatsPage >= maxPage;
}

function renderActivePanel() {
  if (!els.activePanel) return;
  if (!state.isActiveMissionOpen || !state.activePanelSessionId) {
    els.activePanel.hidden = true;
    els.activePanel.innerHTML = '';
    return;
  }
  const sid = state.activePanelSessionId;
  const s = sessionMeta(sid);
  const snap = snapshotFor(sid);
  if (!s && !snap) {
    resetToNewSessionUi();
    return;
  }
  const status = snap?.status || 'idle';
  const prompt = snap?.latestPrompt || 'No prompt captured yet.';
  const response = snap?.latestResponse || 'Waiting for response activity.';
  const previousChat = els.activePanel.querySelector('.mission-panel-chat');
  const previousScrollTop = previousChat ? previousChat.scrollTop : 0;
  const shouldFollow = !previousChat ||
    previousChat.scrollTop + previousChat.clientHeight >= previousChat.scrollHeight - 32;
  els.activePanel.hidden = false;
  const shellKey = `${sid}:${status}:${snap?.needsAttention ? 'attention' : 'normal'}`;
  if (els.activePanel.dataset.shellKey !== shellKey) {
    els.activePanel.dataset.shellKey = shellKey;
    els.activePanel.dataset.sessionId = sid;
    els.activePanel.innerHTML = `
      <div class="mission-panel-head">
        <div>
          <h2 id="mission-active-title">${esc(sessionTitle(sid))}</h2>
          <div class="mission-card-meta" data-mission-panel-meta>${esc((s?.model || snap?.model || '').split('/').pop() || 'Model')} · ${esc(labelForStatus(status))}</div>
        </div>
        <span class="${esc(statusClass(labelForStatus(status), snap))}" data-mission-panel-status>${esc(labelForStatus(status))}</span>
      </div>
      <div class="mission-panel-chat" aria-label="Latest session exchange"></div>
      <div class="mission-activity-strip" aria-label="Activity">
        ${['Preparing','Tool call running','Research active','Listening','Transcribing','Responding','Speaking','Complete','Stopped','Error'].map(label => `<span class="${label.toLowerCase().includes(labelForStatus(status).toLowerCase()) ? 'active' : ''}">${label}</span>`).join('')}
      </div>
      <div class="mission-panel-actions">
        <button type="button" class="memory-toolbar-btn" id="mission-view-full">${icon('open')} View Full Conversation</button>
        <button type="button" class="memory-toolbar-btn" id="mission-close-active">${icon('close')} Close Session</button>
      </div>
    `;
    els.activePanel.querySelector('#mission-view-full')?.addEventListener('click', () => viewFullConversation(sid));
    els.activePanel.querySelector('#mission-close-active')?.addEventListener('click', closeActiveMission);
  }
  const chatMirror = els.activePanel.querySelector('.mission-panel-chat');
  const history = document.getElementById('chat-history');
  const renderKey = `${sid}:${status}:${response.length}:${history ? history.children.length : 0}`;
  if (!(isTerminalStatus(status) && chatMirror?.dataset.renderKey === renderKey)) {
    mirrorLatestChatTurn(chatMirror, prompt, response, status);
    if (chatMirror) chatMirror.dataset.renderKey = renderKey;
  }
  if (chatMirror) {
    if (shouldFollow) chatMirror.scrollTop = chatMirror.scrollHeight;
    else chatMirror.scrollTop = previousScrollTop;
  }
}

function operationsItems() {
  const items = [];
  state.snapshots.forEach(snap => {
    if (snap.status === 'streaming' || snap.isBackground || snap.unreadCompletion || snap.needsAttention) {
      items.push(snap);
    }
  });
  chatModule.getBackgroundStreams?.().forEach(snap => {
    if (!items.some(item => String(item.sessionId) === String(snap.sessionId))) items.push(snap);
  });
  return items.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
}

function renderOperations() {
  const items = operationsItems();
  if (els.opsCount) els.opsCount.textContent = String(items.length);
  if (!els.operationsDrawer) return;
  els.operationsDrawer.hidden = !state.operationsOpen;
  if (!state.operationsOpen) return;
  els.operationsDrawer.innerHTML = `
    <div class="mission-drawer-head">
      <h2>Operations</h2>
      <button type="button" class="input-icon-btn" id="mission-close-ops" aria-label="Close operations">${icon('close')}</button>
    </div>
    ${items.length ? items.map(item => `
      <div class="mission-operation-item" data-session-id="${esc(item.sessionId)}">
        <div>
          <strong>${esc(item.title || sessionTitle(item.sessionId))}</strong>
          <span>${esc(labelForStatus(item.status))}${item.isBackground ? ' · background' : ''}</span>
        </div>
        <div class="mission-operation-actions">
          <button type="button" class="memory-toolbar-btn mission-op-resume">Resume Session</button>
          <button type="button" class="memory-toolbar-btn mission-op-open">Open Chat</button>
          ${item.status === 'streaming' ? '<button type="button" class="memory-toolbar-btn danger mission-op-stop">Stop</button>' : ''}
        </div>
      </div>
    `).join('') : '<div class="mission-empty">No background operations.</div>'}
  `;
  els.operationsDrawer.querySelector('#mission-close-ops')?.addEventListener('click', () => { state.operationsOpen = false; renderOperations(); });
  els.operationsDrawer.querySelectorAll('.mission-op-resume').forEach(btn => btn.addEventListener('click', e => focusMission(e.target.closest('[data-session-id]').dataset.sessionId)));
  els.operationsDrawer.querySelectorAll('.mission-op-open').forEach(btn => btn.addEventListener('click', e => viewFullConversation(e.target.closest('[data-session-id]').dataset.sessionId)));
  els.operationsDrawer.querySelectorAll('.mission-op-stop').forEach(btn => btn.addEventListener('click', e => stopGeneration(e.target.closest('[data-session-id]').dataset.sessionId)));
}

function renderPalette() {
  if (!els.palette || els.palette.hidden) return;
  const rows = [
    ['Start New Session', startNewMissionDraft],
    ['Close Active Session', closeActiveMission],
    ['Stop Active Generation', () => stopGeneration(state.activePanelSessionId)],
    ['End Voice Loop', () => endVoiceLoop(state.activePanelSessionId)],
    ['Open Operations', () => { state.operationsOpen = true; els.palette.hidden = true; updateAll(); }],
    ['Search Chats', () => { els.palette.hidden = true; els.search?.focus(); }],
  ];
  els.palette.innerHTML = `<div class="mission-command-list">${rows.map(([label], i) => `<button type="button" data-command="${i}">${esc(label)}</button>`).join('')}</div>`;
  els.palette.querySelectorAll('[data-command]').forEach(btn => {
    btn.addEventListener('click', () => {
      const fn = rows[Number(btn.dataset.command)]?.[1];
      els.palette.hidden = true;
      if (fn) fn();
    });
  });
}

function updateAll() {
  updateComposerMode();
  renderActivePanel();
  renderChats();
  renderOperations();
}

function bindEvents() {
  document.getElementById('mission-chats-prev')?.addEventListener('click', () => { state.chatsPage = Math.max(0, state.chatsPage - 1); renderChats(); });
  document.getElementById('mission-chats-next')?.addEventListener('click', () => { state.chatsPage += 1; renderChats(); });
  els.operationsBtn?.addEventListener('click', () => { state.operationsOpen = !state.operationsOpen; renderOperations(); });
  els.search?.addEventListener('input', e => { state.query = e.target.value || ''; state.chatsPage = 0; renderChats(); });
  els.filter?.addEventListener('change', e => { state.filter = e.target.value; state.chatsPage = 0; renderChats(); });
  els.sort?.addEventListener('change', e => { state.sort = e.target.value; state.chatsPage = 0; renderChats(); });

  document.addEventListener('odysseus:mission-stream-start', e => {
    const sid = e.detail?.sessionId;
    if (!sid) return;
    state.activePanelSessionId = sid;
    state.isActiveMissionOpen = true;
    state.mode = 'active-mission';
    setDashboardVisible(true);
    updateAll();
  });

  chatModule.subscribeToAllStreams?.(snapshot => {
    if (!snapshot?.sessionId) return;
    state.snapshots.set(String(snapshot.sessionId), snapshot);
    updateAll();
  });

  document.addEventListener('odysseus:sessions-updated', () => {
    state.chatsPage = 0;
    renderChats();
    renderActivePanel();
    renderOperations();
  });

  document.addEventListener('odysseus:new-chat-start', () => {
    if (state.isActiveMissionOpen) closeActiveMission({ silent: true });
    resetToNewSessionUi();
    updateAll();
  });

  document.addEventListener('odysseus:new-chat-ready', () => {
    resetToNewSessionUi();
    updateAll();
  });

  const history = document.getElementById('chat-history');
  if (history) {
    const observer = new MutationObserver(() => {
      if (!state.dashboardVisible || !state.isActiveMissionOpen) return;
      if (_activePanelRenderRaf) return;
      _activePanelRenderRaf = requestAnimationFrame(() => {
        _activePanelRenderRaf = null;
        renderActivePanel();
      });
    });
    observer.observe(history, {
      childList: true,
      subtree: true,
      characterData: true,
      attributes: true,
      attributeFilter: ['class', 'data-raw'],
    });
  }

  document.addEventListener('keydown', e => {
    const mod = e.metaKey || e.ctrlKey;
    if (mod && e.shiftKey && e.key.toLowerCase() === 'k') {
      e.preventDefault();
      els.palette.hidden = !els.palette.hidden;
      renderPalette();
      els.palette.querySelector('button')?.focus();
      return;
    }
    if (e.key === 'Escape') {
      if (els.palette && !els.palette.hidden) { els.palette.hidden = true; return; }
      if (state.operationsOpen) { state.operationsOpen = false; renderOperations(); }
    }
  });

  window.addEventListener('resize', () => {
    clearTimeout(_deckResizeTimer);
    _deckResizeTimer = setTimeout(updateAll, 120);
  });
}

export function init() {
  if (!buildShell()) return;
  bindEvents();
  setDashboardVisible(true);
  updateAll();
}

const missionControlModule = { init, closeActiveMission, focusMission, viewFullConversation, startNewMissionDraft };
window.missionControlModule = missionControlModule;
export default missionControlModule;
