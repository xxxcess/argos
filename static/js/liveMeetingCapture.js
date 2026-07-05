// Argos Venture Live Meeting Capture
//
// Intentionally separate from voiceRecorder.js: that module owns the chat
// composer and conversation loop, while this module owns an independent,
// consent-gated meeting tab.

const CAPTURE_PARAM = 'meeting-capture';
const CAPTURE_VALUE = 'live';
const CAPTURE_CHANNEL = 'argos-venture-meeting-capture';
const SEGMENT_MS = 10_000;
const MAX_TRANSCRIPT_CHARS = 120_000;

let channel = null;
let stream = null;
let recorder = null;
let recognition = null;
let audioContext = null;
let analyser = null;
let audioSource = null;
let waveformFrame = 0;
let elapsedTimer = 0;
let segmentTimer = 0;
let recording = false;
let stopping = false;
let segmentQueue = [];
let drainingQueue = false;
let transcript = '';
let interimTranscript = '';
let startedAt = 0;
let sttProvider = 'disabled';
let sttLanguage = '';
let meetingTitle = '';
let elements = {};

function isCapturePage() {
  return new URLSearchParams(window.location.search).get(CAPTURE_PARAM) === CAPTURE_VALUE;
}

function cleanText(value) {
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

function transcriptTimestamp() {
  return `[${formatElapsed(Date.now() - startedAt)}]`;
}

function setStatus(text, error = false) {
  if (!elements.status) return;
  elements.status.textContent = text || '';
  elements.status.dataset.error = error ? 'true' : 'false';
}

function setRecordingState(isRecording) {
  recording = isRecording;
  document.body.classList.toggle('meeting-capture-recording', isRecording);
  if (elements.start) elements.start.disabled = isRecording || stopping || !elements.consent?.checked;
  if (elements.stop) elements.stop.disabled = !isRecording || stopping;
  if (elements.state) {
    elements.state.textContent = isRecording ? 'Recording live' : (stopping ? 'Finishing transcript' : 'Ready to capture');
    elements.state.dataset.recording = isRecording ? 'true' : 'false';
  }
}

function renderTranscript() {
  const finalText = transcript.trim();
  const interim = interimTranscript.trim();
  const target = elements.transcript;
  if (!target) return;
  target.replaceChildren();
  if (!finalText && !interim) {
    const empty = document.createElement('p');
    empty.className = 'meeting-capture-empty';
    empty.textContent = 'Your live transcript will appear here.';
    target.appendChild(empty);
    return;
  }
  if (finalText) {
    const finalized = document.createElement('pre');
    finalized.className = 'meeting-capture-final';
    finalized.textContent = finalText;
    target.appendChild(finalized);
  }
  if (interim) {
    const pending = document.createElement('span');
    pending.className = 'meeting-capture-interim';
    pending.textContent = `${finalText ? '\n' : ''}${transcriptTimestamp()} ${interim}`;
    target.appendChild(pending);
  }
  target.scrollTop = target.scrollHeight;
}

function sendCaptureMessage(type, extra = {}) {
  const message = {
    type,
    source: 'argos-venture-live-meeting-capture',
    title: meetingTitle || 'Untitled meeting',
    transcript: transcript.trim(),
    ...extra,
  };
  try { channel?.postMessage(message); } catch (_) {}
  try {
    if (window.opener && !window.opener.closed) window.opener.postMessage(message, window.location.origin);
  } catch (_) {}
}

function appendTranscript(text) {
  const cleaned = cleanText(text);
  if (!cleaned) return;
  const entry = `${transcriptTimestamp()} ${cleaned}`;
  transcript = transcript ? `${transcript}\n${entry}` : entry;
  if (transcript.length > MAX_TRANSCRIPT_CHARS) {
    transcript = transcript.slice(transcript.length - MAX_TRANSCRIPT_CHARS).trim();
    setStatus('Transcript display is limited to the most recent 120,000 characters.');
  }
  interimTranscript = '';
  renderTranscript();
  sendCaptureMessage('meeting-capture-progress');
}

function browserRecognitionSupported() {
  return !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

async function loadSttSettings() {
  try {
    const response = await fetch('/api/stt/stats', { credentials: 'same-origin' });
    if (!response.ok) throw new Error('STT status unavailable');
    const stats = await response.json();
    sttProvider = stats.provider || 'disabled';
    sttLanguage = stats.language || '';
  } catch (_) {
    sttProvider = 'disabled';
  }

  if (sttProvider === 'browser' && !browserRecognitionSupported()) {
    setStatus('Browser speech recognition is unavailable. Configure local or endpoint STT in Settings.', true);
  } else if (sttProvider === 'disabled') {
    setStatus('Speech-to-text is disabled. Configure a browser, local, or endpoint STT provider in Settings.', true);
  } else {
    setStatus(sttProvider === 'browser' ? 'Uses browser speech recognition.' : 'Uses the configured Argos STT provider.');
  }
}

function preferredMimeType() {
  const candidates = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus'];
  return candidates.find(type => window.MediaRecorder?.isTypeSupported?.(type)) || '';
}

function createRecorder() {
  const type = preferredMimeType();
  try { return new MediaRecorder(stream, type ? { mimeType: type } : undefined); }
  catch (_) { return new MediaRecorder(stream); }
}

function startBrowserRecognition() {
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Recognition) throw new Error('Browser speech recognition is not supported.');
  recognition = new Recognition();
  recognition.continuous = true;
  recognition.interimResults = true;
  recognition.lang = sttLanguage || '';
  recognition.onresult = event => {
    let interim = '';
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      const result = event.results[index];
      const text = cleanText(result[0]?.transcript || '');
      if (!text) continue;
      if (result.isFinal) appendTranscript(text);
      else interim = cleanText(`${interim} ${text}`);
    }
    interimTranscript = interim;
    renderTranscript();
  };
  recognition.onerror = event => {
    const error = event?.error || 'unknown';
    if (error !== 'aborted' && recording) setStatus(`Browser transcription issue: ${error}.`, true);
  };
  recognition.onend = () => {
    if (recording && !stopping) {
      try { recognition.start(); } catch (_) {}
    }
  };
  recognition.start();
}

