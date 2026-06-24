// Recover an explicit Local Diffusers Image endpoint when an earlier launch
// saved `local-*` as the model but endpoint auto-registration raced or failed.
//
// This is intentionally conservative: it runs only for a persisted local image
// model, never for ordinary cloud image users. The endpoint is pinned to the
// served model id so Settings can render it before the first /v1/models cache
// refresh completes.

const LOCAL_BASE_URL = 'http://127.0.0.1:7861/v1';
const LOCAL_ENDPOINT_NAME = 'Local Diffusers Image';
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

function matchesLocalBase(endpoint) {
  try {
    const url = new URL(endpoint?.base_url || '');
    return Number(url.port) === 7861
      && ['127.0.0.1', 'localhost', 'host.docker.internal'].includes(url.hostname);
  } catch (_) {
    return false;
  }
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
    const current = list.find(endpoint => String(endpoint.id) === selectedId);
    if (current) return current;

    let endpoint = list.find(matchesLocalBase);
    if (!endpoint) {
      endpoint = await createEndpoint(model);
    } else {
      endpoint = await jsonFetch(`/api/model-endpoints/${encodeURIComponent(endpoint.id)}`, {
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
    }
    if (endpoint?.id) {
      await saveEndpointId(endpoint.id);
      window.dispatchEvent(new CustomEvent('ge:model-endpoints-updated'));
    }
    return endpoint || null;
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
