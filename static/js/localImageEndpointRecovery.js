// Reconcile the Local Diffusers Image endpoint after launches and refreshes.
//
// Older Cookbook paths can register the same `src.local_image_server` process as
// generic local endpoints such as `sd-turbo` / `Local SD-Turbo` at localhost:7861
// or localhost:8000. This module owns one canonical row:
//
//   Local Diffusers Image → http://127.0.0.1:7861/v1 → local-*
//
// It only consolidates loopback entries that identify as the active local image
// model or one of those generated aliases. Ollama and unrelated local services
// are deliberately not candidates.

const LOCAL_BASE_URL = 'http://127.0.0.1:7861/v1';
const LOCAL_ENDPOINT_NAME = 'Local Diffusers Image';
const CANONICAL_PORT = 7861;
const LEGACY_IMAGE_PORTS = new Set([7861, 8000, 8100]);
const GENERATED_NAME_ALIASES = new Set([
  'local diffusers image',
  'sd turbo',
  'local sd turbo',
]);
let inFlight = null;

async function jsonFetch(url, options) {
  const response = await fetch(url, { credentials: 'same-origin', ...(options || {}) });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || `HTTP ${response.status}`);
  return data;
}

function isLocalImageModel(model) {
  return String(model || '').trim().toLowerCase().startsWith('local-');
}

function endpointUrl(endpoint) {
  try {
    return new URL(endpoint?.base_url || '');
  } catch (_) {
    return null;
  }
}

function isLoopbackEndpoint(endpoint) {
  const url = endpointUrl(endpoint);
  return !!url && ['127.0.0.1', 'localhost', '::1', 'host.docker.internal'].includes(url.hostname);
}

function endpointPort(endpoint) {
  const url = endpointUrl(endpoint);
  return url ? Number(url.port || (url.protocol === 'https:' ? 443 : 80)) : 0;
}

function normalizedName(endpoint) {
  return String(endpoint?.name || '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function flattenModelIds(value) {
  if (Array.isArray(value)) return value.flatMap(flattenModelIds);
  if (typeof value === 'string') {
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed)) return parsed.map(item => String(item || '').trim());
    } catch (_) {
      // Plain model id; keep it below.
    }
    return [value.trim()];
  }
  return value ? [String(value).trim()] : [];
}

function endpointModelIds(endpoint) {
  return [
    ...flattenModelIds(endpoint?.models),
    ...flattenModelIds(endpoint?.pinned_models),
    ...flattenModelIds(endpoint?.cached_models),
  ].filter(Boolean).map(value => value.toLowerCase());
}

function isLocalImageDuplicate(endpoint, model) {
  if (!isLoopbackEndpoint(endpoint) || !LEGACY_IMAGE_PORTS.has(endpointPort(endpoint))) return false;
  const ids = endpointModelIds(endpoint);
  const current = String(model || '').trim().toLowerCase();
  return GENERATED_NAME_ALIASES.has(normalizedName(endpoint))
    || (current && ids.includes(current))
    || ids.some(id => id.startsWith('local-sd-'));
}

function isCanonical(endpoint) {
  const url = endpointUrl(endpoint);
  return !!url
    && url.hostname === '127.0.0.1'
    && endpointPort(endpoint) === CANONICAL_PORT
    && url.pathname.replace(/\/+$/, '') === '/v1'
    && normalizedName(endpoint) === 'local diffusers image';
}

async function saveEndpointId(id) {
  await jsonFetch('/api/prefs/image_endpoint_id', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ value: id }),
  });
}

async function createEndpoint(model) {
  const form = new FormData();
  form.append('name', LOCAL_ENDPOINT_NAME);
  form.append('base_url', LOCAL_BASE_URL);
  form.append('model_type', 'image');
  form.append('endpoint_kind', 'local');
  form.append('pinned_models', model);
  form.append('skip_probe', 'true');
  // The Cookbook service is launched in the same Argos runtime. Preserve
  // loopback rather than rewriting it to the Docker host gateway.
  form.append('container_local', 'true');
  return jsonFetch('/api/model-endpoints', { method: 'POST', body: form });
}

function needsCanonicalPatch(endpoint, model) {
  const ids = endpointModelIds(endpoint);
  return !isCanonical(endpoint)
    || String(endpoint?.model_type || '').toLowerCase() !== 'image'
    || String(endpoint?.endpoint_kind || '').toLowerCase() !== 'local'
    || !endpoint?.is_enabled
    || !ids.includes(String(model || '').toLowerCase());
}

async function patchCanonicalEndpoint(endpoint, model) {
  return jsonFetch(`/api/model-endpoints/${encodeURIComponent(endpoint.id)}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name: LOCAL_ENDPOINT_NAME,
      base_url: LOCAL_BASE_URL,
      model_type: 'image',
      endpoint_kind: 'local',
      pinned_models: [model],
      is_enabled: true,
    }),
  });
}

async function deleteEndpoint(endpoint) {
  return jsonFetch(`/api/model-endpoints/${encodeURIComponent(endpoint.id)}`, { method: 'DELETE' });
}

function chooseCanonical(candidates, selectedId) {
  return candidates.find(isCanonical)
    || candidates.find(endpoint => endpointPort(endpoint) === CANONICAL_PORT && endpointUrl(endpoint)?.hostname === '127.0.0.1')
    || candidates.find(endpoint => endpointPort(endpoint) === CANONICAL_PORT)
    || candidates.find(endpoint => String(endpoint.id) === String(selectedId))
    || candidates[0]
    || null;
}

export async function recoverLocalImageEndpoint() {
  if (inFlight) return inFlight;
  inFlight = (async () => {
    const [settings, prefs, endpoints] = await Promise.all([
      jsonFetch('/api/auth/settings').catch(() => ({})),
      jsonFetch('/api/prefs').catch(() => ({})),
      jsonFetch('/api/model-endpoints').catch(() => []),
    ]);
    const model = String(settings?.image_model || '').trim();
    if (!isLocalImageModel(model)) return null;

    const list = Array.isArray(endpoints) ? endpoints : [];
    const selectedId = String(prefs?.image_endpoint_id || '').trim();
    const candidates = list.filter(endpoint => isLocalImageDuplicate(endpoint, model));
    let canonical = chooseCanonical(candidates, selectedId);
    let changed = false;

    if (!canonical) {
      canonical = await createEndpoint(model);
      changed = true;
    } else if (needsCanonicalPatch(canonical, model)) {
      canonical = await patchCanonicalEndpoint(canonical, model);
      changed = true;
    }
    if (!canonical?.id) return null;

    // Delete only prior generated aliases / local-image model rows. Deleting a
    // duplicate can clear the current preference server-side, so save the
    // canonical id again after cleanup.
    for (const duplicate of candidates) {
      if (String(duplicate.id) === String(canonical.id)) continue;
      await deleteEndpoint(duplicate);
      changed = true;
    }
    if (selectedId !== String(canonical.id)) {
      await saveEndpointId(canonical.id);
      changed = true;
    }
    if (changed) window.dispatchEvent(new CustomEvent('ge:model-endpoints-updated'));
    return canonical;
  })();
  try {
    return await inFlight;
  } finally {
    inFlight = null;
  }
}

function scheduleRecovery() {
  setTimeout(() => { recoverLocalImageEndpoint().catch(() => {}); }, 150);
}

window.addEventListener('load', scheduleRecovery, { once: true });
window.addEventListener('ge:model-endpoints-updated', scheduleRecovery);
document.addEventListener('click', event => {
  if (event.target.closest?.('[data-settings-tab="ai"], #settings-btn, #tool-settings-btn')) scheduleRecovery();
});