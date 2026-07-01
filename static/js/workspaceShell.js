import Storage from './storage.js';
import {
  canonicalSessionTabId,
  closeTabById,
  createInitialTabState,
  normalizeTabState,
  upsertSessionTab,
} from './workspaceTabs.js';

const TAB_STATE_KEY = 'argos-workspace-tabs-v1';
const SHELL_ENABLED_KEY = 'argos-workspace-shell-enabled';
const API_BASE = window.location.origin;

export function isWorkspaceShellEnabled() {
  try {
    return localStorage.getItem(SHELL_ENABLED_KEY) !== '0';
  } catch (_) {
    return true;
  }
}

let _state = createInitialTabState();
let _initialized = false;
let _activeView = 'inbox';
let _notificationOpen = false;
let _badgeTimer = null;
let _authStatus = null;

function _loadState() {
  _state = normalizeTabState(Storage.getJSON(TAB_STATE_KEY, createInitialTabState()), _sessions());
}

function _saveState() {
  Storage.setJSON(TAB_STATE_KEY, _state);
}

function _sessions() {
  try {
    return window.sessionModule?.getSessions?.() || [];
  } catch (_) {
    return [];
  }
}

function _currentSessionId() {
  try {
    return window.sessionModule?.getCurrentSessionId?.() || null;
  } catch (_) {
    return null;
  }
}

function _sessionById(sessionId) {
  return _sessions().find(s => String(s.id) === String(sessionId));
}

function _saveCurrentViewState() {
  const sid = _currentSessionId();
  if (!sid) return;
  const tabId = canonicalSessionTabId(sid);
  const tab = _state.tabs[tabId];
  if (!tab) return;
  const history = document.getElementById('chat-history');
  const input = document.getElementById('message');
  tab.view = {
    ...tab.view,
    scrollTop: history ? history.scrollTop : tab.view?.scrollTop,
    draft: input ? input.value : tab.view?.draft,
    updatedAt: Date.now(),
  };
  _saveState();
}

function _restoreSelectedViewState() {
  const sid = _currentSessionId();
  if (!sid) return;
  const tab = _state.tabs[canonicalSessionTabId(sid)];
  if (!tab || !tab.view) return;
  const input = document.getElementById('message');
  if (input && tab.view.draft && !input.value) input.value = tab.view.draft;
  const history = document.getElementById('chat-history');
  if (history && Number.isFinite(tab.view.scrollTop)) {
    requestAnimationFrame(() => { history.scrollTop = tab.view.scrollTop; });
  }
}

function _showDefaultHomeDashboard() {
  if (window.sessionModule?.showHome) window.sessionModule.showHome({ focus: false });
  else window.sessionModule?.setCurrentSessionId?.(null);
  window.sessionControlModule?.startNewMissionDraft?.();
}

function _setShellClasses() {
  const isHome = _state.selected === 'home' || !_currentSessionId();
  document.body.classList.add('workspace-shell-enabled');
  document.body.classList.toggle('workspace-home-active', isHome);
  document.body.classList.toggle('workspace-session-active', !isHome);
}

function _isQuestTab(tab) {
  if (!tab || tab.kind !== 'session') return false;
  try {
    return !!window.argosVentureIsQuestSession?.(tab.sessionId);
  } catch (_) {
    return false;
  }
}

function _iconFor(kind, tab = null) {
  if (kind === 'home') return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3z"/></svg>';
  if (_isQuestTab(tab)) return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 19 11.5 3l2.2 7.5L21 12l-6.9 2.2L11.5 21l-2-6.8z"/><path d="M11.5 3v18"/></svg>';
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
}

