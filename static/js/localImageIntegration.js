// Local Diffusers image integration.
//
// This module is deliberately additive: it replaces the old Image Generation
// card's inpaint-only model discovery with endpoint-aware selection, adds
// Cookbook-backed install/launch controls, and persists the selected endpoint
// as a per-user preference. It loads after the existing Settings module so it
// remains compatible with older Settings markup.

const LOCAL_MODEL_ID = 'local-sd-turbo';
const LOCAL_PORT = 7861;
const LOCAL_ENDPOINT_NAME = 'Local Diffusers Image';
let initialized = false;
let state = { endpoints: [], settings: {}, prefs: {}, refreshTimer: null };

function byId(id) { return document.getElementById(id); }
function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

function isCloudImageModel(model) {
  const value = String(model || '').toLowerCase();
  return value.startsWith('gpt-image') || value.startsWith('dall-e') || value.startsWith('chatgpt-image');
}

function isLocalEndpoint(endpoint) {
  const category = String(endpoint?.category || '').toLowerCase();
  if (category === 'local') return true;
  try {
    const host = new URL(endpoint?.base_url || '').hostname.toLowerCase();
    return host === 'localhost' || host === '127.0.0.1' || host === 'host.docker.internal'
      || host.endsWith('.local') || /^10\./.test(host) || /^192\.168\./.test(host)
      || /^172\.(1[6-9]|2\d|3[01])\./.test(host);
  } catch (_) { return false; }
}

function isImageEndpoint(endpoint) {
  if (!endpoint || !endpoint.is_enabled) return false;
  if (String(endpoint.model_type || '').toLowerCase() === 'image') return true;
  return (endpoint.models || []).some(isCloudImageModel);
}

function imageEndpoints() {
  return (state.endpoints || []).filter(isImageEndpoint);
}

