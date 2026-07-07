// Targeted live-status shell for Argos Venture.
//
// The legacy UI remains responsible for the initial Quest panel and all action
// flows. This shell suppresses its full-panel timer and refreshes only the
// Quest Sources and Argo Status cards while asynchronous work is active.

const nativeSetInterval = window.setInterval.bind(window);
const LEGACY_RAIL_POLL_DELAY = 4000;
const IDLE_INTERVAL = 2_147_000_000;

function isLegacyFullRailPoll(callback, delay) {
  return Number(delay) === LEGACY_RAIL_POLL_DELAY
    && /\brenderRightRail\b/.test(Function.prototype.toString.call(callback));
}

// The legacy module closes over its polling timer. Install the narrow timer
// guard before it evaluates, then use a separate, DOM-targeted live updater.
window.setInterval = function ventureScopedSetInterval(callback, delay, ...args) {
  if (isLegacyFullRailPoll(callback, delay)) {
    return nativeSetInterval(() => {}, IDLE_INTERVAL);
  }
  return nativeSetInterval(callback, delay, ...args);
};

const legacyModule = await import('./venture_legacy.js');
const legacy = legacyModule.default;
const analysisModule = await import('./venture_analysis.js');

const API_BASE = window.API_BASE || '';
let liveTimer = null;
let liveQuestId = null;
let liveRefreshInFlight = false;
let observerInstalled = false;
let refreshQueued = false;

function selectedQuestId() {
  if (document.body?.classList.contains('workspace-home-active')) return '';
  const sessionId = window.sessionModule?.getCurrentSessionId?.();
  return sessionId ? String(sessionId) : '';
}

function getJson(url) {
  return fetch(`${API_BASE}${url}`, { credentials: 'same-origin' })
    .then(response => response.ok ? response.json() : null)
    .catch(() => null);
}

function activeSourceWork(sources) {
  return (sources || []).some(source => source.index_status?.has_active_job);
}

function activeArgoWork(status) {
  return (status?.jobs || []).some(job => ['queued', 'running'].includes(job.status));
}

function stopLivePolling() {
  if (liveTimer) window.clearInterval(liveTimer);
  liveTimer = null;
  liveQuestId = null;
}

function syncLivePolling(questId, sources, status) {
  const shouldPoll = !!questId && (activeSourceWork(sources) || activeArgoWork(status));
  if (!shouldPoll) {
    stopLivePolling();
    return;
  }
  if (liveTimer && liveQuestId === questId) return;
  stopLivePolling();
  liveQuestId = questId;
  liveTimer = nativeSetInterval(() => {
    if (document.hidden || selectedQuestId() !== liveQuestId) {
      if (selectedQuestId() !== liveQuestId) stopLivePolling();
      return;
    }
    refreshLivePanels().catch(() => {});
  }, LEGACY_RAIL_POLL_DELAY);
}

function sourceStatusText(source) {
  const index = source.index_status || {};
  const state = index.index_state || source.index_state || 'not_indexed';
  const chunks = index.chunk_count || 0;
  const job = index.current_job || {};
  const safeError = job.safe_error_message || index.warning || '';
  const errorCode = job.error_code ? ` (${job.error_code})` : '';
  if (state === 'running' || state === 'queued' || state === 'indexing') {
    const total = job.progress_total || 0;
    const done = job.progress_completed || 0;
    const percentage = total ? Math.round((done / total) * 100) : 0;
    if (job.error_code && state === 'queued') return `◌ Retrying · ${safeError || 'Waiting to retry'}${errorCode}`;
    return total ? `◌ Indexing · ${percentage}% · ${done} / ${total}` : '◌ Indexing';
  }
  if (state === 'ready' || state === 'indexed') return `● Ready · ${chunks} chunks`;
  if (state === 'failed') return `△ Needs attention · ${safeError || 'Indexing failed'}${errorCode}`;
  if (state === 'partial') return `△ Partial · ${chunks} chunks`;
  return '○ Not indexed';
}

function bibleStateLabel(state) {
  return ({ queued: 'Queued', indexing: 'Indexing', indexed: 'Indexed', partial: 'Partial', failed: 'Failed', paused: 'Paused' })[state] || state || 'Queued';
}

function synthesisStatusText(job) {
  if (!job) return 'No synthesis jobs yet.';
  if (job.status === 'queued') return 'Synthesis queued.';
  if (job.status === 'running') return 'Synthesis running.';
  if (job.status === 'created') return 'Artifact Draft ready for review.';
  if (job.status === 'updated') return 'Artifact Draft updated.';
  if (job.status === 'no_insight') {
    if (job.reason === 'no_source_evidence') return 'No draft created: no indexed Quest source evidence was available.';
    if (job.reason === 'duplicate_claim_without_new_evidence') return 'No draft created: no meaningful new evidence for that claim.';
    return 'No draft created: no durable evidence-backed insight was found.';
  }
  if (job.status === 'failed') return job.safe_error_message || 'Synthesis failed.';
  if (job.status === 'cancelled') return 'Synthesis cancelled.';
  return `Synthesis status: ${job.status}`;
}