function _renderTabs() {
  const list = document.getElementById('workspace-tab-list');
  if (!list) return;
  list.innerHTML = '';
  for (const tabId of _state.order) {
    const tab = _state.tabs[tabId];
    if (!tab) continue;
    const btn = document.createElement('div');
    btn.className = 'workspace-tab';
    btn.dataset.tabId = tabId;
    btn.dataset.state = tab.state || 'idle';
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-selected', String(_state.selected === tabId));
    btn.setAttribute('tabindex', _state.selected === tabId ? '0' : '-1');
    btn.innerHTML = `
      <span class="workspace-tab-icon">${_iconFor(tab.kind, tab)}</span>
      <span class="workspace-tab-title"></span>
      ${tab.kind === 'session' ? '<button type="button" class="workspace-tab-menu-btn" aria-label="Chat controls" title="Chat controls"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="5" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="12" cy="19" r="1.8"/></svg></button>' : ''}
      <span class="workspace-tab-state" aria-hidden="true"></span>
      ${tab.pinned ? '' : '<span class="workspace-tab-close-wrap"><button type="button" class="workspace-tab-close" aria-label="Close tab">&times;</button></span>'}
    `;
    btn.querySelector('.workspace-tab-title').textContent = tab.title || 'Chat';
    btn.addEventListener('click', e => {
      if (e.target.closest('.workspace-tab-close, .workspace-tab-menu-btn')) return;
      _activateTab(tabId);
    });
    btn.addEventListener('keydown', e => {
      if (e.target !== btn || (e.key !== 'Enter' && e.key !== ' ')) return;
      e.preventDefault();
      _activateTab(tabId);
    });
    const menuBtn = btn.querySelector('.workspace-tab-menu-btn');
    if (menuBtn) menuBtn.addEventListener('click', e => {
      e.stopPropagation();
      _openChatControls(tabId);
    });
    const close = btn.querySelector('.workspace-tab-close');
    if (close) close.addEventListener('click', e => {
      e.stopPropagation();
      _closeTab(tabId);
    });
    list.appendChild(btn);
  }
  _setShellClasses();
}

async function _openChatControls(tabId) {
  const tab = _state.tabs[tabId];
  if (!tab || tab.kind !== 'session') return;
  if (_state.selected !== tabId) {
    await _activateTab(tabId);
  }
  const tabEl = Array.from(document.querySelectorAll('.workspace-tab'))
    .find(el => el.dataset.tabId === tabId);
  const anchor = tabEl?.querySelector('.workspace-tab-menu-btn') || tabEl;
  document.dispatchEvent(new CustomEvent('odysseus:open-chat-controls', {
    detail: { sessionId: tab.sessionId, anchor },
  }));
}

function _startTabRename(sessionId, currentName = '') {
  if (!sessionId) return false;
  const tabId = canonicalSessionTabId(sessionId);
  const tab = _state.tabs[tabId];
  const tabEl = Array.from(document.querySelectorAll('.workspace-tab'))
    .find(el => el.dataset.tabId === tabId);
  const title = tabEl?.querySelector('.workspace-tab-title');
  if (!tab || !title || title.querySelector('input')) return false;

  const original = tab.title || currentName || 'Chat';
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'workspace-tab-rename-input';
  input.value = original;
  title.textContent = '';
  title.appendChild(input);
  input.focus();
  input.select();

  const finish = async (commit) => {
    const nextName = input.value.trim();
    if (!commit || !nextName || nextName === original) {
      title.textContent = original;
      return;
    }
    const fd = new FormData();
    fd.append('name', nextName);
    try {
      const res = await fetch(`${API_BASE}/api/session/${encodeURIComponent(sessionId)}`, { method: 'PATCH', body: fd });
      if (!res.ok) throw new Error('rename failed');
      tab.title = nextName;
      const meta = _sessionById(sessionId);
      if (meta) meta.name = nextName;
      const currentMeta = document.getElementById('current-meta');
      if (currentMeta && String(_currentSessionId()) === String(sessionId)) currentMeta.textContent = nextName;
      _saveState();
      _renderTabs();
      window.sessionModule?.loadSessions?.();
    } catch (err) {
      title.textContent = original;
      window.uiModule?.showError?.('Failed to rename chat');
    }
  };

  const onBlur = () => finish(true);
  input.addEventListener('blur', onBlur, { once: true });
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      input.blur();
    } else if (ev.key === 'Escape') {
      ev.preventDefault();
      input.removeEventListener('blur', onBlur);
      finish(false);
    }
  });
  return true;
}

