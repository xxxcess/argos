// static/js/voiceRecorder.js

/**
 * Voice recording with optional Speech-to-Text transcription.
 *
 * STT providers:
 *   "disabled"       — record audio as file attachment (original behavior)
 *   "browser"        — use Web Speech API for real-time transcription
 *   "local"          — send recording to server /api/stt/transcribe (Whisper)
 *   "endpoint:<id>"  — send recording to server /api/stt/transcribe (API)
 */

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let recordingStartTime = null;
let recordingInterval = null;
let _recordingStopReason = 'manual';
let _activeRecordingId = 0;

// Browser STT state
let _recognition = null;
let _browserTranscript = '';
let _browserSpeechDetected = false;

// Client-side voice activity detection. This gates looped auto-submit so silence
// cannot hallucinate or reuse an old prompt.
let _audioContext = null;
let _audioAnalyser = null;
let _audioSource = null;
let _voiceActivityTimer = null;
let _speechDetected = false;
let _speechStartedAt = 0;
let _lastVoiceActivityAt = 0;
let _recordingStartedAt = 0;
let _vadAvailable = false;

const VAD_RMS_THRESHOLD = 0.018;
const VAD_MIN_SPEECH_MS = 250;
const MIN_RECORDING_MS = 400;
const MIN_TRANSCRIPT_CHARS = 2;
const DUPLICATE_SUBMIT_WINDOW_MS = 15000;

// Cached STT provider — refreshed on settings change
let _sttProvider = 'disabled';
let _sttLanguage = '';

// Conversation loop settings/state
let _sttConversationLoop = false;
let _sttLoopSubmitSeconds = 3;
let _sttLoopIdleTimeoutSeconds = 5;
let _loopActive = false;
let _loopWaitingForAssistant = false;
let _loopInstructionDeadline = 0;
let _loopAutoStopTimer = null;
let _loopRestartTimer = null;
let _sendButtonObserver = null;
let _lastSendButtonMode = '';
let _lastAutoSubmittedText = '';
let _lastAutoSubmittedAt = 0;

function _clampNumber(value, fallback, min, max) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, n));
}

function _applyLoopSettings(settings) {
  settings = settings || {};
  _sttConversationLoop = settings.stt_conversation_loop === true;
  _sttLoopSubmitSeconds = _clampNumber(settings.stt_loop_submit_seconds, 3, 1, 60);
  _sttLoopIdleTimeoutSeconds = _clampNumber(settings.stt_loop_idle_timeout_seconds, 5, 1, 300);
}

function _clearLoopTimers() {
  if (_loopAutoStopTimer) {
    clearTimeout(_loopAutoStopTimer);
    _loopAutoStopTimer = null;
  }
  if (_loopRestartTimer) {
    clearTimeout(_loopRestartTimer);
    _loopRestartTimer = null;
  }
}

function stopConversationLoop(reason) {
  const wasActive = _loopActive || _loopWaitingForAssistant;
  _loopActive = false;
  _loopWaitingForAssistant = false;
  _loopInstructionDeadline = 0;
  _clearLoopTimers();
  if (wasActive && reason !== 'silent') {
    const msg = reason === 'idle-timeout'
      ? 'Conversation loop ended — no instruction heard.'
      : 'Conversation loop stopped.';
    try { window.uiModule?.showToast?.(msg); } catch (_) {}
  }
}

function _conversationLoopAvailable() {
  return _sttConversationLoop && _sttProvider !== 'disabled';
}

function _loopRemainingMs() {
  if (!_loopInstructionDeadline) return _sttLoopIdleTimeoutSeconds * 1000;
  return Math.max(0, _loopInstructionDeadline - Date.now());
}

function _normalizeTranscript(text) {
  return String(text || '').replace(/\s+/g, ' ').trim();
}

function _hasFreshTranscript(text) {
  return _normalizeTranscript(text).length >= MIN_TRANSCRIPT_CHARS;
}

function _resetSpeechState() {
  _browserTranscript = '';
  _browserSpeechDetected = false;
  _speechDetected = false;
  _speechStartedAt = 0;
  _lastVoiceActivityAt = 0;
  _recordingStartedAt = Date.now();
  _vadAvailable = false;
}

