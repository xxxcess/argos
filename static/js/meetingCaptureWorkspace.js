// Argos Venture Live Capture workspace tab.
//
// A Live Capture is a normal persisted Argos session with mode=live_capture.
// Each finalized timestamped transcript segment is stored as a ChatMessage.
// The browser microphone prompt is the only permission gate and runs only after
// the user presses Start recording.

const STT_STATS_ENDPOINT = '/api/stt/stats';
const STT_ENDPOINT = '/api/stt/transcribe';
const BRIEF_ENDPOINT = '/api/meeting-briefs/generate';
const DOCUMENT_ENDPOINT = '/api/document';
const LIVE_CAPTURE_ENDPOINT = '/api/meeting-briefs/live-captures';
const SEGMENT_MS = 15_000;
const MAX_TRANSCRIPT_CHARS = 120_000;

const captures = new Map();
let activeCaptureId = null;
let activeRecordingId = null;
let root = null;
let rootWired = false;
let tabSyncQueued = false;

function makeId() {
  return window.crypto?.randomUUID?.() || `live-capture-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function escapeSelector(value) {
  if (window.CSS?.escape) return window.CSS.escape(String(value));
  return String(value).replace(/[^a-zA-Z0-9_-]/g, char => `\\${char}`);
}

function clean(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function titleForSession(title) {
  return `Live Capture — ${clean(title) || 'Live Capture'}`;
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

function timestamp(offsetMs) {
  return `[${formatElapsed(offsetMs)}]`;
}

function microphoneIcon() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5M8 22h8"/></svg>';
}

function recordIcon() {
  return '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="6" fill="currentColor" stroke="none"/></svg>';
}

function newCapture({ captureId = makeId(), title = 'Live Capture', sessionId = null } = {}) {
  return {
    captureId,
    sessionId,
    title: clean(title) || 'Live Capture',
    segments: [],
    phase: 'idle', // idle | recording | paused | stopping | stopped
    provider: 'unchecked',
    language: '',
    elapsedMs: 0,
    runningSince: 0,
    stream: null,
    recorder: null,
    recognition: null,
    segmentTimer: 0,
    segmentQueue: [],
    draining: false,
    starting: false,
    timer: 0,
    audioContext: null,
    analyser: null,
    audioSource: null,
    waveformFrame: 0,
    runId: 0,
    activeRunId: 0,
    closed: false,
    sessionCreating: false,
    sessionCreateFailed: false,
    titleSaveTimer: 0,
    status: 'Ready. Start recording when you want the browser to request microphone access.',
    statusError: false,
  };
}

function activeCapture() {
  return activeCaptureId ? captures.get(activeCaptureId) || null : null;
}

function transcript(capture) {
  return capture.segments.map(segment => segment.content).join('\n');
}

function setStatus(capture, message, isError = false) {
  if (!capture) return;
  capture.status = message || '';
  capture.statusError = !!isError;
  if (capture.captureId === activeCaptureId) render();
}

function phaseLabel(phase) {
  return ({
    idle: 'Ready to record',
    recording: 'Recording',
    paused: 'Recording paused',
    stopping: 'Finishing transcription',
    stopped: 'Transcript ready',
  })[phase] || 'Ready to record';
}

function isCurrentRun(capture, runId, { allowPaused = false, allowStopping = false } = {}) {
  if (!capture || capture.closed || captures.get(capture.captureId) !== capture) return false;
  if (capture.activeRunId !== runId) return false;
  return capture.phase === 'recording'
    || (allowPaused && capture.phase === 'paused')
    || (allowStopping && capture.phase === 'stopping');
}

function installStyles() {
  if (document.getElementById('live-capture-workspace-style')) return;
  const style = document.createElement('style');
  style.id = 'live-capture-workspace-style';
  style.textContent = `
    #live-capture-workspace { position:fixed; inset:var(--workspace-shell-h,0px) 0 0 0; z-index:35; display:none; grid-template-rows:auto minmax(0,1fr) auto; background:var(--bg,#17191d); color:var(--fg,#e8eaed); transition:right .15s ease,left .15s ease; }
    body.workspace-live-capture-active #live-capture-workspace { display:grid; }
    body.workspace-live-capture-active #chat-container { visibility:hidden; pointer-events:none; }
    body.workspace-live-capture-active #sidebar, body.workspace-live-capture-active #icon-rail, body.workspace-live-capture-active #sidebar-toggle, body.workspace-live-capture-active #sidebar-collapse, body.workspace-live-capture-active [data-sidebar-toggle], body.workspace-live-capture-active [data-sidebar-collapse] { visibility:hidden!important; pointer-events:none!important; }
    .live-capture-head { display:flex; align-items:center; gap:14px; padding:14px 24px; border-bottom:1px solid var(--border,#3a3f47); background:var(--panel,#21252b); }
    .live-capture-title { flex:1; min-width:160px; max-width:720px; padding:8px 10px; border:1px solid var(--border,#3a3f47); border-radius:7px; background:var(--bg,#17191d); color:var(--fg,#e8eaed); font:inherit; }
    .live-capture-state { display:inline-flex; align-items:center; gap:7px; font-size:12px; white-space:nowrap; opacity:.82; }
    .live-capture-state::before { content:''; width:9px; height:9px; border-radius:999px; background:var(--border,#646a73); }
    .live-capture-state[data-phase="recording"]::before { background:var(--red,#e06c75); box-shadow:0 0 0 5px color-mix(in srgb,var(--red,#e06c75) 20%,transparent); }
    .live-capture-state[data-phase="paused"]::before { background:#e5c07b; }
    .live-capture-main { min-height:0; padding:20px 24px; overflow:hidden; }
    .live-capture-transcript-card { height:100%; min-height:0; display:flex; flex-direction:column; overflow:hidden; border:1px solid var(--border,#3a3f47); border-radius:12px; background:var(--panel,#21252b); }
    .live-capture-transcript-head { display:flex; justify-content:space-between; align-items:center; padding:13px 16px; border-bottom:1px solid var(--border,#3a3f47); font-size:12px; }
    .live-capture-transcript-head span { opacity:.6; }
    .live-capture-log { flex:1; overflow:auto; padding:18px 20px; font:14px/1.7 ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; white-space:pre-wrap; }
    .live-capture-log pre { margin:0; font:inherit; white-space:pre-wrap; }
    .live-capture-empty { margin:0; color:var(--muted,#a4a8ae); font-family:var(--font-family,system-ui,sans-serif); opacity:.72; }
    .live-capture-interim { display:block; margin-top:10px; opacity:.52; font-style:italic; }
    .live-capture-footer { display:grid; gap:10px; padding:12px 24px 14px; border-top:1px solid var(--border,#3a3f47); background:var(--panel,#21252b); }
    .live-capture-readout { display:flex; align-items:center; gap:14px; min-width:0; }
    .live-capture-time { flex:0 0 auto; font:600 24px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.04em; white-space:nowrap; }
    .live-capture-waveform { height:28px; width:100%; min-width:0; border-radius:6px; background:color-mix(in srgb,var(--bg,#17191d) 65%,transparent); }
    .live-capture-control-row { display:flex; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:10px; }
    .live-capture-recorder-controls, .live-capture-actions { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
    .live-capture-recorder-controls button, .live-capture-actions button { border:1px solid var(--border,#3a3f47); border-radius:8px; padding:9px 12px; background:var(--bg,#17191d); color:var(--fg,#e8eaed); font:inherit; font-size:12px; cursor:pointer; }
    .live-capture-recorder-controls button:disabled, .live-capture-actions button:disabled { cursor:not-allowed; opacity:.45; }
    .live-capture-record-btn { min-width:118px; color:white!important; border-color:var(--red,#e06c75)!important; background:var(--red,#e06c75)!important; }
    .live-capture-stop-btn { color:white!important; border-color:#4b5058!important; background:#4b5058!important; }
    .live-capture-recorder-controls svg { width:13px; height:13px; fill:none; stroke:currentColor; stroke-width:2; vertical-align:-2px; margin-right:4px; }
    .live-capture-record-btn svg { fill:currentColor; stroke:none; }
    .live-capture-actions .primary { border-color:var(--accent,var(--red,#e06c75)); background:color-mix(in srgb,var(--accent,var(--red,#e06c75)) 18%,var(--bg)); }
    .live-capture-status { min-height:1.25em; font-size:11px; opacity:.75; }
    .live-capture-status[data-error="true"] { color:var(--red,#e06c75); opacity:1; }
    .workspace-tab.live-capture-tab .workspace-tab-icon svg { width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:1.8; }
    .workspace-tab.live-capture-tab[data-state="recording"] .workspace-tab-state { display:block; background:var(--red,#e06c75); }
    @media (max-width:820px) {
      #live-capture-workspace { inset:var(--workspace-shell-h,0px) 0 0 0!important; }
      .live-capture-head { padding:12px 14px; }
      .live-capture-main { padding:12px; }
      .live-capture-footer { padding:12px; }
      .live-capture-control-row { align-items:stretch; }
      .live-capture-recorder-controls, .live-capture-actions { width:100%; }
    }
  `;
  document.head.appendChild(style);
}

function renderTranscript(capture, log) {
  log.replaceChildren();
  const saved = transcript(capture);
  if (!saved && !capture.interim) {
    const empty = document.createElement('p');
    empty.className = 'live-capture-empty';
    empty.textContent = 'Start recording to create a persisted, timestamped transcript.';
    log.appendChild(empty);
    return;
  }
  if (saved) {
    const final = document.createElement('pre');
    final.textContent = saved;
    log.appendChild(final);
  }
  if (capture.interim && capture.phase === 'recording') {
    const interim = document.createElement('span');
    interim.className = 'live-capture-interim';
    interim.textContent = `${saved ? '\n' : ''}${timestamp(elapsedMs(capture))} ${capture.interim}`;
    log.appendChild(interim);
  }
  log.scrollTop = log.scrollHeight;
}

function ensureRoot() {
  installStyles();
  if (root) return root;
  root = document.createElement('main');
  root.id = 'live-capture-workspace';
  root.setAttribute('aria-label', 'Live Capture workspace');
  root.innerHTML = `
    <header class="live-capture-head">
      <input id="live-capture-title" class="live-capture-title" type="text" maxlength="180" placeholder="Live Capture title" aria-label="Live Capture title" />
      <div id="live-capture-state" class="live-capture-state" data-phase="idle">Ready to record</div>
    </header>
    <section class="live-capture-main">
      <article class="live-capture-transcript-card">
        <div class="live-capture-transcript-head"><strong>Transcript</strong><span id="live-capture-character-count">0 characters</span></div>
        <div id="live-capture-log" class="live-capture-log" aria-live="polite"></div>
      </article>
    </section>
    <footer class="live-capture-footer">
      <div class="live-capture-readout"><span id="live-capture-time" class="live-capture-time">00:00</span><canvas id="live-capture-waveform" class="live-capture-waveform" aria-label="Microphone activity"></canvas></div>
      <div class="live-capture-control-row">
        <div class="live-capture-recorder-controls">
          <button id="live-capture-record" class="live-capture-record-btn" type="button">${recordIcon()}<span>Start recording</span></button>
          <button id="live-capture-pause" type="button" disabled>Pause</button>
          <button id="live-capture-stop" class="live-capture-stop-btn" type="button" disabled>Stop</button>
        </div>
        <div class="live-capture-actions">
          <button id="live-capture-export-transcript" type="button">Export transcript</button>
          <button id="live-capture-export-brief" class="primary" type="button">Generate & export brief</button>
        </div>
      </div>
      <div id="live-capture-status" class="live-capture-status" aria-live="polite"></div>
    </footer>
  `;
  document.body.appendChild(root);
  wireRoot();
  return root;
}

function wireRoot() {
  if (rootWired || !root) return;
  rootWired = true;
  const title = root.querySelector('#live-capture-title');
  root.querySelector('#live-capture-record')?.addEventListener('click', async () => {
    const capture = activeCapture();
    if (capture?.phase === 'paused') await resumeCapture(capture);
    else await startCapture(capture);
  });
  root.querySelector('#live-capture-pause')?.addEventListener('click', () => pauseCapture(activeCapture()));
  root.querySelector('#live-capture-stop')?.addEventListener('click', () => stopCapture(activeCapture()));
  root.querySelector('#live-capture-export-transcript')?.addEventListener('click', () => exportTranscript(activeCapture()));
  root.querySelector('#live-capture-export-brief')?.addEventListener('click', () => exportBrief(activeCapture()));
  title?.addEventListener('input', () => {
    const capture = activeCapture();
    if (!capture) return;
    capture.title = clean(title.value) || 'Live Capture';
    scheduleTitlePersist(capture);
    queueTabSync();
  });
}

function render() {
  const capture = activeCapture();
  if (!capture || !root) return;
  const title = root.querySelector('#live-capture-title');
  const state = root.querySelector('#live-capture-state');
  const record = root.querySelector('#live-capture-record');
  const pause = root.querySelector('#live-capture-pause');
  const stop = root.querySelector('#live-capture-stop');
  const exportTranscriptButton = root.querySelector('#live-capture-export-transcript');
  const exportBriefButton = root.querySelector('#live-capture-export-brief');
  if (document.activeElement !== title) title.value = capture.title;
  state.dataset.phase = capture.phase;
  state.textContent = phaseLabel(capture.phase);
  renderTranscript(capture, root.querySelector('#live-capture-log'));
  const transcriptText = transcript(capture);
  root.querySelector('#live-capture-character-count').textContent = `${transcriptText.length.toLocaleString()} characters`;
  root.querySelector('#live-capture-time').textContent = formatElapsed(elapsedMs(capture));
  const status = root.querySelector('#live-capture-status');
  status.textContent = capture.status;
  status.dataset.error = capture.statusError ? 'true' : 'false';
  const anotherCaptureIsRecording = !!activeRecordingId && activeRecordingId !== capture.captureId;
  record.disabled = capture.starting || capture.phase === 'recording' || capture.phase === 'stopping' || anotherCaptureIsRecording;
  record.querySelector('span').textContent = capture.phase === 'paused' ? 'Resume recording' : 'Start recording';
  pause.disabled = capture.phase !== 'recording';
  stop.disabled = !['recording', 'paused', 'stopping'].includes(capture.phase);
  const canExport = capture.phase === 'stopped' && !!transcriptText.trim();
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
  list.querySelectorAll('.live-capture-tab').forEach(tab => {
    if (!captures.has(tab.dataset.captureId)) tab.remove();
  });
  captures.forEach(capture => {
    let tab = list.querySelector(`.live-capture-tab[data-capture-id="${escapeSelector(capture.captureId)}"]`);
    if (!tab) {
      tab = document.createElement('div');
      tab.className = 'workspace-tab live-capture-tab';
      tab.dataset.captureId = capture.captureId;
      tab.dataset.tabId = `live-capture:${capture.captureId}`;
      tab.setAttribute('role', 'tab');
      tab.innerHTML = `<span class="workspace-tab-icon">${microphoneIcon()}</span><span class="workspace-tab-title"></span><span class="workspace-tab-state" aria-hidden="true"></span><span class="workspace-tab-close-wrap"><button type="button" class="workspace-tab-close" aria-label="Close Live Capture tab">&times;</button></span>`;
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
  if (activeCaptureId) list.querySelectorAll('.workspace-tab:not(.live-capture-tab)').forEach(tab => tab.setAttribute('aria-selected', 'false'));
}

function setHomeOnlyControlsHidden(hidden) {
  document.querySelectorAll('button, [role="button"]').forEach(element => {
    if (element.closest('#live-capture-workspace, #workspace-shell, #doc-editor-pane')) return;
    const label = `${element.id || ''} ${element.className || ''} ${element.title || ''} ${element.getAttribute('aria-label') || ''}`.toLowerCase();
    if (!/(sidebar|collapse|hamburger)/.test(label)) return;
    if (hidden) {
      element.dataset.liveCaptureHiddenControl = '1';
      element.style.visibility = 'hidden';
      element.style.pointerEvents = 'none';
    } else if (element.dataset.liveCaptureHiddenControl === '1') {
      delete element.dataset.liveCaptureHiddenControl;
      element.style.visibility = '';
      element.style.pointerEvents = '';
    }
  });
}

function documentPaneOffset() {
  if (!root || !document.body.classList.contains('workspace-live-capture-active')) return;
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
  document.body.classList.add('workspace-live-capture-active');
  root.removeAttribute('aria-hidden');
  setHomeOnlyControlsHidden(true);
  documentPaneOffset();
  render();
}

function deactivateCapture() {
  const capture = activeCapture();
  if (capture && ['recording', 'paused', 'stopping'].includes(capture.phase)) stopCapture(capture);
  activeCaptureId = null;
  document.body.classList.remove('workspace-live-capture-active');
  root?.setAttribute('aria-hidden', 'true');
  if (root) root.style.left = root.style.right = '0';
  setHomeOnlyControlsHidden(false);
  queueTabSync();
}

async function createPersistedSession(capture) {
  if (!capture || capture.sessionId || capture.sessionCreating || capture.closed) return capture?.sessionId || null;
  capture.sessionCreating = true;
  capture.sessionCreateFailed = false;
  setStatus(capture, 'Creating persistent Live Capture session…');
  try {
    const response = await fetch(LIVE_CAPTURE_ENDPOINT, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: capture.title }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result?.detail?.message || result?.detail || result?.message || 'Could not create Live Capture session');
    capture.sessionId = result.session_id;
    setStatus(capture, 'Live Capture session ready. Start recording when ready.');
    await flushPendingSegments(capture);
    try { await window.sessionModule?.loadSessions?.(); } catch (_) {}
  } catch (error) {
    capture.sessionCreateFailed = true;
    setStatus(capture, `Transcript persistence is unavailable: ${error.message}`, true);
  } finally {
    capture.sessionCreating = false;
    if (capture.captureId === activeCaptureId) render();
  }
  return capture.sessionId;
}

function openWorkspaceCapture({ title = 'Live Capture' } = {}) {
  const capture = newCapture({ title });
  captures.set(capture.captureId, capture);
  activateCapture(capture.captureId);
  void createPersistedSession(capture);
  return capture.captureId;
}

function browserRecognitionSupported() {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

async function loadSttConfiguration(capture) {
  if (!capture || capture.provider === 'loading') return capture?.provider;
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
  else setStatus(capture, 'Ready. Start recording to request microphone access.');
  render();
  return capture.provider;
}

function addSegment(capture, runId, offsetMs, text) {
  if (!isCurrentRun(capture, runId, { allowPaused: true, allowStopping: true })) return;
  const normalized = clean(text);
  if (!normalized) return;
  const safeOffset = Math.max(0, Math.floor(offsetMs));
  const segment = {
    id: makeId(),
    offsetMs: safeOffset,
    text: normalized,
    content: `${timestamp(safeOffset)} ${normalized}`,
    persisted: false,
    persisting: false,
  };
  capture.segments.push(segment);
  while (transcript(capture).length > MAX_TRANSCRIPT_CHARS && capture.segments.length > 1) capture.segments.shift();
  void persistSegment(capture, segment);
  if (capture.captureId === activeCaptureId) render();
}

async function persistSegment(capture, segment) {
  if (!capture || !segment || segment.persisted || segment.persisting || capture.closed) return;
  if (!capture.sessionId) {
    if (!capture.sessionCreating) void createPersistedSession(capture);
    return;
  }
  segment.persisting = true;
  try {
    const response = await fetch(`${LIVE_CAPTURE_ENDPOINT}/${encodeURIComponent(capture.sessionId)}/segments`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: segment.text, offset_ms: segment.offsetMs, segment_id: segment.id }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result?.detail?.message || result?.detail || result?.message || 'Could not persist transcript segment');
    segment.content = result.content || segment.content;
    segment.persisted = true;
  } catch (error) {
    segment.persistError = error.message || 'Could not persist transcript segment';
    setStatus(capture, `Transcript segment is visible but not yet saved: ${segment.persistError}`, true);
  } finally {
    segment.persisting = false;
  }
}

async function flushPendingSegments(capture) {
  if (!capture?.sessionId) return;
  for (const segment of capture.segments) {
    if (!segment.persisted && !segment.persisting) await persistSegment(capture, segment);
  }
}

function scheduleTitlePersist(capture) {
  if (!capture?.sessionId) return;
  if (capture.titleSaveTimer) clearTimeout(capture.titleSaveTimer);
  capture.titleSaveTimer = setTimeout(async () => {
    try {
      const form = new FormData();
      form.append('name', titleForSession(capture.title));
      await fetch(`/api/session/${encodeURIComponent(capture.sessionId)}`, {
        method: 'PATCH', credentials: 'same-origin', body: form,
      });
    } catch (_) {}
  }, 500);
}

function startTimer(capture) {
  if (capture.timer) clearInterval(capture.timer);
  capture.timer = setInterval(() => {
    if (capture.captureId === activeCaptureId) render();
  }, 250);
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
      const canvas = root?.querySelector('#live-capture-waveform');
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
    if (!isCurrentRun(capture, runId, { allowStopping: true })) return;
    let interim = '';
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const result = event.results[index];
      const text = clean(result[0]?.transcript || '');
      if (!text) continue;
      if (result.isFinal) addSegment(capture, runId, elapsedMs(capture), text);
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
  data.append('file', blob, 'live-capture-segment.webm');
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
      if (!segment?.blob?.size || !isCurrentRun(capture, segment.runId, { allowPaused: true, allowStopping: true })) continue;
      setStatus(capture, capture.phase === 'stopping' ? 'Transcribing final audio…' : 'Transcribing live audio…');
      try {
        const text = await transcribeSegment(segment.blob);
        addSegment(capture, segment.runId, segment.startOffsetMs, text);
      } catch (error) {
        if (isCurrentRun(capture, segment.runId, { allowPaused: true, allowStopping: true })) setStatus(capture, `A segment could not be transcribed: ${error.message}`, true);
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
  const startOffsetMs = elapsedMs(capture);
  const mimeType = preferredMimeType();
  let recorder;
  try { recorder = new MediaRecorder(capture.stream, mimeType ? { mimeType } : undefined); }
  catch (_) { recorder = new MediaRecorder(capture.stream); }
  capture.recorder = recorder;
  const chunks = [];
  recorder.ondataavailable = event => { if (event.data?.size) chunks.push(event.data); };
  recorder.onstop = () => {
    if (capture.recorder === recorder) capture.recorder = null;
    if (!isCurrentRun(capture, runId, { allowPaused: true, allowStopping: true })) return;
    const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
    if (blob.size) capture.segmentQueue.push({ blob, runId, startOffsetMs });
    void drainQueue(capture);
    if (isCurrentRun(capture, runId)) setTimeout(() => startServerSegment(capture, runId), 0);
    else if (capture.phase === 'stopping' && !capture.segmentQueue.length && !capture.draining) finishStop(capture);
    else if (capture.phase === 'paused' && !capture.segmentQueue.length && !capture.draining) releaseMicrophone(capture);
  };
  recorder.start();
  capture.segmentTimer = setTimeout(() => {
    if (recorder.state === 'recording') recorder.stop();
  }, SEGMENT_MS);
}

function stopActiveSegment(capture) {
  if (!capture) return;
  if (capture.segmentTimer) clearTimeout(capture.segmentTimer);
  capture.segmentTimer = 0;
  if (capture.recorder?.state === 'recording') capture.recorder.stop();
}

async function startCapture(capture) {
  if (!capture || capture.starting || capture.phase === 'recording' || capture.phase === 'stopping') return;
  if (activeRecordingId && activeRecordingId !== capture.captureId) {
    setStatus(capture, 'Another Live Capture tab is already recording. Stop it before starting this one.', true);
    return;
  }
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    setStatus(capture, 'Recording requires HTTPS or localhost in a supported browser.', true);
    return;
  }
  capture.starting = true;
  render();
  try {
    if (capture.provider === 'unchecked') await loadSttConfiguration(capture);
    if (!['browser', 'local', 'endpoint'].includes(capture.provider)) {
      setStatus(capture, 'Configure an available Speech-to-Text provider before recording.', true);
      return;
    }
    const runId = capture.phase === 'paused' && capture.activeRunId ? capture.activeRunId : capture.runId + 1;
    // This is the only microphone acquisition path. Browsers show their own
    // permission prompt in direct response to Start recording.
    capture.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    if (capture.closed || captures.get(capture.captureId) !== capture) {
      capture.stream.getTracks().forEach(track => track.stop());
      return;
    }
    capture.runId = Math.max(capture.runId, runId);
    capture.activeRunId = runId;
    capture.phase = 'recording';
    capture.runningSince = Date.now();
    activeRecordingId = capture.captureId;
    startTimer(capture);
    startMeter(capture);
    if (capture.provider === 'browser') startBrowserRecognition(capture, runId);
    else startServerSegment(capture, runId);
    setStatus(capture, capture.sessionId ? 'Recording. Final segments are saved to this Live Capture session.' : 'Recording. Creating its persistent Live Capture session…');
  } catch (error) {
    capture.phase = capture.segments.length ? 'stopped' : 'idle';
    releaseMicrophone(capture);
    setStatus(capture, `Microphone access failed: ${error.message}`, true);
  } finally {
    capture.starting = false;
    render();
  }
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
  setStatus(capture, 'Recording paused. Resume to continue the same Live Capture session.');
  render();
}

function finishStop(capture) {
  if (!capture || capture.phase !== 'stopping') return;
  capture.phase = 'stopped';
  capture.runningSince = 0;
  capture.interim = '';
  stopTimer(capture);
  releaseMicrophone(capture);
  void flushPendingSegments(capture);
  setStatus(capture, transcript(capture) ? 'Transcript ready. Export the transcript or generate a Meeting Brief.' : 'Recording stopped. No speech was transcribed.');
  render();
}

function stopCapture(capture) {
  if (!capture || !['recording', 'paused', 'stopping'].includes(capture.phase)) return;
  if (capture.phase === 'paused') {
    capture.phase = 'stopped';
    capture.interim = '';
    releaseMicrophone(capture);
    void flushPendingSegments(capture);
    setStatus(capture, transcript(capture) ? 'Transcript ready. Export the transcript or generate a Meeting Brief.' : 'Recording stopped.');
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
    // Keep Live Capture open and put the Library document beside it.
    documentPaneOffset();
  } catch (_) {
    window.documentModule?.openLibrary?.();
  }
}

async function ensureSegmentsPersisted(capture) {
  await createPersistedSession(capture);
  await flushPendingSegments(capture);
  const unsaved = capture.segments.filter(segment => !segment.persisted).length;
  if (unsaved) throw new Error(`${unsaved} transcript segment${unsaved === 1 ? '' : 's'} could not be saved yet`);
}

async function exportTranscript(capture) {
  if (!capture || capture.phase !== 'stopped' || !transcript(capture).trim()) {
    setStatus(capture, 'Stop recording and wait for final transcription before exporting.', true);
    return;
  }
  const title = clean(capture.title) || 'Live Capture';
  setStatus(capture, 'Saving transcript and exporting it to your document library…');
  try {
    await ensureSegmentsPersisted(capture);
    const document = await createLibraryDocument(`${title} — Transcript`, `# ${title}\n\n## Live Transcript\n\n${transcript(capture).trim()}`);
    setStatus(capture, 'Transcript exported. Opening it in the document panel.');
    await openLibraryDocument(document);
  } catch (error) {
    setStatus(capture, `Transcript export failed: ${error.message}`, true);
  }
  render();
}

async function exportBrief(capture) {
  if (!capture || capture.phase !== 'stopped' || !transcript(capture).trim()) {
    setStatus(capture, 'Stop recording and wait for final transcription before exporting.', true);
    return;
  }
  const title = clean(capture.title) || 'Live Capture';
  setStatus(capture, 'Saving transcript and generating a grounded Meeting Brief…');
  try {
    await ensureSegmentsPersisted(capture);
    const response = await fetch(BRIEF_ENDPOINT, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, transcript: transcript(capture), focus: '' }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const providerMessage = result?.detail?.message || result?.detail || result?.message || 'Meeting Brief generation failed';
      if (response.status === 429) throw new Error(`The Utility model is rate-limited. Argos retried once; retry after the provider limit resets. (${providerMessage})`);
      throw new Error(providerMessage);
    }
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

async function restorePersistedCapture(meta) {
  const sessionId = String(meta?.id || meta?.sessionId || '');
  if (!sessionId) return;
  const existing = [...captures.values()].find(capture => capture.sessionId === sessionId);
  if (existing) {
    activateCapture(existing.captureId);
    return;
  }
  const capture = newCapture({
    title: clean(meta?.name || '').replace(/^Live Capture\s+—\s+/i, '') || 'Live Capture',
    sessionId,
  });
  capture.phase = 'stopped';
  capture.status = 'Loading persisted Live Capture transcript…';
  captures.set(capture.captureId, capture);
  activateCapture(capture.captureId);
  try {
    const response = await fetch(`${LIVE_CAPTURE_ENDPOINT}/${encodeURIComponent(sessionId)}`, { credentials: 'same-origin' });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result?.detail?.message || result?.detail || result?.message || 'Could not load Live Capture transcript');
    capture.title = result.title || capture.title;
    capture.segments = (result.segments || []).map(segment => ({
      id: segment.id || makeId(),
      offsetMs: Number(segment.offset_ms) || 0,
      text: String(segment.content || '').replace(/^\[[^\]]+\]\s*/, ''),
      content: String(segment.content || ''),
      persisted: true,
      persisting: false,
    }));
    capture.elapsedMs = capture.segments.reduce((max, segment) => Math.max(max, segment.offsetMs), 0);
    capture.status = capture.segments.length ? 'Persisted transcript ready. Resume recording or export a document.' : 'Live Capture is ready to record.';
  } catch (error) {
    capture.status = `Could not load persisted Live Capture: ${error.message}`;
    capture.statusError = true;
  }
  render();
}

async function closeCapture(captureId) {
  const capture = captures.get(captureId);
  if (!capture) return;
  const active = ['recording', 'paused', 'stopping'].includes(capture.phase);
  const unsaved = capture.segments.some(segment => !segment.persisted);
  if (active || unsaved) {
    const message = active
      ? 'Stop this recording and close the Live Capture tab? Unsaved transcript text will be discarded.'
      : 'Close this Live Capture tab? Unsaved transcript text will be discarded.';
    const accepted = window.uiModule?.styledConfirm
      ? await window.uiModule.styledConfirm(message, { confirmText: 'Close Live Capture' })
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
  if (!modal || !backdrop || modal.dataset.liveCaptureAugmented === '1') return;
  const grid = modal.querySelector('.venture-choice-grid');
  const chatFields = modal.querySelector('[data-chat-fields]');
  const questFields = modal.querySelector('[data-quest-fields]');
  const submit = modal.querySelector('[data-submit]');
  if (!grid || !chatFields || !questFields || !submit) return;

  const choice = document.createElement('label');
  choice.className = 'venture-choice';
  choice.innerHTML = '<input type="radio" name="session_type" value="live_capture"><strong>Live Capture</strong><span class="venture-muted">Open a persisted, timestamped recorder workspace tab.</span>';
  grid.appendChild(choice);
  const captureFields = document.createElement('div');
  captureFields.dataset.liveCaptureFields = 'true';
  captureFields.hidden = true;
  captureFields.innerHTML = '<label>Capture title</label><input name="live_capture_title" autocomplete="off" placeholder="Weekly project review"><div class="venture-muted" style="margin-top:8px">The browser asks for microphone permission only when you press Start recording.</div>';
  questFields.after(captureFields);

  const sync = () => {
    const selected = modal.querySelector('[name="session_type"]:checked')?.value;
    const isLiveCapture = selected === 'live_capture';
    captureFields.hidden = !isLiveCapture;
    if (isLiveCapture) {
      chatFields.hidden = true;
      questFields.hidden = true;
      submit.textContent = 'Open Live Capture';
    }
  };
  modal.querySelectorAll('[name="session_type"]').forEach(input => input.addEventListener('change', sync));
  modal.addEventListener('submit', event => {
    if (modal.querySelector('[name="session_type"]:checked')?.value !== 'live_capture') return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const title = clean(captureFields.querySelector('[name="live_capture_title"]')?.value) || 'Live Capture';
    modal.remove();
    backdrop.remove();
    openWorkspaceCapture({ title });
  }, true);
  modal.dataset.liveCaptureAugmented = '1';
}

function wrapVentureWizard() {
  const original = window.argosVentureOpenSessionWizard;
  if (typeof original !== 'function' || original.__liveCaptureWrapped) return false;
  const wrapped = function wrappedVentureSessionWizard(...args) {
    const result = original.apply(this, args);
    queueMicrotask(augmentNewTabWizard);
    return result;
  };
  wrapped.__liveCaptureWrapped = true;
  window.argosVentureOpenSessionWizard = wrapped;
  return true;
}

function init() {
  installStyles();
  const installWizard = () => {
    if (!wrapVentureWizard()) setTimeout(installWizard, 250);
  };
  installWizard();
  document.addEventListener('odysseus:session-selected', event => {
    const meta = event.detail?.meta;
    if (meta?.mode === 'live_capture') {
      setTimeout(() => { void restorePersistedCapture(meta); }, 0);
    } else if (activeCaptureId) {
      deactivateCapture();
    }
    queueTabSync();
  });
  document.addEventListener('odysseus:workspace-tab-activated', event => {
    if (event.detail?.kind !== 'meeting' && event.detail?.kind !== 'live_capture' && activeCaptureId) deactivateCapture();
    queueTabSync();
  });
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

const meetingCaptureModule = { openWorkspaceCapture, activateCapture, deactivateCapture, closeCapture, restorePersistedCapture };
window.meetingCaptureModule = meetingCaptureModule;

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();

export default meetingCaptureModule;