async function _activateTab(tabId) {
  _saveCurrentViewState();
  if (tabId === 'home') {
    _state.selected = 'home';
    _saveState();
    _showDefaultHomeDashboard();
    _renderTabs();
    document.dispatchEvent(new CustomEvent('odysseus:workspace-tab-activated', {
      detail: { tabId: 'home', kind: 'home' },
    }));
    return;
  }
  const tab = _state.tabs[tabId];
  if (!tab || tab.kind !== 'session') return;
  _state.selected = tabId;
  _saveState();
  _renderTabs();
  if (window.sessionControlModule?.viewFullConversation) {
    window.sessionControlModule.viewFullConversation(tab.sessionId);
  } else {
    await window.sessionModule?.selectSession?.(tab.sessionId);
  }
  _restoreSelectedViewState();
  document.dispatchEvent(new CustomEvent('odysseus:workspace-tab-activated', {
    detail: { tabId, kind: 'session', sessionId: tab.sessionId },
  }));
}

async function _closeTab(tabId) {
  if (tabId === 'home') return;
  const tab = _state.tabs[tabId];
  const dirty = !!tab?.view?.draft;
  const waiting = tab?.state === 'waiting-for-user' || tab?.state === 'active/running' || tab?.state === 'error';
  if (dirty || waiting) {
    const prompt = dirty
      ? 'Close this tab and keep the conversation? Your unsent draft in this tab will be hidden.'
      : 'Close this tab and keep the underlying task/session running?';
    let ok = true;
    if (window.uiModule?.styledConfirm) ok = await window.uiModule.styledConfirm(prompt, { confirmText: 'Close tab' });
    else ok = window.confirm(prompt);
    if (!ok) return;
  }
  const wasSelected = _state.selected === tabId;
  _state = closeTabById(_state, tabId);
  _saveState();
  _renderTabs();
  if (wasSelected) _activateTab(_state.selected);
}

function _syncFromSession({ selected = true } = {}) {
  const sid = _currentSessionId();
  _state = normalizeTabState(_state, _sessions());
  if (sid) {
    const meta = _sessionById(sid) || { id: sid, name: 'Chat' };
    _state = upsertSessionTab(_state, meta, { selected });
  } else if (selected) {
    _state.selected = 'home';
  }
  _saveState();
  _renderTabs();
}

function _setSessionRuntimeState(sessionId, visualState) {
  if (!sessionId) return;
  const tabId = canonicalSessionTabId(sessionId);
  const tab = _state.tabs[tabId];
  if (!tab) return;
  tab.state = visualState || 'idle';
  _saveState();
  _renderTabs();
}

function _wireKeyboard() {
  const list = document.getElementById('workspace-tab-list');
  if (!list) return;
  list.addEventListener('keydown', e => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', 'Delete', 'Backspace'].includes(e.key)) return;
    const tabs = Array.from(list.querySelectorAll('.workspace-tab'));
    const idx = tabs.findIndex(t => t.getAttribute('aria-selected') === 'true');
    let nextIdx = idx;
    if (e.key === 'ArrowLeft') nextIdx = Math.max(0, idx - 1);
    if (e.key === 'ArrowRight') nextIdx = Math.min(tabs.length - 1, idx + 1);
    if (e.key === 'Home') nextIdx = 0;
    if (e.key === 'End') nextIdx = tabs.length - 1;
    if (e.key === 'Delete' || e.key === 'Backspace') {
      const tabId = tabs[idx]?.dataset.tabId;
      if (tabId && tabId !== 'home') _closeTab(tabId);
      e.preventDefault();
      return;
    }
    if (nextIdx !== idx && tabs[nextIdx]) {
      e.preventDefault();
      tabs[nextIdx].focus();
      _activateTab(tabs[nextIdx].dataset.tabId);
    }
  });
}