function _markVoiceActivity() {
  const now = Date.now();
  if (!_speechStartedAt) _speechStartedAt = now;
  _lastVoiceActivityAt = now;
  if (now - _speechStartedAt >= VAD_MIN_SPEECH_MS) {
    _speechDetected = true;
  }
  if (_loopActive) {
    _loopInstructionDeadline = now + (_sttLoopIdleTimeoutSeconds * 1000);
  }
}

function _stopVoiceActivityDetection() {
  if (_voiceActivityTimer) {
    clearInterval(_voiceActivityTimer);
    _voiceActivityTimer = null;
  }
  try { _audioSource?.disconnect?.(); } catch (_) {}
  try { _audioAnalyser?.disconnect?.(); } catch (_) {}
  try { _audioContext?.close?.(); } catch (_) {}
  _audioSource = null;
  _audioAnalyser = null;
  _audioContext = null;
}

function _startVoiceActivityDetection(stream) {
  _stopVoiceActivityDetection();
  try {
    const AudioCtx = window.AudioContext || window.webkitAudioContext;
    if (!AudioCtx) return;
    _audioContext = new AudioCtx();
    _audioAnalyser = _audioContext.createAnalyser();
    _audioAnalyser.fftSize = 512;
    _audioSource = _audioContext.createMediaStreamSource(stream);
    _audioSource.connect(_audioAnalyser);
    const samples = new Uint8Array(_audioAnalyser.fftSize);
    _vadAvailable = true;
    _voiceActivityTimer = setInterval(() => {
      if (!_audioAnalyser || !isRecording) return;
      _audioAnalyser.getByteTimeDomainData(samples);
      let sum = 0;
      for (let i = 0; i < samples.length; i++) {
        const centered = (samples[i] - 128) / 128;
        sum += centered * centered;
      }
      const rms = Math.sqrt(sum / samples.length);
      if (rms >= VAD_RMS_THRESHOLD) _markVoiceActivity();
    }, 100);
  } catch (e) {
    _vadAvailable = false;
    console.warn('Voice activity detection unavailable:', e);
  }
}

function _scheduleLoopAutoStop() {
  if (!_loopActive || !isRecording) return;
  if (_loopAutoStopTimer) clearTimeout(_loopAutoStopTimer);

  const remaining = _loopRemainingMs();
  if (remaining <= 0) {
    stopConversationLoop('idle-timeout');
    _stopRecordingInternal('loop-timeout');
    return;
  }

  // Do not auto-submit before the user speaks. While no speech is detected,
  // keep listening until the idle deadline ends the loop.
  if (!_speechDetected && !_browserSpeechDetected) {
    _loopAutoStopTimer = setTimeout(_scheduleLoopAutoStop, Math.min(250, remaining));
    return;
  }

  const lastSpeechAt = Math.max(_lastVoiceActivityAt || 0, _recordingStartedAt || 0);
  const silenceForMs = Date.now() - lastSpeechAt;
  const silenceNeededMs = _sttLoopSubmitSeconds * 1000;
  if (silenceForMs >= silenceNeededMs && Date.now() - _recordingStartedAt >= MIN_RECORDING_MS) {
    _loopAutoStopTimer = null;
    _stopRecordingInternal('loop-auto');
    return;
  }

  const nextCheck = Math.max(100, Math.min(250, silenceNeededMs - silenceForMs, remaining));
  _loopAutoStopTimer = setTimeout(_scheduleLoopAutoStop, nextCheck);
}

function _submitCurrentTranscription(expectedText) {
  const input = document.getElementById('message');
  const text = _normalizeTranscript(input?.value || '');
  const expected = _normalizeTranscript(expectedText || text);
  if (!text || !expected || text !== expected) return false;

  const now = Date.now();
  if (text === _lastAutoSubmittedText && now - _lastAutoSubmittedAt < DUPLICATE_SUBMIT_WINDOW_MS) {
    return false;
  }

  const sendBtn = document.querySelector('.send-btn');
  if (!sendBtn || sendBtn.dataset.mode === 'streaming' || sendBtn.dataset.mode === 'recording') return false;

  _lastAutoSubmittedText = text;
  _lastAutoSubmittedAt = now;
  _loopWaitingForAssistant = true;
  _lastSendButtonMode = sendBtn.dataset.mode || '';
  setTimeout(() => sendBtn.click(), 0);
  return true;
}

