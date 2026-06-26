// Turns the stable [video-job:<id>] tool result token into anchor-first media progress.
const JOB = /\[video-job:([a-f0-9]{24,64})\]/i;
const active = new Set();

async function load(id) {
  const response = await fetch('/api/video/generations/' + encodeURIComponent(id), { credentials: 'same-origin' });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || 'Video status unavailable');
  return data;
}

function card(host, id) {
  let el = host.querySelector('.video-agent-job[data-video-job="' + id + '"]');
  if (el) return el;
  el = document.createElement('div');
  el.className = 'video-agent-job';
  el.dataset.videoJob = id;
  el.style.cssText = 'display:flex;flex-direction:column;gap:6px;margin-top:8px;padding:8px;border:1px solid var(--border);border-radius:7px;font-size:12px;';
  host.appendChild(el);
  return el;
}

async function follow(host, id) {
  if (active.has(id)) return;
  active.add(id);
  const el = card(host, id);
  try {
    while (true) {
      const job = await load(id);
      el.innerHTML = '';
      const state = document.createElement('div');
      state.textContent = job.stage || job.status;
      el.appendChild(state);
      if (job.anchor_url) {
        const label = document.createElement('strong');
        label.textContent = 'Animation anchor generated — now creating depth-aware motion.';
        const image = document.createElement('img');
        image.src = job.anchor_url;
        image.alt = 'Generated animation anchor';
        image.style.cssText = 'max-width:220px;border-radius:7px;border:1px solid var(--border);';
        el.append(label, image);
        window.dispatchEvent(new Event('gallery-refresh'));
      }
      if (job.video_url) {
        const video = document.createElement('video');
        video.controls = true; video.muted = true; video.playsInline = true; video.preload = 'metadata'; video.src = job.video_url;
        video.style.cssText = 'max-width:100%;border-radius:7px;background:#000;';
        el.appendChild(video);
        window.dispatchEvent(new Event('gallery-refresh'));
      }
      if (['succeeded', 'failed', 'cancelled', 'interrupted'].includes(job.status)) break;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  } catch (error) {
    el.textContent = error.message;
  } finally {
    active.delete(id);
  }
}

function scan() {
  document.querySelectorAll('.agent-thread, .agent-tool-output, .msg-ai').forEach(item => {
    if (item.dataset.videoJobBound) return;
    const match = JOB.exec(item.textContent || '');
    if (!match) return;
    item.dataset.videoJobBound = '1';
    follow(item.querySelector('.agent-thread-content') || item.querySelector('.body') || item, match[1]);
  });
}

setInterval(scan, 1000);
scan();
