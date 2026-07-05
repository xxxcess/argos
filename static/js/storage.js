// static/js/storage.js
// Centralized localStorage access with key constants and JSON parse safety

// Side-effect integrations load here because storage.js is the first UI module
// loaded by index.html on every page.
import './localImageIntegration.js';
import './localImageFreshLoad.js';
import './localImageEndpointRecovery.js';
import './localImageChatGuard.js';
import './localImageCookbookPanel.js';
import './streamRecoveryComposerGuard.js';
import './meetingCaptureWorkspace.js';
import './liveCaptureDocumentDock.js';

export const KEYS = {
  THEME: 'odysseus-theme',
  TOGGLES: 'odysseus-toggles',
  SIDEBAR_COLLAPSED: 'sidebar-collapsed',
  SIDEBAR_WIDTH: 'sidebar-width',
  SIDEBAR_SIDE: 'sidebar-side',
  CURRENT_SESSION: 'currentSessionId',
  COMPARE_SAVE: 'compare-save-results',
  COMPARE_CHAT: 'compare-continue-chat',
  COMPARE_BLIND: 'compare-blind',
  COMPARE_RANDOM: 'compare-randomize',
  MODELS_EXPANDED: 'odysseus-models-expanded',
  MODEL_ENDPOINTS: 'odysseus-model-endpoints',
  MODEL_SELECTED: 'odysseus-selected-model',
  SORT_ORDER: 'odysseus-sessions-sort',
  CHAT_SEARCH_SCOPE: 'odysseus-search-scope',
  INCOGNITO: 'odysseus-incognito',
  RAG_ACTIVE: 'odysseus-rag-active',
  MCP_ACTIVE: 'odysseus-mcp-active',
  SECTION_ORDER: 'sidebar-section-order',
  ADMIN_LAST_TAB: 'admin-last-tab',
  DENSITY: 'odysseus-density',
  UI_SCALE: 'odysseus-ui-scale',
  WORKSPACE: 'odysseus-workspace'
};

export function getJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return fallback !== undefined ? fallback : null;
    return JSON.parse(raw);
  } catch (e) {
    console.warn('[Storage] Failed to parse key "' + key + '":', e.message);
    return fallback !== undefined ? fallback : null;
  }
}

export function setJSON(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (e) {
    console.warn('[Storage] Failed to set key "' + key + '":', e.message);
  }
}

export function get(key, fallback) {
  try {
    const val = localStorage.getItem(key);
    return val !== null ? val : (fallback !== undefined ? fallback : null);
  } catch (e) {
    return fallback !== undefined ? fallback : null;
  }
}

export function set(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (e) {
    console.warn('[Storage] Failed to set key "' + key + '":', e.message);
  }
}

export function remove(key) {
  try {
    localStorage.removeItem(key);
  } catch (e) {
    // Ignore removal errors.
  }
}

export function loadToggleState() {
  return getJSON(KEYS.TOGGLES, {});
}

export function saveToggleState(state) {
  setJSON(KEYS.TOGGLES, state);
}

export function getToggle(name, fallback) {
  const state = loadToggleState();
  return state[name] !== undefined ? state[name] : (fallback !== undefined ? fallback : false);
}

export function setToggle(name, value) {
  const state = loadToggleState();
  state[name] = value;
  saveToggleState(state);
}

// Most legacy UI modules import Storage as the default binding. Preserve that
// contract alongside named exports so a storage integration cannot block app
// initialization at module-load time.
const Storage = {
  KEYS,
  getJSON,
  setJSON,
  get,
  set,
  remove,
  loadToggleState,
  saveToggleState,
  getToggle,
  setToggle,
};

export default Storage;
