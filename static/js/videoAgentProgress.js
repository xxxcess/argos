// Renders the job token emitted by Generate video in the agent tool output.
const RE = /\[video-job:([a-f0-9]{24,64})\]/i;
const active = new Set();

async function status(id) {
  const response = await fetch('/api/video/generations/' + encodeURIComponent(id), { credentials: 'same-origin' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Video status unavailable');
  return data;
}

function panel(host, id) {
  let out = host.querySelector('.video-job-card[data-id="' + id + '"]');
  if (out) return out;
  out = document.createElement('div');
  out.className = 'video-job-card';
  out.dataset.id = id;
  out.style.cssText = 'display:flex;flex-direction:column;gap:6px;margin-top:8px;padding:8px;border:1px solid var(--border);border-radius:7px;font-size:12px;';
  out.textContent = 'Queued anchor-first video generation…';
  host.appendChild(out);
  return out;
}

async function follow(host, id) {
  if (active.has(id)) return;
  active.add(id);
  const out = panel(host, id);
  try {
    for (;;) {
      const job = await status(id);
      out.innerHTML = '';
      const state = document.createElement('div');
      state.textContent = job.stage || job.status;
      out.appendChild(state);
      if (job.anchor_url) {
        const label = document.createElement('strong');
        label.textContent = 'Animation anchor generated — now creating motion.';
        const image = document.createElement('img');
        image.src = job.anchor_url;
        image.alt = 'Generated animation anchor';
        image.style.cssText = 'max-width:220px;border-radius:7px;border:1px solid var(--border);';
        out.append(label, image);
        window.dispatchEvent(new Event('gallery-refresh'));
      }
      if (job.video_url) {
        const video = document.createElement('video');
        video.controls = true;
        video.muted = true;
        video.playsInline = true;
        video.preload = 'metadata';
        video.src = job.video_url;
        video.style.cssText = 'max-width:100%;border-radius:7px;background:#000;';
        out.appendChild(video);
        window.dispatchEvent(new Event('gallery-refresh'));
      }
      if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(job.status)) break;
      await new Promise(resolve => setTimeout(resolve, 1200));
    }
  } catch (error) {
    out.textContent = error.message;
  } finally {
    active.delete(id);
  }
}

function scan() {
  document.querySelectorAll('.agent-thread, .agent-tool-output, .msg-ai').forEach(node => {
    if (node.dataset.videoJobFound) return;
    const match = RE.exec(node.textContent || '');
    if (!match) return;
    node.dataset.videoJobFound = '1';
    follow(node.querySelector('.agent-thread-content') || node.querySelector('.body') || node, match[1]);
  });
}

setInterval(scan, 1000);
scan();