async function fetchJson(url, opts) {
  const res = await fetch(url, { credentials: 'same-origin', ...(opts || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
  return data;
}

async function loadState() {
  const [endpoints, settings, prefs] = await Promise.all([
    fetchJson('/api/model-endpoints').catch(() => []),
    fetchJson('/api/auth/settings').catch(() => ({})),
    fetchJson('/api/prefs').catch(() => ({})),
  ]);
  state.endpoints = Array.isArray(endpoints) ? endpoints : [];
  state.settings = settings || {};
  state.prefs = prefs || {};
}

function cardForImageSettings() {
  return byId('set-imgModelSelect')?.closest('.admin-card') || null;
}

function setStatus(text, error) {
  const status = byId('local-image-runtime-status') || byId('set-imgSettingsMsg');
  if (!status) return;
  status.textContent = text || '';
  status.style.color = error ? 'var(--red)' : 'var(--fg)';
}

function ensureImageEndpointRow() {
  const modelSelect = byId('set-imgModelSelect');
  const card = cardForImageSettings();
  if (!modelSelect || !card) return null;
  let endpointSelect = byId('set-imgEndpointSelect');
  if (endpointSelect) return endpointSelect;

  const modelRow = modelSelect.closest('div[style*="align-items"]');
  if (!modelRow) return null;
  const row = document.createElement('div');
  row.className = 'settings-row';
  row.id = 'set-imgEndpointRow';
  row.style.cssText = 'display:flex;align-items:center;gap:0.75rem;';
  row.innerHTML = '<label class="settings-label">Endpoint</label><select id="set-imgEndpointSelect" class="settings-select"><option value="">Auto-detect</option></select>';
  modelRow.parentNode.insertBefore(row, modelRow);
  endpointSelect = byId('set-imgEndpointSelect');
  endpointSelect?.addEventListener('change', async () => {
    renderModelOptions('');
    await saveImageDefaults();
  });
  return endpointSelect;
}

function ensureRuntimePanel() {
  const card = cardForImageSettings();
  if (!card || byId('local-image-runtime')) return;
  const panel = document.createElement('div');
  panel.id = 'local-image-runtime';
  panel.style.cssText = 'margin-top:10px;padding-top:10px;border-top:1px solid var(--border);display:flex;flex-direction:column;gap:7px;';
  panel.innerHTML = `
    <div style="font-size:12px;font-weight:600;">Local Diffusers</div>
    <div style="font-size:11px;opacity:0.62;line-height:1.35;">Mac M2 / 8 GB default: SD Turbo at 512×512, one step, serialized inference. No cloud API key or image billing.</div>
    <div style="display:flex;gap:6px;flex-wrap:wrap;">
      <button type="button" class="admin-btn-sm" id="local-image-open-cookbook">Open Cookbook</button>
      <button type="button" class="admin-btn-sm" id="local-image-install">Install Diffusers runtime</button>
      <button type="button" class="admin-btn-add" id="local-image-start">Start SD Turbo (512px)</button>
    </div>
    <div id="local-image-runtime-status" style="font-size:11px;opacity:0.85;"></div>
  `;
  card.appendChild(panel);

  byId('local-image-open-cookbook')?.addEventListener('click', () => {
    if (window.cookbookModule?.open) window.cookbookModule.open({ tab: 'Dependencies' });
    else byId('tool-cookbook-btn')?.click();
  });
  byId('local-image-install')?.addEventListener('click', installRuntime);
  byId('local-image-start')?.addEventListener('click', startLocalServer);
}

function renderEndpointOptions() {
  const select = ensureImageEndpointRow();
  if (!select) return;
  const selected = state.prefs.image_endpoint_id || state.settings.image_endpoint_id || select.value || '';
  select.innerHTML = '<option value="">Auto-detect</option>';
  const local = imageEndpoints().filter(isLocalEndpoint);
  const remote = imageEndpoints().filter(ep => !isLocalEndpoint(ep));
  const appendGroup = (label, list) => {
    if (!list.length) return;
    const group = document.createElement('optgroup');
    group.label = label;
    list.forEach(ep => {
      const opt = document.createElement('option');
      opt.value = ep.id;
      const health = ep.online ? '' : ' (offline)';
      opt.textContent = `${ep.name}${health}`;
      group.appendChild(opt);
    });
    select.appendChild(group);
  };
  appendGroup('Local Image Models', local);
  appendGroup('Remote Image Models', remote);
  if (selected && Array.from(select.options).some(opt => opt.value === selected)) select.value = selected;
}

function endpointForSelection() {
  const id = byId('set-imgEndpointSelect')?.value || '';
  return (state.endpoints || []).find(ep => String(ep.id) === String(id)) || null;
}

function renderModelOptions(explicitModel) {
  const modelSelect = byId('set-imgModelSelect');
  if (!modelSelect) return;
  const previous = explicitModel !== undefined ? explicitModel : (state.settings.image_model || modelSelect.value || '');
  const selectedEndpoint = endpointForSelection();
  const source = selectedEndpoint ? [selectedEndpoint] : imageEndpoints();
  const models = [];
  source.forEach(ep => (ep.models || []).forEach(model => {
    const value = String(model || '').trim();
    if (value && !models.includes(value)) models.push(value);
  }));
  modelSelect.innerHTML = '';
  if (!models.length) {
    const opt = document.createElement('option');
    opt.value = '';
    opt.textContent = 'No image models detected';
    modelSelect.appendChild(opt);
    setStatus('No image generation models found. Start a Local Diffusers Image Server from Cookbook → Serve, or add an Image endpoint.', true);
    return;
  }
  models.sort((a, b) => a.localeCompare(b));
  models.forEach(model => {
    const opt = document.createElement('option');
    opt.value = model;
    opt.textContent = model;
    modelSelect.appendChild(opt);
  });
  if (previous && models.includes(previous)) modelSelect.value = previous;
  else modelSelect.value = models[0];
  const endpoint = endpointForSelection();
  if (endpoint) {
    setStatus(`${endpoint.name}${endpoint.online ? ' is ready.' : ' is configured; waiting for its server.'}`, !endpoint.online);
  } else {
    setStatus('Select an Image endpoint to make image generation deterministic.', false);
  }
}

async function saveImageDefaults() {
  const endpointId = byId('set-imgEndpointSelect')?.value || '';
  const model = byId('set-imgModelSelect')?.value || '';
  const quality = byId('set-imgQualitySelect')?.value || 'medium';
  const enabled = !!byId('set-imgEnabledToggle')?.checked;
  try {
    await Promise.all([
      fetchJson('/api/prefs/image_endpoint_id', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ value: endpointId }),
      }),
      fetchJson('/api/auth/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ image_gen_enabled: enabled, image_model: model, image_quality: quality }),
      }),
    ]);
    state.prefs.image_endpoint_id = endpointId;
    state.settings.image_model = model;
    state.settings.image_quality = quality;
    state.settings.image_gen_enabled = enabled;
    setStatus('Saved', false);
  } catch (err) {
    setStatus(`Failed to save: ${err.message}`, true);
  }
}

