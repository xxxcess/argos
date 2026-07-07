// Venture resource controller.
//
// The legacy module renders the initial Quest rail. This controller blocks its
// expensive full-rail/history polling and replaces it with compact Quest status
// probes plus an explicit Artifact review queue.

const API_BASE = window.API_BASE || '';
const LEGACY_POLL_MS = 4000;
const QUEST_STATUS_PROBE_MS = 8000;
const nativeSetInterval = window.setInterval.bind(window);
const nativeClearInterval = window.clearInterval.bind(window);

// Compatibility firewall for legacy's two full-refresh timers. It is installed
// before importing the legacy module and only suppresses the known 4s callbacks;
// every other application's timer remains untouched.
if (!window.__argosVenturePollingFirewallInstalled) {
  window.__argosVenturePollingFirewallInstalled = true;
  window.setInterval = (callback, delay, ...args) => {
    const source = Function.prototype.toString.call(callback);
    const isLegacyPoll = Number(delay) === LEGACY_POLL_MS
      && (source.includes('renderRightRail') || source.includes('syncActiveQuestHistory'));
    return isLegacyPoll ? nativeSetInterval(() => {}, 2_147_000_000) : nativeSetInterval(callback, delay, ...args);
  };
}

const legacyModule = await import('./venture_legacy.js');
const legacy = legacyModule.default;
const analysisModule = await import('./venture_analysis.js');
const analysisDecisionModule = await import('./venture_analysis_decision_trail.js');

const state = window.__argosVentureResourceController || {
  initialized: false,
  timer: null,
  questId: null,
  inFlight: false,
  refreshQueued: false,
  artifactRefreshQueued: false,
  controllers: new Map(),
  lastLifecycle: '',
  managementBusy: false,
};
window.__argosVentureResourceController = state;

function selectedQuestId() {
  if (document.body?.classList.contains('workspace-home-active')) return '';
  const id = window.sessionModule?.getCurrentSessionId?.();
  return id ? String(id) : '';
}

function abortRequest(key) {
  state.controllers.get(key)?.abort();
  const controller = new AbortController();
  state.controllers.set(key, controller);
  return controller;
}

async function getJson(url, key, { replace = true } = {}) {
  const controller = replace ? abortRequest(key) : new AbortController();
  try {
    const response = await fetch(`${API_BASE}${url}`, { credentials: 'same-origin', signal: controller.signal });
    return response.ok ? await response.json() : null;
  } catch (error) {
    if (error?.name !== 'AbortError') console.debug('[venture] request failed', url, error);
    return null;
  } finally {
    if (state.controllers.get(key) === controller) state.controllers.delete(key);
  }
}

async function postJson(url, payload = {}) {
  const response = await fetch(`${API_BASE}${url}`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || 'Quest management request failed.');
  return data;
}

function activeWork(payload) {
  return (payload?.sources || []).some(source => source.index_status?.has_active_job)
    || (payload?.argo?.jobs || []).some(job => ['queued', 'running'].includes(job.status));
}

function stopPolling() {
  if (state.timer) nativeClearInterval(state.timer);
  state.timer = null;
  state.questId = null;
}

function syncPolling(questId, payload) {
  if (!questId) {
    stopPolling();
    return;
  }
  const delay = activeWork(payload) ? LEGACY_POLL_MS : QUEST_STATUS_PROBE_MS;
  if (state.timer && state.questId === questId && state.pollDelay === delay) return;
  stopPolling();
  state.questId = questId;
  state.pollDelay = delay;
  // Keep a low-cost idle probe alive. Agent-originated tool calls do not always
  // emit a browser event, so an idle panel otherwise never learns that a draft
  // moved from queued to reviewable.
  state.timer = nativeSetInterval(() => {
    if (document.hidden || selectedQuestId() !== state.questId) {
      if (selectedQuestId() !== state.questId) stopPolling();
      return;
    }
    refreshCompactStatus().catch(() => {});
  }, delay);
}