function _clearMessageInputForLoop() {
  const input = document.getElementById('message');
  if (!input) return;
  if (input.value) {
    input.value = '';
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }
}

function _beginLoopListening() {
  if (!_loopActive || isRecording || _loopWaitingForAssistant) return;
  if (!_conversationLoopAvailable()) {
    stopConversationLoop('silent');
    return;
  }
  const sendBtn = document.querySelector('.send-btn');
  if (sendBtn && (sendBtn.dataset.mode === 'streaming' || sendBtn.dataset.mode === 'recording')) return;
  _clearMessageInputForLoop();
  _loopInstructionDeadline = Date.now() + (_sttLoopIdleTimeoutSeconds * 1000);
  startRecording(null, window.uiModule?.showToast, window.uiModule?.showError, { fromConversationLoop: true });
}

function _scheduleLoopRestartAfterAssistant() {
  if (!_loopActive) return;
  if (_loopRestartTimer) clearTimeout(_loopRestartTimer);
  _loopRestartTimer = setTimeout(() => {
    _loopRestartTimer = null;
    _loopWaitingForAssistant = false;
    _beginLoopListening();
  }, 250);
}

function _watchSendButtonForLoop() {
  if (_sendButtonObserver) return;
  const attach = () => {
    const sendBtn = document.querySelector('.send-btn');
    if (!sendBtn) return false;
    _lastSendButtonMode = sendBtn.dataset.mode || '';
    _sendButtonObserver = new MutationObserver(() => {
      const mode = sendBtn.dataset.mode || '';
      const wasStreaming = _lastSendButtonMode === 'streaming';
      _lastSendButtonMode = mode;
      if (_loopWaitingForAssistant && wasStreaming && mode !== 'streaming') {
        _scheduleLoopRestartAfterAssistant();
      }
    });
    _sendButtonObserver.observe(sendBtn, { attributes: true, attributeFilter: ['data-mode'] });
    return true;
  };
  if (attach()) return;
  const bodyObserver = new MutationObserver(() => {
    if (attach()) bodyObserver.disconnect();
  });
  if (document.body) bodyObserver.observe(document.body, { childList: true, subtree: true });
}

/**
 * Fetch current STT provider from server settings
 */
async function refreshSttProvider() {
  try {
    const res = await fetch('/api/stt/stats', { credentials: 'same-origin' });
    if (res.ok) {
      const stats = await res.json();
      _sttProvider = stats.provider || 'disabled';
      _sttLanguage = stats.language || '';
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
    }
  } catch (e) {
    console.warn('Failed to fetch STT stats:', e);
  }
  try {
    const settingsRes = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    if (settingsRes.ok) _applyLoopSettings(await settingsRes.json());
  } catch (_) {}
}

/**
 * Format seconds as MM:SS
 */
function formatTime(seconds) {
  const mins = Math.floor(seconds / 60).toString().padStart(2, '0');
  const secs = (seconds % 60).toString().padStart(2, '0');
  return `${mins}:${secs}`;
}

/**
 * Reset UI state after recording ends
 */
function _resetRecordingUI() {
  isRecording = false;
  _stopVoiceActivityDetection();
  if (recordingInterval) {
    clearInterval(recordingInterval);
    recordingInterval = null;
  }
  if (_loopAutoStopTimer) {
    clearTimeout(_loopAutoStopTimer);
    _loopAutoStopTimer = null;
  }
  const sendBtn = document.querySelector('.send-btn');
  if (sendBtn) {
    sendBtn.classList.remove('recording');
    sendBtn.dataset.mode = '';
  }
  if (window._updateSendBtnIcon) {
    setTimeout(window._updateSendBtnIcon, 50);
  }
}

/**
 * Start browser speech recognition alongside recording
 */
