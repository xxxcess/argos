const TERMINAL_STATUSES = new Set(['succeeded', 'failed', 'cancelled', 'interrupted']);

function mediaUrl(raw) {
  if (typeof raw !== 'string' || !raw.trim()) return '';
  try {
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin) return '';
    if (!url.pathname.startsWith('/api/generated-image/')) return '';
    return url.pathname + url.search;
  } catch (_) {
    return '';
  }
}

function videoJobId(payload) {
  return String(payload?.video_job_id || payload?.job_id || '').trim();
}

function normalizedJob(payload) {
  return {
    job_id: videoJobId(payload),
    provider: String(payload?.provider || payload?.video_provider || 'depth_parallax'),
    status: String(payload?.video_status || payload?.status || 'queued'),
    stage: String(payload?.video_stage || payload?.stage || 'planning_anchor'),
    error: payload?.error || '',
    anchor_url: payload?.anchor_url || '',
    video_url: payload?.video_url || '',
    display_duration: payload?.display_duration || '',
  };
}

function stageText(job) {
  if (job.status === 'succeeded') return job.display_duration || (job.provider === 'remote_ltx' ? 'About 8 seconds - Remote LTX - 30 FPS' : '8 seconds - Local Motion - 24 FPS');
  if (job.status === 'failed') return job.error || 'Video generation failed';
  if (job.status === 'cancelled') return 'Video generation cancelled';
  if (job.status === 'interrupted') return 'Video generation was interrupted';
  if (job.stage === 'generating_anchor') return 'Creating image anchor';
  if (job.stage === 'anchor_ready') return 'Animation anchor generated';
  if (job.stage === 'planning_remote_motion') return 'Preparing motion direction';
  if (job.stage === 'submitting_remote_ltx') return 'Submitting to public LTX queue';
  if (job.stage === 'waiting_remote_queue') return 'Waiting for shared GPU';
  if (job.stage === 'generating_remote_ltx') return 'Generating video';
  if (job.stage === 'downloading_remote_video') return 'Downloading final video';
  if (job.stage === 'validating_remote_video') return 'Validating final video';
  if (job.stage === 'planning_local_motion') return 'Preparing local camera motion';
  if (job.stage === 'rendering_depth_parallax') return 'Rendering depth-aware motion';
  return 'Preparing image anchor';
}

async function loadJob(jobId) {
  const response = await fetch('/api/video/generations/' + encodeURIComponent(jobId), {
    credentials: 'same-origin',
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || data.error || 'Video status unavailable');
  return data;
}

function appendGalleryButton(card) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'footer-copy-btn video-agent-gallery-link';
  button.textContent = 'Open in Gallery';
  button.addEventListener('click', () => {
    import('./gallery.js').then(mod => {
      const open = mod.openGallery || (mod.default && mod.default.openGallery);
      if (open) open();
    }).catch(() => {});
  });
  card.appendChild(button);
}

function scrollToVideoSettingsCard() {
  const scroll = () => {
    const card = document.getElementById('local-depth-video-card');
    if (card && typeof card.scrollIntoView === 'function') {
      card.scrollIntoView({ behavior: 'smooth', block: 'start' });
      const providerSection = document.getElementById('video-provider-section');
      if (providerSection && typeof providerSection.focus === 'function') {
        providerSection.setAttribute('tabindex', '-1');
        providerSection.focus({ preventScroll: true });
      }
    }
  };
  if (typeof requestAnimationFrame === 'function') requestAnimationFrame(scroll);
  else setTimeout(scroll, 0);
}

function openSettingsTabFallback(tab) {
  const modal = document.getElementById('settings-modal');
  if (modal) modal.classList.remove('hidden');
  const root = modal || document;
  const tabButton = root.querySelector(`[data-settings-tab="${tab}"]`) || document.querySelector(`[data-settings-tab="${tab}"]`);
  if (tabButton && typeof tabButton.click === 'function') tabButton.click();
}