function _buildShell() {
  if (document.getElementById('workspace-shell')) return;
  const shell = document.createElement('header');
  shell.id = 'workspace-shell';
  shell.className = 'workspace-shell';
  shell.innerHTML = `
    <div class="workspace-tabbar" role="navigation" aria-label="Open workspace tabs">
      <div id="workspace-tab-list" class="workspace-tab-list" role="tablist" aria-label="Workspace tabs"></div>
      <button type="button" class="workspace-new-tab" id="workspace-new-tab" title="New chat" aria-label="New chat">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>
      </button>
    </div>
    <div class="workspace-shell-actions">
      <button type="button" class="workspace-shell-btn" id="workspace-notification-bell" title="Notifications" aria-label="Notifications" aria-expanded="false">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M18 8a6 6 0 1 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9"/><path d="M10 21h4"/></svg>
        <span id="workspace-notification-badge" class="workspace-notification-badge" hidden>0</span>
      </button>
      <div class="workspace-account-wrap">
        <button type="button" class="workspace-avatar-btn" id="workspace-account-btn" title="Account" aria-label="Account menu" aria-haspopup="menu" aria-expanded="false">
          <span id="workspace-avatar-initial">U</span>
        </button>
        <div class="workspace-account-menu hidden" id="workspace-account-menu" role="menu" aria-label="Account menu">
          <button type="button" role="menuitem" data-account-action="settings">Settings</button>
          <button type="button" role="menuitem" data-account-action="theme">Theme</button>
          <button type="button" role="menuitem" data-account-action="logout">Sign out</button>
        </div>
      </div>
    </div>
  `;
  document.body.prepend(shell);

  const panel = document.createElement('section');
  panel.id = 'workspace-notification-panel';
  panel.className = 'workspace-notification-panel hidden';
  panel.setAttribute('aria-label', 'Notifications');
  panel.setAttribute('aria-modal', 'false');
  panel.innerHTML = `
    <div class="workspace-notification-header">
      <div class="workspace-notification-title">Notifications</div>
      <button type="button" class="workspace-notification-close" id="workspace-notification-close" aria-label="Close notifications">&times;</button>
    </div>
    <div class="workspace-notification-tabs" role="tablist" aria-label="Notification views">
      <button type="button" class="active" data-notification-view="activity" role="tab" aria-selected="false">Activity</button>
      <button type="button" data-notification-view="progress" role="tab" aria-selected="false">Progress</button>
      <button type="button" data-notification-view="inbox" role="tab" aria-selected="true">Inbox</button>
    </div>
    <div class="workspace-notification-list" id="workspace-notification-list" role="list"></div>
  `;
  document.body.appendChild(panel);
}

function _wireShellControls() {
  document.getElementById('workspace-new-tab')?.addEventListener('click', async () => {
    if (window.argosVentureOpenSessionWizard && window.argosVentureCapabilities?.can_create_quest) {
      window.argosVentureOpenSessionWizard();
      return;
    }
    _saveCurrentViewState();
    let created = false;
    if (window.createDirectChatFromPreferredModel) {
      created = !!await window.createDirectChatFromPreferredModel();
    } else {
      document.getElementById('rail-new-session')?.click();
    }
    if (created && window.sessionModule?.hasPendingChat?.() && window.sessionModule?.materializePendingSession) {
      await window.sessionModule.materializePendingSession({ source: 'workspace-plus', openInFullView: true });
    }
    _syncFromSession({ selected: true });
    const sid = _currentSessionId();
    if (sid) await _activateTab(canonicalSessionTabId(sid));
  });

  document.getElementById('workspace-notification-bell')?.addEventListener('click', () => {
    if (window.matchMedia('(max-width: 768px)').matches && window.location.pathname !== '/notifications') {
      history.pushState({ notifications: true, returnTo: window.location.pathname + window.location.hash }, '', '/notifications?view=inbox');
    }
    _toggleNotifications(true);
  });
  document.getElementById('workspace-notification-close')?.addEventListener('click', () => _toggleNotifications(false));
  document.querySelectorAll('[data-notification-view]').forEach(btn => {
    btn.addEventListener('click', () => _loadNotificationView(btn.dataset.notificationView || 'inbox'));
  });

  const accountBtn = document.getElementById('workspace-account-btn');
  const menu = document.getElementById('workspace-account-menu');
  accountBtn?.addEventListener('click', () => {
    const open = menu?.classList.toggle('hidden') === false;
    accountBtn.setAttribute('aria-expanded', String(open));
    if (open) menu?.querySelector('button')?.focus();
  });
  menu?.addEventListener('click', async e => {
    const action = e.target.closest('[data-account-action]')?.dataset.accountAction;
    if (!action) return;
    menu.classList.add('hidden');
    accountBtn?.setAttribute('aria-expanded', 'false');
    if (action === 'settings') window.settingsModule?.open?.('account');
    else if (action === 'theme') {
      document.getElementById('theme-modal')?.classList.remove('hidden');
    }
    else if (action === 'logout') {
      await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' }).catch(() => {});
      window.location.href = '/login';
    }
  });
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    _toggleNotifications(false);
    menu?.classList.add('hidden');
    accountBtn?.setAttribute('aria-expanded', 'false');
  });
  document.addEventListener('click', e => {
    if (!e.target.closest('.workspace-account-wrap')) {
      menu?.classList.add('hidden');
      accountBtn?.setAttribute('aria-expanded', 'false');
    }
  });
}

