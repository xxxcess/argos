export const MAX_WORKSPACE_TABS = 12;
export const MAX_SESSION_TABS = MAX_WORKSPACE_TABS;

export function canonicalSessionTabId(sessionId) {
  return `session:${String(sessionId || '')}`;
}

export function canonicalMeetingTabId(captureId) {
  return `meeting:${String(captureId || '')}`;
}

export function createInitialTabState() {
  return {
    version: 2,
    order: ['home'],
    selected: 'home',
    tabs: {
      home: { id: 'home', kind: 'home', title: 'Home', pinned: true, state: 'idle', view: {} },
    },
  };
}

function _cloneState(state) {
  return JSON.parse(JSON.stringify(state || createInitialTabState()));
}

export function normalizeTabState(state, sessions = null) {
  const next = _cloneState(state);
  const sessionList = Array.isArray(sessions) ? sessions : [];
  const sessionIds = new Set(sessionList.map(s => String(s.id)));
  next.version = 2;
  next.tabs = next.tabs && typeof next.tabs === 'object' ? next.tabs : {};
  next.tabs.home = { id: 'home', kind: 'home', title: 'Home', pinned: true, state: 'idle', view: next.tabs.home?.view || {} };

  const cleaned = ['home'];
  for (const rawId of next.order || []) {
    if (rawId === 'home') continue;
    const tab = next.tabs[rawId];
    if (!tab) continue;

    if (tab.kind === 'session') {
      const sid = String(tab.sessionId || rawId.slice('session:'.length));
      if (sessionIds.size && !sessionIds.has(sid)) continue;
      const id = canonicalSessionTabId(sid);
      const meta = sessionList.find(s => String(s.id) === sid);
      next.tabs[id] = {
        ...tab,
        id,
        kind: 'session',
        sessionId: sid,
        title: meta?.name || tab.title || 'Chat',
        pinned: false,
        state: tab.state || 'idle',
        view: tab.view || {},
      };
      if (!cleaned.includes(id)) cleaned.push(id);
      continue;
    }

    if (tab.kind === 'meeting') {
      const captureId = String(tab.captureId || rawId.slice('meeting:'.length));
      if (!captureId) continue;
      const id = canonicalMeetingTabId(captureId);
      next.tabs[id] = {
        ...tab,
        id,
        kind: 'meeting',
        captureId,
        title: tab.title || 'Audio Capture',
        pinned: false,
        state: tab.state || 'idle',
        // Keep tab chrome only. Sensitive transcript and recorder state live
        // in memory and are never serialized into browser storage.
        view: {},
      };
      if (!cleaned.includes(id)) cleaned.push(id);
    }
  }
  next.order = cleaned.slice(0, MAX_WORKSPACE_TABS + 1);
  if (!next.order.includes(next.selected)) next.selected = 'home';
  for (const key of Object.keys(next.tabs)) {
    if (!next.order.includes(key)) delete next.tabs[key];
  }
  return next;
}

export function upsertSessionTab(state, session, { selected = true, state: visualState = 'idle' } = {}) {
  const next = normalizeTabState(state);
  if (!session || !session.id) return next;
  const id = canonicalSessionTabId(session.id);
  const prior = next.tabs[id] || {};
  next.tabs[id] = {
    ...prior,
    id,
    kind: 'session',
    sessionId: String(session.id),
    title: session.name || prior.title || 'Chat',
    pinned: false,
    state: visualState || prior.state || 'idle',
    view: prior.view || {},
  };
  if (!next.order.includes(id)) next.order.push(id);
  const workspaceTabs = next.order.filter(t => t !== 'home');
  while (workspaceTabs.length > MAX_WORKSPACE_TABS) {
    const stale = workspaceTabs.shift();
    next.order = next.order.filter(t => t !== stale);
    delete next.tabs[stale];
  }
  next.order = ['home', ...next.order.filter(t => t !== 'home')];
  if (selected) next.selected = id;
  return next;
}

export function upsertMeetingTab(state, meeting, { selected = true, state: visualState = 'idle' } = {}) {
  const next = normalizeTabState(state);
  const captureId = String(meeting?.captureId || meeting?.id || '');
  if (!captureId) return next;
  const id = canonicalMeetingTabId(captureId);
  const prior = next.tabs[id] || {};
  next.tabs[id] = {
    ...prior,
    id,
    kind: 'meeting',
    captureId,
    title: meeting?.title || prior.title || 'Audio Capture',
    pinned: false,
    state: visualState || prior.state || 'idle',
    view: {},
  };
  if (!next.order.includes(id)) next.order.push(id);
  const workspaceTabs = next.order.filter(t => t !== 'home');
  while (workspaceTabs.length > MAX_WORKSPACE_TABS) {
    const stale = workspaceTabs.shift();
    next.order = next.order.filter(t => t !== stale);
    delete next.tabs[stale];
  }
  next.order = ['home', ...next.order.filter(t => t !== 'home')];
  if (selected) next.selected = id;
  return next;
}

export function closeTabById(state, tabId) {
  const next = normalizeTabState(state);
  if (!tabId || tabId === 'home') return next;
  const idx = next.order.indexOf(tabId);
  delete next.tabs[tabId];
  next.order = next.order.filter(id => id !== tabId);
  if (next.selected === tabId) {
    next.selected = next.order[Math.max(0, idx - 1)] || 'home';
  }
  return next;
}
