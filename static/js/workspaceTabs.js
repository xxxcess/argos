export const MAX_WORKSPACE_TABS = 12;
export const MAX_SESSION_TABS = MAX_WORKSPACE_TABS;

export function canonicalSessionTabId(sessionId) {
  return `session:${String(sessionId || '')}`;
}

export function createInitialTabState() {
  return {
    version: 3,
    order: ['home'],
    selected: 'home',
    tabs: {
      home: { id: 'home', kind: 'home', title: 'Home', pinned: true, state: 'idle', view: {} },
    },
  };
}

function cloneState(state) {
  return JSON.parse(JSON.stringify(state || createInitialTabState()));
}

export function normalizeTabState(state, sessions = null) {
  const next = cloneState(state);
  const sessionList = Array.isArray(sessions) ? sessions : [];
  const sessionIds = new Set(sessionList.map(session => String(session.id)));
  next.version = 3;
  next.tabs = next.tabs && typeof next.tabs === 'object' ? next.tabs : {};
  next.tabs.home = {
    id: 'home', kind: 'home', title: 'Home', pinned: true, state: 'idle', view: next.tabs.home?.view || {},
  };

  const order = ['home'];
  for (const rawId of next.order || []) {
    if (rawId === 'home') continue;
    const tab = next.tabs[rawId];
    if (!tab || tab.kind !== 'session') continue;
    const sessionId = String(tab.sessionId || rawId.slice('session:'.length));
    if (sessionIds.size && !sessionIds.has(sessionId)) continue;
    const id = canonicalSessionTabId(sessionId);
    const meta = sessionList.find(session => String(session.id) === sessionId);
    next.tabs[id] = {
      ...tab,
      id,
      kind: 'session',
      sessionId,
      title: meta?.name || tab.title || 'Chat',
      mode: meta?.mode || tab.mode || null,
      pinned: false,
      state: tab.state || 'idle',
      view: tab.view || {},
    };
    if (!order.includes(id)) order.push(id);
  }

  next.order = order.slice(0, MAX_WORKSPACE_TABS + 1);
  if (!next.order.includes(next.selected)) next.selected = 'home';
  for (const tabId of Object.keys(next.tabs)) {
    if (!next.order.includes(tabId)) delete next.tabs[tabId];
  }
  return next;
}

export function upsertSessionTab(state, session, { selected = true, state: visualState = 'idle' } = {}) {
  const next = normalizeTabState(state);
  if (!session?.id) return next;
  const id = canonicalSessionTabId(session.id);
  const prior = next.tabs[id] || {};
  next.tabs[id] = {
    ...prior,
    id,
    kind: 'session',
    sessionId: String(session.id),
    title: session.name || prior.title || 'Chat',
    mode: session.mode || prior.mode || null,
    pinned: false,
    state: visualState || prior.state || 'idle',
    view: prior.view || {},
  };
  if (!next.order.includes(id)) next.order.push(id);
  const workspaceTabs = next.order.filter(tabId => tabId !== 'home');
  while (workspaceTabs.length > MAX_WORKSPACE_TABS) {
    const stale = workspaceTabs.shift();
    next.order = next.order.filter(tabId => tabId !== stale);
    delete next.tabs[stale];
  }
  next.order = ['home', ...next.order.filter(tabId => tabId !== 'home')];
  if (selected) next.selected = id;
  return next;
}

export function closeTabById(state, tabId) {
  const next = normalizeTabState(state);
  if (!tabId || tabId === 'home') return next;
  const index = next.order.indexOf(tabId);
  delete next.tabs[tabId];
  next.order = next.order.filter(id => id !== tabId);
  if (next.selected === tabId) next.selected = next.order[Math.max(0, index - 1)] || 'home';
  return next;
}
