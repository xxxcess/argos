// Transparent decision trail for Argos Venture Data Analysis.
// This module reads the fact-based selection_decisions payload and augments the
// already-rendered workspace. It never renders model chain-of-thought.

const API_BASE = window.API_BASE || '';
let initialized = false;
let observer = null;
let timer = null;
let requestEpoch = 0;
let cachedSessionId = '';
let cachedPayload = null;

function element(tag, className = '', text = '') {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

function currentSessionId() {
  const id = window.sessionModule?.getCurrentSessionId?.();
  return id ? String(id) : '';
}

function workspace() {
  const panel = document.getElementById('venture-analysis-workspace');
  return panel && !panel.hidden ? panel : null;
}

function installStyles() {
  if (document.getElementById('venture-analysis-decision-trail-style')) return;
  const style = document.createElement('style');
  style.id = 'venture-analysis-decision-trail-style';
  style.textContent = `
    .venture-analysis-selection-context { border-left:3px solid var(--accent, var(--fg)); }
    .venture-analysis-selection-context h3 { margin-bottom:6px; }
    .venture-analysis-selection-context p { margin:0 0 8px; font-size:12px; line-height:1.45; }
    .venture-analysis-selection-context strong { font-weight:650; }
    .venture-analysis-selection-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; }
    .venture-analysis-selection-list { margin:0; padding-left:18px; display:flex; flex-direction:column; gap:5px; font-size:12px; line-height:1.4; }
    .venture-analysis-selection-list li { opacity:.84; }
    .venture-analysis-selection-label { display:block; margin:0 0 5px; font-size:11px; letter-spacing:.05em; text-transform:uppercase; opacity:.66; }
    .venture-analysis-selection-reasons { margin-top:8px; border-top:1px solid color-mix(in srgb, var(--border) 60%, transparent); padding-top:7px; }
    .venture-analysis-selection-reasons summary { cursor:pointer; font-size:11px; opacity:.72; }
    .venture-analysis-selection-reasons ul { margin:6px 0 0; padding-left:17px; display:flex; flex-direction:column; gap:4px; font-size:11px; line-height:1.38; opacity:.84; }
    @media (max-width:620px) { .venture-analysis-selection-grid { grid-template-columns:1fr; } }
  `;
  document.head.appendChild(style);
}

function list(items = []) {
  const node = element('ul', 'venture-analysis-selection-list');
  for (const item of items) node.append(element('li', '', String(item)));
  return node;
}

function addReasons(target, reasons = []) {
  if (!target) return;
  target.querySelector('.venture-analysis-selection-reasons')?.remove();
  if (!reasons.length) return;
  const details = element('details', 'venture-analysis-selection-reasons');
  details.append(element('summary', '', 'Why this was selected'));
  details.append(list(reasons));
  target.append(details);
}

function renderSummary(panel, decisions) {
  panel.querySelector('#venture-analysis-selection-context')?.remove();
  const layout = panel.querySelector('#venture-analysis-layout');
  const main = layout?.querySelector('.venture-analysis-main');
  if (!main || !decisions) return;

  const card = element('section', 'venture-analysis-card venture-analysis-selection-context');
  card.id = 'venture-analysis-selection-context';
  card.append(element('h3', '', 'Why you are seeing these findings'));
  card.append(element('p', '', decisions.selection_detail || 'Selections are based on verified dataset signals.'));

  const grid = element('div', 'venture-analysis-selection-grid');
  const selection = element('div');
  selection.append(element('span', 'venture-analysis-selection-label', decisions.selection_label || 'Selection method'));
  const source = decisions.selection_source === 'llm'
    ? 'Configured model selected from verified candidates'
    : decisions.selection_source === 'saved'
      ? 'Stored selection reused'
      : 'Objective-aware deterministic fallback';
  selection.append(element('p', '', source));
  selection.append(list(decisions.dataset_signals || []));
  grid.append(selection);

  const objective = element('div');
  objective.append(element('span', 'venture-analysis-selection-label', 'Objective decisions'));
  if (decisions.objective?.provided) {
    objective.append(element('p', '', decisions.objective.text || 'Objective supplied'));
    objective.append(list(decisions.objective.decisions || []));
  } else {
    objective.append(element('p', '', 'No objective was supplied. Argos selected from detected column types and data-quality signals.'));
  }
  grid.append(objective);
  card.append(grid);

  const briefingCard = [...main.querySelectorAll(':scope > .venture-analysis-card')]
    .find(node => node.querySelector('.venture-analysis-insight-grid'));
  if (briefingCard) briefingCard.insertAdjacentElement('afterend', card);
  else main.insertBefore(card, main.firstElementChild?.nextElementSibling || null);
}

function apply(payload) {
  const panel = workspace();
  if (!panel || !payload?.selection_decisions) return;
  const decisions = payload.selection_decisions;
  renderSummary(panel, decisions);

  const cardById = new Map((decisions.cards || []).map(item => [String(item.id || ''), item.reasons || []]));
  const visualById = new Map((decisions.visuals || []).map(item => [String(item.id || ''), item.reasons || []]));
  const selectedCards = payload.briefing?.cards || [];
  const selectedVisuals = payload.visuals || [];
  const cardNodes = [...panel.querySelectorAll('.venture-analysis-insight')];
  const visualNodes = [...panel.querySelectorAll('.venture-analysis-visual')];

  selectedCards.forEach((card, index) => addReasons(cardNodes[index], card.selection_reasons || cardById.get(String(card.id || '')) || []));
  selectedVisuals.forEach((visual, index) => addReasons(visualNodes[index], visual.selection_reasons || visualById.get(String(visual.id || '')) || []));
}

async function refresh() {
  const panel = workspace();
  const sessionId = currentSessionId();
  if (!panel || !sessionId) return;
  const epoch = ++requestEpoch;
  try {
    const response = await fetch(`${API_BASE}/api/venture/analysis/sessions/${encodeURIComponent(sessionId)}`, { credentials: 'same-origin' });
    if (!response.ok) return;
    const payload = await response.json();
    if (epoch !== requestEpoch || currentSessionId() !== sessionId || !workspace()) return;
    cachedSessionId = sessionId;
    cachedPayload = payload;
    apply(payload);
  } catch (_) {
    // The base workspace already renders its own API failures. Do not add noise.
  }
}

function scheduleRefresh({ force = false } = {}) {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    const sessionId = currentSessionId();
    const panel = workspace();
    if (!sessionId || !panel) return;
    if (!force && cachedSessionId === sessionId && cachedPayload) {
      apply(cachedPayload);
      return;
    }
    refresh();
  }, 80);
}