async function openVideoSettingsPanel() {
  try {
    const settings = window.settingsModule;
    if (settings && typeof settings.open === 'function') {
      settings.open('ai');
      scrollToVideoSettingsCard();
      return;
    }
  } catch (_) {}

  try {
    const mod = await import('./settings.js');
    const settings = mod.default || mod;
    if (settings && typeof settings.open === 'function') {
      settings.open('ai');
      scrollToVideoSettingsCard();
      return;
    }
  } catch (_) {}

  openSettingsTabFallback('ai');
  scrollToVideoSettingsCard();
}

function appendSettingsButton(card) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'footer-copy-btn video-agent-retry-link';
  button.textContent = 'Open Video Settings';
  button.addEventListener('click', () => { openVideoSettingsPanel(); });
  card.appendChild(button);
}

export function updateVideoJobCard(card, payload) {
  const job = normalizedJob(payload);
  card.dataset.videoJobStatus = job.status;
  card.dataset.videoJobStage = job.stage;
  card.replaceChildren();

  const status = document.createElement('div');
  status.className = 'video-agent-status';
  status.textContent = stageText(job);
  card.appendChild(status);

  const anchorSrc = mediaUrl(job.anchor_url);
  if (anchorSrc) {
    const label = document.createElement('strong');
    label.textContent = TERMINAL_STATUSES.has(job.status) && job.status !== 'succeeded'
      ? 'Animation anchor generated before rendering stopped.'
      : job.status === 'succeeded'
        ? 'Animation anchor'
      : job.provider === 'remote_ltx'
        ? 'Animation anchor generated - preparing remote motion.'
      : 'Animation anchor generated - now creating depth-aware motion.';
    const image = document.createElement('img');
    image.src = anchorSrc;
    image.alt = 'Generated animation anchor';
    image.className = 'video-agent-anchor';
    image.style.cssText = 'max-width:240px;border-radius:7px;border:1px solid var(--border);';
    card.append(label, image);
  }

  const videoSrc = mediaUrl(job.video_url);
  if (videoSrc) {
    const video = document.createElement('video');
    video.controls = true;
    video.muted = true;
    video.playsInline = true;
    video.preload = 'metadata';
    video.src = videoSrc;
    video.className = 'video-agent-video';
    video.style.cssText = 'max-width:100%;border-radius:7px;background:#000;';
    card.appendChild(video);
    appendGalleryButton(card);
  } else if (TERMINAL_STATUSES.has(job.status) && job.status !== 'succeeded') {
    appendSettingsButton(card);
  }

  if (anchorSrc && !card.dataset.videoAnchorRefreshSent) {
    card.dataset.videoAnchorRefreshSent = '1';
    window.dispatchEvent(new Event('gallery-refresh'));
  }
  if (videoSrc && !card.dataset.videoFinalRefreshSent) {
    card.dataset.videoFinalRefreshSent = '1';
    window.dispatchEvent(new Event('gallery-refresh'));
  }
}

export async function pollVideoJobCard(card, jobId) {
  if (!card || !jobId || card._videoPolling) return;
  card._videoPolling = true;
  try {
    while (document.body.contains(card)) {
      const job = await loadJob(jobId);
      updateVideoJobCard(card, job);
      if (TERMINAL_STATUSES.has(String(job.status || ''))) break;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  } catch (error) {
    updateVideoJobCard(card, {
      job_id: jobId,
      status: 'failed',
      stage: 'failed',
      error: error?.message || 'Video status unavailable',
    });
  } finally {
    card._videoPolling = false;
  }
}

export function renderVideoJobCard(host, payload) {
  const job = normalizedJob(payload);
  if (!host || !job.job_id) return null;
  let card = Array.from(host.querySelectorAll('.video-agent-job')).find(
    item => item.dataset.videoJob === job.job_id,
  );
  if (!card) {
    card = document.createElement('div');
    card.className = 'video-agent-job';
    card.dataset.videoJob = job.job_id;
    card.style.cssText = 'display:flex;flex-direction:column;gap:6px;margin-top:8px;padding:8px;border:1px solid var(--border);border-radius:7px;font-size:12px;';
    host.appendChild(card);
  }
  updateVideoJobCard(card, job);
  if (!TERMINAL_STATUSES.has(job.status)) {
    pollVideoJobCard(card, job.job_id);
  }
  return card;
}

export default {
  renderVideoJobCard,
  updateVideoJobCard,
  pollVideoJobCard,
};