async function _loadAuthStatus() {
  try {
    const res = await fetch('/api/auth/status', { credentials: 'same-origin' });
    if (!res.ok) return;
    _authStatus = await res.json();
    const initial = (_authStatus.username || 'U').slice(0, 1).toUpperCase();
    const avatar = document.getElementById('workspace-avatar-initial');
    if (avatar) avatar.textContent = initial;
  } catch (_) {}
}

async function _refreshBadge() {
  try {
    const res = await fetch(`${API_BASE}/api/notifications/unread-count`, { credentials: 'same-origin' });
    if (!res.ok) return;
    const data = await res.json();
    const count = Number(data.actionable || data.unread || 0);
    const badge = document.getElementById('workspace-notification-badge');
    if (!badge) return;
    badge.hidden = count <= 0;
    badge.textContent = String(Math.min(count, 99));
  } catch (_) {}
}

function _toggleNotifications(open) {
  _notificationOpen = !!open;
  const panel = document.getElementById('workspace-notification-panel');
  const bell = document.getElementById('workspace-notification-bell');
  if (!panel || !bell) return;
  panel.classList.toggle('hidden', !_notificationOpen);
  bell.setAttribute('aria-expanded', String(_notificationOpen));
  if (_notificationOpen) {
    _loadNotificationView(_activeView);
    panel.querySelector('[data-notification-view]')?.focus({ preventScroll: true });
  } else {
    bell.focus({ preventScroll: true });
  }
}

