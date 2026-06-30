// Guided anchor-first video UI.

const REMOTE_LTX_CONSENT_VERSION = 'public-ltx-v1';

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
  const icon = node('span', { text: '-', style: 'font-size:15px;width:14px;opacity:.55;' });
  const detail = node('span', { text: 'Checking...', style: 'font-size:12px;opacity:.78;flex:1;' });
  return {
    el: node('div', { style: 'display:flex;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid var(--border);' }, [icon, node('span', { text: label, style: 'font-size:12px;min-width:114px;' }), detail]),
    icon, detail,
  };
}

function setRow(item, ready, text) {
  item.icon.textContent = ready ? 'OK' : '-';
  item.icon.style.opacity = ready ? '1' : '.55';
  item.icon.style.color = ready ? 'var(--accent,var(--red))' : 'var(--fg)';
  item.detail.textContent = text || (ready ? 'Ready' : 'Needs setup');
}

function installationText(runtime) {
  if (runtime?.phase === 'installing_video_helper') return 'Installing local FFmpeg helper...';
  if (runtime?.phase === 'downloading_depth_model') return 'Downloading small depth model...';
  if (runtime?.phase === 'verifying_ffmpeg') return 'Verifying FFmpeg interpolation...';
  return 'Preparing local depth engine...';
}

function stageText(job) {
  const provider = job.provider || 'depth_parallax';
  if (job.status === 'succeeded') return job.display_duration || (provider === 'remote_ltx' ? 'About 8 seconds - Remote LTX - 30 FPS' : '8 seconds - Local Motion - 24 FPS');
  if (job.status === 'failed') return job.error || 'Video generation failed';
  if (job.status === 'cancelled') return 'Video generation cancelled';
  if (job.stage === 'generating_anchor') return 'Creating image anchor';
  if (job.stage === 'anchor_ready') return 'Animation anchor generated';
  if (job.stage === 'planning_remote_motion') return 'Preparing motion direction';
  if (job.stage === 'submitting_remote_ltx') return 'Submitting to public LTX queue';
  if (job.stage === 'waiting_remote_queue') return 'Waiting for shared GPU';
  if (job.stage === 'generating_remote_ltx') return 'Generating video';
  if (job.stage === 'downloading_remote_video') return 'Downloading final video';
  if (job.stage === 'validating_remote_video') return 'Validating final video';
  if (job.stage === 'planning_local_motion') return 'Preparing local camera motion';
  if (job.stage === 'rendering_depth_parallax') return 'Rendering local depth-aware motion';
  if (job.stage === 'saving_gallery') return 'Saving final video to Gallery';
  return 'Creating image anchor';
}