function directList(container) {
  return Array.from(container.children).find(child => child.classList?.contains('venture-list')) || null;
}

function sourceRowFor(sourceList, displayName) {
  return Array.from(sourceList?.children || []).find(row => (
    row.classList?.contains('venture-row')
    && row.children[0]?.textContent?.trim() === String(displayName || '').trim()
  ));
}

function updateSourcesCard(card, sources) {
  if (!card) return;
  card.dataset.argoLiveReady = 'true';
  card.setAttribute('aria-live', 'polite');
  const counts = (sources || []).reduce((all, source) => {
    const state = source.index_status?.index_state || source.index_state || 'not_indexed';
    if (state === 'ready' || state === 'indexed') all.ready += 1;
    else if (state === 'queued' || state === 'running' || state === 'indexing') all.indexing += 1;
    else if (state === 'failed' || state === 'partial') all.attention += 1;
    return all;
  }, { ready: 0, indexing: 0, attention: 0 });
  const summary = Array.from(card.children).find(child => child.classList?.contains('venture-muted'));
  if (summary) summary.textContent = `${counts.ready} ready · ${counts.indexing} indexing · ${counts.attention} needs attention`;

  const list = directList(card);
  (sources || []).forEach(source => {
    const row = sourceRowFor(list, source.display_name);
    if (!row) return;
    const status = Array.from(row.children).find(child => child.classList?.contains('venture-muted'));
    if (status) status.textContent = `${source.access_mode} · ${sourceStatusText(source)}`;

    const bookList = row.querySelector('.venture-full-span.venture-list');
    (source.index_status?.books || []).forEach(book => {
      const bookRow = sourceRowFor(bookList, book.book_name);
      if (!bookRow) return;
      const bookStatus = Array.from(bookRow.children).find(child => child.classList?.contains('venture-muted'));
      if (bookStatus) bookStatus.textContent = bibleStateLabel(book.state);
    });
  });
}

function updateArgoStatusCard(card, status) {
  if (!card) return;
  card.dataset.argoLiveReady = 'true';
  card.setAttribute('aria-live', 'polite');
  const muted = Array.from(card.children).filter(child => child.classList?.contains('venture-muted'));
  if (muted[0]) muted[0].textContent = `${Number(status?.memory_count || 0)} Voyage Memory entries visible.`;
  if (muted[1]) muted[1].textContent = synthesisStatusText((status?.jobs || [])[0] || null);
}

async function refreshLivePanels() {
  if (liveRefreshInFlight || document.hidden) return;
  const rail = document.getElementById('venture-right-rail');
  const sourcesCard = rail?.querySelector('.QuestSourceCard');
  const argoCard = rail?.querySelector('.ArgoStatusCard');
  const questId = selectedQuestId();
  if (!rail || !questId || (!sourcesCard && !argoCard)) {
    stopLivePolling();
    return;
  }

  liveRefreshInFlight = true;
  try {
    const [sourcesPayload, argoStatus] = await Promise.all([
      sourcesCard ? getJson(`/api/quests/${encodeURIComponent(questId)}/sources`) : Promise.resolve(null),
      argoCard ? getJson(`/api/quests/${encodeURIComponent(questId)}/argo-status`) : Promise.resolve(null),
    ]);
    if (questId !== selectedQuestId()) return;
    const sources = sourcesPayload?.sources || [];
    updateSourcesCard(sourcesCard, sources);
    updateArgoStatusCard(argoCard, argoStatus || {});
    syncLivePolling(questId, sources, argoStatus || {});
  } finally {
    liveRefreshInFlight = false;
  }
}

function queueLiveRefresh() {
  if (refreshQueued) return;
  refreshQueued = true;
  window.setTimeout(() => {
    refreshQueued = false;
    refreshLivePanels().catch(() => {});
  }, 0);
}

function installLivePanelObserver() {
  if (observerInstalled || !document.body) return;
  observerInstalled = true;
  const observer = new MutationObserver(mutations => {
    const shouldRefresh = mutations.some(mutation => Array.from(mutation.addedNodes).some(node => {
      if (node.nodeType !== Node.ELEMENT_NODE) return false;
      const element = node;
      const card = element.matches?.('.QuestSourceCard, .ArgoStatusCard')
        ? element
        : element.querySelector?.('.QuestSourceCard, .ArgoStatusCard');
      return Boolean(card && card.dataset.argoLiveReady !== 'true');
    }));
    if (shouldRefresh) queueLiveRefresh();
  });
  observer.observe(document.body, { childList: true, subtree: true });
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) refreshLivePanels().catch(() => {});
  });
  document.addEventListener('odysseus:session-selected', () => queueLiveRefresh());
  document.addEventListener('odysseus:workspace-tab-activated', () => queueLiveRefresh());
}

function startLivePanelUpdates() {
  analysisModule.initVentureDataAnalysis?.().catch?.(() => {});
  installLivePanelObserver();
  queueLiveRefresh();
}

if (document.body) startLivePanelUpdates();
else document.addEventListener('DOMContentLoaded', startLivePanelUpdates, { once: true });

export default legacy;