async function _loadNotificationView(view) {
  _activeView = ['activity', 'progress', 'inbox'].includes(view) ? view : 'inbox';
  document.querySelectorAll('[data-notification-view]').forEach(btn => {
    const active = btn.dataset.notificationView === _activeView;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-selected', String(active));
  });
  const list = document.getElementById('workspace-notification-list');
  if (!list) return;
  list.innerHTML = '<div class="workspace-notification-empty">Loading...</div>';
  try {
    const res = await fetch(`${API_BASE}/api/notifications?view=${encodeURIComponent(_activeView)}&limit=40`, { credentials: 'same-origin' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    const rows = data.notifications || [];
    if (!rows.length) {
      list.innerHTML = `<div class="workspace-notification-empty">No ${_activeView} items.</div>`;
      return;
    }
    list.innerHTML = '';
    for (const row of rows) list.appendChild(_renderNotificationRow(row));
  } catch (e) {
    list.innerHTML = `<div class="workspace-notification-empty">Notifications unavailable.</div>`;
  }
  _refreshBadge();
}

function _renderNotificationRow(row) {
  const item = document.createElement('article');
  item.className = 'workspace-notification-item';
  item.dataset.severity = row.severity || 'info';
  item.dataset.state = row.state || '';
  item.setAttribute('role', 'listitem');
  const when = row.updated_at ? new Date(row.updated_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
  const actions = Array.isArray(row.actions) ? row.actions : [];
  const actionButtons = actions.length
    ? actions.map(a => `<button type="button" data-action="venture:${_escAttr(a.id || '')}" class="${a.style === 'primary' ? 'primary' : ''}"></button>`).join('')
    : (row.action_label ? '<button type="button" data-action="open"></button>' : '');
  item.innerHTML = `
    <div class="workspace-notification-item-top">
      <span class="workspace-notification-item-title"></span>
      <span class="workspace-notification-time">${when}</span>
    </div>
    <div class="workspace-notification-message"></div>
    <div class="workspace-notification-feedback" aria-live="polite"></div>
    <div class="workspace-notification-actions">
      ${actionButtons}
      <button type="button" data-action="read">${row.read ? 'Unread' : 'Read'}</button>
      <button type="button" data-action="archive">Archive</button>
    </div>
  `;
  item.querySelector('.workspace-notification-item-title').textContent = row.title || 'Notification';
  item.querySelector('.workspace-notification-message').textContent = row.message || '';
  const open = item.querySelector('[data-action="open"]');
  if (open) open.textContent = row.action_label || 'Open';
  actions.forEach(a => {
    const btn = item.querySelector(`[data-action="venture:${CSS.escape(a.id || '')}"]`);
    if (btn) btn.textContent = a.label || a.id || 'Action';
  });
  item.addEventListener('click', async e => {
    const button = e.target.closest('button');
    const action = button?.dataset.action;
    if (!action) return;
    try {
      item.querySelectorAll('button').forEach(b => { b.disabled = true; });
      if (action.startsWith('venture:')) {
        await _runVentureNotificationAction(row, action.slice('venture:'.length), item);
      } else if (action === 'open') {
        _openNotificationTarget(row);
        await _markNotification(row.id, true);
      } else if (action === 'read') {
        await _markNotification(row.id, !row.read);
      } else if (action === 'archive') {
        await fetch(`${API_BASE}/api/notifications/${encodeURIComponent(row.id)}/archive`, { method: 'POST', credentials: 'same-origin' }).catch(() => {});
      }
      _loadNotificationView(_activeView);
    } catch (err) {
      const fb = item.querySelector('.workspace-notification-feedback');
      if (fb) fb.textContent = err?.message || 'Action failed. Try again.';
      item.querySelectorAll('button').forEach(b => { b.disabled = false; });
    }
  });
  return item;
}

function _escAttr(v) {
  return String(v || '').replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

async function _runVentureNotificationAction(row, actionId, item) {
  let url = '';
  if (row.resource_type === 'quest_invitation') {
    if (actionId === 'accept') url = `/api/quest-invitations/${encodeURIComponent(row.resource_id)}/accept`;
    if (actionId === 'decline') url = `/api/quest-invitations/${encodeURIComponent(row.resource_id)}/decline`;
  } else if (row.resource_type === 'quest_artifact_proposal') {
    if (actionId === 'review') {
      window.dispatchEvent(new CustomEvent('argos-venture:review-artifact-draft', { detail: { proposalId: row.resource_id } }));
      await _markNotification(row.id, true);
      return;
    }
    const proposal = await _fetchArtifactProposalLocation(row.resource_id);
    if (proposal?.session_id) {
      if (actionId === 'publish') url = `/api/quests/${encodeURIComponent(proposal.session_id)}/artifact-proposals/${encodeURIComponent(row.resource_id)}/publish`;
      if (actionId === 'decline') url = `/api/quests/${encodeURIComponent(proposal.session_id)}/artifact-proposals/${encodeURIComponent(row.resource_id)}/decline`;
    }
  }
  if (!url) throw new Error('Unsupported notification action');
  const res = await fetch(`${API_BASE}${url}`, { method: 'POST', credentials: 'same-origin' });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || 'Action failed');
  }
  const fb = item?.querySelector('.workspace-notification-feedback');
  if (fb) fb.textContent = 'Saved.';
}

async function _fetchArtifactProposalLocation(proposalId) {
  const res = await fetch(`${API_BASE}/api/library/artifact-drafts/${encodeURIComponent(proposalId)}`, { credentials: 'same-origin' });
  if (!res.ok) return null;
  const data = await res.json().catch(() => ({}));
  return data.draft || null;
}

async function _markNotification(id, read) {
  await fetch(`${API_BASE}/api/notifications/${encodeURIComponent(id)}/read`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ read: !!read }),
  }).catch(() => {});
}

