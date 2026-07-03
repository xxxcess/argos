// Targeted live-status shell for Argos Venture.
//
// The legacy UI owns initial Quest rendering and actions. This shell refreshes
// high-frequency status in place and refreshes Artifact cards only after an
// Artifact lifecycle transition, so generated titles appear without restoring
// the old full-rail polling loop.

const nativeSetInterval = window.setInterval.bind(window);
const LEGACY_RAIL_POLL_DELAY = 4000;
const IDLE_INTERVAL = 2_147_000_000;

function isLegacyFullRailPoll(callback, delay) {
  return Number(delay) === LEGACY_RAIL_POLL_DELAY
    && /\brenderRightRail\b/.test(Function.prototype.toString.call(callback));
}

window.setInterval = function ventureScopedSetInterval(callback, delay, ...args) {
  if (isLegacyFullRailPoll(callback, delay)) {
    return nativeSetInterval(() => {}, IDLE_INTERVAL);
  }
  return nativeSetInterval(callback, delay, ...args);
};

const legacyModule = await import('./venture_legacy.js');
const legacy = legacyModule.default;

const API_BASE = window.API_BASE || '';
let liveTimer = null;
let liveQuestId = null;
let liveRefreshInFlight = false;
let observerInstalled = false;
let refreshQueued = false;
let artifactRefreshQueued = false;
let latestArtifactLifecycle = '';

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
  if (job.status === 'completed') return 'Voyage Memory updated from published Artifact key points.';
  if (job.status === 'no_insight') {
    if (job.reason === 'no_grounded_material') return 'No draft created: no grounded conversation or source evidence was available.';
    if (job.reason === 'insufficient_grounded_conversation' || job.reason === 'insufficient_grounded_key_points') return 'No draft created: the conversation did not yet support enough cited key points.';
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

function artifactTitle(artifact) {
  const title = String(artifact?.artifact_title || artifact?.title || '').trim();
  const generic = new Set(['insight', 'insights', 'quest insight', 'summary', 'artifact', 'quest artifact']);
  return title && !generic.has(title.toLowerCase()) ? title : 'Untitled Artifact';
}

async function openArtifactDocument(documentId) {
  if (!documentId) return;
  try {
    const imported = window.documentModule ? null : await import('./document.js');
    const documentModule = window.documentModule || imported?.default || imported;
    if (!documentModule?.loadDocument) throw new Error('Document module unavailable.');
    await documentModule.loadDocument(documentId);
    documentModule.openPanel?.();
    documentModule.switchToDoc?.(documentId);
  } catch (_) {
    window.dispatchEvent(new CustomEvent('odysseus:toast', { detail: { type: 'error', message: 'Unable to open this Quest Artifact.' } }));
  }
}

function artifactRow(artifact) {
  const row = document.createElement('div');
  row.className = 'venture-row ArtifactCard';
  const label = document.createElement('span');
  label.textContent = artifactTitle(artifact);
  const button = document.createElement('button');
  button.className = 'venture-btn';
  button.type = 'button';
  button.textContent = 'Open';
  button.setAttribute('aria-label', `Open ${artifactTitle(artifact)}`);
  button.addEventListener('click', () => openArtifactDocument(artifact.id));
  row.append(label, button);
  const count = Number(artifact?.key_point_count || 0);
  if (count) {
    const meta = document.createElement('div');
    meta.className = 'venture-muted venture-full-span';
    meta.textContent = `${count} key point${count === 1 ? '' : 's'} · ${artifact.status || 'published'}`;
    row.appendChild(meta);
  }
  return row;
}

function artifactReviewRow(proposal) {
  const row = document.createElement('div');
  row.className = 'venture-row ArtifactReviewRow';
  const label = document.createElement('span');
  label.textContent = artifactTitle(proposal);
  const button = document.createElement('button');
  button.className = 'venture-btn';
  button.type = 'button';
  button.textContent = 'Review';
  button.addEventListener('click', () => {
    window.dispatchEvent(new CustomEvent('argos-venture:review-artifact-draft', { detail: { proposalId: proposal.id } }));
  });
  row.append(label, button);
  const count = Number(proposal?.synthesis?.key_points?.length || proposal?.key_point_count || 0);
  if (count) {
    const meta = document.createElement('div');
    meta.className = 'venture-muted venture-full-span';
    meta.textContent = `Draft · ${count} key point${count === 1 ? '' : 's'} · awaiting Captain review`;
    row.appendChild(meta);
  }
  return row;
}

async function refreshArtifactCards(questId) {
  if (!questId || selectedQuestId() !== questId) return;
  const rail = document.getElementById('venture-right-rail');
  if (!rail) return;
  const shelf = rail.querySelector('.ArtifactShelf');
  const capabilities = window.argosVentureCapabilities || {};
  const [artifactPayload, proposalPayload] = await Promise.all([
    shelf ? getJson(`/api/quests/${encodeURIComponent(questId)}/artifacts`) : Promise.resolve(null),
    capabilities.can_review_artifacts ? getJson(`/api/quests/${encodeURIComponent(questId)}/artifact-proposals`) : Promise.resolve(null),
  ]);
  if (selectedQuestId() !== questId) return;

  if (shelf && artifactPayload) {
    const heading = shelf.querySelector('h3') || document.createElement('h3');
    heading.textContent = 'Quest Artifacts';
    const documents = artifactPayload.documents || [];
    const gallery = artifactPayload.gallery || [];
    const children = [heading, ...documents.map(artifactRow)];
    gallery.forEach(image => {
      const row = document.createElement('div');
      row.className = 'venture-row ArtifactCard';
      const label = document.createElement('span');
      label.textContent = image.prompt || image.filename || 'Gallery Artifact';
      row.appendChild(label);
      children.push(row);
    });
    if (!documents.length && !gallery.length) {
      const empty = document.createElement('div');
      empty.className = 'venture-muted';
      empty.textContent = 'No published Artifacts yet.';
      children.push(empty);
    }
    shelf.replaceChildren(...children);
  }

  if (capabilities.can_review_artifacts && proposalPayload) {
    const pending = (proposalPayload.proposals || []).filter(proposal => proposal.status === 'pending_review');
    let queue = rail.querySelector('.ArtifactReviewQueue');
    if (!queue && pending.length) {
      queue = document.createElement('section');
      queue.className = 'venture-card ArtifactReviewQueue';
      rail.insertBefore(queue, shelf || rail.querySelector('.ArgoStatusCard') || null);
    }
    if (queue) {
      const heading = document.createElement('h3');
      heading.textContent = 'Artifact Review';
      const children = [heading];
      if (!pending.length) {
        const empty = document.createElement('div');
        empty.className = 'venture-muted';
        empty.textContent = 'No pending drafts.';
        children.push(empty);
      } else {
        children.push(...pending.map(artifactReviewRow));
      }
      queue.replaceChildren(...children);
    }
  }
}

function queueArtifactRefresh(questId) {
  if (artifactRefreshQueued || !questId) return;
  artifactRefreshQueued = true;
  window.setTimeout(() => {
    artifactRefreshQueued = false;
    refreshArtifactCards(questId).catch(() => {});
  }, 0);
}

function updateArgoStatusCard(card, status) {
  if (!card) return;
  card.dataset.argoLiveReady = 'true';
  card.setAttribute('aria-live', 'polite');
  const muted = Array.from(card.children).filter(child => child.classList?.contains('venture-muted'));
  if (muted[0]) muted[0].textContent = `${Number(status?.memory_count || 0)} Voyage Memory entries visible.`;
  const latestJob = (status?.jobs || [])[0] || null;
  if (muted[1]) muted[1].textContent = synthesisStatusText(latestJob);
  if (latestJob && ['created', 'updated', 'completed', 'no_insight', 'failed'].includes(latestJob.status)) {
    const lifecycle = `${latestJob.id || ''}:${latestJob.status}:${latestJob.finished_at || ''}`;
    if (lifecycle && lifecycle !== latestArtifactLifecycle) {
      latestArtifactLifecycle = lifecycle;
      queueArtifactRefresh(selectedQuestId());
    }
  }
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
  installLivePanelObserver();
  queueLiveRefresh();
}

if (document.body) startLivePanelUpdates();
else document.addEventListener('DOMContentLoaded', startLivePanelUpdates, { once: true });

export default legacy;
