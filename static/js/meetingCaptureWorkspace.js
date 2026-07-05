// Argos Venture Audio Capture workspace tab.
//
// Capture is opt-in and scoped to an explicit recording run. A late browser-STT
// or server-STT callback from a stopped, closed, or superseded run is ignored.

const STT_STATS_ENDPOINT = '/api/stt/stats';
const STT_ENDPOINT = '/api/stt/transcribe';
const BRIEF_ENDPOINT = '/api/meeting-briefs/generate';
const DOCUMENT_ENDPOINT = '/api/document';
const SEGMENT_MS = 10_000;
const MAX_TRANSCRIPT_CHARS = 120_000;

const captures = new Map();
let activeCaptureId = null;
let activeRecordingId = null;
let root = null;
let rootWired = false;
let tabSyncQueued = false;

function makeId() {
  return window.crypto?.randomUUID?.() || `meeting-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function escapeSelector(value) {
  if (window.CSS?.escape) return window.CSS.escape(String(value));
  return String(value).replace(/[^a-zA-Z0-9_-]/g, char => `\\${char}`);
}

function clean(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function elapsedMs(capture) {
  return capture.elapsedMs + (capture.phase === 'recording' && capture.runningSince ? Date.now() - capture.runningSince : 0);
}

function formatElapsed(milliseconds) {
  const seconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(seconds / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  const remainder = seconds % 60;
  return hours
    ? `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`
    : `${String(minutes).padStart(2, '0')}:${String(remainder).padStart(2, '0')}`;
}

function timestamp(capture) {
  return `[${formatElapsed(elapsedMs(capture))}]`;
}

function microphoneIcon() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5M8 22h8"/></svg>';
}

function recordIcon() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="6" fill="currentColor" stroke="none"/></svg>';
}

function newCapture({ captureId = makeId(), title = 'Audio Capture' } = {}) {
  return {
    captureId,
    title: clean(title) || 'Audio Capture',
    transcript: '',
    interim: '',
    phase: 'idle', // idle | recording | paused | stopping | stopped
    provider: 'unchecked',
    language: '',
    consent: false,
    elapsedMs: 0,
    runningSince: 0,
    stream: null,
    recorder: null,
    recognition: null,
    segmentTimer: 0,
    segmentQueue: [],
    draining: false,
    timer: 0,
    audioContext: null,
    analyser: null,
    audioSource: null,
    waveformFrame: 0,
    runId: 0,
    activeRunId: 0,
    closed: false,
    status: 'This tab is idle. No microphone access is requested until Start capture.',
    statusError: false,
  };
}

function activeCapture() {
  return activeCaptureId ? captures.get(activeCaptureId) || null : null;
}

function installStyles() {
  if (document.getElementById('meeting-capture-workspace-style')) return;
  const style = document.createElement('style');
  style.id = 'meeting-capture-workspace-style';
  style.textContent = `
    #meeting-capture-workspace { position:fixed; inset:var(--workspace-shell-h,0px) 0 0 0; z-index:35; display:none; grid-template-rows:auto minmax(0,1fr) auto; background:var(--bg,#17191d); color:var(--fg,#e8eaed); transition:right .15s ease,left .15s ease; }
    body.workspace-meeting-capture-active #meeting-capture-workspace { display:grid; }
    body.workspace-meeting-capture-active #chat-container { visibility:hidden; pointer-events:none; }
    body.workspace-meeting-capture-active #sidebar, body.workspace-meeting-capture-active #icon-rail, body.workspace-meeting-capture-active #sidebar-toggle, body.workspace-meeting-capture-active #sidebar-collapse, body.workspace-meeting-capture-active [data-sidebar-toggle], body.workspace-meeting-capture-active [data-sidebar-collapse] { visibility:hidden!important; pointer-events:none!important; }
    .meeting-capture-head { display:flex; justify-content:flex-end; align-items:center; gap:14px; min-width:0; padding:14px 24px; border-bottom:1px solid var(--border,#3a3f47); background:var(--panel,#21252b); }
    .meeting-capture-title-input { order:-1; flex:1; max-width:720px; min-width:160px; padding:8px 10px; border:1px solid var(--border,#3a3f47); border-radius:7px; background:var(--bg,#17191d); color:var(--fg,#e8eaed); font:inherit; }
    .meeting-capture-state { display:inline-flex; align-items:center; gap:7px; font-size:12px; white-space:nowrap; opacity:.82; }
    .meeting-capture-state::before { content:''; width:9px; height:9px; border-radius:999px; background:var(--border,#646a73); }
    .meeting-capture-state[data-phase="recording"]::before { background:var(--red,#e06c75); box-shadow:0 0 0 5px color-mix(in srgb,var(--red,#e06c75) 20%,transparent); }
    .meeting-capture-state[data-phase="paused"]::before { background:#e5c07b; }
    .meeting-capture-main { min-height:0; display:grid; grid-template-columns:minmax(0,1fr) minmax(230px,300px); gap:18px; padding:20px 24px; overflow:hidden; }
    .meeting-capture-transcript-card, .meeting-capture-details { min-height:0; border:1px solid var(--border,#3a3f47); border-radius:12px; background:var(--panel,#21252b); }
    .meeting-capture-transcript-card { display:flex; flex-direction:column; overflow:hidden; }
    .meeting-capture-transcript-head { display:flex; justify-content:space-between; align-items:center; padding:13px 16px; border-bottom:1px solid var(--border,#3a3f47); font-size:12px; }
    .meeting-capture-transcript-head span { opacity:.6; }
    .meeting-capture-log { flex:1; overflow:auto; padding:18px 20px; font:14px/1.7 ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; white-space:pre-wrap; }
    .meeting-capture-log pre { margin:0; font:inherit; white-space:pre-wrap; }
    .meeting-capture-empty { margin:0; color:var(--muted,#a4a8ae); font-family:var(--font-family,system-ui,sans-serif); opacity:.72; }
    .meeting-capture-interim { display:block; margin-top:10px; opacity:.52; font-style:italic; }
    .meeting-capture-details { padding:16px; overflow:auto; }
    .meeting-capture-details h2 { margin:0 0 10px; font-size:13px; }
    .meeting-capture-details p { margin:0 0 12px; font-size:12px; line-height:1.5; opacity:.72; }
    .meeting-capture-consent { display:flex; align-items:flex-start; gap:8px; padding:10px; border:1px solid color-mix(in srgb,var(--border,#3a3f47) 75%,transparent); border-radius:8px; font-size:12px; line-height:1.4; }
    .meeting-capture-consent input { margin-top:2px; }
    .meeting-capture-provider { margin-top:14px; padding-top:14px; border-top:1px solid var(--border,#3a3f47); font-size:12px; }
    .meeting-capture-provider strong { display:block; margin-bottom:4px; }
    .meeting-capture-footer { display:grid; gap:10px; padding:12px 24px 14px; border-top:1px solid var(--border,#3a3f47); background:var(--panel,#21252b); }
    .meeting-recorder-readout { display:flex; align-items:center; gap:14px; min-width:0; }
    .meeting-recorder-time { flex:0 0 auto; font:600 24px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.04em; white-space:nowrap; }
    .meeting-waveform { height:28px; width:100%; min-width:0; border-radius:6px; background:color-mix(in srgb,var(--bg,#17191d) 65%,transparent); }
    .meeting-capture-control-row { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; }
    .meeting-recorder-controls, .meeting-capture-actions { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
    .meeting-recorder-controls button, .meeting-capture-actions button { border:1px solid var(--border,#3a3f47); border-radius:8px; padding:9px 12px; background:var(--bg,#17191d); color:var(--fg,#e8eaed); font:inherit; font-size:12px; cursor:pointer; }
    .meeting-recorder-controls button:disabled, .meeting-capture-actions button:disabled { cursor:not-allowed; opacity:.45; }
    .meeting-record-btn { min-width:112px; color:white!important; border-color:var(--red,#e06c75)!important; background:var(--red,#e06c75)!important; }
    .meeting-stop-btn { color:white!important; border-color:#4b5058!important; background:#4b5058!important; }
    .meeting-recorder-controls svg { width:13px; height:13px; fill:none; stroke:currentColor; stroke-width:2; vertical-align:-2px; margin-right:4px; }
    .meeting-record-btn svg { fill:currentColor; stroke:none; }
    .meeting-capture-actions .primary { border-color:var(--accent,var(--red,#e06c75)); background:color-mix(in srgb,var(--accent,var(--red,#e06c75)) 18%,var(--bg)); }
    .meeting-capture-status { min-height:1.25em; font-size:11px; opacity:.75; }
    .meeting-capture-status[data-error="true"] { color:var(--red,#e06c75); opacity:1; }
    .workspace-tab.meeting-capture-tab .workspace-tab-icon svg { width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:1.8; }
    .workspace-tab.meeting-capture-tab[data-state="recording"] .workspace-tab-state { display:block; background:var(--red,#e06c75); }
    @media (max-width:820px) {
      #meeting-capture-workspace { inset:var(--workspace-shell-h,0px) 0 0 0!important; }
      .meeting-capture-head { padding:12px 14px; }
      .meeting-capture-main { grid-template-columns:1fr; padding:12px; overflow:auto; }
      .meeting-capture-transcript-card { min-height:42vh; }
      .meeting-capture-footer { padding:12px; }
      .meeting-capture-control-row { align-items:stretch; }
      .meeting-recorder-controls, .meeting-capture-actions { width:100%; }
    }
  `;
  document.head.appendChild(style);
}

function setStatus(capture, message, isError = false) {
  if (!capture) return;
  capture.status = message || '';
  capture.statusError = !!isError;
  if (capture.captureId === activeCaptureId) render();
}

function phaseLabel(phase) {
  return ({ idle: 'Ready to capture', recording: 'Recording live', paused: 'Capture paused', stopping: 'Finishing transcript', stopped: 'Transcript ready' })[phase] || 'Ready to capture';
}

function isCurrentRun(capture, runId, { allowStopping = false } = {}) {
  if (!capture || capture.closed || captures.get(capture.captureId) !== capture) return false;
  if (capture.activeRunId !== runId) return false;
  return capture.phase === 'recording' || (allowStopping && capture.phase === 'stopping');
}

function renderTranscript(capture, log) {
  log.replaceChildren();
  if (!capture.transcript && !capture.interim) {
    const empty = document.createElement('p');
    empty.className = 'meeting-capture-empty';
    empty.textContent = 'Your live transcript will appear here after you start capture.';
    log.appendChild(empty);
    return;
  }
  if (capture.transcript) {
    const final = document.createElement('pre');
    final.textContent = capture.transcript;
    log.appendChild(final);
  }
  if (capture.interim && capture.phase === 'recording') {
    const interim = document.createElement('span');
    interim.className = 'meeting-capture-interim';
    interim.textContent = `${capture.transcript ? '\n' : ''}${timestamp(capture)} ${capture.interim}`;
    log.appendChild(interim);
  }
  log.scrollTop = log.scrollHeight;
}

function ensureRoot() {
  installStyles();
  if (root) return root;
  root = document.createElement('main');
  root.id = 'meeting-capture-workspace';
  root.setAttribute('aria-label', 'Audio capture workspace');
  root.innerHTML = `
    <header class="meeting-capture-head">
      <input id="meeting-capture-title" class="meeting-capture-title-input" type="text" maxlength="180" placeholder="Meeting title" aria-label="Meeting title" />
      <div id="meeting-capture-state" class="meeting-capture-state" data-phase="idle">Ready to capture</div>
    </header>
    <section class="meeting-capture-main">
      <article class="meeting-capture-transcript-card">
        <div class="meeting-capture-transcript-head"><strong>Transcript</strong><span id="meeting-capture-character-count">0 characters</span></div>
        <div id="meeting-capture-log" class="meeting-capture-log" aria-live="polite"></div>
      </article>
      <aside class="meeting-capture-details">
        <h2>Capture settings</h2>
        <p>This tab does not access your microphone until you check consent and press Start capture.</p>
        <label class="meeting-capture-consent"><input id="meeting-capture-consent" type="checkbox" /><span>I have consent and authority to capture and transcribe this meeting.</span></label>
        <div class="meeting-capture-provider"><strong>Speech-to-text</strong><span id="meeting-capture-provider">Check consent to verify configuration.</span></div>
      </aside>
    </section>
    <footer class="meeting-capture-footer">
      <div class="meeting-recorder-readout"><span id="meeting-capture-time" class="meeting-recorder-time">00:00</span><canvas id="meeting-capture-waveform" class="meeting-waveform" aria-label="Microphone activity"></canvas></div>
      <div class="meeting-capture-control-row">
        <div class="meeting-recorder-controls">
          <button id="meeting-capture-record" class="meeting-record-btn" type="button">${recordIcon()}<span>Start capture</span></button>
          <button id="meeting-capture-pause" type="button" disabled>Pause</button>
          <button id="meeting-capture-stop" class="meeting-stop-btn" type="button" disabled>Stop</button>
        </div>
        <div class="meeting-capture-actions">
          <button id="meeting-capture-export-transcript" type="button">Export transcript</button>
          <button id="meeting-capture-export-brief" class="primary" type="button">Generate & export brief</button>
        </div>
      </div>
      <div id="meeting-capture-status" class="meeting-capture-status" aria-live="polite"></div>
    </footer>
  `;
  document.body.appendChild(root);
  wireRoot();
  return root;
}

function wireRoot() {
  if (rootWired || !root) return;
  rootWired = true;
  const title = root.querySelector('#meeting-capture-title');
  const consent = root.querySelector('#meeting-capture-consent');
  root.querySelector('#meeting-capture-record')?.addEventListener('click', async () => {
    const capture = activeCapture();
    if (capture?.phase === 'paused') await resumeCapture(capture);
    else await startCapture(capture);
  });
  root.querySelector('#meeting-capture-pause')?.addEventListener('click', () => pauseCapture(activeCapture()));
  root.querySelector('#meeting-capture-stop')?.addEventListener('click', () => stopCapture(activeCapture()));
  root.querySelector('#meeting-capture-export-transcript')?.addEventListener('click', () => exportTranscript(activeCapture()));
  root.querySelector('#meeting-capture-export-brief')?.addEventListener('click', () => exportBrief(activeCapture()));
  title?.addEventListener('input', () => {
    const capture = activeCapture();
    if (!capture) return;
    capture.title = clean(title.value) || 'Audio Capture';
    queueTabSync();
  });
  consent?.addEventListener('change', async () => {
    const capture = activeCapture();
    if (!capture) return;
    capture.consent = !!consent.checked;
    if (capture.consent && capture.provider === 'unchecked') await loadSttConfiguration(capture);
    render();
  });
}

function render() {
  const capture = activeCapture();
  if (!capture || !root) return;
  const title = root.querySelector('#meeting-capture-title');
  const consent = root.querySelector('#meeting-capture-consent');
  const state = root.querySelector('#meeting-capture-state');
  const provider = root.querySelector('#meeting-capture-provider');
  const record = root.querySelector('#meeting-capture-record');
  const pause = root.querySelector('#meeting-capture-pause');
  const stop = root.querySelector('#meeting-capture-stop');
  const exportTranscriptButton = root.querySelector('#meeting-capture-export-transcript');
  const exportBriefButton = root.querySelector('#meeting-capture-export-brief');
  if (document.activeElement !== title) title.value = capture.title;
  consent.checked = capture.consent;
  state.dataset.phase = capture.phase;
  state.textContent = phaseLabel(capture.phase);
  provider.textContent = capture.provider === 'unchecked' ? 'Check consent to verify configuration.'
    : capture.provider === 'loading' ? 'Checking configuration…'
    : capture.provider === 'browser' ? 'Browser speech recognition'
    : capture.provider === 'disabled' ? 'Speech-to-text is not configured'
    : `Argos ${capture.provider} provider`;
  renderTranscript(capture, root.querySelector('#meeting-capture-log'));
  root.querySelector('#meeting-capture-character-count').textContent = `${capture.transcript.length.toLocaleString()} characters`;
  root.querySelector('#meeting-capture-time').textContent = formatElapsed(elapsedMs(capture));
  const status = root.querySelector('#meeting-capture-status');
  status.textContent = capture.status;
  status.dataset.error = capture.statusError ? 'true' : 'false';
  const providerReady = ['browser', 'local', 'endpoint'].includes(capture.provider);
  const anotherCaptureIsRecording = !!activeRecordingId && activeRecordingId !== capture.captureId;
  record.disabled = !capture.consent || !providerReady || capture.phase === 'recording' || capture.phase === 'stopping' || anotherCaptureIsRecording;
  record.querySelector('span').textContent = capture.phase === 'paused' ? 'Resume capture' : 'Start capture';
  pause.disabled = capture.phase !== 'recording';
  stop.disabled = !['recording', 'paused', 'stopping'].includes(capture.phase);
  const canExport = capture.phase === 'stopped' && !!capture.transcript.trim();
  exportTranscriptButton.disabled = !canExport;
  exportBriefButton.disabled = !canExport;
  queueTabSync();
}

function queueTabSync() {
  if (tabSyncQueued) return;
  tabSyncQueued = true;
  requestAnimationFrame(() => {
    tabSyncQueued = false;
    ensureTabChrome();
  });
}

function ensureTabChrome() {
  const list = document.getElementById('workspace-tab-list');
  if (!list) return;
  list.querySelectorAll('.meeting-capture-tab').forEach(tab => {
    if (!captures.has(tab.dataset.captureId)) tab.remove();
  });
  captures.forEach(capture => {
    let tab = list.querySelector(`.meeting-capture-tab[data-capture-id="${escapeSelector(capture.captureId)}"]`);
    if (!tab) {
      tab = document.createElement('div');
      tab.className = 'workspace-tab meeting-capture-tab';
      tab.dataset.captureId = capture.captureId;
      tab.dataset.tabId = `meeting:${capture.captureId}`;
      tab.setAttribute('role', 'tab');
      tab.innerHTML = `<span class="workspace-tab-icon">${microphoneIcon()}</span><span class="workspace-tab-title"></span><span class="workspace-tab-state" aria-hidden="true"></span><span class="workspace-tab-close-wrap"><button type="button" class="workspace-tab-close" aria-label="Close audio capture tab">&times;</button></span>`;
      tab.addEventListener('click', event => {
        if (!event.target.closest('.workspace-tab-close')) activateCapture(capture.captureId);
      });
      tab.addEventListener('keydown', event => {
        event.stopPropagation();
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          activateCapture(capture.captureId);
        }
      });
      tab.querySelector('.workspace-tab-close')?.addEventListener('click', async event => {
        event.stopPropagation();
        await closeCapture(capture.captureId);
      });
      list.appendChild(tab);
    }
    tab.querySelector('.workspace-tab-title').textContent = capture.title;
    tab.dataset.state = capture.phase === 'recording' ? 'recording' : (capture.phase === 'paused' ? 'paused' : 'idle');
    tab.setAttribute('aria-selected', String(capture.captureId === activeCaptureId));
    tab.tabIndex = capture.captureId === activeCaptureId ? 0 : -1;
  });
  if (activeCaptureId) list.querySelectorAll('.workspace-tab:not(.meeting-capture-tab)').forEach(tab => tab.setAttribute('aria-selected', 'false'));
}

function setHomeOnlyControlsHidden(hidden) {
  const candidates = document.querySelectorAll('button, [role="button"]');
  candidates.forEach(element => {
    if (element.closest('#meeting-capture-workspace, #workspace-shell, #doc-editor-pane')) return;
    const label = `${element.id || ''} ${element.className || ''} ${element.title || ''} ${element.getAttribute('aria-label') || ''}`.toLowerCase();
    if (!/(sidebar|collapse)/.test(label)) return;
    if (hidden) {
      element.dataset.captureHiddenControl = '1';
      element.style.visibility = 'hidden';
      element.style.pointerEvents = 'none';
    } else if (element.dataset.captureHiddenControl === '1') {
      delete element.dataset.captureHiddenControl;
      element.style.visibility = '';
      element.style.pointerEvents = '';
    }
  });
}

function documentPaneOffset() {
  if (!root) return;
  const pane = document.getElementById('doc-editor-pane');
  if (!pane || pane.getBoundingClientRect().width < 80) {
    root.style.left = '0';
    root.style.right = '0';
    return;
  }
  const rect = pane.getBoundingClientRect();
  if (rect.left >= window.innerWidth / 2) {
    root.style.left = '0';
    root.style.right = `${Math.ceil(window.innerWidth - rect.left)}px`;
  } else {
    root.style.left = `${Math.ceil(rect.right)}px`;
    root.style.right = '0';
  }
}

function activateCapture(captureId) {
  const capture = captures.get(captureId);
  if (!capture) return;
  activeCaptureId = captureId;
  ensureRoot();
  document.body.classList.add('workspace-meeting-capture-active');
  root.removeAttribute('aria-hidden');
  setHomeOnlyControlsHidden(true);
  documentPaneOffset();
  render();
}

function deactivateCapture() {
  const capture = activeCapture();
  if (capture && ['recording', 'paused', 'stopping'].includes(capture.phase)) stopCapture(capture);
  activeCaptureId = null;
  document.body.classList.remove('workspace-meeting-capture-active');
  root?.setAttribute('aria-hidden', 'true');
  root && (root.style.left = root.style.right = '0');
  setHomeOnlyControlsHidden(false);
  queueTabSync();
}

function openWorkspaceCapture({ title = 'Audio Capture' } = {}) {
  const capture = newCapture({ title });
  captures.set(capture.captureId, capture);
  activateCapture(capture.captureId);
  return capture.captureId;
}

function browserRecognitionSupported() {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

async function loadSttConfiguration(capture) {
  if (!capture || capture.provider === 'loading') return;
  capture.provider = 'loading';
  setStatus(capture, 'Checking Speech-to-Text configuration…');
  try {
    const response = await fetch(STT_STATS_ENDPOINT, { credentials: 'same-origin' });
    if (!response.ok) throw new Error('status unavailable');
    const stats = await response.json();
    capture.provider = stats.provider || 'disabled';
    capture.language = stats.language || '';
  } catch (_) {
    capture.provider = 'disabled';
  }
  if (capture.provider === 'disabled') setStatus(capture, 'Configure browser, local, or endpoint Speech-to-Text in Settings before recording.', true);
  else if (capture.provider === 'browser' && !browserRecognitionSupported()) setStatus(capture, 'Browser recognition is unavailable. Configure local or endpoint Speech-to-Text.', true);
  else setStatus(capture, 'Ready. Press Start capture when you want to request microphone access.');
  render();
}

function appendTranscript(capture, runId, text) {
  if (!isCurrentRun(capture, runId, { allowStopping: true })) return;
  const sentence = clean(text);
  if (!sentence) return;
  capture.transcript = capture.transcript ? `${capture.transcript}\n${timestamp(capture)} ${sentence}` : `${timestamp(capture)} ${sentence}`;
  if (capture.transcript.length > MAX_TRANSCRIPT_CHARS) {
    capture.transcript = capture.transcript.slice(-MAX_TRANSCRIPT_CHARS).trim();
    setStatus(capture, 'Transcript is limited to its most recent 120,000 characters.', true);
  }
  capture.interim = '';
  if (capture.captureId === activeCaptureId) render();
}

function startTimer(capture) {
  if (capture.timer) clearInterval(capture.timer);
  capture.timer = setInterval(() => { if (capture.captureId === activeCaptureId) render(); }, 250);
}

function stopTimer(capture) {
  if (capture?.timer) clearInterval(capture.timer);
  if (capture) capture.timer = 0;
}

function startMeter(capture) {
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext || !capture.stream) return;
  try {
    capture.audioContext = new AudioContext();
    capture.analyser = capture.audioContext.createAnalyser();
    capture.analyser.fftSize = 128;
    capture.audioSource = capture.audioContext.createMediaStreamSource(capture.stream);
    capture.audioSource.connect(capture.analyser);
    const draw = () => {
      if (capture.phase !== 'recording' || !capture.analyser) return;
      const canvas = root?.querySelector('#meeting-capture-waveform');
      const context = canvas?.getContext?.('2d');
      if (canvas && context && capture.captureId === activeCaptureId) {
        const values = new Uint8Array(capture.analyser.frequencyBinCount);
        capture.analyser.getByteFrequencyData(values);
        const width = canvas.width = Math.max(1, Math.floor(canvas.clientWidth * devicePixelRatio));
        const height = canvas.height = Math.max(1, Math.floor(canvas.clientHeight * devicePixelRatio));
        context.clearRect(0, 0, width, height);
        const barWidth = Math.max(2, width / values.length - 2);
        values.forEach((value, index) => {
          const barHeight = Math.max(2, (value / 255) * height);
          context.fillStyle = index % 2 ? 'rgba(224,108,117,.9)' : 'rgba(156,222,242,.72)';
          context.fillRect(index * (barWidth + 2), (height - barHeight) / 2, barWidth, barHeight);
        });
      }
      capture.waveformFrame = requestAnimationFrame(draw);
    };
    draw();
  } catch (_) {}
}

function stopMeter(capture) {
  if (!capture) return;
  if (capture.waveformFrame) cancelAnimationFrame(capture.waveformFrame);
  capture.waveformFrame = 0;
  try { capture.audioSource?.disconnect?.(); } catch (_) {}
  try { capture.audioContext?.close?.(); } catch (_) {}
  capture.audioSource = null;
  capture.analyser = null;
  capture.audioContext = null;
}

function releaseMicrophone(capture) {
  if (!capture) return;
  stopMeter(capture);
  try { capture.stream?.getTracks?.().forEach(track => track.stop()); } catch (_) {}
  capture.stream = null;
  if (activeRecordingId === capture.captureId && capture.phase !== 'recording') activeRecordingId = null;
}

function startBrowserRecognition(capture, runId) {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) throw new Error('Browser speech recognition is unavailable.');
  const recognition = new Recognition();
  capture.recognition = recognition;
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = capture.language || '';
  recognition.onresult = event => {
    if (!isCurrentRun(capture, runId)) return;
    let interim = '';
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const result = event.results[index];
      const text = clean(result[0]?.transcript || '');
      if (!text) continue;
      if (result.isFinal) appendTranscript(capture, runId, text);
      else interim = clean(`${interim} ${text}`);
    }
    if (isCurrentRun(capture, runId)) {
      capture.interim = interim;
      render();
    }
  };
  recognition.onerror = event => {
    if (isCurrentRun(capture, runId) && event?.error !== 'aborted') setStatus(capture, `Browser transcription issue: ${event?.error || 'unknown'}.`, true);
  };
  recognition.onend = () => {
    if (isCurrentRun(capture, runId)) {
      try { recognition.start(); } catch (_) {}
    }
  };
  recognition.start();
}

function stopBrowserRecognition(capture) {
  if (!capture?.recognition) return;
  try { capture.recognition.onend = null; capture.recognition.stop(); } catch (_) {}
  capture.recognition = null;
  capture.interim = '';
}

function preferredMimeType() {
  return ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'].find(type => window.MediaRecorder?.isTypeSupported?.(type)) || '';
}

async function transcribeSegment(blob) {
  const data = new FormData();
  data.append('file', blob, 'audio-capture-segment.webm');
  const response = await fetch(STT_ENDPOINT, { method: 'POST', credentials: 'same-origin', body: data });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result?.detail?.message || result?.message || 'Transcription failed');
  return clean(result.text || '');
}

async function drainQueue(capture) {
  if (!capture || capture.draining) return;
  capture.draining = true;
  try {
    while (capture.segmentQueue.length) {
      const segment = capture.segmentQueue.shift();
      if (!segment?.blob?.size || !isCurrentRun(capture, segment.runId, { allowStopping: true })) continue;
      setStatus(capture, capture.phase === 'stopping' ? 'Transcribing final audio…' : 'Transcribing live audio…');
      try {
        const text = await transcribeSegment(segment.blob);
        appendTranscript(capture, segment.runId, text);
      } catch (error) {
        if (isCurrentRun(capture, segment.runId, { allowStopping: true })) setStatus(capture, `A segment could not be transcribed: ${error.message}`, true);
      }
    }
  } finally {
    capture.draining = false;
    if (capture.phase === 'stopping' && !capture.recorder && !capture.segmentQueue.length) finishStop(capture);
    if (capture.phase === 'paused' && !capture.recorder && !capture.segmentQueue.length) releaseMicrophone(capture);
  }
}

function startServerSegment(capture, runId) {
  if (!capture?.stream || !isCurrentRun(capture, runId)) return;
  const mimeType = preferredMimeType();
  let recorder;
  try { recorder = new MediaRecorder(capture.stream, mimeType ? { mimeType } : undefined); }
  catch (_) { recorder = new MediaRecorder(capture.stream); }
  capture.recorder = recorder;
  const chunks = [];
  recorder.ondataavailable = event => { if (event.data?.size) chunks.push(event.data); };
  recorder.onstop = () => {
    if (capture.recorder === recorder) capture.recorder = null;
    if (!isCurrentRun(capture, runId, { allowStopping: true })) return;
    const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
    if (blob.size) capture.segmentQueue.push({ blob, runId });
    drainQueue(capture);
    if (isCurrentRun(capture, runId)) setTimeout(() => startServerSegment(capture, runId), 0);
    else if (capture.phase === 'stopping' && !capture.segmentQueue.length && !capture.draining) finishStop(capture);
    else if (capture.phase === 'paused' && !capture.segmentQueue.length && !capture.draining) releaseMicrophone(capture);
  };
  recorder.start();
  capture.segmentTimer = setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); }, SEGMENT_MS);
}

function stopActiveSegment(capture) {
  if (!capture) return;
  if (capture.segmentTimer) clearTimeout(capture.segmentTimer);
  capture.segmentTimer = 0;
  if (capture.recorder?.state === 'recording') capture.recorder.stop();
}

async function startCapture(capture) {
  if (!capture || capture.phase === 'recording' || capture.phase === 'stopping') return;
  if (!capture.consent) {
    setStatus(capture, 'Confirm participant consent before starting capture.', true);
    return;
  }
  if (capture.provider === 'unchecked') await loadSttConfiguration(capture);
  if (!['browser', 'local', 'endpoint'].includes(capture.provider)) {
    setStatus(capture, 'Configure an available Speech-to-Text provider before recording.', true);
    return;
  }
  if (activeRecordingId && activeRecordingId !== capture.captureId) {
    setStatus(capture, 'Another Audio Capture tab is already recording. Stop it before starting this one.', true);
    return;
  }
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    setStatus(capture, 'Microphone capture requires HTTPS or localhost in a supported browser.', true);
    return;
  }
  const runId = capture.runId + 1;
  try {
    // This is the only microphone acquisition path in the module.
    capture.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    if (capture.closed || captures.get(capture.captureId) !== capture) {
      capture.stream.getTracks().forEach(track => track.stop());
      return;
    }
    capture.runId = runId;
    capture.activeRunId = runId;
    capture.phase = 'recording';
    capture.runningSince = Date.now();
    activeRecordingId = capture.captureId;
    startTimer(capture);
    startMeter(capture);
    if (capture.provider === 'browser') startBrowserRecognition(capture, runId);
    else startServerSegment(capture, runId);
    setStatus(capture, 'Recording. Audio is processed only by your configured Argos Speech-to-Text provider.');
  } catch (error) {
    capture.phase = 'idle';
    releaseMicrophone(capture);
    setStatus(capture, `Microphone access failed: ${error.message}`, true);
  }
  render();
}

async function resumeCapture(capture) {
  if (capture?.phase === 'paused') await startCapture(capture);
}

function pauseCapture(capture) {
  if (!capture || capture.phase !== 'recording') return;
  capture.elapsedMs += Date.now() - capture.runningSince;
  capture.runningSince = 0;
  capture.phase = 'paused';
  stopTimer(capture);
  stopBrowserRecognition(capture);
  stopActiveSegment(capture);
  if (capture.provider === 'browser') releaseMicrophone(capture);
  setStatus(capture, 'Capture paused. Resume to continue the same transcript.');
  render();
}

function finishStop(capture) {
  if (!capture || capture.phase !== 'stopping') return;
  capture.phase = 'stopped';
  capture.runningSince = 0;
  capture.interim = '';
  stopTimer(capture);
  releaseMicrophone(capture);
  setStatus(capture, capture.transcript ? 'Transcript ready. Export the transcript or generate a Meeting Brief.' : 'Capture stopped. No speech was transcribed.');
  render();
}

function stopCapture(capture) {
  if (!capture || !['recording', 'paused', 'stopping'].includes(capture.phase)) return;
  if (capture.phase === 'paused') {
    capture.phase = 'stopped';
    capture.interim = '';
    releaseMicrophone(capture);
    setStatus(capture, capture.transcript ? 'Transcript ready. Export the transcript or generate a Meeting Brief.' : 'Capture stopped.');
    render();
    return;
  }
  capture.elapsedMs += Date.now() - capture.runningSince;
  capture.runningSince = 0;
  capture.phase = 'stopping';
  stopTimer(capture);
  stopBrowserRecognition(capture);
  if (capture.provider === 'browser') {
    finishStop(capture);
    return;
  }
  stopActiveSegment(capture);
  if (!capture.recorder && !capture.segmentQueue.length && !capture.draining) finishStop(capture);
  render();
}

async function createLibraryDocument(title, content) {
  const response = await fetch(DOCUMENT_ENDPOINT, {
    method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: null, title, language: 'markdown', content }),
  });
  const document = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(document?.detail?.message || document?.detail || document?.message || 'Document export failed');
  return document;
}

function afterPaint() {
  return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
}

async function openLibraryDocument(document) {
  const docId = document?.id || document?.doc_id;
  if (!docId) return;
  try {
    await window.documentModule?.loadDocument?.(docId);
    window.documentModule?.openPanel?.();
    await afterPaint();
    // Keep Audio Capture open; make room for the document editor at the side.
    documentPaneOffset();
  } catch (_) {
    window.documentModule?.openLibrary?.();
  }
}

async function exportTranscript(capture) {
  if (!capture?.transcript.trim() || capture.phase !== 'stopped') {
    setStatus(capture, 'Stop capture and wait for final transcription before exporting.', true);
    return;
  }
  const title = clean(capture.title) || 'Audio Capture';
  setStatus(capture, 'Exporting transcript to your document library…');
  try {
    const document = await createLibraryDocument(`${title} — Transcript`, `# ${title}\n\n## Live Transcript\n\n${capture.transcript.trim()}`);
    setStatus(capture, 'Transcript exported. Opening it in the document panel.');
    await openLibraryDocument(document);
  } catch (error) {
    setStatus(capture, `Transcript export failed: ${error.message}`, true);
  }
  render();
}

async function exportBrief(capture) {
  if (!capture?.transcript.trim() || capture.phase !== 'stopped') {
    setStatus(capture, 'Stop capture and wait for final transcription before exporting.', true);
    return;
  }
  const title = clean(capture.title) || 'Audio Capture';
  setStatus(capture, 'Generating a grounded Meeting Brief…');
  try {
    const response = await fetch(BRIEF_ENDPOINT, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, transcript: capture.transcript.trim(), focus: '' }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result?.detail?.message || result?.detail || result?.message || 'Meeting Brief generation failed');
    const brief = String(result.brief || '').trim();
    if (!brief) throw new Error('The Utility model returned an empty Meeting Brief');
    const document = await createLibraryDocument(`${title} — Meeting Brief`, brief);
    setStatus(capture, 'Meeting Brief exported. Opening it in the document panel.');
    await openLibraryDocument(document);
  } catch (error) {
    setStatus(capture, `Meeting Brief export failed: ${error.message}`, true);
  }
  render();
}

async function closeCapture(captureId) {
  const capture = captures.get(captureId);
  if (!capture) return;
  const hasUnexportedTranscript = !!capture.transcript.trim();
  if (capture.phase === 'recording' || capture.phase === 'stopping' || hasUnexportedTranscript) {
    const message = capture.phase === 'recording' || capture.phase === 'stopping'
      ? 'Stop this live capture and close the tab? Unexported transcript text will be discarded.'
      : 'Close this Audio Capture tab? Its unexported transcript text will be discarded.';
    const accepted = window.uiModule?.styledConfirm
      ? await window.uiModule.styledConfirm(message, { confirmText: 'Close capture' })
      : window.confirm(message);
    if (!accepted) return;
  }
  capture.closed = true;
  capture.activeRunId += 1;
  stopBrowserRecognition(capture);
  stopActiveSegment(capture);
  stopTimer(capture);
  releaseMicrophone(capture);
  captures.delete(captureId);
  if (activeCaptureId === captureId) deactivateCapture();
  queueTabSync();
  if (!captures.size) document.querySelector('.workspace-tab[data-tab-id="home"]')?.click();
}

function augmentNewTabWizard() {
  const modal = document.querySelector('.QuestCreationWizard');
  const backdrop = document.querySelector('.venture-modal-backdrop[data-venture-session-wizard]');
  if (!modal || !backdrop || modal.dataset.audioCaptureAugmented === '1') return;
  const grid = modal.querySelector('.venture-choice-grid');
  const chatFields = modal.querySelector('[data-chat-fields]');
  const questFields = modal.querySelector('[data-quest-fields]');
  const submit = modal.querySelector('[data-submit]');
  if (!grid || !chatFields || !questFields || !submit) return;

  const choice = document.createElement('label');
  choice.className = 'venture-choice';
  choice.innerHTML = '<input type="radio" name="session_type" value="audio_capture"><strong>Audio capture</strong><span class="venture-muted">Open a native live-transcript workspace tab with recorder controls.</span>';
  grid.appendChild(choice);
  const audioFields = document.createElement('div');
  audioFields.dataset.audioCaptureFields = 'true';
  audioFields.hidden = true;
  audioFields.innerHTML = '<label>Meeting title</label><input name="audio_capture_title" autocomplete="off" placeholder="Weekly project review"><div class="venture-muted" style="margin-top:8px">No microphone permission is requested until you press Start capture in the new tab.</div>';
  questFields.after(audioFields);

  const sync = () => {
    const isAudio = modal.querySelector('[name="session_type"]:checked')?.value === 'audio_capture';
    audioFields.hidden = !isAudio;
    if (isAudio) {
      chatFields.hidden = true;
      questFields.hidden = true;
      submit.textContent = 'Open Audio Capture';
    }
  };
  modal.querySelectorAll('[name="session_type"]').forEach(input => input.addEventListener('change', sync));
  modal.addEventListener('submit', event => {
    if (modal.querySelector('[name="session_type"]:checked')?.value !== 'audio_capture') return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const title = clean(audioFields.querySelector('[name="audio_capture_title"]')?.value) || 'Audio Capture';
    modal.remove();
    backdrop.remove();
    openWorkspaceCapture({ title });
  }, true);
  modal.dataset.audioCaptureAugmented = '1';
}

function wrapVentureWizard() {
  const original = window.argosVentureOpenSessionWizard;
  if (typeof original !== 'function' || original.__audioCaptureWrapped) return false;
  const wrapped = function wrappedVentureSessionWizard(...args) {
    const result = original.apply(this, args);
    queueMicrotask(augmentNewTabWizard);
    return result;
  };
  wrapped.__audioCaptureWrapped = true;
  window.argosVentureOpenSessionWizard = wrapped;
  return true;
}

function init() {
  installStyles();
  const installWizard = () => {
    if (!wrapVentureWizard()) setTimeout(installWizard, 250);
  };
  installWizard();
  document.addEventListener('odysseus:workspace-tab-activated', event => {
    if (event.detail?.kind !== 'meeting') deactivateCapture();
    queueTabSync();
  });
  document.addEventListener('odysseus:session-selected', queueTabSync);
  document.addEventListener('odysseus:session-materialized', queueTabSync);
  document.addEventListener('argos-venture:quest-registry-updated', queueTabSync);
  window.addEventListener('resize', documentPaneOffset, { passive: true });
  window.addEventListener('pagehide', () => {
    captures.forEach(capture => {
      capture.closed = true;
      capture.activeRunId += 1;
      stopBrowserRecognition(capture);
      stopActiveSegment(capture);
      stopTimer(capture);
      releaseMicrophone(capture);
    });
  });
}

const meetingCaptureModule = { openWorkspaceCapture, activateCapture, deactivateCapture, closeCapture };
window.meetingCaptureModule = meetingCaptureModule;

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();

export default meetingCaptureModule;
