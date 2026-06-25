import './imageBridge.js';

// Guided anchor-first video setup. This mirrors the image-generation product
// path: choose a configured Image Default, verify the planner, install the
// native runtime and its LTX model, run a test, then enable generation.

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  Object.entries(props).forEach(([key, value]) => {
    if (key === 'text') node.textContent = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key === 'className') node.className = value;
    else if (key === 'style') node.style.cssText = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value);
  });
  children.forEach(child => node.appendChild(child));
  return node;
}

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: 'same-origin', ...options });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || `Request failed (${response.status})`);
  return data;
}

function openCookbook() {
  const button = document.getElementById('tool-cookbook-btn') || document.getElementById('rail-cookbook');
  if (button) button.click();
}

function setupRow(label) {
  const status = el('span', { text: 'Checking…', style: 'font-size:12px;opacity:.8;flex:1;' });
  const row = el('div', { style: 'display:flex;align-items:center;gap:8px;padding:7px 0;border-bottom:1px solid var(--border);' }, [
    el('span', { text: '○', style: 'font-size:15px;line-height:1;color:var(--fg);opacity:.45;' }),
    el('span', { text: label, style: 'font-size:12px;min-width:118px;' }),
    status,
  ]);
  return { row, status };
}

function applyCheck(row, ready, message) {
  const icon = row.row.firstElementChild;
  icon.textContent = ready ? '✓' : '○';
  icon.style.color = ready ? 'var(--accent,var(--red))' : 'var(--fg)';
  icon.style.opacity = ready ? '1' : '.45';
  row.status.textContent = message || (ready ? 'Ready' : 'Needs setup');
}

function installMessage(runtime) {
  const phase = runtime?.phase || '';
  if (phase === 'installing_runtime') return 'Installing the MLX video runtime…';
  if (phase === 'downloading_ltx_model') return 'Downloading the LTX-2 distilled model…';
  if (phase === 'queued') return 'Preparing local video setup…';
  return 'Installing the local runtime and LTX-2 model…';
}