function startBrowserSTT(recordingId) {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return;

  _browserTranscript = '';
  _browserSpeechDetected = false;
  _recognition = new SpeechRecognition();
  _recognition.continuous = true;
  _recognition.interimResults = false;
  _recognition.lang = _sttLanguage || '';

  _recognition.onresult = (event) => {
    if (recordingId !== _activeRecordingId) return;
    for (let i = event.resultIndex; i < event.results.length; i++) {
      if (event.results[i].isFinal) {
        const part = _normalizeTranscript(event.results[i][0].transcript || '');
        if (part) {
          _browserTranscript = _normalizeTranscript(_browserTranscript + ' ' + part);
          _browserSpeechDetected = true;
          _markVoiceActivity();
          if (_loopActive) _scheduleLoopAutoStop();
        }
      }
    }
  };

  _recognition.onerror = (e) => {
    console.warn('Browser STT error:', e.error);
  };

  try { _recognition.start(); } catch (e) { console.warn('Browser STT start failed:', e); }
}

function stopBrowserSTT() {
  const transcript = _normalizeTranscript(_browserTranscript);
  if (_recognition) {
    try { _recognition.stop(); } catch (e) { /* ignore */ }
    _recognition = null;
  }
  _browserTranscript = '';
  return transcript;
}

/**
 * Send audio to server for transcription
 */
async function transcribeOnServer(audioBlob) {
  const formData = new FormData();
  formData.append('file', audioBlob, 'audio.webm');

  const res = await fetch('/api/stt/transcribe', {
    method: 'POST',
    credentials: 'same-origin',
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail?.message || 'Transcription failed');
  }

  const data = await res.json();
  return _normalizeTranscript(data.text || '');
}

/**
 * Insert transcribed text into the chat input
 */
function insertTranscription(text, showToast, opts = {}) {
  const cleanText = _normalizeTranscript(text);
  if (!cleanText) return false;
  const input = document.getElementById('message');
  if (!input) return false;

  if (opts.replaceExisting) {
    input.value = cleanText;
  } else {
    const existing = input.value.trim();
    input.value = existing ? existing + ' ' + cleanText : cleanText;
  }

  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();

  if (showToast) showToast('Transcribed');
  if (opts.autoSubmit) return _submitCurrentTranscription(cleanText);
  return true;
}

