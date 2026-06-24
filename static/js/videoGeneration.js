// Native mlx-video settings and direct-generation card.
// Loaded as a small optional module so older deployments simply show a helpful
// unavailable state instead of affecting the existing image-generation UI.

const CARD_ID = 'video-generation-settings-card';
let installing = false;

function make(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(props).forEach(([key, value]) => {
    if (key === 'className') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'style') node.style.cssText = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  });
  children.forEach(child => node.appendChild(child));
  return node;
}

function select(options, value) {
  const node = make('select', { className: 'settings-select' });
  options.forEach(([v, label]) => {
    const option = make('option', { value: String(v), text: label });
    if (String(v) === String(value)) option.selected = true;
    node.appendChild(option);
  });
  return node;
}

function settingsPanel() {
  return document.querySelector('#settings-modal [data-settings-panel="ai"]');
}

async function jsonFetch(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || `Request failed (${response.status})`);
  return data;
}

function install() {
  if (installing || document.getElementById(CARD_ID)) return;
  const panel = settingsPanel();
  if (!panel) return;
  installing = true;

  const card = make('section', {
    id: CARD_ID,
    className: 'admin-card',
    style: 'margin-top:12px;display:flex;flex-direction:column;gap:10px;',
  });
  const titleRow = make('div', { style: 'display:flex;align-items:center;gap:8px;' });
  titleRow.append(
    make('h2', { text: 'Video Generation', style: 'margin:0;font-size:15px;flex:1;' }),
    make('span', { text: 'Silent video only', style: 'font-size:11px;opacity:.65;' }),
  );
  const status = make('div', { text: 'Checking local mlx-video runtime…', style: 'font-size:12px;opacity:.72;' });
  const form = make('div', { style: 'display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;' });
  const result = make('div', { style: 'display:none;gap:8px;flex-direction:column;' });
  const prompt = make('textarea', {
    placeholder: 'Describe a short silent video…',
    style: 'width:100%;min-height:62px;resize:vertical;box-sizing:border-box;border:1px solid var(--border);border-radius:8px;background:var(--input-bg,var(--bg));color:var(--fg);padding:8px;font:inherit;',
  });

  const enabled = make('input', { type: 'checkbox' });
  const enabledLabel = make('label', { style: 'display:flex;align-items:center;gap:7px;font-size:12px;grid-column:1/-1;' }, [enabled, document.createTextNode('Enable local video generation')]);
  const provider = make('input', { type: 'text', value: 'mlx-video (local Apple Silicon)', readonly: 'readonly', className: 'settings-input', style: 'opacity:.7;' });
  const model = select([['Lightricks/LTX-2', 'LTX-2']], 'Lightricks/LTX-2');
  const pipeline = select([['distilled', 'Distilled']], 'distilled');
  const resolution = select([['512x512', '512 × 512'], ['768x512', '768 × 512'], ['512x768', '512 × 768']], '512x512');
  const frames = select([['33', '33 frames'], ['49', '49 frames'], ['97', '97 frames']], '33');
  const fps = select([['24', '24 FPS']], '24');
  const tiling = select([['auto', 'Tiling: Auto'], ['enabled', 'Tiling: Enabled'], ['disabled', 'Tiling: Disabled']], 'auto');
  const seedMode = select([['random', 'Seed: Random'], ['fixed', 'Seed: Fixed']], 'random');
  const seed = make('input', { type: 'number', min: '0', max: '2147483647', placeholder: 'Seed', className: 'settings-input', style: 'display:none;' });
  const enhance = make('input', { type: 'checkbox' });
  const enhanceLabel = make('label', { style: 'display:flex;align-items:center;gap:7px;font-size:12px;grid-column:1/-1;' }, [enhance, document.createTextNode('Enhance prompt before generation')]);

  function field(label, node) {
    return make('label', { style: 'display:flex;flex-direction:column;gap:4px;font-size:11px;opacity:.9;' }, [document.createTextNode(label), node]);
  }
  form.append(
    enabledLabel,
    field('Provider', provider),
    field('Model', model),
    field('Pipeline', pipeline),
    field('Resolution', resolution),
    field('Duration', frames),
    field('Frame rate', fps),
    field('Tiling', tiling),
    field('Seed mode', seedMode),
    field('Fixed seed', seed),
    enhanceLabel,
  );

  const generator = make('div', { style: 'display:flex;flex-direction:column;gap:7px;border-top:1px solid var(--border);padding-top:10px;' });
  const actionRow = make('div', { style: 'display:flex;align-items:center;gap:8px;' });
  const generate = make('button', { type: 'button', className: 'admin-btn-add', text: 'Generate video' });
  const jobStatus = make('span', { style: 'font-size:12px;opacity:.75;' });
  actionRow.append(generate, jobStatus);
  generator.append(make('div', { text: 'Generate a silent video', style: 'font-weight:600;font-size:13px;' }), prompt, actionRow, result);
  card.append(titleRow, status, form, generator);
  panel.appendChild(card);

  const controls = { enabled, model, pipeline, resolution, frames, fps, tiling, seedMode, seed, enhance };
  let currentRuntime = null;
  let saving = false;

  function currentPatch() {
    const [width, height] = resolution.value.split('x').map(Number);
    return {
      video_gen_enabled: enabled.checked,
      video_model: model.value,
      video_pipeline: pipeline.value,
      video_width: width,
      video_height: height,
      video_num_frames: Number(frames.value),
      video_fps: Number(fps.value),
      video_seed: seedMode.value === 'fixed' && seed.value !== '' ? Number(seed.value) : null,
      video_tiling: tiling.value,
      video_enhance_prompt: enhance.checked,
    };
  }

  async function save() {
    if (saving) return;
    saving = true;
    try {
      await jsonFetch('/api/video/defaults', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(currentPatch()) });
      status.textContent = currentRuntime?.available ? 'Saved. Silent MP4 output uses your local mlx-video runtime.' : (currentRuntime?.reason || 'Saved. Runtime unavailable.');
    } catch (error) {
      status.textContent = error.message;
    } finally {
      saving = false;
    }
  }

  function syncSeedVisibility() {
    seed.style.display = seedMode.value === 'fixed' ? '' : 'none';
  }
  seedMode.addEventListener('change', () => { syncSeedVisibility(); save(); });
  Object.values(controls).forEach(control => {
    if (control !== seedMode) control.addEventListener('change', save);
  });

  async function poll(jobId) {
    for (;;) {
      const job = await jsonFetch(`/api/video/generations/${encodeURIComponent(jobId)}`);
      jobStatus.textContent = job.stage || job.status;
      if (job.status === 'succeeded') {
        generate.disabled = false;
        result.style.display = 'flex';
        result.innerHTML = '';
        const video = make('video', { controls: 'controls', muted: 'muted', playsinline: 'playsinline', preload: 'metadata', style: 'max-width:100%;border-radius:8px;background:#000;' });
        video.src = job.video_url;
        const link = make('a', { href: '/gallery', text: 'Open in Gallery', style: 'font-size:12px;color:var(--accent,var(--red));' });
        result.append(video, link);
        window.dispatchEvent(new Event('gallery-refresh'));
        return;
      }
      if (['failed', 'cancelled', 'interrupted'].includes(job.status)) {
        generate.disabled = false;
        jobStatus.textContent = job.error || job.stage || job.status;
        return;
      }
      await new Promise(resolve => setTimeout(resolve, 1200));
    }
  }

  generate.addEventListener('click', async () => {
    if (!prompt.value.trim()) {
      jobStatus.textContent = 'Describe the video first.';
      return;
    }
    generate.disabled = true;
    result.style.display = 'none';
    jobStatus.textContent = 'Queuing…';
    try {
      const data = await jsonFetch('/api/video/generations', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: prompt.value.trim(), ...currentPatch(), session_id: window.sessionModule?.getCurrentSessionId?.() || null }),
      });
      await poll(data.job_id);
    } catch (error) {
      jobStatus.textContent = error.message;
      generate.disabled = false;
    }
  });

  Promise.all([jsonFetch('/api/video/defaults'), jsonFetch('/api/video/runtime')])
    .then(([defaultsData, runtime]) => {
      const d = defaultsData.defaults || {};
      currentRuntime = runtime;
      enabled.checked = d.video_gen_enabled === true;
      model.value = d.video_model || model.value;
      pipeline.value = d.video_pipeline || pipeline.value;
      resolution.value = `${d.video_width || 512}x${d.video_height || 512}`;
      frames.value = String(d.video_num_frames || 33);
      fps.value = String(d.video_fps || 24);
      tiling.value = d.video_tiling || 'auto';
      enhance.checked = d.video_enhance_prompt === true;
      if (d.video_seed !== null && d.video_seed !== undefined && d.video_seed !== '') {
        seedMode.value = 'fixed'; seed.value = d.video_seed;
      }
      syncSeedVisibility();
      status.textContent = runtime.available
        ? 'Local LTX-2 runtime ready. Output is silent MP4.'
        : (runtime.reason || 'Video runtime unavailable.');
      generate.disabled = !runtime.available || !enabled.checked;
      enabled.addEventListener('change', () => { generate.disabled = !currentRuntime?.available || !enabled.checked; });
    })
    .catch(error => { status.textContent = error.message || 'Video settings unavailable.'; generate.disabled = true; });

  installing = false;
}

function scheduleInstall() {
  install();
  const observer = new MutationObserver(install);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  window.addEventListener('odysseus:modal-opened', install);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', scheduleInstall, { once: true });
else scheduleInstall();