function createVideoCard() {
  if (document.getElementById('anchor-video-card')) return;
  const panel = document.querySelector('#settings-modal [data-settings-panel="ai"]');
  if (!panel) return;

  const card = el('section', {
    id: 'anchor-video-card',
    className: 'admin-card',
    style: 'display:flex;flex-direction:column;gap:10px;margin-top:12px;',
  });
  const runtimeText = el('div', { text: 'Checking guided setup…', style: 'font-size:12px;opacity:.78;' });
  const plannerRow = setupRow('Prompt planner');
  const imageRow = setupRow('Image anchor');
  const engineRow = setupRow('LTX-2 engine');
  const checklist = el('div', { style: 'border:1px solid var(--border);border-radius:8px;padding:0 9px;' }, [plannerRow.row, imageRow.row, engineRow.row]);

  const cookbookButton = el('button', { type: 'button', className: 'admin-btn-secondary', text: 'Open Cookbook', style: 'font-size:12px;' });
  cookbookButton.addEventListener('click', openCookbook);
  const installButton = el('button', { type: 'button', className: 'admin-btn-add', text: 'Install LTX-2 engine', style: 'font-size:12px;' });
  const installLog = el('button', { type: 'button', className: 'admin-btn-secondary', text: 'Setup details', style: 'font-size:12px;display:none;' });
  const testButton = el('button', { type: 'button', className: 'admin-btn-secondary', text: 'Run guided test', style: 'font-size:12px;' });
  const actionRow = el('div', { style: 'display:flex;gap:8px;align-items:center;flex-wrap:wrap;' }, [cookbookButton, installButton, testButton, installLog]);

  const enabled = el('input', { type: 'checkbox' });
  const enableLabel = el('label', { style: 'display:flex;gap:7px;align-items:center;font-size:12px;' }, [enabled, document.createTextNode('Enable anchor-first video generation')]);
  const seed = el('input', { type: 'number', min: '0', max: '2147483647', placeholder: 'Random seed', className: 'settings-input', style: 'max-width:180px;' });
  const intent = el('textarea', {
    placeholder: 'Describe the video. Argos will generate a stable Gallery anchor, notify you when it is ready, then animate it.',
    style: 'min-height:78px;width:100%;box-sizing:border-box;resize:vertical;padding:8px;border:1px solid var(--border);border-radius:7px;background:var(--input-bg,var(--bg));color:var(--fg);font:inherit;',
  });
  const generate = el('button', { type: 'button', className: 'admin-btn-add', text: 'Create anchor and animate' });
  const cancel = el('button', { type: 'button', className: 'admin-btn-secondary', text: 'Cancel', style: 'display:none;' });
  const status = el('div', { style: 'font-size:12px;min-height:18px;' });
  const output = el('div', { style: 'display:none;flex-direction:column;gap:8px;' });

  card.append(
    el('div', { style: 'display:flex;align-items:center;gap:8px;' }, [
      el('h2', { text: 'Video Generation', style: 'margin:0;flex:1;font-size:15px;' }),
      el('span', { text: 'Anchor-first · muted', style: 'font-size:11px;opacity:.65;' }),
    ]),
    el('div', { text: 'Uses your existing Image Default and Gallery workflow, then animates that anchor with a managed local LTX-2 engine.', style: 'font-size:12px;opacity:.78;' }),
    runtimeText,
    checklist,
    actionRow,
    enableLabel,
    el('label', { style: 'display:flex;flex-direction:column;gap:4px;font-size:12px;' }, [document.createTextNode('Optional fixed seed'), seed]),
    intent,
    el('div', { style: 'display:flex;gap:8px;align-items:center;' }, [generate, cancel]),
    status,
    output,
  );
  panel.appendChild(card);

  let setup = null;
  let jobId = '';
  let sawAnchor = false;
  let installingPoll = null;

  const ready = () => Boolean(setup?.runtime?.available && setup?.image_default?.ready && setup?.planner?.ready);
  const refreshButtons = () => {
    const canGenerate = ready() && enabled.checked && !jobId;
    generate.disabled = !canGenerate;
    testButton.disabled = !ready();
    installButton.style.display = setup?.runtime?.available ? 'none' : '';
    installButton.disabled = Boolean(setup?.runtime?.installing);
    installButton.textContent = setup?.runtime?.installing ? installMessage(setup.runtime) : 'Install LTX-2 engine';
    installLog.style.display = setup?.runtime?.last_error ? '' : 'none';
  };

  async function refreshSetup() {
    try {
      setup = await api('/api/video/setup');
      applyCheck(plannerRow, setup.planner?.ready, setup.planner?.message);
      applyCheck(imageRow, setup.image_default?.ready, setup.image_default?.message);
      const runtimeMessage = setup.runtime?.available
        ? `Installed: ${setup.runtime?.model_repo || 'LTX-2 distilled'}`
        : (setup.runtime?.installing ? installMessage(setup.runtime) : (setup.runtime?.reason || 'Install required'));
      applyCheck(engineRow, setup.runtime?.available, runtimeMessage);
      runtimeText.textContent = ready()
        ? 'Setup complete. Argos derives separate detailed anchor and motion prompts automatically.'
        : 'Finish the checklist to enable video generation. No terminal commands are required.';
      refreshButtons();
      return setup;
    } catch (error) {
      runtimeText.textContent = error.message || 'Could not check video setup.';
      return null;
    }
  }

  async function saveDefaults() {
    await api('/api/video/defaults', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ video_gen_enabled: enabled.checked, video_seed: seed.value === '' ? null : Number(seed.value) }),
    });
  }

  enabled.addEventListener('change', () => saveDefaults().then(refreshSetup).catch(error => { status.textContent = error.message; }));
  seed.addEventListener('change', () => saveDefaults().catch(error => { status.textContent = error.message; }));

  installButton.addEventListener('click', async () => {
    try {
      await api('/api/video/setup/install', { method: 'POST' });
      status.textContent = installMessage(setup?.runtime);
      if (installingPoll) clearInterval(installingPoll);
      installingPoll = setInterval(async () => {
        await refreshSetup();
        if (setup?.runtime?.installing) {
          status.textContent = installMessage(setup.runtime);
          return;
        }
        clearInterval(installingPoll);
        installingPoll = null;
        status.textContent = setup?.runtime?.available
          ? 'LTX-2 engine and model are installed and ready.'
          : (setup?.runtime?.last_error || 'LTX-2 installation did not complete.');
      }, 1800);
      await refreshSetup();
    } catch (error) {
      status.textContent = error.message;
    }
  });

  installLog.addEventListener('click', async () => {
    try {
      const data = await api('/api/video/setup/install-log');
      const text = data.log || 'No installer output is available yet.';
      const details = el('pre', { text, style: 'max-height:240px;overflow:auto;margin:0;white-space:pre-wrap;font-size:11px;padding:8px;border:1px solid var(--border);border-radius:7px;' });
      output.style.display = 'flex'; output.innerHTML = ''; output.append(el('strong', { text: 'LTX-2 setup details' }), details);
    } catch (error) { status.textContent = error.message; }
  });

  function render(job) {
    output.style.display = 'flex';
    output.innerHTML = '';
    if (job.anchor_url) {
      const image = el('img', { src: job.anchor_url, alt: 'Generated animation anchor', style: 'max-width:240px;border-radius:8px;border:1px solid var(--border);' });
      output.append(el('strong', { text: 'Animation anchor generated — now creating motion.' }), image);
    }
    if (job.video_url) {
      const video = el('video', { controls: 'controls', muted: 'muted', playsinline: 'playsinline', preload: 'metadata', style: 'max-width:100%;border-radius:8px;background:#000;' });
      video.src = job.video_url;
      output.append(video, el('a', { href: '/gallery', text: 'Open final video in Gallery', style: 'font-size:12px;color:var(--accent,var(--red));' }));
    }
  }

  async function pollJob() {
    while (jobId) {
      const job = await api(`/api/video/generations/${encodeURIComponent(jobId)}`);
      status.textContent = job.stage || job.status;
      if (job.anchor_ready && !sawAnchor) {
        sawAnchor = true;
        render(job);
        window.dispatchEvent(new Event('gallery-refresh'));
      }
      if (job.status === 'succeeded') {
        render(job);
        window.dispatchEvent(new Event('gallery-refresh'));
        jobId = '';
        cancel.style.display = 'none';
        await refreshSetup();
        return job;
      }
      if (['failed', 'cancelled', 'interrupted'].includes(job.status)) {
        status.textContent = job.error || job.stage || job.status;
        jobId = '';
        cancel.style.display = 'none';
        await refreshSetup();
        return job;
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    return null;
  }

  async function queue(body, label) {
    if (!ready()) { status.textContent = 'Finish the setup checklist first.'; return; }
    generate.disabled = true;
    testButton.disabled = true;
    cancel.style.display = '';
    sawAnchor = false;
    output.style.display = 'none';
    status.textContent = label;
    try {
      const created = await api('/api/video/generations', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      jobId = created.job_id;
      await pollJob();
    } catch (error) {
      status.textContent = error.message;
      jobId = '';
      cancel.style.display = 'none';
      refreshButtons();
    }
  }

  generate.addEventListener('click', () => {
    if (!intent.value.trim()) { status.textContent = 'Describe the video first.'; return; }
    queue({ prompt: intent.value.trim(), video_seed: seed.value === '' ? null : Number(seed.value), session_id: window.sessionModule?.getCurrentSessionId?.() || null }, 'Creating the animation anchor…');
  });
  cancel.addEventListener('click', () => {
    if (!jobId) return;
    api(`/api/video/generations/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }).catch(error => { status.textContent = error.message; });
  });
  testButton.addEventListener('click', async () => {
    if (!ready()) return;
    generate.disabled = true; testButton.disabled = true; cancel.style.display = '';
    sawAnchor = false; output.style.display = 'none'; status.textContent = 'Running the guided setup test…';
    try {
      const created = await api('/api/video/setup/test', { method: 'POST' });
      jobId = created.job_id;
      await pollJob();
    } catch (error) {
      status.textContent = error.message;
      jobId = ''; cancel.style.display = 'none'; refreshButtons();
    }
  });

  api('/api/video/defaults').then(data => {
    enabled.checked = data.defaults?.video_gen_enabled === true;
    if (data.defaults?.video_seed !== null && data.defaults?.video_seed !== undefined) seed.value = data.defaults.video_seed;
    refreshSetup();
  }).catch(error => { runtimeText.textContent = error.message; });
}

function addMediaToolControl() {
  if (document.getElementById('video-agent-tool-control')) return;
  const candidates = [...document.querySelectorAll('h1,h2,h3,h4,h5,.settings-title,.section-title')];
  const header = candidates.find(node => /built[-\s]?in agent tools/i.test(node.textContent || ''));
  if (!header) return;
  const container = header.closest('.admin-card, .settings-section, section, div');
  if (!container || /generate video/i.test(container.textContent || '')) return;
  const row = el('div', { id: 'video-agent-tool-control', style: 'display:flex;align-items:center;gap:9px;padding:9px 0;border-top:1px solid var(--border);margin-top:8px;' });
  const toggle = el('input', { type: 'checkbox' });
  const copy = el('div', { style: 'flex:1;' }, [
    el('div', { text: 'Generate video', style: 'font-size:13px;font-weight:600;' }),
    el('div', { text: 'Creates a Gallery image anchor through your Image Default, then animates it into a muted 10-second clip.', style: 'font-size:11px;opacity:.72;margin-top:2px;' }),
  ]);
  const label = el('label', { className: 'admin-switch', style: 'transform:scale(.9);' }, [toggle, el('span', { className: 'admin-slider' })]);
  row.append(copy, label);
  container.appendChild(row);
  api('/api/prefs/video_agent_enabled').then(data => {
    toggle.checked = data.value !== false;
  }).catch(() => { toggle.checked = true; });
  toggle.addEventListener('change', () => {
    api('/api/prefs/video_agent_enabled', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ value: toggle.checked }) }).catch(() => { toggle.checked = !toggle.checked; });
  });
}

function boot() {
  createVideoCard();
  addMediaToolControl();
  new MutationObserver(() => { createVideoCard(); addMediaToolControl(); }).observe(document.documentElement, { childList: true, subtree: true });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
else boot();