function _openNotificationTarget(row) {
  _toggleNotifications(false);
  if (row.resource_type === 'task' || row.action_url?.startsWith('/tasks')) {
    if (window.tasksModule?.openActivity) {
      window.tasksModule.openActivity(row.task_id ? { task_id: row.task_id } : null);
    } else if (window.tasksModule?.openTasks) {
      window.tasksModule.openTasks();
    } else {
      window.location.href = row.action_url || '/tasks';
    }
    return;
  }
  if (row.resource_type === 'session' && row.resource_id) {
    window.sessionModule?.selectSession?.(row.resource_id);
    return;
  }
  if (row.resource_type === 'quest_artifact_proposal' && row.resource_id) {
    window.dispatchEvent(new CustomEvent('argos-venture:review-artifact-draft', { detail: { proposalId: row.resource_id } }));
    return;
  }
  if (row.action_url) window.location.href = row.action_url;
}

function _wireSessionEvents() {
  document.addEventListener('odysseus:session-selected', () => {
    _syncFromSession({ selected: true });
    _restoreSelectedViewState();
  });
  document.addEventListener('odysseus:new-chat-ready', () => {
    _state.selected = 'home';
    _saveState();
    _renderTabs();
  });
  document.addEventListener('odysseus:session-materialized', e => {
    const detail = e.detail || {};
    const shouldSelect = detail.openInFullView || !window.sessionControlModule?.isComposingNewSession?.();
    _state = upsertSessionTab(_state, { id: detail.sessionId, name: detail.name || 'Chat' }, { selected: shouldSelect });
    _saveState();
    _renderTabs();
  });
  document.addEventListener('odysseus:session-runtime-state', e => {
    const detail = e.detail || {};
    _setSessionRuntimeState(detail.sessionId, detail.state);
  });
  document.addEventListener('argos-venture:quest-registry-updated', () => {
    _renderTabs();
  });
  document.addEventListener('odysseus:request-session-tab-rename', e => {
    const detail = e.detail || {};
    if (_startTabRename(detail.sessionId, detail.currentName)) e.preventDefault();
  });
  document.addEventListener('odysseus:session-deleted', e => {
    const sessionId = e.detail?.sessionId;
    if (!sessionId) return;
    const tabId = canonicalSessionTabId(sessionId);
    const wasSelected = _state.selected === tabId;
    _state = closeTabById(_state, tabId);
    _saveState();
    _renderTabs();
    if (wasSelected) _activateTab(_state.selected || 'home');
  });
  window.addEventListener('hashchange', () => setTimeout(() => _syncFromSession({ selected: true }), 0));
  window.addEventListener('popstate', () => {
    if (window.location.pathname === '/notifications') _toggleNotifications(true);
    else _toggleNotifications(false);
  });
  document.getElementById('message')?.addEventListener('input', () => {
    const sid = _currentSessionId();
    if (!sid) return;
    const tab = _state.tabs[canonicalSessionTabId(sid)];
    if (!tab) return;
    const input = document.getElementById('message');
    tab.view = { ...(tab.view || {}), draft: input?.value || '', updatedAt: Date.now() };
    tab.state = input?.value ? 'dirty/unsaved' : 'idle';
    _saveState();
    _renderTabs();
  });
  document.getElementById('chat-history')?.addEventListener('scroll', () => {
    clearTimeout(_saveCurrentViewState._timer);
    _saveCurrentViewState._timer = setTimeout(_saveCurrentViewState, 120);
  }, { passive: true });
}

export function initWorkspaceShell() {
  if (_initialized) return;
  if (!isWorkspaceShellEnabled()) return;
  _initialized = true;
  const start = () => {
    _buildShell();
    _loadState();
    _wireKeyboard();
    _wireShellControls();
    _wireSessionEvents();
    _loadAuthStatus();
    _syncFromSession({ selected: true });
    _refreshBadge();
    if (!_badgeTimer) _badgeTimer = setInterval(_refreshBadge, 15000);
    if (window.location.pathname === '/notifications') _toggleNotifications(true);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
}

const workspaceShell = { initWorkspaceShell };
export default workspaceShell;