function sourceStatusText(source) {
  const status = source.index_status || {};
  const job = status.current_job || {};
  const stateName = status.index_state || source.index_state || 'not_indexed';
  if (['queued', 'running', 'indexing'].includes(stateName)) {
    const total = Number(job.progress_total || 0);
    const done = Number(job.progress_completed || 0);
    return total ? `◌ Indexing · ${Math.round((done / total) * 100)}% · ${done} / ${total}` : '◌ Indexing';
  }
  if (stateName === 'ready' || stateName === 'indexed') return `● Ready · ${Number(status.chunk_count || 0)} chunks`;
  if (stateName === 'failed') return `△ Needs attention · ${job.safe_error_message || status.warning || 'Indexing failed'}`;
  if (stateName === 'partial') return `△ Partial · ${Number(status.chunk_count || 0)} chunks`;
  return '○ Not indexed';
}

function updateSourcesCard(sources) {
  const card = document.querySelector('#venture-right-rail .QuestSourceCard');
  if (!card) return;
  const counts = (sources || []).reduce((result, source) => {
    const status = source.index_status?.index_state || source.index_state || 'not_indexed';
    if (status === 'ready' || status === 'indexed') result.ready += 1;
    else if (['queued', 'running', 'indexing'].includes(status)) result.indexing += 1;
    else if (['failed', 'partial'].includes(status)) result.attention += 1;
    return result;
  }, { ready: 0, indexing: 0, attention: 0 });
  const summary = card.querySelector(':scope > .venture-muted');
  if (summary) summary.textContent = `${counts.ready} ready · ${counts.indexing} indexing · ${counts.attention} needs attention`;
  const rows = Array.from(card.querySelectorAll(':scope > .venture-list > .venture-row'));
  for (const source of sources || []) {
    const row = rows.find(item => item.firstElementChild?.textContent?.trim() === String(source.display_name || '').trim());
    const meta = row?.querySelector('.venture-muted');
    if (meta) meta.textContent = `${source.access_mode} · ${sourceStatusText(source)}`;
  }
}

function synthesisText(job) {
  if (!job) return 'No synthesis jobs yet.';
  if (job.status === 'queued') return 'Synthesis queued.';
  if (job.status === 'running') return 'Synthesis running.';
  if (job.status === 'created') return 'Artifact Draft ready for review.';
  if (job.status === 'updated') return 'Artifact Draft updated.';
  if (job.status === 'completed') return 'Voyage Memory updated from published Artifact revelations.';
  if (job.status === 'no_insight') return 'No durable, evidence-backed Artifact was created.';
  if (job.status === 'failed') return job.safe_error_message || 'Synthesis failed.';
  return `Synthesis status: ${job.status}`;
}

function updateArgoCard(argo) {
  const card = document.querySelector('#venture-right-rail .ArgoStatusCard');
  if (!card) return;
  const muted = Array.from(card.children).filter(child => child.classList?.contains('venture-muted'));
  if (muted[0]) muted[0].textContent = `${Number(argo?.memory_count || 0)} Voyage Memory entries visible.`;
  const latest = (argo?.jobs || [])[0];
  if (muted[1]) muted[1].textContent = synthesisText(latest);
  if (latest && ['created', 'updated', 'completed', 'no_insight', 'failed'].includes(latest.status)) {
    const lifecycle = `${latest.id}:${latest.status}:${latest.finished_at || ''}`;
    if (lifecycle !== state.lastLifecycle) {
      state.lastLifecycle = lifecycle;
      queueArtifactRefresh(selectedQuestId());
    }
  }
}

function artifactTitle(artifact) {
  const value = String(artifact?.artifact_title || artifact?.title || '').trim();
  return value && !/^(?:insight|insights|summary|artifact|quest artifact|1\.?)$/i.test(value) ? value : 'Artifact needs a new summary';
}

async function openArtifact(documentId) {
  if (!documentId) return;
  try {
    const imported = window.documentModule ? null : await import('./document.js');
    const docs = window.documentModule || imported?.default || imported;
    await docs.loadDocument?.(documentId);
    docs.openPanel?.();
    docs.switchToDoc?.(documentId);
  } catch (_) {
    window.dispatchEvent(new CustomEvent('odysseus:toast', { detail: { type: 'error', message: 'Unable to open this Quest Artifact.' } }));
  }
}