function _escStt(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _browserSttSupported() {
  return typeof window !== 'undefined' && !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

function mountSttSettingsUi() {
  const defaultMsg = document.getElementById('set-defaultChatMsg');
  if (!defaultMsg) return false;

  const defaultCard = defaultMsg.closest('.admin-card');
  if (!defaultCard || !defaultCard.parentNode) return false;

  let card = document.getElementById('set-sttSettingsCard');
  if (!card) {
    card = document.createElement('div');
    card.className = 'admin-card';
    card.id = 'set-sttSettingsCard';
    card.innerHTML = `
      <h2 style="display:flex;align-items:center;gap:6px;">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:5px;opacity:0.6;flex-shrink:0"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
        Speech to Text
        <span style="flex:1"></span>
        <label class="admin-switch" title="Transcribe microphone recordings into the chat input"><input type="checkbox" id="set-sttEnabledToggle" checked><span class="admin-slider"></span></label>
      </h2>
      <div class="admin-toggle-sub" style="margin-bottom:8px">Configure microphone transcription. Browser mode is enabled by default and falls back to attaching audio if speech recognition is unavailable.</div>
      <div id="set-sttConfigWrap" style="display:flex;flex-direction:column;gap:0.5rem;">
        <div style="display:flex;align-items:center;gap:0.75rem;">
          <label class="settings-label">Provider</label>
          <select id="set-sttProviderSelect" class="settings-select">
            <option value="browser">Browser (built-in)</option>
            <option value="local">Local (Whisper)</option>
            <option value="disabled">Disabled</option>
          </select>
        </div>
        <div id="set-sttModelRow" style="display:flex;align-items:center;gap:0.75rem;">
          <label class="settings-label">Model</label>
          <select id="set-sttModelSelect" class="settings-select">
            <option value="tiny">tiny (fastest)</option>
            <option value="base" selected>base (default)</option>
            <option value="small">small</option>
            <option value="medium">medium</option>
            <option value="large-v3">large-v3</option>
          </select>
          <input id="set-sttModelInput" type="text" placeholder="whisper-1" style="flex:1;padding:5px;display:none;">
        </div>
        <div id="set-sttLangRow" style="display:flex;align-items:center;gap:0.75rem;">
          <label class="settings-label">Language</label>
          <input id="set-sttLangInput" type="text" placeholder="Auto-detect or ISO code, e.g. en" class="settings-select" style="flex:1;">
        </div>
        <div class="settings-row">
          <label class="settings-label">Conversation loop</label>
          <div style="flex:1;display:flex;align-items:center;gap:8px;justify-content:space-between;">
            <span class="admin-toggle-sub" style="margin:0;">Wait for voice activity, auto-submit after silence, then listen again after each final response.</span>
            <label class="admin-switch" title="Keep a hands-free voice conversation going until silence timeout or manual stop">
              <input type="checkbox" id="set-sttConversationLoopToggle">
              <span class="admin-slider"></span>
            </label>
          </div>
        </div>
        <div id="set-sttLoopTimingRows" style="display:flex;flex-direction:column;gap:0.5rem;">
          <div class="settings-row">
            <label class="settings-label">Submit after silence</label>
            <input id="set-sttLoopSubmitSeconds" type="number" min="1" max="60" step="0.5" class="settings-select" style="width:120px;flex:0 0 auto;margin-left:auto;" value="3">
            <span class="admin-toggle-sub" style="margin:0 0 0 6px;">seconds</span>
          </div>
          <div class="settings-row">
            <label class="settings-label">Stop listening after</label>
            <input id="set-sttLoopIdleTimeoutSeconds" type="number" min="1" max="300" step="0.5" class="settings-select" style="width:120px;flex:0 0 auto;margin-left:auto;" value="5">
            <span class="admin-toggle-sub" style="margin:0 0 0 6px;">seconds idle</span>
          </div>
        </div>
        <div id="set-sttSettingsMsg" style="font-size:11px;color:color-mix(in srgb, var(--fg) 45%, transparent);"></div>
      </div>`;
  }

  const ttsToggle = document.getElementById('set-ttsEnabledToggle');
  const ttsCard = ttsToggle ? ttsToggle.closest('.admin-card') : null;
  const anchor = ttsCard || defaultCard;
  if (anchor && anchor.parentNode && anchor.nextSibling !== card) {
    anchor.parentNode.insertBefore(card, anchor.nextSibling);
  }

  bindSttSettingsUi(card).catch(function(e) { console.warn('Failed to bind STT settings UI', e); });
  return true;
}

async function bindSttSettingsUi(card) {
  const provSel = document.getElementById('set-sttProviderSelect');
  const modelSelect = document.getElementById('set-sttModelSelect');
  const modelInput = document.getElementById('set-sttModelInput');
  const modelRow = document.getElementById('set-sttModelRow');
  const langRow = document.getElementById('set-sttLangRow');
  const langInput = document.getElementById('set-sttLangInput');
  const sttMsg = document.getElementById('set-sttSettingsMsg');
  const sttEnabledToggle = document.getElementById('set-sttEnabledToggle');
  const sttConfigWrap = document.getElementById('set-sttConfigWrap');
  const loopToggle = document.getElementById('set-sttConversationLoopToggle');
  const loopTimingRows = document.getElementById('set-sttLoopTimingRows');
  const loopSubmitInput = document.getElementById('set-sttLoopSubmitSeconds');
  const loopIdleInput = document.getElementById('set-sttLoopIdleTimeoutSeconds');
  if (!provSel || provSel.dataset.boundVoiceRecorderStt === '1') return;
  provSel.dataset.boundVoiceRecorderStt = '1';

  function isEndpoint() { return provSel.value.startsWith('endpoint:'); }
  function getModel() { return isEndpoint() ? modelInput.value.trim() : modelSelect.value; }
  function effectiveProvider() { return sttEnabledToggle && !sttEnabledToggle.checked ? 'disabled' : provSel.value; }

  function setMsg(text, isError) {
    if (!sttMsg) return;
    sttMsg.textContent = text || '';
    sttMsg.style.color = isError ? 'var(--red, #e55)' : 'var(--fg)';
  }

  function readLoopSeconds(input, fallback, min, max) {
    return _clampNumber(input ? input.value : fallback, fallback, min, max);
  }

  function syncLoopTimingDisabled() {
    const off = !loopToggle || !loopToggle.checked;
    if (loopTimingRows) loopTimingRows.style.opacity = off ? '0.5' : '';
    if (loopTimingRows) loopTimingRows.style.pointerEvents = off ? 'none' : '';
  }

  function updateVisibility() {
    const prov = provSel.value;
    const showModel = prov === 'local' || prov.startsWith('endpoint:');
    const showLang = prov !== 'disabled';
    if (modelRow) modelRow.style.display = showModel ? 'flex' : 'none';
    if (langRow) langRow.style.display = showLang ? 'flex' : 'none';
    if (isEndpoint()) {
      modelSelect.style.display = 'none';
      modelInput.style.display = '';
    } else {
      modelSelect.style.display = '';
      modelInput.style.display = 'none';
    }

    const off = sttEnabledToggle && !sttEnabledToggle.checked;
    if (card) card.style.opacity = off ? '0.45' : '';
    if (sttConfigWrap) sttConfigWrap.style.pointerEvents = off ? 'none' : '';
    syncLoopTimingDisabled();

    if (!off && prov === 'browser' && !_browserSttSupported()) {
      setMsg('Browser speech recognition is not available here; recordings will fall back to audio attachments.', true);
    }
  }

  async function loadEndpoints() {
    try {
      const epRes = await fetch('/api/model-endpoints', { credentials: 'same-origin' });
      const endpoints = await epRes.json();
      if (!Array.isArray(endpoints)) return;
      endpoints.forEach(function(ep) {
        if (!ep.is_enabled) return;
        const value = 'endpoint:' + ep.id;
        if (Array.from(provSel.options).some(function(o) { return o.value === value; })) return;
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = (ep.name || ep.id) + ' (API)';
        provSel.appendChild(opt);
      });
    } catch (e) {
      console.warn('Failed to load endpoints for STT', e);
    }
  }

  async function loadSettings() {
    try {
      const settingsRes = await fetch('/api/auth/settings', { credentials: 'same-origin' });
      const settings = await settingsRes.json();
      const provider = settings.stt_provider || 'browser';
      if (Array.from(provSel.options).some(function(o) { return o.value === provider; })) provSel.value = provider;
      else if (provider.startsWith('endpoint:')) {
        const opt = document.createElement('option');
        opt.value = provider;
        opt.textContent = provider.replace('endpoint:', 'Endpoint ') + ' (API)';
        provSel.appendChild(opt);
        provSel.value = provider;
      }
      if (settings.stt_model) {
        modelSelect.value = settings.stt_model;
        modelInput.value = settings.stt_model;
      }
      if (settings.stt_language) langInput.value = settings.stt_language;
      if (sttEnabledToggle) sttEnabledToggle.checked = settings.stt_enabled !== false;
      if (loopToggle) loopToggle.checked = settings.stt_conversation_loop === true;
      if (loopSubmitInput) loopSubmitInput.value = _clampNumber(settings.stt_loop_submit_seconds, 3, 1, 60);
      if (loopIdleInput) loopIdleInput.value = _clampNumber(settings.stt_loop_idle_timeout_seconds, 5, 1, 300);
      _applyLoopSettings(settings);
      _sttProvider = effectiveProvider();
      _sttLanguage = langInput.value.trim();
    } catch (e) {
      console.warn('Failed to load STT settings', e);
    }
  }

  async function refreshStatus() {
    try {
      const statsRes = await fetch('/api/stt/stats', { credentials: 'same-origin' });
      if (!statsRes.ok) return;
      const stats = await statsRes.json();
      _sttProvider = stats.provider || 'disabled';
      _sttLanguage = stats.language || '';
      const label = stats.provider === 'browser' ? 'Browser' : stats.provider === 'local' ? 'Local Whisper' : stats.provider === 'disabled' ? 'Disabled' : stats.provider;
      if (stats.provider === 'browser' && !_browserSttSupported()) {
        setMsg('Active: Browser (unsupported here; audio attachment fallback)', true);
      } else {
        const loopText = _sttConversationLoop && stats.provider !== 'disabled' ? ' · loop on' : '';
        setMsg('Active: ' + label + (stats.model ? ' · ' + stats.model : '') + loopText, stats.provider === 'disabled');
      }
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
    } catch (_) {}
  }

  async function saveSTT() {
    try {
      const enabled = sttEnabledToggle ? sttEnabledToggle.checked : true;
      const submitSeconds = readLoopSeconds(loopSubmitInput, 3, 1, 60);
      const idleSeconds = readLoopSeconds(loopIdleInput, 5, 1, 300);
      if (loopSubmitInput) loopSubmitInput.value = submitSeconds;
      if (loopIdleInput) loopIdleInput.value = idleSeconds;
      const payload = {
        stt_enabled: enabled,
        stt_provider: provSel.value,
        stt_model: getModel() || 'base',
        stt_language: langInput.value.trim(),
        stt_conversation_loop: loopToggle ? loopToggle.checked : false,
        stt_loop_submit_seconds: submitSeconds,
        stt_loop_idle_timeout_seconds: idleSeconds
      };
      const res = await fetch('/api/auth/settings', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error('Settings save failed');
      _applyLoopSettings(payload);
      if (!_sttConversationLoop) stopConversationLoop('silent');
      _sttProvider = enabled ? provSel.value : 'disabled';
      _sttLanguage = payload.stt_language;
      if (window.voiceRecorderModule) window.voiceRecorderModule._sttProvider = _sttProvider;
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
      setMsg('Saved', false);
      setTimeout(refreshStatus, 500);
    } catch (e) {
      setMsg('Failed to save STT settings', true);
    }
  }

  await loadEndpoints();
  await loadSettings();
  updateVisibility();
  refreshStatus();

  provSel.addEventListener('change', function() { updateVisibility(); saveSTT(); });
  modelSelect.addEventListener('change', saveSTT);
  modelInput.addEventListener('change', saveSTT);
  langInput.addEventListener('change', saveSTT);
  if (loopToggle) loopToggle.addEventListener('change', function() { syncLoopTimingDisabled(); saveSTT(); });
  if (loopSubmitInput) loopSubmitInput.addEventListener('change', saveSTT);
  if (loopIdleInput) loopIdleInput.addEventListener('change', saveSTT);
  if (sttEnabledToggle) sttEnabledToggle.addEventListener('change', function() { updateVisibility(); saveSTT(); });
}

function initSttSettingsUi() {
  if (typeof document === 'undefined') return;
  if (mountSttSettingsUi()) return;
  const observer = new MutationObserver(function() {
    if (mountSttSettingsUi()) observer.disconnect();
  });
  const start = function() {
    if (document.body) observer.observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
}

/**
 * Start voice recording
 */
export function startRecording(onFileCreated, showToast, showError, opts = {}) {
  const fromConversationLoop = opts && opts.fromConversationLoop === true;
  if (!fromConversationLoop && _conversationLoopAvailable()) {
    _loopActive = true;
    _loopWaitingForAssistant = false;
    _clearMessageInputForLoop();
    _loopInstructionDeadline = Date.now() + (_sttLoopIdleTimeoutSeconds * 1000);
    if (showToast) showToast('Conversation loop started');
  }

  if (!window.isSecureContext) {
    if (showError) showError('Microphone requires HTTPS. Use a reverse proxy with SSL or access via localhost.');
    stopConversationLoop('silent');
    _resetRecordingUI();
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    if (showError) showError('Microphone not supported in this browser.');
    stopConversationLoop('silent');
    _resetRecordingUI();
    return;
  }

  audioChunks = [];
  _recordingStopReason = 'manual';
  const recordingId = ++_activeRecordingId;
  _resetSpeechState();

  navigator.mediaDevices.getUserMedia({ audio: true })
    .then(stream => {
      mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      _startVoiceActivityDetection(stream);

      mediaRecorder.ondataavailable = event => {
        if (event.data.size > 0) {
          audioChunks.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach(track => track.stop());
        _stopVoiceActivityDetection();

        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
        const provider = _sttProvider;
        const stopReason = _recordingStopReason;
        const autoSubmit = _loopActive && stopReason === 'loop-auto';
        const loopRecording = autoSubmit || stopReason === 'loop-timeout';
        const hadVoiceActivity = _speechDetected || _browserSpeechDetected || !_vadAvailable;
        let inserted = false;

        if (provider === 'browser') {
          const transcript = stopBrowserSTT();
          const fresh = _hasFreshTranscript(transcript) && (_browserSpeechDetected || _speechDetected || !_vadAvailable);
          if (fresh) {
            inserted = insertTranscription(transcript, showToast, { autoSubmit, replaceExisting: autoSubmit });
          } else {
            if (showToast) showToast('No speech detected');
            if (!loopRecording && !autoSubmit) {
              const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
              if (onFileCreated) onFileCreated(audioFile);
            }
          }
        } else if (provider === 'local' || provider.startsWith('endpoint:')) {
          if (autoSubmit && !hadVoiceActivity) {
            if (showToast) showToast('No speech detected');
          } else {
            if (showToast) showToast('Transcribing...', 5000);
            try {
              const transcript = await transcribeOnServer(audioBlob);
              const fresh = _hasFreshTranscript(transcript) && (!autoSubmit || hadVoiceActivity);
              if (fresh) {
                inserted = insertTranscription(transcript, showToast, { autoSubmit, replaceExisting: autoSubmit });
              } else {
                if (showToast) showToast('No speech detected');
              }
            } catch (e) {
              console.error('STT transcription error:', e);
              if (showError) showError('Transcription failed: ' + e.message);
              if (!loopRecording && !autoSubmit) {
                const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
                if (onFileCreated) onFileCreated(audioFile);
              }
            }
          }
        } else {
          const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
          if (onFileCreated) onFileCreated(audioFile);
        }

        if (autoSubmit && _loopActive && !inserted) {
          if (_loopRemainingMs() > 250) {
            _loopRestartTimer = setTimeout(() => _beginLoopListening(), 100);
          } else {
            stopConversationLoop('idle-timeout');
          }
        }

        _resetRecordingUI();
      };

      mediaRecorder.start();
      isRecording = true;
      recordingStartTime = new Date();

      if (_sttProvider === 'browser') {
        startBrowserSTT(recordingId);
      }

      if (_loopActive) _scheduleLoopAutoStop();

      if (showToast) {
        const loopHint = _loopActive ? ` · waiting for speech` : '';
        showToast('Recording...' + loopHint);
      }
    })
    .catch(error => {
      console.error('Microphone access error:', error);
      if (showError) {
        if (error.name === 'NotAllowedError') {
          showError('Microphone access denied. Check browser permissions.');
        } else if (error.name === 'NotFoundError') {
          showError('No microphone found.');
        } else {
          showError('Microphone error: ' + error.message);
        }
      }
      stopConversationLoop('silent');
      _resetRecordingUI();
    });
}

function _stopRecordingInternal(reason) {
  _recordingStopReason = reason || 'manual';
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
  } else {
    _resetRecordingUI();
  }
}

/**
 * Stop voice recording
 */
export function stopRecording() {
  if (_loopActive) stopConversationLoop('manual');
  _stopRecordingInternal('manual');
}

/**
 * Check if currently recording
 */
export function getIsRecording() {
  return isRecording;
}

/**
 * Initialize recording state
 */
export function init() {
  isRecording = false;
  refreshSttProvider();
  initSttSettingsUi();
  _watchSendButtonForLoop();
}

const voiceRecorderModule = {
  startRecording,
  stopRecording,
  stopConversationLoop,
  getIsRecording,
  init,
  refreshSttProvider,
  get _sttProvider() { return _sttProvider; },
  set _sttProvider(v) { _sttProvider = v; },
  get _sttConversationLoop() { return _sttConversationLoop; },
};

window.voiceRecorderModule = voiceRecorderModule;
initSttSettingsUi();
_watchSendButtonForLoop();

export default voiceRecorderModule;