function createCard() {
  if (document.getElementById('local-depth-video-card')) return;
  const panel = document.querySelector('#settings-modal [data-settings-panel="ai"]');
  if (!panel) return;

  const planner = row('Prompt planner');
  const anchor = row('Image anchor');
  const providerRow = row('Render provider');
  const card = node('section', { id: 'local-depth-video-card', className: 'admin-card', style: 'display:flex;flex-direction:column;gap:10px;margin-top:12px;' });
  const heading = node('div', { style: 'display:flex;align-items:center;gap:8px;' }, [
    node('h2', { text: 'Video Generation', style: 'margin:0;flex:1;font-size:15px;' }),
    node('span', { id: 'video-provider-badge', text: 'Local Motion', style: 'font-size:11px;opacity:.66;' }),
  ]);
  const explanation = node('div', { text: 'Creates a Gallery image anchor through your Image Default, then renders a short muted video with the selected provider.', style: 'font-size:12px;opacity:.78;' });
  const statusLine = node('div', { text: 'Checking guided setup...', style: 'font-size:12px;min-height:18px;' });
  const checklist = node('div', { style: 'border:1px solid var(--border);border-radius:8px;padding:0 9px;' }, [planner.el, anchor.el, providerRow.el]);

  const providerSelect = node('select', { id: 'video-provider-select', className: 'settings-input', style: 'max-width:260px;' }, [
    node('option', { value: 'depth_parallax', text: 'Local Motion - private' }),
    node('option', { value: 'remote_ltx', text: 'Remote LTX Video - public shared GPU' }),
  ]);
  const providerDescription = node('div', { id: 'video-provider-description', style: 'font-size:12px;opacity:.78;' });
  const providerSection = node('section', { id: 'video-provider-section', style: 'display:flex;flex-direction:column;gap:8px;padding:9px;border:1px solid var(--border);border-radius:8px;' }, [
    node('label', { style: 'font-size:12px;display:flex;flex-direction:column;gap:4px;' }, [document.createTextNode('Render provider'), providerSelect]),
    providerDescription,
  ]);

  const enabled = node('input', { type: 'checkbox' });
  const enableLine = node('label', { style: 'display:flex;gap:7px;align-items:center;font-size:12px;' }, [enabled, document.createTextNode('Enable video generation')]);
  const seed = node('input', { type: 'number', min: '0', max: '2147483647', placeholder: 'Random seed', className: 'settings-input', style: 'max-width:190px;' });

  const install = node('button', { type: 'button', className: 'admin-btn-add', text: 'Install local depth engine', style: 'font-size:12px;' });
  const repairImage = node('button', { type: 'button', className: 'admin-btn-add', text: 'Repair local Image Default', style: 'font-size:12px;display:none;' });
  const test = node('button', { type: 'button', className: 'admin-btn-secondary', text: 'Run guided local test', style: 'font-size:12px;' });
  const details = node('button', { type: 'button', className: 'admin-btn-secondary', text: 'Setup details', style: 'font-size:12px;display:none;' });
  const imageDefaults = node('button', { type: 'button', className: 'admin-btn-secondary', text: 'Manage Image Default', style: 'font-size:12px;' });
  const localPanel = node('section', { id: 'video-local-provider-panel', style: 'display:flex;gap:8px;flex-wrap:wrap;' }, [imageDefaults, repairImage, install, test, details]);

  const consent = node('input', { id: 'remote-ltx-consent', type: 'checkbox' });
  const consentText = 'I understand my anchor image and derived motion prompt will be uploaded to the public Lightricks LTX Video Hugging Face Space on shared third-party infrastructure. Queue time, availability, retention, and output quality are outside Argos control.';
  const consentLine = node('label', { style: 'display:flex;gap:7px;align-items:flex-start;font-size:12px;line-height:1.35;' }, [consent, node('span', { text: consentText })]);
  const remoteStatus = node('div', { id: 'remote-ltx-status', text: 'Remote status is checked when selected.', style: 'font-size:12px;opacity:.78;' });
  const checkRemote = node('button', { type: 'button', className: 'admin-btn-secondary', text: 'Check Remote LTX availability', style: 'font-size:12px;' });
  const remotePanel = node('section', { id: 'video-remote-provider-panel', style: 'display:none;flex-direction:column;gap:8px;' }, [
    node('div', { text: 'Remote LTX Video - public shared GPU. About eight seconds at 30 FPS. Enables actual subject and environmental motion rather than camera-only pixel movement.', style: 'font-size:12px;opacity:.78;' }),
    consentLine,
    remoteStatus,
    checkRemote,
  ]);

  const prompt = node('textarea', { placeholder: 'Describe the video. Argos creates the image anchor first, then renders motion with the selected provider.', style: 'min-height:78px;width:100%;box-sizing:border-box;padding:8px;border-radius:7px;border:1px solid var(--border);background:var(--input-bg,var(--bg));color:var(--fg);font:inherit;resize:vertical;' });
  const generate = node('button', { type: 'button', className: 'admin-btn-add', text: 'Create anchor and animate' });
  const cancel = node('button', { type: 'button', className: 'admin-btn-secondary', text: 'Cancel', style: 'display:none;' });
  const output = node('div', { style: 'display:none;flex-direction:column;gap:8px;' });
  card.append(heading, explanation, statusLine, checklist, providerSection, localPanel, remotePanel, enableLine, node('label', { style: 'font-size:12px;display:flex;flex-direction:column;gap:4px;' }, [document.createTextNode('Optional fixed seed'), seed]), prompt, node('div', { style: 'display:flex;gap:8px;flex-wrap:wrap;' }, [generate, cancel]), output);
  panel.appendChild(card);

  let setup = null;
  let jobId = '';
  let observedAnchor = false;
  let installing = null;
  let repairingImage = null;

  const provider = () => providerSelect.value || 'depth_parallax';
  const isRemote = () => provider() === 'remote_ltx';
  const hasConsent = () => consent.checked;
  const imageRuntime = () => setup?.image_default?.runtime || null;
  const selectedProviderStatus = () => isRemote() ? setup?.remote_ltx : setup?.runtime;
  const isReady = () => Boolean(setup?.image_default?.ready && setup?.planner?.ready && selectedProviderStatus()?.available);

  function renderProviderMode() {
    document.getElementById('video-provider-badge').textContent = isRemote() ? 'Remote LTX' : 'Local Motion';
    providerDescription.textContent = isRemote()
      ? 'Public image-to-video generation for subject motion, weather, fire, smoke, walking, flying, cloth, and water.'
      : 'Private eight-second depth-aware camera movement. Best for subtle pan, tilt, push-in, pull-back, or drift.';
    localPanel.style.display = isRemote() ? 'none' : 'flex';
    remotePanel.style.display = isRemote() ? 'flex' : 'none';
    generate.textContent = isRemote() ? 'Create anchor and generate remote motion' : 'Create anchor and animate locally';
  }

  const buttons = () => {
    renderProviderMode();
    const runtime = setup?.runtime || {};
    install.style.display = runtime.available ? 'none' : '';
    install.disabled = Boolean(runtime.installing);
    install.textContent = runtime.installing ? installationText(runtime) : 'Install local depth engine';
    const image = imageRuntime();
    repairImage.style.display = image && !image.available ? '' : 'none';
    repairImage.disabled = Boolean(image?.repairing);
    repairImage.textContent = image?.repairing ? 'Repairing local Image Default...' : 'Repair local Image Default';
    details.style.display = runtime.last_error || image?.last_error ? '' : 'none';
    details.textContent = image?.last_error ? 'Image repair details' : 'Setup details';
    test.disabled = isRemote() || !setup?.runtime?.available || !setup?.image_default?.ready || !setup?.planner?.ready || Boolean(jobId);
    generate.disabled = !isReady() || !enabled.checked || Boolean(jobId) || (isRemote() && !hasConsent());
  };

  async function refresh() {
    try {
      setup = await api('/api/video/setup');
      const defaults = setup.defaults || {};
      providerSelect.value = defaults.video_provider || setup.provider || 'depth_parallax';
      consent.checked = defaults.video_remote_ltx_consent_version === REMOTE_LTX_CONSENT_VERSION;
      enabled.checked = defaults.video_gen_enabled === true;
      if (defaults.video_seed !== null && defaults.video_seed !== undefined) seed.value = defaults.video_seed;
      setRow(planner, setup.planner?.ready, setup.planner?.message);
      setRow(anchor, setup.image_default?.ready, setup.image_default?.message);
      const selected = selectedProviderStatus() || {};
      setRow(providerRow, selected.available, selected.message || selected.reason);
      remoteStatus.textContent = setup.remote_ltx?.message || setup.remote_ltx?.reason || 'Remote status is checked when selected.';
      const image = imageRuntime();
      if (image?.repairing) statusLine.textContent = 'Repairing the local Diffusers and Transformers compatibility set...';
      else if (isReady()) statusLine.textContent = isRemote() ? 'Remote LTX ready. Generation uploads the anchor and derived motion prompt to the public Space.' : 'Local Motion ready. Generation stays private on this machine.';
      else statusLine.textContent = selected.message || setup.image_default?.message || 'Finish the checklist to use video generation.';
      buttons();
      return setup;
    } catch (error) {
      statusLine.textContent = error.message || 'Could not check video setup.';
      return null;
    }
  }

  function show(job) {
    output.style.display = 'flex';
    output.innerHTML = '';
    output.append(node('div', { text: stageText(job), style: 'font-size:12px;' }));
    if (job.anchor_url) {
      const label = job.provider === 'remote_ltx'
        ? 'Animation anchor generated - preparing remote motion.'
        : 'Animation anchor generated - creating depth-aware camera motion.';
      output.append(node('strong', { text: job.status === 'succeeded' ? 'Animation anchor' : label }));
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
      statusLine.textContent = stageText(job);
      show(job);
      if (job.anchor_ready && !observedAnchor) {
        observedAnchor = true;
        window.dispatchEvent(new Event('gallery-refresh'));
      }
      if (job.status === 'succeeded') {
        window.dispatchEvent(new Event('gallery-refresh'));
        jobId = ''; cancel.style.display = 'none'; await refresh(); return;
      }
      if (['failed', 'cancelled', 'interrupted'].includes(job.status)) {
        jobId = ''; cancel.style.display = 'none'; await refresh(); return;
      }
      await new Promise(resolve => setTimeout(resolve, 900));
    }
  }

  async function queue(url, body, message) {
    if (isRemote() && !hasConsent()) { statusLine.textContent = 'Acknowledge public Remote LTX processing before selecting or using Remote LTX.'; buttons(); return; }
    if (!isReady()) { statusLine.textContent = setup?.image_default?.message || selectedProviderStatus()?.message || 'Finish the setup checklist first.'; return; }
    observedAnchor = false; output.style.display = 'none'; cancel.style.display = ''; statusLine.textContent = message; buttons();
    try {
      const created = await api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      jobId = created.job_id;
      await poll();
    } catch (error) {
      statusLine.textContent = error.message; jobId = ''; cancel.style.display = 'none'; buttons();
    }
  }

  async function save(updates = {}) {
    const body = {
      video_gen_enabled: enabled.checked,
      video_seed: seed.value === '' ? null : Number(seed.value),
      ...updates,
    };
    const data = await api('/api/video/defaults', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    setup = { ...(setup || {}), defaults: data.defaults };
    return data.defaults;
  }

  imageDefaults.addEventListener('click', () => panel.scrollTo({ top: 0, behavior: 'smooth' }));
  enabled.addEventListener('change', () => save().then(refresh).catch(error => { statusLine.textContent = error.message; }));
  seed.addEventListener('change', () => save().catch(error => { statusLine.textContent = error.message; }));
  providerSelect.addEventListener('change', async () => {
    const selected = provider();
    if (selected === 'remote_ltx' && !hasConsent()) {
      statusLine.textContent = 'Acknowledge public Remote LTX processing before selecting Remote LTX.';
      renderProviderMode();
      buttons();
      return;
    }
    try {
      await save({ video_provider: selected });
      await refresh();
    } catch (error) {
      statusLine.textContent = error.message;
      await refresh();
    }
  });
  consent.addEventListener('change', async () => {
    if (!consent.checked) { await refresh(); return; }
    try {
      const updates = { video_remote_ltx_consent_version: REMOTE_LTX_CONSENT_VERSION };
      if (provider() === 'remote_ltx') updates.video_provider = 'remote_ltx';
      await save(updates);
      statusLine.textContent = provider() === 'remote_ltx'
        ? 'Remote LTX acknowledgement saved. Remote LTX Video is selected.'
        : 'Remote LTX acknowledgement saved. You can now select Remote LTX Video.';
      await refresh();
    } catch (error) {
      consent.checked = false;
      statusLine.textContent = error.message;
    }
  });
  checkRemote.addEventListener('click', async () => {
    try {
      remoteStatus.textContent = 'Checking Remote LTX availability...';
      const status = await api('/api/video/setup/remote-status');
      remoteStatus.textContent = status.message || status.reason || 'Remote LTX status unavailable.';
      await refresh();
    } catch (error) {
      remoteStatus.textContent = error.message;
    }
  });
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
  repairImage.addEventListener('click', async () => {
    try {
      await api('/api/video/setup/repair-image-runtime', { method: 'POST' });
      statusLine.textContent = 'Repairing the local image runtime...';
      clearInterval(repairingImage);
      repairingImage = setInterval(async () => {
        await refresh();
        const image = imageRuntime();
        if (!image?.repairing) {
          clearInterval(repairingImage); repairingImage = null;
          statusLine.textContent = image?.available ? 'Image runtime repaired. Open Cookbook and restart Local Diffusers, then rerun the test.' : (image?.last_error || image?.reason || 'Image runtime repair did not complete.');
        }
      }, 1600);
      await refresh();
    } catch (error) { statusLine.textContent = error.message; }
  });
  details.addEventListener('click', async () => {
    try {
      const image = imageRuntime();
      const result = await api(image?.last_error ? '/api/video/setup/repair-image-runtime/log' : '/api/video/setup/install-log');
      output.style.display = 'flex'; output.innerHTML = '';
      output.append(node('pre', { text: result.log || 'No setup output is available yet.', style: 'margin:0;max-height:240px;overflow:auto;white-space:pre-wrap;padding:8px;border:1px solid var(--border);border-radius:7px;font-size:11px;' }));
    } catch (error) { statusLine.textContent = error.message; }
  });
  test.addEventListener('click', () => queue('/api/video/setup/test', {}, 'Running the local guided test...'));
  generate.addEventListener('click', () => {
    if (!prompt.value.trim()) { statusLine.textContent = 'Describe the video first.'; return; }
    queue('/api/video/generations', { prompt: prompt.value.trim(), video_seed: seed.value === '' ? null : Number(seed.value), session_id: window.sessionModule?.getCurrentSessionId?.() || null }, isRemote() ? 'Creating anchor for remote motion...' : 'Creating the stable animation anchor...');
  });
  cancel.addEventListener('click', () => { if (jobId) api(`/api/video/generations/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }).catch(error => { statusLine.textContent = error.message; }); });
  refresh();
}

function boot() {
  createCard();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true }); else boot();
