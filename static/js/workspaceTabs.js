export const MAX_SESSION_TABS = 12;

export function canonicalSessionTabId(sessionId) {
  return `session:${String(sessionId || '')}`;
}

export function createInitialTabState() {
  return {
    version: 1,
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
  next.version = 1;
  next.tabs = next.tabs && typeof next.tabs === 'object' ? next.tabs : {};
  next.tabs.home = { id: 'home', kind: 'home', title: 'Home', pinned: true, state: 'idle', view: next.tabs.home?.view || {} };

  const cleaned = ['home'];
  for (const rawId of next.order || []) {
    if (rawId === 'home') continue;
    const tab = next.tabs[rawId];
    if (!tab || tab.kind !== 'session') continue;
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
  }
  next.order = cleaned.slice(0, MAX_SESSION_TABS + 1);
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
  next.order = next.order.filter(t => t !== id);
  next.order.push(id);
  const sessionTabs = next.order.filter(t => t !== 'home');
  while (sessionTabs.length > MAX_SESSION_TABS) {
    const stale = sessionTabs.shift();
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