function stopBrowserRecognition() {
  if (!recognition) return;
  try { recognition.onend = null; recognition.stop(); } catch (_) {}
  recognition = null;
  interimTranscript = '';
}

async function transcribeSegment(blob) {
  const data = new FormData();
  data.append('file', blob, 'live-meeting-segment.webm');
  const response = await fetch('/api/stt/transcribe', {
    method: 'POST',
    credentials: 'same-origin',
    body: data,
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result?.detail?.message || result?.message || 'Transcription failed');
  return cleanText(result.text || '');
}

async function drainSegmentQueue() {
  if (drainingQueue) return;
  drainingQueue = true;
  try {
    while (segmentQueue.length) {
      const blob = segmentQueue.shift();
      if (!blob?.size) continue;
      setStatus(stopping ? 'Transcribing final audio…' : 'Transcribing live audio…');
      try {
        const text = await transcribeSegment(blob);
        if (text) appendTranscript(text);
      } catch (error) {
        console.error('Live meeting STT segment failed:', error);
        setStatus(`A segment could not be transcribed: ${error.message}`, true);
      }
    }
  } finally {
    drainingQueue = false;
    if (stopping && !recorder && !segmentQueue.length) finishStop();
  }
}

function startServerSegment() {
  if (!recording || stopping || !stream) return;
  const activeRecorder = createRecorder();
  recorder = activeRecorder;
  const chunks = [];
  activeRecorder.ondataavailable = event => {
    if (event.data?.size) chunks.push(event.data);
  };
  activeRecorder.onstop = () => {
    if (recorder === activeRecorder) recorder = null;
    const blob = new Blob(chunks, { type: activeRecorder.mimeType || 'audio/webm' });
    if (blob.size) segmentQueue.push(blob);
    drainSegmentQueue();
    if (recording && !stopping) window.setTimeout(startServerSegment, 0);
    else if (stopping && !segmentQueue.length && !drainingQueue) finishStop();
  };
  activeRecorder.start();
  segmentTimer = window.setTimeout(() => {
    if (activeRecorder.state === 'recording') activeRecorder.stop();
  }, SEGMENT_MS);
}

function stopActiveServerSegment() {
  if (segmentTimer) window.clearTimeout(segmentTimer);
  segmentTimer = 0;
  if (recorder?.state === 'recording') recorder.stop();
}

function startMeter() {
  const AudioContext = window.AudioContext || window.webkitAudioContext;
  if (!AudioContext || !stream) return;
  try {
    audioContext = new AudioContext();
    analyser = audioContext.createAnalyser();
    analyser.fftSize = 128;
    audioSource = audioContext.createMediaStreamSource(stream);
    audioSource.connect(analyser);
    const canvas = elements.waveform;
    const context = canvas?.getContext?.('2d');
    const draw = () => {
      if (!recording || !context || !canvas || !analyser) return;
      const values = new Uint8Array(analyser.frequencyBinCount);
      analyser.getByteFrequencyData(values);
      const width = canvas.width = Math.max(1, Math.floor(canvas.clientWidth * devicePixelRatio));
      const height = canvas.height = Math.max(1, Math.floor(canvas.clientHeight * devicePixelRatio));
      context.clearRect(0, 0, width, height);
      const barWidth = Math.max(2, width / values.length - 2);
      for (let index = 0; index < values.length; index += 1) {
        const barHeight = Math.max(2, (values[index] / 255) * height);
        context.fillStyle = index % 2 ? 'rgba(224,108,117,.9)' : 'rgba(156,222,242,.72)';
        context.fillRect(index * (barWidth + 2), (height - barHeight) / 2, barWidth, barHeight);
      }
      waveformFrame = requestAnimationFrame(draw);
    };
    draw();
  } catch (_) {}
}

function stopMeter() {
  if (waveformFrame) cancelAnimationFrame(waveformFrame);
  waveformFrame = 0;
  try { audioSource?.disconnect?.(); } catch (_) {}
  try { analyser?.disconnect?.(); } catch (_) {}
  try { audioContext?.close?.(); } catch (_) {}
  audioSource = null;
  analyser = null;
  audioContext = null;
}

function releaseMicrophone() {
  stopMeter();
  try { stream?.getTracks?.().forEach(track => track.stop()); } catch (_) {}
  stream = null;
}

function startElapsedTimer() {
  if (elapsedTimer) window.clearInterval(elapsedTimer);
  const update = () => {
    if (elements.elapsed && startedAt) elements.elapsed.textContent = formatElapsed(Date.now() - startedAt);
  };
  update();
  elapsedTimer = window.setInterval(update, 250);
}

function stopElapsedTimer() {
  if (elapsedTimer) window.clearInterval(elapsedTimer);
  elapsedTimer = 0;
}

async function startCapture() {
  meetingTitle = cleanText(elements.title?.value) || 'Untitled meeting';
  if (!elements.consent?.checked) {
    setStatus('Confirm participant consent before starting capture.', true);
    return;
  }
  if (!window.isSecureContext) {
    setStatus('Microphone capture requires HTTPS or localhost.', true);
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    setStatus('Microphone capture is unavailable in this browser.', true);
    return;
  }
  if (sttProvider === 'disabled' || (sttProvider === 'browser' && !browserRecognitionSupported())) return;

  try {
    stopping = false;
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    startedAt = Date.now();
    setRecordingState(true);
    startElapsedTimer();
    startMeter();
    if (sttProvider === 'browser') startBrowserRecognition();
    else startServerSegment();
    setStatus('Recording. The microphone stays local except for STT segments sent to your configured Argos provider.');
  } catch (error) {
    console.error('Live meeting microphone error:', error);
    releaseMicrophone();
    setRecordingState(false);
    setStatus(`Microphone access failed: ${error.message}`, true);
  }
}

function finishStop() {
  if (!stopping) return;
  stopping = false;
  stopElapsedTimer();
  releaseMicrophone();
  setRecordingState(false);
  renderTranscript();
  setStatus(transcript.trim() ? 'Transcript ready for export or review.' : 'Capture ended. No speech was transcribed.');
  sendCaptureMessage('meeting-capture-complete');
}

function stopCapture() {
  if (!recording && !stopping) return;
  stopping = true;
  setRecordingState(false);
  stopElapsedTimer();
  stopBrowserRecognition();
  if (sttProvider === 'browser') {
    finishStop();
    return;
  }
  stopActiveServerSegment();
  if (!recorder && !segmentQueue.length && !drainingQueue) finishStop();
}

async function exportTranscript() {
  const content = transcript.trim();
  if (!content) {
    setStatus('Record or add speech before exporting a transcript.', true);
    return;
  }
  const title = cleanText(elements.title?.value) || meetingTitle || 'Untitled meeting';
  const button = elements.export;
  if (button) button.disabled = true;
  setStatus('Exporting transcript to your document library…');
  try {
    const response = await fetch('/api/document', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: null,
        title: `${title} — Transcript`,
        language: 'markdown',
        content: `# ${title}\n\n## Live Transcript\n\n${content}`,
      }),
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.detail || result.message || 'Document export failed');
    setStatus(`Exported “${result.title || `${title} — Transcript`}” to your document library.`);
    sendCaptureMessage('meeting-capture-exported', { document_id: result.id || null });
  } catch (error) {
    setStatus(`Transcript export failed: ${error.message}`, true);
  } finally {
    if (button) button.disabled = false;
  }
}

