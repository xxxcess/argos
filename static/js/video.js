// Guided local depth-parallax video UI.
// This follows the existing image pipeline: a configured Image Default makes the
// stable Gallery anchor, then the local renderer creates subtle camera motion.

function node(tag, options = {}, children = []) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(options)) {
    if (key === 'text') el.textContent = value;
    else if (key === 'className') el.className = value;
    else if (key === 'style') el.style.cssText = value;
    else if (key.startsWith('on')) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value);
  }
  children.forEach(child => el.appendChild(child));
  return el;
}

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || `Request failed (${response.status})`);
  return data;
}

function row(label) {
  const icon = node('span', { text: '○', style: 'font-size:15px;width:14px;opacity:.55;' });
  const detail = node('span', { text: 'Checking…', style: 'font-size:12px;opacity:.78;flex:1;' });
  return {
    el: node('div', { style: 'display:flex;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);' }, [icon, node('span', { text: label, style: 'font-size:12px;min-width:114px;' }), detail]),
    icon, detail,
  };
}

function setRow(item, ready, text) {
  item.icon.textContent = ready ? '✓' : '○';
  item.icon.style.opacity = ready ? '1' : '.55';
  item.icon.style.color = ready ? 'var(--accent,var(--red))' : 'var(--fg)';
  item.detail.textContent = text || (ready ? 'Ready' : 'Needs setup');
}

function installationText(runtime) {
  if (runtime?.phase === 'installing_depth_runtime') return 'Installing local depth runtime…';
  if (runtime?.phase === 'downloading_depth_model') return 'Downloading small depth model…';
  if (runtime?.phase === 'verifying_ffmpeg') return 'Verifying FFmpeg interpolation…';
  return 'Preparing local depth engine…';
}