async function publishArtifact(questId, proposalId) {
  if (!proposalId || state.managementBusy) return;
  state.managementBusy = true;
  try {
    await postJson(`/api/quests/${encodeURIComponent(questId)}/artifact-proposals/${encodeURIComponent(proposalId)}/publish`);
    await refreshCompactStatus();
    queueArtifactRefresh(questId);
  } catch (error) {
    window.dispatchEvent(new CustomEvent('odysseus:toast', { detail: { type: 'error', message: error.message || 'Unable to publish this Artifact.' } }));
  } finally {
    state.managementBusy = false;
  }
}

function createArtifactRow(artifact, questId, { review = false } = {}) {
  const row = document.createElement('div');
  row.className = 'venture-row ArtifactCard';
  const label = document.createElement('span');
  label.textContent = artifactTitle(artifact);
  const open = document.createElement('button');
  open.type = 'button';
  open.className = 'venture-btn';
  open.textContent = review ? 'Review' : 'Open';
  open.addEventListener('click', () => openArtifact(artifact.document_id || artifact.id));
  row.append(label, open);
  if (review && artifact.proposal_id) {
    const publish = document.createElement('button');
    publish.type = 'button';
    publish.className = 'venture-btn primary';
    publish.textContent = 'Publish';
    publish.disabled = state.managementBusy;
    publish.addEventListener('click', () => publishArtifact(questId, artifact.proposal_id));
    row.append(publish);
  }
  const meta = document.createElement('div');
  meta.className = 'venture-muted venture-full-span';
  const count = Number(artifact.key_point_count || 0);
  meta.textContent = `${review ? 'Awaiting Captain review' : 'Published'}${count ? ` · ${count} key point${count === 1 ? '' : 's'}` : ''}`;
  row.append(meta);
  return row;
}

async function refreshArtifactShelf(questId) {
  if (!questId || selectedQuestId() !== questId) return;
  const shelf = document.querySelector('#venture-right-rail .ArtifactShelf');
  if (!shelf) return;
  const payload = await getJson(`/api/quests/${encodeURIComponent(questId)}/artifacts`, 'artifacts');
  if (!payload || selectedQuestId() !== questId) return;
  const nodes = [];
  const heading = document.createElement('h3');
  heading.textContent = 'Quest Artifacts';
  nodes.push(heading);

  const reviewQueue = payload.review_queue || [];
  if (reviewQueue.length) {
    const reviewHeading = document.createElement('div');
    reviewHeading.className = 'venture-muted';
    reviewHeading.textContent = 'Awaiting your review';
    nodes.push(reviewHeading, ...reviewQueue.map(artifact => createArtifactRow(artifact, questId, { review: true })));
  }

  const documents = payload.documents || [];
  if (documents.length) {
    if (reviewQueue.length) {
      const publishedHeading = document.createElement('div');
      publishedHeading.className = 'venture-muted';
      publishedHeading.textContent = 'Published Artifacts';
      nodes.push(publishedHeading);
    }
    nodes.push(...documents.map(artifact => createArtifactRow(artifact, questId)));
  } else if (!reviewQueue.length && !(payload.gallery || []).length) {
    const empty = document.createElement('div');
    empty.className = 'venture-muted';
    empty.textContent = 'No Artifacts yet.';
    nodes.push(empty);
  }
  shelf.replaceChildren(...nodes);
}

function queueArtifactRefresh(questId) {
  if (state.artifactRefreshQueued || !questId) return;
  state.artifactRefreshQueued = true;
  queueMicrotask(() => {
    state.artifactRefreshQueued = false;
    refreshArtifactShelf(questId).catch(() => {});
    renderManagementCard(questId).catch(() => {});
  });
}

