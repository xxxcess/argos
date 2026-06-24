// First-class Local Diffusers card for Cookbook → Serve.
// The existing Cookbook serve task API supplies lifecycle/logging/stop handling;
// this card supplies an image-specific, memory-safe command and promotes its
// auto-created endpoint to model_type="image" immediately after launch.

const LOCAL_PORT = 7861;
const LOCAL_ENDPOINT_NAME = 'Local Diffusers Image';
const PROFILES = {
  'sd-turbo': {
    model: 'local-sd-turbo',
    note: 'Recommended for Mac M2 / 8 GB · 512×512 · one step',
    repo: 'stabilityai/sd-turbo',
  },
  'sd15-lcm': {
    model: 'local-sd15-lcm',
    note: 'Advanced small SD 1.5-compatible profile · keep 512×512',
    repo: 'runwayml/stable-diffusion-v1-5',
  },
};

function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

async function jsonFetch(url, opts) {
  const res = await fetch(url, { credentials: 'same-origin', ...(opts || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || `HTTP ${res.status}`);
  return data;
}

function status(text, isError) {
  const node = document.getElementById('cookbook-local-image-status');
  if (!node) return;
  node.textContent = text || '';
  node.style.color = isError ? 'var(--red)' : '';
}

function sameLocalServer(endpoint) {
  try {
    const url = new URL(endpoint?.base_url || '');
    return Number(url.port) === LOCAL_PORT
      && ['localhost', '127.0.0.1', 'host.docker.internal'].includes(url.hostname);
  } catch (_) { return false; }
}

async function promoteEndpoint(endpointId, model) {
  let id = endpointId || '';
  for (let attempt = 0; !id && attempt < 20; attempt += 1) {
    const endpoints = await jsonFetch('/api/model-endpoints').catch(() => []);
    const found = (endpoints || []).find(sameLocalServer);
    if (found) id = found.id;
    else await sleep(500);
  }
  if (!id) throw new Error('Cookbook did not register the local server endpoint');

  await jsonFetch(`/api/model-endpoints/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: LOCAL_ENDPOINT_NAME,
      model_type: 'image',
      endpoint_kind: 'local',
      pinned_models: [model],
      is_enabled: true,
    }),
  });
  await Promise.all([
    jsonFetch('/api/prefs/image_endpoint_id', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value: id }),
    }),
    jsonFetch('/api/auth/settings', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ image_gen_enabled: true, image_model: model, image_quality: 'medium' }),
    }),
  ]);
  window.dispatchEvent(new CustomEvent('ge:model-endpoints-updated'));
  return id;
}

async function installRuntime() {
  const button = document.getElementById('cookbook-local-image-install');
  if (button) button.disabled = true;
  status('Starting Diffusers dependency task…');
  try {
    await jsonFetch('/api/model/serve', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        repo_id: 'pip-diffusers',
        cmd: "python3 -m pip install --no-cache-dir 'diffusers[torch]' transformers accelerate safetensors",
      }),
    });
    status('Install task started. Open Running to follow progress, then launch the image server.');
    window.cookbookModule?.open?.({ tab: 'Running' });
  } catch (err) {
    status(`Could not start install: ${err.message}`, true);
  } finally {
    if (button) button.disabled = false;
  }
}

async function startServer() {
  const button = document.getElementById('cookbook-local-image-start');
  const profileId = document.getElementById('cookbook-local-image-profile')?.value || 'sd-turbo';
  const customRepo = document.getElementById('cookbook-local-image-repo')?.value.trim() || '';
  const profile = PROFILES[profileId] || PROFILES['sd-turbo'];
  if (button) button.disabled = true;
  status(`Launching ${profileId} through Cookbook…`);
  try {
    const repo = customRepo || profile.repo;
    const model = customRepo ? `local-${profileId}` : profile.model;
    const repoArg = customRepo ? ` --model-repo ${JSON.stringify(repo)}` : '';
    const result = await jsonFetch('/api/model/serve', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        repo_id: repo,
        cmd: `PYTORCH_ENABLE_MPS_FALLBACK=1 python3 -m src.local_image_server --profile ${profileId}${repoArg} --served-model-id ${model} --device auto --host 127.0.0.1 --port ${LOCAL_PORT} --max-size 512`,
      }),
    });
    await promoteEndpoint(result.endpoint_id, model);
    status(`Local ${profileId} is selected in AI Defaults. Generate an image through normal chat.`);
    window.cookbookModule?.open?.({ tab: 'Running' });
  } catch (err) {
    status(`Could not launch local image server: ${err.message}`, true);
  } finally {
    if (button) button.disabled = false;
  }
}

function ensurePanel() {
  const serveGroup = document.querySelector('#cookbook-modal [data-backend-group="Serve"]');
  if (!serveGroup || document.getElementById('cookbook-local-image-server')) return;
  const card = document.createElement('div');
  card.id = 'cookbook-local-image-server';
  card.className = 'admin-card';
  card.style.cssText = 'margin-bottom:10px;';
  card.innerHTML = `
    <h2 style="display:flex;align-items:center;gap:6px;margin-bottom:5px;">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>
      Diffusers Image Server
    </h2>
    <div style="font-size:11px;opacity:0.65;line-height:1.35;margin-bottom:8px;">Launch a small local image model as an Argos Image endpoint. The default is safe for an 8 GB Apple Silicon Mac: SD Turbo, 512×512, one sampling step.</div>
    <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:6px;">
      <label style="font-size:11px;opacity:0.7;">Profile</label>
      <select id="cookbook-local-image-profile" class="cookbook-field-input" style="min-width:230px;height:28px;">
        <option value="sd-turbo">SD Turbo — recommended</option>
        <option value="sd15-lcm">SD 1.5-compatible — advanced</option>
      </select>
      <button type="button" class="cookbook-btn" id="cookbook-local-image-install">Install runtime</button>
      <button type="button" class="cookbook-btn" id="cookbook-local-image-start">Start local image server</button>
    </div>
    <input id="cookbook-local-image-repo" class="cookbook-field-input" style="width:100%;box-sizing:border-box;height:28px;" placeholder="Optional custom Hugging Face repo (advanced)" />
    <div id="cookbook-local-image-profile-note" style="font-size:10.5px;opacity:0.55;margin-top:5px;">${PROFILES['sd-turbo'].note}</div>
    <div id="cookbook-local-image-status" style="font-size:11px;margin-top:6px;"></div>
  `;
  serveGroup.insertBefore(card, serveGroup.firstChild);
  document.getElementById('cookbook-local-image-install')?.addEventListener('click', installRuntime);
  document.getElementById('cookbook-local-image-start')?.addEventListener('click', startServer);
  document.getElementById('cookbook-local-image-profile')?.addEventListener('change', event => {
    const profile = PROFILES[event.target.value] || PROFILES['sd-turbo'];
    const note = document.getElementById('cookbook-local-image-profile-note');
    if (note) note.textContent = profile.note;
  });
}

function install() {
  const body = document.querySelector('#cookbook-modal .cookbook-body');
  if (!body || body.dataset.localImageCookbook === '1') return;
  body.dataset.localImageCookbook = '1';
  new MutationObserver(ensurePanel).observe(body, { childList: true, subtree: true });
  ensurePanel();
}

function scheduleInstall() { setTimeout(install, 30); }

document.addEventListener('click', event => {
  if (event.target.closest('#tool-cookbook-btn, #rail-cookbook')) scheduleInstall();
});
window.addEventListener('load', scheduleInstall, { once: true });
