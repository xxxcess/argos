// Argos Venture Audio Capture workspace tab.
//
// Meeting Capture is an in-app workspace surface, not a browser window. Raw
// transcript and recorder state remain in memory until the user explicitly
// exports a Markdown transcript or Meeting Brief to the existing Document Library.

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

function makeId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `meeting-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function clean(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
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

function elapsedMs(capture) {
  return capture.elapsedMs + (capture.phase === 'recording' && capture.runningSince ? Date.now() - capture.runningSince : 0);
}

function transcriptStamp(capture) {
  return `[${formatElapsed(elapsedMs(capture))}]`;
}

function newCapture({ captureId = makeId(), title = 'Audio Capture' } = {}) {
  return {
    captureId,
    title: clean(title) || 'Audio Capture',
    transcript: '',
    interim: '',
    phase: 'idle', // idle | recording | paused | stopping | stopped
    status: 'Ready to capture. Confirm consent, then record.',
    statusError: false,
    elapsedMs: 0,
    runningSince: 0,
    provider: 'loading',
    language: '',
    stream: null,
    recorder: null,
    recognition: null,
    segmentTimer: 0,
    segmentQueue: [],
    draining: false,
    audioContext: null,
    analyser: null,
    audioSource: null,
    waveformFrame: 0,
    timer: 0,
    consent: false,
  };
}

function icon(path) {
  return `<svg viewBox="0 0 24 24" aria-hidden="true">${path}</svg>`;
}

function microphoneIcon() {
  return icon('<rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0M12 17v5M8 22h8"/>');
}

function recordingIcon() {
  return icon('<circle cx="12" cy="12" r="6" fill="currentColor" stroke="none"/>');
}

function installStyles() {
  if (document.getElementById('meeting-capture-workspace-style')) return;
  const style = document.createElement('style');
  style.id = 'meeting-capture-workspace-style';
  style.textContent = `
    #meeting-capture-workspace { position:fixed; inset:var(--workspace-shell-h, 0px) 0 0 var(--icon-rail-w, 0px); z-index:35; display:none; grid-template-rows:auto minmax(0, 1fr) auto; background:var(--bg,#17191d); color:var(--fg,#e8eaed); }
    body.workspace-meeting-capture-active #meeting-capture-workspace { display:grid; }
    body.workspace-meeting-capture-active #chat-container { visibility:hidden; pointer-events:none; }
    body.workspace-meeting-capture-active #sidebar { pointer-events:none; }
    .meeting-capture-head { display:flex; align-items:center; gap:14px; min-width:0; padding:16px 24px; border-bottom:1px solid var(--border,#3a3f47); background:var(--panel,#21252b); }
    .meeting-capture-brand { display:flex; align-items:center; gap:10px; min-width:0; flex:1; }
    .meeting-capture-brand svg { width:22px; height:22px; fill:none; stroke:currentColor; stroke-width:1.8; flex:0 0 auto; color:var(--accent,var(--red,#e06c75)); }
    .meeting-capture-brand-copy { min-width:0; }
    .meeting-capture-kicker { font-size:10px; text-transform:uppercase; letter-spacing:.13em; opacity:.62; font-weight:700; }
    .meeting-capture-brand h1 { margin:3px 0 0; font-size:18px; line-height:1.2; font-weight:650; }
    .meeting-capture-title-input { width:min(360px, 35vw); min-width:140px; padding:8px 10px; border:1px solid var(--border,#3a3f47); border-radius:7px; background:var(--bg,#17191d); color:var(--fg,#e8eaed); font:inherit; }
    .meeting-capture-state { display:inline-flex; align-items:center; gap:7px; font-size:12px; white-space:nowrap; opacity:.82; }
    .meeting-capture-state::before { content:''; width:9px; height:9px; border-radius:999px; background:var(--border,#646a73); }
    .meeting-capture-state[data-phase="recording"]::before { background:var(--red,#e06c75); box-shadow:0 0 0 5px color-mix(in srgb,var(--red,#e06c75) 20%,transparent); }
    .meeting-capture-state[data-phase="paused"]::before { background:#e5c07b; }
    .meeting-capture-main { min-height:0; display:grid; grid-template-columns:minmax(0, 1fr) minmax(220px, 300px); gap:18px; padding:20px 24px; overflow:hidden; }
    .meeting-capture-transcript-card, .meeting-capture-details { min-height:0; border:1px solid var(--border,#3a3f47); border-radius:12px; background:var(--panel,#21252b); }
    .meeting-capture-transcript-card { display:flex; flex-direction:column; overflow:hidden; }
    .meeting-capture-transcript-head { display:flex; justify-content:space-between; align-items:center; padding:13px 16px; border-bottom:1px solid var(--border,#3a3f47); font-size:12px; }
    .meeting-capture-transcript-head span { opacity:.6; }
    .meeting-capture-log { flex:1; overflow:auto; padding:18px 20px; font:14px/1.7 ui-monospace,SFMono-Regular,Menlo,Monaco,Consolas,monospace; white-space:pre-wrap; }
    .meeting-capture-log pre { margin:0; font:inherit; white-space:pre-wrap; }
    .meeting-capture-empty { margin:0; color:var(--muted,#a4a8ae); font-family:var(--font-family,system-ui,sans-serif); opacity:.72; }
    .meeting-capture-interim { display:block; margin-top:10px; opacity:.5; font-style:italic; }
    .meeting-capture-details { padding:16px; overflow:auto; }
    .meeting-capture-details h2 { margin:0 0 10px; font-size:13px; }
    .meeting-capture-details p { margin:0 0 12px; font-size:12px; line-height:1.5; opacity:.72; }
    .meeting-capture-consent { display:flex; align-items:flex-start; gap:8px; padding:10px; border:1px solid color-mix(in srgb,var(--border,#3a3f47) 75%,transparent); border-radius:8px; font-size:12px; line-height:1.4; }
    .meeting-capture-consent input { margin-top:2px; }
    .meeting-capture-provider { margin-top:14px; padding-top:14px; border-top:1px solid var(--border,#3a3f47); font-size:12px; }
    .meeting-capture-provider strong { display:block; margin-bottom:4px; }
    .meeting-capture-footer { display:grid; grid-template-columns:auto minmax(110px,1fr) auto; align-items:center; gap:14px; padding:14px 24px; border-top:1px solid var(--border,#3a3f47); background:var(--panel,#21252b); }
    .meeting-recorder-controls { display:flex; align-items:center; gap:8px; }
    .meeting-recorder-controls button, .meeting-capture-actions button { border:1px solid var(--border,#3a3f47); border-radius:8px; padding:9px 12px; background:var(--bg,#17191d); color:var(--fg,#e8eaed); font:inherit; font-size:12px; cursor:pointer; }
    .meeting-recorder-controls button:disabled, .meeting-capture-actions button:disabled { cursor:not-allowed; opacity:.45; }
    .meeting-record-btn { min-width:112px; color:white!important; border-color:var(--red,#e06c75)!important; background:var(--red,#e06c75)!important; }
    .meeting-stop-btn { color:white!important; border-color:#4b5058!important; background:#4b5058!important; }
    .meeting-recorder-controls svg { width:13px; height:13px; fill:none; stroke:currentColor; stroke-width:2; vertical-align:-2px; margin-right:4px; }
    .meeting-record-btn svg { fill:currentColor; stroke:none; }
    .meeting-recorder-readout { display:flex; align-items:center; gap:12px; min-width:0; }
    .meeting-recorder-time { font:600 24px/1 ui-monospace,SFMono-Regular,Menlo,monospace; letter-spacing:.04em; white-space:nowrap; }
    .meeting-waveform { height:34px; width:100%; border-radius:6px; background:color-mix(in srgb,var(--bg,#17191d) 65%,transparent); }
    .meeting-capture-actions { display:flex; justify-content:flex-end; flex-wrap:wrap; gap:8px; }
    .meeting-capture-actions .primary { border-color:var(--accent,var(--red,#e06c75)); background:color-mix(in srgb,var(--accent,var(--red,#e06c75)) 18%,var(--bg)); }
    .meeting-capture-status { min-height:1.25em; font-size:11px; opacity:.75; }
    .meeting-capture-status[data-error="true"] { color:var(--red,#e06c75); opacity:1; }
    .workspace-tab.meeting-capture-tab .workspace-tab-icon svg { width:16px; height:16px; fill:none; stroke:currentColor; stroke-width:1.8; }
    .workspace-tab.meeting-capture-tab[data-state="recording"] .workspace-tab-state { display:block; background:var(--red,#e06c75); }
    @media (max-width:820px) {
      #meeting-capture-workspace { inset:var(--workspace-shell-h, 0px) 0 0 0; }
      .meeting-capture-head { padding:12px 14px; flex-wrap:wrap; }
      .meeting-capture-title-input { order:3; width:100%; }
      .meeting-capture-main { grid-template-columns:1fr; padding:12px; overflow:auto; }
      .meeting-capture-transcript-card { min-height:42vh; }
      .meeting-capture-footer { grid-template-columns:1fr; padding:12px; }
      .meeting-capture-actions { justify-content:flex-start; }
    }
  `;
  document.head.appendChild(style);
}

function activeCapture() {
  return activeCaptureId ? captures.get(activeCaptureId) || null : null;
}

function setStatus(capture, message, isError = false) {
  if (!capture) return;
  capture.status = message || '';
  capture.statusError = !!isError;
  if (capture.captureId === activeCaptureId) render();
}

function setTabRuntime(capture) {
  const tab = document.querySelector(`.workspace-tab[data-capture-id="${CSS.escape(capture.captureId)}"]`);
  if (tab) tab.dataset.state = capture.phase === 'recording' ? 'recording' : (capture.phase === 'paused' ? 'paused' : 'idle');
}

function renderTranscript(capture, log) {
  log.replaceChildren();
  if (!capture.transcript && !capture.interim) {
    const empty = document.createElement('p');
    empty.className = 'meeting-capture-empty';
    empty.textContent = 'Your live transcript will appear here as you record.';
    log.appendChild(empty);
    return;
  }
  if (capture.transcript) {
    const final = document.createElement('pre');
    final.textContent = capture.transcript;
    log.appendChild(final);
  }
  if (capture.interim) {
    const interim = document.createElement('span');
    interim.className = 'meeting-capture-interim';
    interim.textContent = `${capture.transcript ? '\n' : ''}${transcriptStamp(capture)} ${capture.interim}`;
    log.appendChild(interim);
  }
  log.scrollTop = log.scrollHeight;
}

function phaseLabel(phase) {
  return ({ idle: 'Ready to capture', recording: 'Recording live', paused: 'Capture paused', stopping: 'Finishing transcript', stopped: 'Transcript ready' })[phase] || 'Ready to capture';
}

function ensureRoot() {
  installStyles();
  if (root) return root;
  root = document.createElement('main');
  root.id = 'meeting-capture-workspace';
  root.setAttribute('aria-label', 'Audio capture workspace');
  root.innerHTML = `
    <header class="meeting-capture-head">
      <div class="meeting-capture-brand">
        ${microphoneIcon()}
        <div class="meeting-capture-brand-copy"><div class="meeting-capture-kicker">Argos Venture · Audio Capture</div><h1>Live Meeting Transcript</h1></div>
      </div>
      <div class="meeting-capture-state" id="meeting-capture-state" data-phase="idle">Ready to capture</div>
      <input id="meeting-capture-title" class="meeting-capture-title-input" type="text" maxlength="180" placeholder="Meeting title" aria-label="Meeting title" />
    </header>
    <section class="meeting-capture-main">
      <article class="meeting-capture-transcript-card">
        <div class="meeting-capture-transcript-head"><strong>Live transcript</strong><span id="meeting-capture-word-count">0 characters</span></div>
        <div class="meeting-capture-log" id="meeting-capture-log" aria-live="polite"></div>
      </article>
      <aside class="meeting-capture-details">
        <h2>Capture settings</h2>
        <p>Use the microphone only when you have authority and participant consent. Audio is sent only to the Speech-to-Text provider configured in Argos.</p>
        <label class="meeting-capture-consent"><input id="meeting-capture-consent" type="checkbox" /> <span>I have consent and authority to capture and transcribe this meeting.</span></label>
        <div class="meeting-capture-provider"><strong>Speech-to-text</strong><span id="meeting-capture-provider">Checking configuration…</span></div>
      </aside>
    </section>
    <footer class="meeting-capture-footer">
      <div class="meeting-recorder-controls">
        <button class="meeting-record-btn" id="meeting-capture-record" type="button">${recordingIcon()}<span>Start capture</span></button>
        <button id="meeting-capture-pause" type="button" disabled>Pause</button>
        <button class="meeting-stop-btn" id="meeting-capture-stop" type="button" disabled>Stop</button>
      </div>
      <div class="meeting-recorder-readout">
        <span class="meeting-recorder-time" id="meeting-capture-time">00:00</span>
        <canvas class="meeting-waveform" id="meeting-capture-waveform" aria-label="Microphone activity"></canvas>
      </div>
      <div class="meeting-capture-actions">
        <button id="meeting-capture-export-transcript" type="button">Export transcript</button>
        <button class="primary" id="meeting-capture-export-brief" type="button">Generate & export brief</button>
      </div>
      <div class="meeting-capture-status" id="meeting-capture-status" aria-live="polite"></div>
    </footer>
  `;
  document.body.appendChild(root);
  if (!rootWired) wireRoot();
  return root;
}

function wireRoot() {
  rootWired = true;
  const title = root.querySelector('#meeting-capture-title');
  const consent = root.querySelector('#meeting-capture-consent');
  root.querySelector('#meeting-capture-record')?.addEventListener('click', () => {
    const capture = activeCapture();
    if (!capture) return;
    if (capture.phase === 'paused') resumeCapture(capture);
    else startCapture(capture);
  });
  root.querySelector('#meeting-capture-pause')?.addEventListener('click', () => pauseCapture(activeCapture()));
  root.querySelector('#meeting-capture-stop')?.addEventListener('click', () => stopCapture(activeCapture()));
  root.querySelector('#meeting-capture-export-transcript')?.addEventListener('click', () => exportTranscript(activeCapture()));
  root.querySelector('#meeting-capture-export-brief')?.addEventListener('click', () => exportBrief(activeCapture()));
  title?.addEventListener('input', () => {
    const capture = activeCapture();
    if (!capture) return;
    capture.title = clean(title.value) || 'Audio Capture';
    const tabTitle = document.querySelector(`.workspace-tab[data-capture-id="${CSS.escape(capture.captureId)}"] .workspace-tab-title`);
    if (tabTitle) tabTitle.textContent = capture.title;
  });
  consent?.addEventListener('change', () => {
    const capture = activeCapture();
    if (!capture) return;
    capture.consent = !!consent.checked;
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
  const log = root.querySelector('#meeting-capture-log');
  const characters = root.querySelector('#meeting-capture-word-count');
  const record = root.querySelector('#meeting-capture-record');
  const pause = root.querySelector('#meeting-capture-pause');
  const stop = root.querySelector('#meeting-capture-stop');
  const time = root.querySelector('#meeting-capture-time');
  const status = root.querySelector('#meeting-capture-status');
  const exportTranscriptButton = root.querySelector('#meeting-capture-export-transcript');
  const exportBriefButton = root.querySelector('#meeting-capture-export-brief');
  if (document.activeElement !== title) title.value = capture.title;
  consent.checked = capture.consent;
  state.dataset.phase = capture.phase;
  state.textContent = phaseLabel(capture.phase);
  provider.textContent = capture.provider === 'loading' ? 'Checking configuration…'
    : capture.provider === 'browser' ? 'Browser speech recognition'
    : capture.provider === 'disabled' ? 'Speech-to-text is not configured'
    : `Argos ${capture.provider} provider`;
  renderTranscript(capture, log);
  characters.textContent = `${capture.transcript.length.toLocaleString()} characters`;
  time.textContent = formatElapsed(elapsedMs(capture));
  status.textContent = capture.status;
  status.dataset.error = capture.statusError ? 'true' : 'false';
  const canCapture = capture.provider !== 'disabled' && !(capture.provider === 'browser' && !browserRecognitionSupported());
  record.disabled = capture.phase === 'recording' || capture.phase === 'stopping' || !capture.consent || !canCapture || (activeRecordingId && activeRecordingId !== capture.captureId);
  record.querySelector('span').textContent = capture.phase === 'paused' ? 'Resume capture' : 'Start capture';
  pause.disabled = capture.phase !== 'recording';
  pause.textContent = capture.phase === 'recording' ? 'Pause' : 'Resume';
  stop.disabled = !['recording', 'paused', 'stopping'].includes(capture.phase);
  exportTranscriptButton.disabled = !capture.transcript.trim();
  exportBriefButton.disabled = !capture.transcript.trim() || capture.phase === 'stopping';
  setTabRuntime(capture);
}

function ensureTabChrome() {
  const list = document.getElementById('workspace-tab-list');
  if (!list) return;
  for (const stale of list.querySelectorAll('.meeting-capture-tab')) {
    if (!captures.has(stale.dataset.captureId)) stale.remove();
  }
  for (const capture of captures.values()) {
    let tab = list.querySelector(`.meeting-capture-tab[data-capture-id="${CSS.escape(capture.captureId)}"]`);
    if (!tab) {
      tab = document.createElement('div');
      tab.className = 'workspace-tab meeting-capture-tab';
      tab.dataset.captureId = capture.captureId;
      tab.dataset.state = 'idle';
      tab.setAttribute('role', 'tab');
      tab.tabIndex = 0;
      tab.innerHTML = `<span class="workspace-tab-icon">${microphoneIcon()}</span><span class="workspace-tab-title"></span><span class="workspace-tab-state" aria-hidden="true"></span><span class="workspace-tab-close-wrap"><button type="button" class="workspace-tab-close" aria-label="Close audio capture tab">&times;</button></span>`;
      tab.addEventListener('click', event => {
        if (event.target.closest('.workspace-tab-close')) return;
        activateCapture(capture.captureId);
      });
      tab.addEventListener('keydown', event => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          activateCapture(capture.captureId);
        }
      });
      tab.querySelector('.workspace-tab-close')?.addEventListener('click', event => {
        event.stopPropagation();
        closeCapture(capture.captureId);
      });
      list.appendChild(tab);
    }
    tab.querySelector('.workspace-tab-title').textContent = capture.title;
    tab.dataset.state = capture.phase === 'recording' ? 'recording' : (capture.phase === 'paused' ? 'paused' : 'idle');
    tab.setAttribute('aria-selected', String(activeCaptureId === capture.captureId));
  }
  if (activeCaptureId) {
    list.querySelectorAll('.workspace-tab:not(.meeting-capture-tab)').forEach(tab => tab.setAttribute('aria-selected', 'false'));
  }
}

function deactivateCapture() {
  if (!activeCaptureId) return;
  activeCaptureId = null;
  document.body.classList.remove('workspace-meeting-capture-active');
  if (root) root.setAttribute('aria-hidden', 'true');
  ensureTabChrome();
}

function activateCapture(captureId) {
  const capture = captures.get(captureId);
  if (!capture) return;
  activeCaptureId = captureId;
  ensureRoot();
  document.body.classList.add('workspace-meeting-capture-active');
  root.removeAttribute('aria-hidden');
  ensureTabChrome();
  render();
  loadSttConfiguration(capture);
  window.dispatchEvent(new CustomEvent('argos:meeting-capture-activated', { detail: { captureId } }));
}

function openWorkspaceCapture({ title = 'Audio Capture' } = {}) {
  const capture = newCapture({ title });
  captures.set(capture.captureId, capture);
  ensureTabChrome();
  activateCapture(capture.captureId);
  return capture.captureId;
}

function browserRecognitionSupported() {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

async function loadSttConfiguration(capture) {
  try {
    const response = await fetch(STT_STATS_ENDPOINT, { credentials: 'same-origin' });
    if (!response.ok) throw new Error('Speech-to-text status unavailable');
    const stats = await response.json();
    capture.provider = stats.provider || 'disabled';
    capture.language = stats.language || '';
  } catch (_) {
    capture.provider = 'disabled';
  }
  if (capture.provider === 'disabled') setStatus(capture, 'Configure browser, local, or endpoint Speech-to-Text in Settings before recording.', true);
  else if (capture.provider === 'browser' && !browserRecognitionSupported()) setStatus(capture, 'Browser speech recognition is unavailable. Configure local or endpoint Speech-to-Text.', true);
  else if (capture.phase === 'idle') setStatus(capture, capture.provider === 'browser' ? 'Ready. Browser speech recognition is configured.' : 'Ready. Argos will transcribe short microphone segments.');
  if (capture.captureId === activeCaptureId) render();
}

function preferredMimeType() {
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
  return candidates.find(type => window.MediaRecorder?.isTypeSupported?.(type)) || '';
}

function createRecorder(capture) {
  const type = preferredMimeType();
  try { return new MediaRecorder(capture.stream, type ? { mimeType: type } : undefined); }
  catch (_) { return new MediaRecorder(capture.stream); }
}

function appendTranscript(capture, text) {
  const cleaned = clean(text);
  if (!cleaned) return;
  const entry = `${transcriptStamp(capture)} ${cleaned}`;
  capture.transcript = capture.transcript ? `${capture.transcript}\n${entry}` : entry;
  if (capture.transcript.length > MAX_TRANSCRIPT_CHARS) {
    capture.transcript = capture.transcript.slice(capture.transcript.length - MAX_TRANSCRIPT_CHARS).trim();
    setStatus(capture, 'Transcript is limited to the most recent 120,000 characters.', true);
  }
  capture.interim = '';
  if (capture.captureId === activeCaptureId) render();
}

function startBrowserRecognition(capture) {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) throw new Error('Browser speech recognition is unavailable.');
  const recognition = new Recognition();
  capture.recognition = recognition;
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = capture.language || '';
  recognition.onresult = event => {
    let interim = '';
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const result = event.results[index];
      const text = clean(result[0]?.transcript || '');
      if (!text) continue;
      if (result.isFinal) appendTranscript(capture, text);
      else interim = clean(`${interim} ${text}`);
    }
    capture.interim = interim;
    if (capture.captureId === activeCaptureId) render();
  };
  recognition.onerror = event => {
    const code = event?.error || 'unknown';
    if (code !== 'aborted' && capture.phase === 'recording') setStatus(capture, `Browser transcription issue: ${code}.`, true);
  };
  recognition.onend = () => {
    if (capture.phase === 'recording') {
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
      const blob = capture.segmentQueue.shift();
      if (!blob?.size) continue;
      setStatus(capture, capture.phase === 'stopping' ? 'Transcribing final audio…' : 'Transcribing live audio…');
      try {
        const text = await transcribeSegment(blob);
        if (text) appendTranscript(capture, text);
      } catch (error) {
        setStatus(capture, `A segment could not be transcribed: ${error.message}`, true);
      }
    }
  } finally {
    capture.draining = false;
    if (capture.phase === 'stopping' && !capture.recorder && !capture.segmentQueue.length) finishStop(capture);
    if (capture.phase === 'paused' && !capture.recorder && !capture.segmentQueue.length) releaseMicrophone(capture);
  }
}

function startServerSegment(capture) {
  if (!capture || capture.phase !== 'recording' || !capture.stream) return;
  const recorder = createRecorder(capture);
  capture.recorder = recorder;
  const chunks = [];
  recorder.ondataavailable = event => { if (event.data?.size) chunks.push(event.data); };
  recorder.onstop = () => {
    if (capture.recorder === recorder) capture.recorder = null;
    const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
    if (blob.size) capture.segmentQueue.push(blob);
    drainQueue(capture);
    if (capture.phase === 'recording') window.setTimeout(() => startServerSegment(capture), 0);
    else if (capture.phase === 'stopping' && !capture.segmentQueue.length && !capture.draining) finishStop(capture);
    else if (capture.phase === 'paused' && !capture.segmentQueue.length && !capture.draining) releaseMicrophone(capture);
  };
  recorder.start();
  capture.segmentTimer = window.setTimeout(() => {
    if (recorder.state === 'recording') recorder.stop();
  }, SEGMENT_MS);
}

function stopActiveSegment(capture) {
  if (!capture) return;
  if (capture.segmentTimer) window.clearTimeout(capture.segmentTimer);
  capture.segmentTimer = 0;
  if (capture.recorder?.state === 'recording') capture.recorder.stop();
}

function startTimer(capture) {
  if (capture.timer) window.clearInterval(capture.timer);
  capture.timer = window.setInterval(() => { if (capture.captureId === activeCaptureId) render(); }, 250);
}

function stopTimer(capture) {
  if (capture?.timer) window.clearInterval(capture.timer);
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

async function startCapture(capture) {
  if (!capture || capture.phase === 'recording' || capture.phase === 'stopping') return;
  if (!capture.consent) {
    setStatus(capture, 'Confirm participant consent before starting capture.', true);
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
  if (capture.provider === 'loading') await loadSttConfiguration(capture);
  if (capture.provider === 'disabled' || (capture.provider === 'browser' && !browserRecognitionSupported())) {
    setStatus(capture, 'Configure an available Speech-to-Text provider before recording.', true);
    return;
  }
  try {
    capture.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    capture.phase = 'recording';
    capture.runningSince = Date.now();
    activeRecordingId = capture.captureId;
    startTimer(capture);
    startMeter(capture);
    if (capture.provider === 'browser') startBrowserRecognition(capture);
    else startServerSegment(capture);
    setStatus(capture, 'Recording. Audio is processed only by your configured Argos Speech-to-Text provider.');
    render();
  } catch (error) {
    releaseMicrophone(capture);
    capture.phase = 'idle';
    setStatus(capture, `Microphone access failed: ${error.message}`, true);
  }
}

async function resumeCapture(capture) {
  if (!capture || capture.phase !== 'paused') return;
  await startCapture(capture);
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
  stopTimer(capture);
  releaseMicrophone(capture);
  setStatus(capture, capture.transcript ? 'Transcript ready. Export the transcript or generate a Meeting Brief.' : 'Capture stopped. No speech was transcribed.');
  render();
}

function stopCapture(capture) {
  if (!capture || !['recording', 'paused', 'stopping'].includes(capture.phase)) return;
  if (capture.phase === 'paused') {
    capture.phase = 'stopped';
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
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: null, title, language: 'markdown', content }),
  });
  const document = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(document?.detail?.message || document?.detail || document?.message || 'Document export failed');
  return document;
}

async function openLibraryDocument(document) {
  const docId = document?.id || document?.doc_id;
  if (!docId) return;
  const module = window.documentModule;
  try {
    await module?.loadDocument?.(docId);
    module?.openPanel?.();
    return;
  } catch (_) {}
  try {
    module?.openLibrary?.();
  } catch (_) {}
}

async function exportTranscript(capture) {
  if (!capture?.transcript.trim()) {
    setStatus(capture, 'Record speech before exporting a transcript.', true);
    return;
  }
  const title = clean(capture.title) || 'Audio Capture';
  setStatus(capture, 'Exporting transcript to your document library…');
  render();
  try {
    const document = await createLibraryDocument(`${title} — Transcript`, `# ${title}\n\n## Live Transcript\n\n${capture.transcript.trim()}`);
    setStatus(capture, `Transcript exported to your document library. Opening it now.`);
    await openLibraryDocument(document);
  } catch (error) {
    setStatus(capture, `Transcript export failed: ${error.message}`, true);
  }
  render();
}

async function exportBrief(capture) {
  if (!capture?.transcript.trim()) {
    setStatus(capture, 'Record speech before generating a Meeting Brief.', true);
    return;
  }
  const title = clean(capture.title) || 'Audio Capture';
  setStatus(capture, 'Generating a grounded Meeting Brief…');
  render();
  try {
    const response = await fetch(BRIEF_ENDPOINT, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, transcript: capture.transcript.trim(), focus: '' }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result?.detail?.message || result?.detail || result?.message || 'Meeting Brief generation failed');
    const brief = clean(result.brief);
    if (!brief) throw new Error('The Utility model returned an empty Meeting Brief');
    const document = await createLibraryDocument(`${title} — Meeting Brief`, brief);
    setStatus(capture, 'Meeting Brief exported to your document library. Opening it now.');
    await openLibraryDocument(document);
  } catch (error) {
    setStatus(capture, `Meeting Brief export failed: ${error.message}`, true);
  }
  render();
}

async function closeCapture(captureId) {
  const capture = captures.get(captureId);
  if (!capture) return;
  if (capture.phase === 'recording' || capture.phase === 'stopping') {
    const message = 'Stop this live capture and close the tab? Unexported transcript text will be discarded.';
    const accepted = window.uiModule?.styledConfirm ? await window.uiModule.styledConfirm(message, { confirmText: 'Stop and close' }) : window.confirm(message);
    if (!accepted) return;
  }
  stopBrowserRecognition(capture);
  stopActiveSegment(capture);
  stopTimer(capture);
  releaseMicrophone(capture);
  captures.delete(captureId);
  if (activeCaptureId === captureId) deactivateCapture();
  ensureTabChrome();
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
  audioFields.innerHTML = '<label>Meeting title</label><input name="audio_capture_title" autocomplete="off" placeholder="Weekly project review"><div class="venture-muted" style="margin-top:8px">This opens an in-app Audio Capture tab. Microphone access is requested only when you start recording.</div>';
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
    window.dispatchEvent(new CustomEvent('argos:open-meeting-capture', { detail: { title } }));
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
  document.addEventListener('odysseus:workspace-tab-activated', event => {
    if (event.detail?.kind !== 'meeting') deactivateCapture();
  });
  window.addEventListener('argos:open-meeting-capture', event => openWorkspaceCapture(event.detail || {}));
  window.addEventListener('pagehide', () => {
    for (const capture of captures.values()) {
      stopBrowserRecognition(capture);
      stopActiveSegment(capture);
      stopTimer(capture);
      releaseMicrophone(capture);
    }
  });
  const install = () => {
    if (!wrapVentureWizard()) window.setTimeout(install, 250);
    else augmentNewTabWizard();
  };
  install();
  const target = document.body || document.documentElement;
  if (target && typeof MutationObserver !== 'undefined') {
    new MutationObserver(() => {
      ensureTabChrome();
      augmentNewTabWizard();
    }).observe(target, { childList: true, subtree: true });
  }
}

const meetingCaptureModule = { openWorkspaceCapture, activateCapture, deactivateCapture, closeCapture };
window.meetingCaptureModule = meetingCaptureModule;

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();

export default meetingCaptureModule;
