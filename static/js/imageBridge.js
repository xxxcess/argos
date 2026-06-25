// Adds a minimal endpoint-aware Image Default selector to the anchor-first card.
// Cookbook registers local Diffusers as model_type=image, so it appears here
// alongside configured remote image endpoints.

async function fetchJson(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(value.detail || value.error || 'Request failed');
  return value;
}

function mount() {
  const card = document.getElementById('anchor-video-card');
  if (!card || document.getElementById('anchor-image-default')) return;
  const wrap = document.createElement('div');
  wrap.id = 'anchor-image-default';
  wrap.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:7px;padding-top:3px;';
  const endpoint = document.createElement('select');
  endpoint.className = 'settings-select';
  const model = document.createElement('select');
  model.className = 'settings-select';
  const label1 = document.createElement('label'); label1.style.cssText = 'display:flex;flex-direction:column;gap:4px;font-size:12px;'; label1.append(document.createTextNode('Image anchor endpoint'), endpoint);
  const label2 = document.createElement('label'); label2.style.cssText = 'display:flex;flex-direction:column;gap:4px;font-size:12px;'; label2.append(document.createTextNode('Image anchor model'), model);
  const note = document.createElement('div'); note.style.cssText = 'grid-column:1/-1;font-size:11px;opacity:.72;'; note.textContent = 'This is the existing Image Default used to generate every animation anchor.';
  wrap.append(label1, label2, note);
  card.insertBefore(wrap, card.children[4] || null);

  let endpoints = [];
  const fillModels = (selected = '') => {
    model.innerHTML = '';
    const ep = endpoints.find(item => item.id === endpoint.value);
    (ep?.models || []).forEach(id => { const opt = document.createElement('option'); opt.value = id; opt.textContent = id; model.append(opt); });
    if (selected && [...model.options].some(opt => opt.value === selected)) model.value = selected;
  };
  const save = async () => {
    if (!endpoint.value || !model.value) return;
    await fetchJson('/api/prefs/image_endpoint_id', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: endpoint.value }) });
    await fetchJson('/api/prefs/image_model', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: model.value }) });
    await fetchJson('/api/prefs/image_gen_enabled', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: true }) });
    note.textContent = 'Image Default saved. Video anchors will use this endpoint/model.';
  };
  endpoint.onchange = () => { fillModels(''); save().catch(error => { note.textContent = error.message; }); };
  model.onchange = () => save().catch(error => { note.textContent = error.message; });

  Promise.all([fetchJson('/api/model-endpoints'), fetchJson('/api/prefs')]).then(([all, prefs]) => {
    endpoints = (Array.isArray(all) ? all : []).filter(ep => ep.is_enabled && String(ep.model_type || '').toLowerCase() === 'image');
    endpoint.innerHTML = '';
    if (!endpoints.length) { const opt = document.createElement('option'); opt.textContent = 'No Image endpoint configured'; opt.value = ''; endpoint.append(opt); model.disabled = true; return; }
    endpoints.forEach(ep => { const opt = document.createElement('option'); opt.value = ep.id; opt.textContent = ep.name + (ep.online === false ? ' (offline)' : ''); endpoint.append(opt); });
    if (prefs.image_endpoint_id && endpoints.some(ep => ep.id === prefs.image_endpoint_id)) endpoint.value = prefs.image_endpoint_id;
    fillModels(prefs.image_model || '');
  }).catch(error => { note.textContent = 'Image Default unavailable: ' + error.message; });
}

function boot() { mount(); new MutationObserver(mount).observe(document.documentElement, { childList: true, subtree: true }); }
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true }); else boot();