function createCard() {
  if (document.getElementById('local-depth-video-card')) return;
  const panel = document.querySelector('#settings-modal [data-settings-panel="ai"]');
  if (!panel) return;

  const planner = row('Prompt planner');
  const anchor = row('Image anchor');
  const engine = row('Local renderer');
  const card = node('section', { id: 'local-depth-video-card', className: 'admin-card', style: 'display:flex;flex-direction:column;gap:10px;margin-top:12px;' });
  const heading = node('div', { style: 'display:flex;align-items:center;gap:8px;' }, [
    node('h2', { text: 'Video Generation', style: 'margin:0;flex:1;font-size:15px;' }),
    node('span', { text: 'Local · muted', style: 'font-size:11px;opacity:.66;' }),
  ]);
  const explanation = node('div', { text: 'Creates a stable Gallery image through your Image Default, then applies subtle depth-aware camera motion locally. It preserves the image rather than inventing new object motion.', style: 'font-size:12px;opacity:.78;' });
  const statusLine = node('div', { text: 'Checking guided setup…', style: 'font-size:12px;min-height:18px;' });
  const checklist = node('div', { style: 'border:1px solid var(--border);border-radius:8px;padding:0 9px;' }, [planner.el, anchor.el, engine.el]);
  const install = node('button', { type: 'button', className: 'admin-btn-add', text: 'Install local depth engine', style: 'font-size:12px;' });
  const test = node('button', { type: 'button', className: 'admin-btn-secondary', text: 'Run guided test', style: 'font-size:12px;' });
  const details = node('button', { type: 'button', className: 'admin-btn-secondary', text: 'Setup details', style: 'font-size:12px;display:none;' });
  const imageDefaults = node('button', { type: 'button', className: 'admin-btn-secondary', text: 'Manage Image Default', style: 'font-size:12px;' });
  const actions = node('div', { style: 'display:flex;gap:8px;flex-wrap:wrap;' }, [imageDefaults, install, test, details]);
  const enabled = node('input', { type: 'checkbox' });
  const enableLine = node('label', { style: 'display:flex;gap:7px;align-items:center;font-size:12px;' }, [enabled, document.createTextNode('Enable local depth-parallax video generation')]);
  const seed = node('input', { type: 'number', min: '0', max: '2147483647', placeholder: 'Random seed', className: 'settings-input', style: 'max-width:190px;' });
  const prompt = node('textarea', { placeholder: 'Describe the video. Argos will create a stable anchor image first, then animate it with subtle camera movement.', style: 'min-height:78px;width:100%;box-sizing:border-box;padding:8px;border-radius:7px;border:1px solid var(--border);background:var(--input-bg,var(--bg));color:var(--fg);font:inherit;resize:vertical;' });
  const generate = node('button', { type: 'button', className: 'admin-btn-add', text: 'Create anchor and animate' });
  const cancel = node('button', { type: 'button', className: 'admin-btn-secondary', text: 'Cancel', style: 'display:none;' });
  const output = node('div', { style: 'display:none;flex-direction:column;gap:8px;' });
  card.append(heading, explanation, statusLine, checklist, actions, enableLine, node('label', { style: 'font-size:12px;display:flex;flex-direction:column;gap:4px;' }, [document.createTextNode('Optional fixed seed'), seed]), prompt, node('div', { style: 'display:flex;gap:8px;' }, [generate, cancel]), output);
  panel.appendChild(card);

  let setup = null;
  let jobId = '';
  let observedAnchor = false;
  let installing = null;

  const isReady = () => Boolean(setup?.runtime?.available && setup?.image_default?.ready && setup?.planner?.ready);
  const buttons = () => {
    install.style.display = setup?.runtime?.available ? 'none' : '';
    install.disabled = Boolean(setup?.runtime?.installing);
    install.textContent = setup?.runtime?.installing ? installationText(setup.runtime) : 'Install local depth engine';
    details.style.display = setup?.runtime?.last_error ? '' : 'none';
    test.disabled = !isReady() || Boolean(jobId);
    generate.disabled = !isReady() || !enabled.checked || Boolean(jobId);
  };

  async function refresh() {
    try {
      setup = await api('/api/video/setup');
      setRow(planner, setup.planner?.ready, setup.planner?.message);
      setRow(anchor, setup.image_default?.ready, setup.image_default?.message);
      const engineMessage = setup.runtime?.available ? 'Depth model + FFmpeg interpolation ready' : (setup.runtime?.installing ? installationText(setup.runtime) : (setup.runtime?.reason || 'Install required'));
      setRow(engine, setup.runtime?.available, engineMessage);
      statusLine.textContent = isReady() ? 'Setup complete. Every clip is a stable image anchor followed by local depth-aware camera motion.' : 'Finish the checklist to use local video generation. No terminal commands are required.';
      buttons();
      return setup;
    } catch (error) {
      statusLine.textContent = error.message || 'Could not check local video setup.';
      return null;
    }
  }

  function show(job) {
    output.style.display = 'flex';
    output.innerHTML = '';
    if (job.anchor_url) {
      output.append(node('strong', { text: 'Animation anchor generated — now creating depth-aware motion.' }));
      output.append(node('img', { src: job.anchor_url, alt: 'Generated animation anchor', style: 'max-width:240px;border-radius:8px;border:1px solid var(--border);' }));
    }
    if (job.video_url) {
      const video = node('video', { controls: 'controls', muted: 'muted', playsinline: 'playsinline', preload: 'metadata', style: 'max-width:100%;border-radius:8px;background:#000;' });
      video.src = job.video_url;
      output.append(video, node('a', { href: '/gallery', text: 'Open final video in Gallery', style: 'font-size:12px;color:var(--accent,var(--red));' }));
    }
  }

  async function poll() {
    while (jobId) {
      const job = await api(`/api/video/generations/${encodeURIComponent(jobId)}`);
      statusLine.textContent = job.stage || job.status;
      if (job.anchor_ready && !observedAnchor) {
        observedAnchor = true;
        show(job);
        window.dispatchEvent(new Event('gallery-refresh'));
      }
      if (job.status === 'succeeded') {
        show(job); window.dispatchEvent(new Event('gallery-refresh')); jobId = ''; cancel.style.display = 'none'; await refresh(); return;
      }
      if (['failed', 'cancelled', 'interrupted'].includes(job.status)) {
        statusLine.textContent = job.error || job.stage || job.status; jobId = ''; cancel.style.display = 'none'; await refresh(); return;
      }
      await new Promise(resolve => setTimeout(resolve, 900));
    }
  }

  async function queue(url, body, message) {
    if (!isReady()) { statusLine.textContent = 'Finish the setup checklist first.'; return; }
    observedAnchor = false; output.style.display = 'none'; cancel.style.display = ''; statusLine.textContent = message; buttons();
    try {
      const created = await api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      jobId = created.job_id;
      await poll();
    } catch (error) {
      statusLine.textContent = error.message; jobId = ''; cancel.style.display = 'none'; buttons();
    }
  }

  async function save() {
    await api('/api/video/defaults', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ video_gen_enabled: enabled.checked, video_seed: seed.value === '' ? null : Number(seed.value) }) });
  }

  imageDefaults.addEventListener('click', () => panel.scrollTo({ top: 0, behavior: 'smooth' }));
  enabled.addEventListener('change', () => save().then(refresh).catch(error => { statusLine.textContent = error.message; }));
  seed.addEventListener('change', () => save().catch(error => { statusLine.textContent = error.message; }));
  install.addEventListener('click', async () => {
    try {
      await api('/api/video/setup/install', { method: 'POST' });
      statusLine.textContent = installationText(setup?.runtime);
      clearInterval(installing);
      installing = setInterval(async () => {
        await refresh();
        if (!setup?.runtime?.installing) { clearInterval(installing); installing = null; statusLine.textContent = setup?.runtime?.available ? 'Local depth engine ready.' : (setup?.runtime?.last_error || 'Local depth setup did not complete.'); }
      }, 1600);
      await refresh();
    } catch (error) { statusLine.textContent = error.message; }
  });
  details.addEventListener('click', async () => {
    try { const result = await api('/api/video/setup/install-log'); output.style.display = 'flex'; output.innerHTML = ''; output.append(node('pre', { text: result.log || 'No setup output is available yet.', style: 'margin:0;max-height:240px;overflow:auto;white-space:pre-wrap;padding:8px;border:1px solid var(--border);border-radius:7px;font-size:11px;' })); } catch (error) { statusLine.textContent = error.message; }
  });
  test.addEventListener('click', () => queue('/api/video/setup/test', {}, 'Running the local guided test…'));
  generate.addEventListener('click', () => {
    if (!prompt.value.trim()) { statusLine.textContent = 'Describe the video first.'; return; }
    queue('/api/video/generations', { prompt: prompt.value.trim(), video_seed: seed.value === '' ? null : Number(seed.value), session_id: window.sessionModule?.getCurrentSessionId?.() || null }, 'Creating the stable animation anchor…');
  });
  cancel.addEventListener('click', () => { if (jobId) api(`/api/video/generations/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }).catch(error => { statusLine.textContent = error.message; }); });
  api('/api/video/defaults').then(data => { enabled.checked = data.defaults?.video_gen_enabled === true; if (data.defaults?.video_seed !== null && data.defaults?.video_seed !== undefined) seed.value = data.defaults.video_seed; refresh(); }).catch(error => { statusLine.textContent = error.message; });
}

function addAgentToolToggle() {
  if (document.getElementById('generate-video-tool-toggle')) return;
  const headers = [...document.querySelectorAll('h1,h2,h3,h4,h5,.settings-title,.section-title')];
  const header = headers.find(item => /built[-\s]?in agent tools/i.test(item.textContent || ''));
  if (!header) return;
  const container = header.closest('.admin-card, .settings-section, section, div');
  if (!container) return;
  const toggle = node('input', { type: 'checkbox' });
  const shell = node('div', { id: 'generate-video-tool-toggle', style: 'display:flex;align-items:center;gap:9px;padding:9px 0;border-top:1px solid var(--border);margin-top:8px;' }, [
    node('div', { style: 'flex:1;' }, [node('div', { text: 'Generate video', style: 'font-size:13px;font-weight:600;' }), node('div', { text: 'Creates the same local image-anchor → depth-parallax Gallery video flow.', style: 'font-size:11px;opacity:.72;margin-top:2px;' })]),
    toggle,
  ]);
  container.appendChild(shell);
  api('/api/prefs/video_agent_enabled').then(data => { toggle.checked = data.value !== false; }).catch(() => { toggle.checked = true; });
  toggle.addEventListener('change', () => api('/api/prefs/video_agent_enabled', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: toggle.checked }) }).catch(() => { toggle.checked = !toggle.checked; }));
}

function boot() {
  createCard(); addAgentToolToggle();
  new MutationObserver(() => { createCard(); addAgentToolToggle(); }).observe(document.documentElement, { childList: true, subtree: true });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true }); else boot();