function managementButton(label, action, questId, select) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'venture-btn';
  button.textContent = label;
  button.disabled = state.managementBusy;
  button.addEventListener('click', async () => {
    state.managementBusy = true;
    button.disabled = true;
    try {
      await postJson(`/api/quests/${encodeURIComponent(questId)}/manage`, {
        action,
        artifact_proposal_id: select?.value || undefined,
      });
      await refreshCompactStatus();
      await renderManagementCard(questId);
    } catch (error) {
      window.dispatchEvent(new CustomEvent('odysseus:toast', { detail: { type: 'error', message: error.message || 'Quest management failed.' } }));
    } finally {
      state.managementBusy = false;
      button.disabled = false;
    }
  });
  return button;
}

async function renderManagementCard(questId) {
  if (!questId || selectedQuestId() !== questId) return;
  const rail = document.getElementById('venture-right-rail');
  if (!rail || !window.argosVentureCapabilities?.can_review_artifacts) return;
  const payload = await getJson(`/api/quests/${encodeURIComponent(questId)}/manage`, 'management');
  if (!payload || selectedQuestId() !== questId) return;
  let card = rail.querySelector('.QuestManagementCard');
  if (!card) {
    card = document.createElement('section');
    card.className = 'venture-card QuestManagementCard';
    rail.insertBefore(card, rail.querySelector('.ArgoStatusCard') || null);
  }
  const heading = document.createElement('h3');
  heading.textContent = 'Manage Quest';
  const summary = document.createElement('div');
  summary.className = 'venture-muted';
  const jobs = payload.jobs || [];
  const active = jobs.filter(job => ['queued', 'running'].includes(job.status));
  summary.textContent = active.length
    ? `${active.length} synthesis job${active.length === 1 ? '' : 's'} active.`
    : synthesisText(jobs[0]);
  const actions = document.createElement('div');
  actions.className = 'venture-source-actions';
  actions.append(managementButton('Synthesize Artifact', 'synthesize_artifact', questId));
  const select = document.createElement('select');
  select.className = 'venture-btn';
  const artifacts = payload.published_artifacts || [];
  select.disabled = !artifacts.length;
  for (const artifact of artifacts) {
    const option = document.createElement('option');
    option.value = artifact.id;
    option.textContent = artifact.title || 'Published Artifact';
    select.append(option);
  }
  actions.append(select, managementButton('Synthesize Memory', 'synthesize_memory', questId, select));
  card.replaceChildren(heading, summary, actions);
}

async function refreshCompactStatus() {
  if (state.inFlight || document.hidden) return;
  const questId = selectedQuestId();
  if (!questId || !document.getElementById('venture-right-rail')) {
    stopPolling();
    return;
  }
  state.inFlight = true;
  try {
    const payload = await getJson(`/api/quests/${encodeURIComponent(questId)}/live-status`, 'live-status');
    if (!payload || selectedQuestId() !== questId) return;
    updateSourcesCard(payload.sources || []);
    updateArgoCard(payload.argo || {});
    syncPolling(questId, payload);
  } finally {
    state.inFlight = false;
  }
}

function queueRefresh() {
  if (state.refreshQueued) return;
  state.refreshQueued = true;
  queueMicrotask(() => {
    state.refreshQueued = false;
    refreshCompactStatus().catch(() => {});
    const questId = selectedQuestId();
    if (questId) {
      renderManagementCard(questId).catch(() => {});
      refreshArtifactShelf(questId).catch(() => {});
    }
  });
}

function install() {
  if (state.initialized) return;
  state.initialized = true;
  document.addEventListener('odysseus:session-selected', queueRefresh);
  document.addEventListener('odysseus:workspace-tab-activated', queueRefresh);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) queueRefresh(); });
  window.addEventListener('beforeunload', () => {
    stopPolling();
    state.controllers.forEach(controller => controller.abort());
  });
  queueRefresh();
  Promise.resolve(analysisModule.initVentureDataAnalysis?.()).catch(() => {});
  Promise.resolve(analysisDecisionModule.initVentureAnalysisDecisionTrail?.()).catch(() => {});
}

if (document.body) install();
else document.addEventListener('DOMContentLoaded', install, { once: true });

export default legacy;