function reviewInMeetingBrief() {
  if (recording || stopping) stopCapture();
  sendCaptureMessage('meeting-capture-review');
  try { window.opener?.focus?.(); } catch (_) {}
  setStatus('Transcript sent to Meeting Brief for review.');
}

function injectStyles() {
  const style = document.createElement('style');
  style.textContent = `
    body.meeting-capture-page { margin:0; overflow:hidden; background:var(--bg,#1e2127); color:var(--fg,#e6e6e6); }
    body.meeting-capture-page > :not(#meeting-capture-root) { display:none !important; }
    #meeting-capture-root { position:fixed; inset:0; z-index:2147483647; display:grid; grid-template-rows:auto 1fr auto; background:var(--bg,#1e2127); font-family:var(--font-family,system-ui,sans-serif); }
    .meeting-capture-header { display:flex; align-items:center; gap:14px; padding:18px 24px; border-bottom:1px solid var(--border,#3b4048); background:var(--panel,#282c34); }
    .meeting-capture-brand { min-width:0; flex:1; }
    .meeting-capture-kicker { color:var(--brand-color,var(--red,#e06c75)); font-size:11px; font-weight:700; letter-spacing:.12em; text-transform:uppercase; }
    .meeting-capture-title { margin:4px 0 0; font-size:20px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .meeting-capture-state { display:flex; align-items:center; gap:7px; font-size:12px; opacity:.8; }
    .meeting-capture-state::before { content:''; width:8px; height:8px; border-radius:999px; background:var(--border,#606770); }
    .meeting-capture-state[data-recording=true]::before { background:var(--red,#e06c75); box-shadow:0 0 0 5px color-mix(in srgb,var(--red,#e06c75) 18%,transparent); }
    .meeting-capture-main { min-height:0; display:grid; grid-template-columns:minmax(0,1fr) 300px; gap:18px; padding:22px 24px; }
    .meeting-capture-transcript { min-height:0; display:flex; flex-direction:column; border:1px solid var(--border,#3b4048); border-radius:12px; background:var(--panel,#282c34); overflow:hidden; }
    .meeting-capture-transcript-head { display:flex; justify-content:space-between; align-items:center; padding:14px 16px; border-bottom:1px solid var(--border,#3b4048); font-size:12px; }
    .meeting-capture-log { flex:1; overflow:auto; padding:20px; font:14px/1.7 var(--font-family,ui-monospace,monospace); }
    .meeting-capture-final { margin:0; font:inherit; white-space:pre-wrap; color:inherit; }
    .meeting-capture-interim { opacity:.5; font-style:italic; }
    .meeting-capture-empty { margin:0; opacity:.48; }
    .meeting-capture-side { display:flex; flex-direction:column; gap:14px; }
    .meeting-capture-card { border:1px solid var(--border,#3b4048); border-radius:12px; padding:16px; background:var(--panel,#282c34); }
    .meeting-capture-card label { display:block; margin-bottom:8px; font-size:12px; opacity:.72; }
    .meeting-capture-card input[type=text] { width:100%; box-sizing:border-box; padding:9px 10px; color:inherit; background:var(--bg,#1e2127); border:1px solid var(--border,#3b4048); border-radius:7px; }
    .meeting-capture-consent { display:flex; gap:8px; align-items:flex-start; font-size:12px; line-height:1.45; opacity:.84; }
    .meeting-capture-consent input { margin-top:3px; }
    .meeting-capture-timer { font:600 32px/1 ui-monospace,monospace; letter-spacing:.04em; }
    .meeting-capture-wave { width:100%; height:58px; margin-top:14px; border-radius:8px; background:color-mix(in srgb,var(--bg,#1e2127) 78%,transparent); }
    .meeting-capture-actions { display:flex; flex-wrap:wrap; gap:8px; }
    .meeting-capture-actions button { flex:1 1 120px; padding:10px 12px; border-radius:7px; cursor:pointer; color:inherit; border:1px solid var(--border,#3b4048); background:var(--bg,#1e2127); }
    .meeting-capture-actions button.primary { color:white; background:var(--brand-color,var(--red,#e06c75)); border-color:var(--brand-color,var(--red,#e06c75)); }
    .meeting-capture-actions button.stop { color:white; background:var(--red,#e06c75); border-color:var(--red,#e06c75); }
    .meeting-capture-actions button:disabled { cursor:not-allowed; opacity:.45; }
    .meeting-capture-footer { display:flex; gap:12px; justify-content:space-between; align-items:center; padding:14px 24px; border-top:1px solid var(--border,#3b4048); background:var(--panel,#282c34); font-size:12px; }
    .meeting-capture-status { min-height:1em; opacity:.7; }
    .meeting-capture-status[data-error=true] { color:var(--red,#e06c75); opacity:1; }
    @media (max-width:760px) { .meeting-capture-main { grid-template-columns:1fr; overflow:auto; } .meeting-capture-transcript { min-height:45vh; } .meeting-capture-header { padding:14px; } .meeting-capture-footer { padding:12px 14px; } }
  `;
  document.head.appendChild(style);
}

