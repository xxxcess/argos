// Anchor-first video controls for Settings → AI Defaults.
function make(tag, text) { const n = document.createElement(tag); if (text) n.textContent = text; return n; }
async function request(url, options = {}) {
  const r = await fetch(url, { credentials: 'same-origin', ...options });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || data.error || 'Request failed');
  return data;
}
function addCard() {
  if (document.getElementById('anchor-video-card')) return;
  const panel = document.querySelector('#settings-modal [data-settings-panel="ai"]');
  if (!panel) return;
  const card = make('section');
  card.id = 'anchor-video-card'; card.className = 'admin-card';
  card.style.cssText = 'display:flex;flex-direction:column;gap:9px;margin-top:12px;';
  const title = make('h2', 'Video Generation'); title.style.margin = '0';
  const note = make('div', 'Anchor-first LTX-2 · 512×512 · ~10 seconds · silent MP4'); note.style.cssText = 'font-size:12px;opacity:.75;';
  const runtime = make('div', 'Checking video runtime…'); runtime.style.cssText = 'font-size:12px;opacity:.75;';
  const enabled = document.createElement('input'); enabled.type = 'checkbox';
  const enableLabel = document.createElement('label'); enableLabel.style.cssText = 'display:flex;gap:7px;align-items:center;font-size:12px;'; enableLabel.append(enabled, document.createTextNode('Enable anchor-first video generation'));
  const seed = document.createElement('input'); seed.type = 'number'; seed.placeholder = 'Random seed'; seed.min = '0'; seed.max = '2147483647'; seed.className = 'settings-input';
  const prompt = document.createElement('textarea'); prompt.placeholder = 'Describe the video. Argos creates a Gallery anchor first, then animates it.'; prompt.style.cssText = 'min-height:70px;resize:vertical;padding:8px;border-radius:7px;font:inherit;';
  const go = make('button', 'Create anchor and animate'); go.type = 'button'; go.className = 'admin-btn-add';
  const cancel = make('button', 'Cancel'); cancel.type = 'button'; cancel.className = 'admin-btn-secondary'; cancel.style.display = 'none';
  const status = make('div'); status.style.cssText = 'font-size:12px;min-height:18px;';
  const output = make('div'); output.style.cssText = 'display:none;flex-direction:column;gap:7px;';
  card.append(title, note, runtime, enableLabel, seed, prompt, go, cancel, status, output); panel.append(card);
  let ready = false, jobId = '', sawAnchor = false;
  async function save() { await request('/api/video/defaults', {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({video_gen_enabled:enabled.checked,video_seed:seed.value===''?null:Number(seed.value)})}); }
  enabled.onchange = () => save().then(()=>go.disabled=!ready||!enabled.checked).catch(e=>runtime.textContent=e.message);
  seed.onchange = () => save().catch(e=>runtime.textContent=e.message);
  function render(job) {
    output.style.display='flex'; output.innerHTML='';
    if (job.anchor_url) { output.append(make('strong','Animation anchor generated — now creating motion.')); const img=document.createElement('img'); img.src=job.anchor_url; img.style.cssText='max-width:220px;border-radius:8px;'; output.append(img); }
    if (job.video_url) { const v=document.createElement('video'); v.controls=true; v.muted=true; v.playsInline=true; v.preload='metadata'; v.src=job.video_url; v.style.cssText='max-width:100%;border-radius:8px;background:#000;'; output.append(v); const link=document.createElement('a'); link.href='/gallery'; link.textContent='Open in Gallery'; output.append(link); }
  }
  async function poll() {
    while (jobId) {
      const job=await request('/api/video/generations/'+encodeURIComponent(jobId)); status.textContent=job.stage||job.status;
      if (job.anchor_ready&&!sawAnchor) { sawAnchor=true; render(job); window.dispatchEvent(new Event('gallery-refresh')); }
      if (job.status==='succeeded'||['failed','cancelled','interrupted'].includes(job.status)) { if(job.status==='succeeded'){render(job);window.dispatchEvent(new Event('gallery-refresh'));} else status.textContent=job.error||job.stage; jobId='';go.disabled=false;cancel.style.display='none';return; }
      await new Promise(r=>setTimeout(r,1000));
    }
  }
  go.onclick=async()=>{ if(!prompt.value.trim()){status.textContent='Describe the video first.';return;} go.disabled=true;cancel.style.display='';sawAnchor=false;output.style.display='none';try{const j=await request('/api/video/generations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({prompt:prompt.value.trim(),video_seed:seed.value===''?null:Number(seed.value),session_id:window.sessionModule?.getCurrentSessionId?.()||null})});jobId=j.job_id;poll();}catch(e){status.textContent=e.message;go.disabled=false;cancel.style.display='none';}};
  cancel.onclick=()=>request('/api/video/generations/'+encodeURIComponent(jobId)+'/cancel',{method:'POST'}).catch(e=>status.textContent=e.message);
  Promise.all([request('/api/video/defaults'),request('/api/video/runtime')]).then(([d,r])=>{enabled.checked=d.defaults?.video_gen_enabled===true;if(d.defaults?.video_seed!==null&&d.defaults?.video_seed!==undefined)seed.value=d.defaults.video_seed;ready=r.available===true;runtime.textContent=ready?'Ready. Argos plans distinct anchor and motion prompts automatically.':(r.reason||'Video runtime unavailable.');go.disabled=!ready||!enabled.checked;}).catch(e=>{runtime.textContent=e.message;go.disabled=true;});
}
function boot(){addCard();new MutationObserver(addCard).observe(document.documentElement,{childList:true,subtree:true});}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