async function startCookbookTask(repoId, cmd) {
  return fetchJson('/api/model/serve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ repo_id: repoId, cmd }),
  });
}

async function installRuntime() {
  const button = byId('local-image-install');
  if (button) button.disabled = true;
  setStatus('Starting Cookbook dependency task…', false);
  try {
    await startCookbookTask(
      'pip-diffusers',
      "python3 -m pip install --no-cache-dir 'diffusers[torch]' transformers accelerate safetensors"
    );
    setStatus('Dependency install started. Follow progress in Cookbook → Running, then start SD Turbo.', false);
    if (window.cookbookModule?.open) window.cookbookModule.open({ tab: 'Running' });
  } catch (err) {
    setStatus(`Could not start dependency install: ${err.message}`, true);
  } finally {
    if (button) button.disabled = false;
  }
}

function sameLocalServer(endpoint) {
  try {
    const url = new URL(endpoint?.base_url || '');
    return Number(url.port) === LOCAL_PORT && ['localhost', '127.0.0.1', 'host.docker.internal'].includes(url.hostname);
  } catch (_) { return false; }
}

async function registerStartedServer() {
  for (let i = 0; i < 20; i += 1) {
    const endpoints = await fetchJson('/api/model-endpoints').catch(() => []);
    const serverEndpoint = (endpoints || []).find(sameLocalServer);
    if (serverEndpoint) {
      await fetchJson(`/api/model-endpoints/${encodeURIComponent(serverEndpoint.id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: LOCAL_ENDPOINT_NAME,
          model_type: 'image',
          endpoint_kind: 'local',
          pinned_models: [LOCAL_MODEL_ID],
          is_enabled: true,
        }),
      });
      await refreshImageDefaults();
      const select = byId('set-imgEndpointSelect');
      if (select) select.value = serverEndpoint.id;
      renderModelOptions(LOCAL_MODEL_ID);
      await saveImageDefaults();
      window.dispatchEvent(new CustomEvent('ge:model-endpoints-updated'));
      return true;
    }
    await sleep(750);
  }
  return false;
}

async function startLocalServer() {
  const button = byId('local-image-start');
  if (button) button.disabled = true;
  setStatus('Starting local SD Turbo in Cookbook…', false);
  try {
    await startCookbookTask(
      'stabilityai/sd-turbo',
      'PYTORCH_ENABLE_MPS_FALLBACK=1 python3 -m src.local_image_server --profile sd-turbo --served-model-id local-sd-turbo --device auto --host 127.0.0.1 --port 7861 --max-size 512'
    );
    if (window.cookbookModule?.open) window.cookbookModule.open({ tab: 'Running' });
    const registered = await registerStartedServer();
    if (!registered) {
      setStatus('Launch task started. The server will appear here once it reaches /v1/models; refresh this card after startup.', false);
    } else {
      setStatus('Local SD Turbo selected. New image requests will use this endpoint.', false);
    }
  } catch (err) {
    setStatus(`Could not start local SD Turbo: ${err.message}`, true);
  } finally {
    if (button) button.disabled = false;
  }
}

async function refreshImageDefaults() {
  await loadState();
  renderEndpointOptions();
  renderModelOptions();
}

function scheduleRefresh() {
  clearTimeout(state.refreshTimer);
  state.refreshTimer = setTimeout(() => { refreshImageDefaults().catch(() => {}); }, 80);
}

function init() {
  if (initialized || !byId('set-imgModelSelect')) return;
  initialized = true;
  ensureImageEndpointRow();
  ensureRuntimePanel();
  byId('set-imgModelSelect')?.addEventListener('change', saveImageDefaults);
  byId('set-imgQualitySelect')?.addEventListener('change', saveImageDefaults);
  byId('set-imgEnabledToggle')?.addEventListener('change', saveImageDefaults);
  window.addEventListener('ge:model-endpoints-updated', scheduleRefresh);
  document.addEventListener('click', event => {
    if (event.target.closest('[data-settings-tab="ai"]')) scheduleRefresh();
  });
  refreshImageDefaults().catch(() => {});
  // Settings.js loads its original models asynchronously. Re-render after it
  // settles so stale hard-coded “not detected” entries never win the race.
  setTimeout(scheduleRefresh, 500);
  setTimeout(scheduleRefresh, 1600);
}

if (document.readyState === 'complete') setTimeout(init, 0);
else window.addEventListener('load', () => setTimeout(init, 0), { once: true });

export { refreshImageDefaults };