function buildPage() {
  document.body.classList.add('meeting-capture-page');
  document.title = 'Live Meeting Capture · Argos Venture';
  injectStyles();
  const queryTitle = cleanText(new URLSearchParams(window.location.search).get('title'));
  meetingTitle = queryTitle || 'Untitled meeting';
  const root = document.createElement('main');
  root.id = 'meeting-capture-root';
  root.innerHTML = `
    <header class="meeting-capture-header">
      <div class="meeting-capture-brand"><div class="meeting-capture-kicker">Argos Venture · Meeting Brief</div><h1 class="meeting-capture-title">Live Meeting Capture</h1></div>
      <div class="meeting-capture-state" id="meeting-capture-state" data-recording="false">Ready to capture</div>
    </header>
    <section class="meeting-capture-main">
      <article class="meeting-capture-transcript">
        <div class="meeting-capture-transcript-head"><strong>Live transcript</strong><span id="meeting-capture-elapsed">00:00</span></div>
        <div class="meeting-capture-log" id="meeting-capture-transcript" aria-live="polite"></div>
      </article>
      <aside class="meeting-capture-side">
        <section class="meeting-capture-card"><label for="meeting-capture-title-input">Meeting title</label><input id="meeting-capture-title-input" type="text" maxlength="180" value="${meetingTitle.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')}" /></section>
        <section class="meeting-capture-card"><div class="meeting-capture-timer" id="meeting-capture-timer">00:00</div><canvas class="meeting-capture-wave" id="meeting-capture-waveform" aria-label="Microphone activity"></canvas></section>
        <section class="meeting-capture-card"><label class="meeting-capture-consent"><input id="meeting-capture-consent" type="checkbox" /> <span>I have authority and consent from participants to capture and transcribe this meeting.</span></label></section>
        <section class="meeting-capture-actions"><button class="primary" id="meeting-capture-start" type="button" disabled>Start capture</button><button class="stop" id="meeting-capture-stop" type="button" disabled>Stop capture</button><button id="meeting-capture-export" type="button">Export transcript</button><button id="meeting-capture-review" type="button">Review in Meeting Brief</button></section>
      </aside>
    </section>
    <footer class="meeting-capture-footer"><span class="meeting-capture-status" id="meeting-capture-status"></span><span>Audio is processed only by the configured Argos STT provider.</span></footer>
  `;
  document.body.appendChild(root);
  elements = {
    title: root.querySelector('#meeting-capture-title-input'),
    consent: root.querySelector('#meeting-capture-consent'),
    start: root.querySelector('#meeting-capture-start'),
    stop: root.querySelector('#meeting-capture-stop'),
    export: root.querySelector('#meeting-capture-export'),
    review: root.querySelector('#meeting-capture-review'),
    transcript: root.querySelector('#meeting-capture-transcript'),
    elapsed: root.querySelector('#meeting-capture-elapsed'),
    timer: root.querySelector('#meeting-capture-timer'),
    waveform: root.querySelector('#meeting-capture-waveform'),
    status: root.querySelector('#meeting-capture-status'),
    state: root.querySelector('#meeting-capture-state'),
  };
  const updateTitle = () => { meetingTitle = cleanText(elements.title.value) || 'Untitled meeting'; };
  elements.title.addEventListener('input', updateTitle);
  elements.consent.addEventListener('change', () => setRecordingState(recording));
  elements.start.addEventListener('click', startCapture);
  elements.stop.addEventListener('click', stopCapture);
  elements.export.addEventListener('click', exportTranscript);
  elements.review.addEventListener('click', reviewInMeetingBrief);
  renderTranscript();
  setRecordingState(false);
}

function initChannel() {
  if (!('BroadcastChannel' in window)) return;
  channel = new BroadcastChannel(CAPTURE_CHANNEL);
}

function boot() {
  if (!isCapturePage()) return;
  initChannel();
  buildPage();
  loadSttSettings().finally(() => setRecordingState(false));
  window.addEventListener('pagehide', () => {
    if (recording || stopping) {
      stopping = true;
      stopBrowserRecognition();
      stopActiveServerSegment();
      releaseMicrophone();
    }
    try { channel?.close?.(); } catch (_) {}
  });
}

boot();