function mutationNeedsRefresh(records) {
  return records.some(record => [...record.addedNodes, ...record.removedNodes].some(node => {
    if (node.nodeType !== Node.ELEMENT_NODE) return false;
    return !node.matches?.('.venture-analysis-selection-context, .venture-analysis-selection-reasons')
      && !node.closest?.('.venture-analysis-selection-context, .venture-analysis-selection-reasons');
  }));
}

export function initVentureAnalysisDecisionTrail() {
  if (initialized) return;
  initialized = true;
  installStyles();
  document.addEventListener('odysseus:session-selected', () => {
    cachedSessionId = '';
    cachedPayload = null;
    scheduleRefresh({ force: true });
  });
  document.addEventListener('odysseus:workspace-tab-activated', () => scheduleRefresh({ force: true }));
  document.addEventListener('odysseus:new-chat-shown', () => {
    cachedSessionId = '';
    cachedPayload = null;
  });
  observer = new MutationObserver(records => {
    // Base workspace renders must always re-fetch so a replacement CSV receives
    // fresh explanations. Mutations inside this module's own trail are ignored.
    if (mutationNeedsRefresh(records)) scheduleRefresh({ force: true });
  });
  observer.observe(document.body, { childList: true, subtree: true });
  scheduleRefresh({ force: true });
}

export default { initVentureAnalysisDecisionTrail };
