// static/js/voiceRecorder.js

/**
 * Voice recording with optional Speech-to-Text transcription.
 *
 * STT providers:
 *   "disabled"       — record audio as a file attachment
 *   "browser"        — use the Web Speech API for real-time transcription
 *   "local"          — send recording to /api/stt/transcribe (Whisper)
 *   "endpoint:<id>"  — send recording to /api/stt/transcribe (API)
 */

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let recordingInterval = null;
let _recordingStopReason = 'manual';
let _activeRecordingId = 0;

// Browser STT state
let _recognition = null;
let _browserTranscript = '';
let _browserSpeechDetected = false;

// Voice activity detection state
let _audioContext = null;
let _audioAnalyser = null;
let _audioSource = null;
let _voiceActivityTimer = null;
let _vadAvailable = false;
let _speechDetected = false;
let _speechStartedAt = 0;
let _lastVoiceActivityAt = 0;
let _recordingStartedAt = 0;

const VAD_RMS_THRESHOLD = 0.018;
const VAD_MIN_SPEECH_MS = 250;
const MIN_RECORDING_MS = 400;
const MIN_TRANSCRIPT_CHARS = 2;
const DUPLICATE_SUBMIT_WINDOW_MS = 15000;

// Cached STT settings
let _sttProvider = 'disabled';
let _sttLanguage = '';
let _sttConversationLoop = false;
let _sttLoopSubmitSeconds = 3;
let _sttLoopIdleTimeoutSeconds = 5;

// Conversation-loop state
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
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, parsed));
}

function _normalizeTranscript(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function _hasFreshTranscript(value) {
  return _normalizeTranscript(value).length >= MIN_TRANSCRIPT_CHARS;
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

function _startVoiceActivityDetection(stream) {
  _stopVoiceActivityDetection();
  try {
    const AudioContextConstructor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextConstructor) return;

    _audioContext = new AudioContextConstructor();
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
      for (let index = 0; index < samples.length; index += 1) {
        const centered = (samples[index] - 128) / 128;
        sum += centered * centered;
      }
      const rms = Math.sqrt(sum / samples.length);
      if (rms >= VAD_RMS_THRESHOLD) _markVoiceActivity();
    }, 100);
  } catch (error) {
    _vadAvailable = false;
    console.warn('Voice activity detection unavailable:', error);
  }
}

function _releaseRecordingUi() {
  isRecording = false;
  if (recordingInterval) {
    clearInterval(recordingInterval);
    recordingInterval = null;
  }
  if (_loopAutoStopTimer) {
    clearTimeout(_loopAutoStopTimer);
    _loopAutoStopTimer = null;
  }

  const sendButton = document.querySelector('.send-btn');
  if (sendButton) {
    sendButton.classList.remove('recording');
    sendButton.dataset.mode = '';
  }

  if (window._updateSendBtnIcon) {
    setTimeout(window._updateSendBtnIcon, 0);
  }
}

function _resetRecordingUI() {
  _stopVoiceActivityDetection();
  _releaseRecordingUi();
}

function stopConversationLoop(reason) {
  const wasActive = _loopActive || _loopWaitingForAssistant;
  _loopActive = false;
  _loopWaitingForAssistant = false;
  _loopInstructionDeadline = 0;
  _clearLoopTimers();

  if (wasActive && reason !== 'silent') {
    const message = reason === 'idle-timeout'
      ? 'Conversation loop ended — no instruction heard.'
      : 'Conversation loop stopped.';
    try { window.uiModule?.showToast?.(message); } catch (_) {}
  }
}

function _conversationLoopAvailable() {
  return _sttConversationLoop && _sttProvider !== 'disabled';
}

function _loopRemainingMs() {
  if (!_loopInstructionDeadline) return _sttLoopIdleTimeoutSeconds * 1000;
  return Math.max(0, _loopInstructionDeadline - Date.now());
}

function _clearMessageInputForLoop() {
  const input = document.getElementById('message');
  if (!input || !input.value) return;
  input.value = '';
  input.dispatchEvent(new Event('input', { bubbles: true }));
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

  const sendButton = document.querySelector('.send-btn');
  if (!sendButton || sendButton.disabled || sendButton.dataset.mode === 'streaming') {
    return false;
  }

  // The recorder has been released before this function is called. Clearing a
  // stale visual mode here makes the order explicit and preserves the send
  // button's normal click handler.
  sendButton.classList.remove('recording');
  sendButton.dataset.mode = '';

  _lastAutoSubmittedText = text;
  _lastAutoSubmittedAt = now;
  _loopWaitingForAssistant = true;
  _lastSendButtonMode = '';

  setTimeout(() => {
    if (_loopWaitingForAssistant && !isRecording) sendButton.click();
  }, 0);
  return true;
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

  // Never turn a timer into an instruction. The loop remains in the listening
  // state until the user speaks or the no-speech deadline expires.
  if (!_speechDetected && !_browserSpeechDetected) {
    _loopAutoStopTimer = setTimeout(_scheduleLoopAutoStop, Math.min(250, remaining));
    return;
  }

  const lastSpeechAt = Math.max(_lastVoiceActivityAt || 0, _recordingStartedAt || 0);
  const silenceForMs = Date.now() - lastSpeechAt;
  const silenceNeededMs = _sttLoopSubmitSeconds * 1000;
  const recordedLongEnough = Date.now() - _recordingStartedAt >= MIN_RECORDING_MS;

  if (recordedLongEnough && silenceForMs >= silenceNeededMs) {
    _stopRecordingInternal('loop-auto');
    return;
  }

  const nextCheck = Math.max(100, Math.min(250, silenceNeededMs - silenceForMs, remaining));
  _loopAutoStopTimer = setTimeout(_scheduleLoopAutoStop, nextCheck);
}

function _beginLoopListening() {
  if (!_loopActive || isRecording || _loopWaitingForAssistant) return;
  if (!_conversationLoopAvailable()) {
    stopConversationLoop('silent');
    return;
  }

  const sendButton = document.querySelector('.send-btn');
  if (sendButton && (sendButton.dataset.mode === 'streaming' || sendButton.dataset.mode === 'recording')) return;

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
    const sendButton = document.querySelector('.send-btn');
    if (!sendButton) return false;

    _lastSendButtonMode = sendButton.dataset.mode || '';
    _sendButtonObserver = new MutationObserver(() => {
      const mode = sendButton.dataset.mode || '';
      const wasStreaming = _lastSendButtonMode === 'streaming';
      _lastSendButtonMode = mode;
      if (_loopWaitingForAssistant && wasStreaming && mode !== 'streaming') {
        _scheduleLoopRestartAfterAssistant();
      }
    });
    _sendButtonObserver.observe(sendButton, { attributes: true, attributeFilter: ['data-mode'] });
    return true;
  };

  if (attach()) return;
  const bodyObserver = new MutationObserver(() => {
    if (attach()) bodyObserver.disconnect();
  });
  if (document.body) bodyObserver.observe(document.body, { childList: true, subtree: true });
}

async function refreshSttProvider() {
  try {
    const response = await fetch('/api/stt/stats', { credentials: 'same-origin' });
    if (response.ok) {
      const stats = await response.json();
      _sttProvider = stats.provider || 'disabled';
      _sttLanguage = stats.language || '';
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
    }
  } catch (error) {
    console.warn('Failed to fetch STT stats:', error);
  }

  try {
    const response = await fetch('/api/auth/settings', { credentials: 'same-origin' });
    if (response.ok) _applyLoopSettings(await response.json());
  } catch (_) {}
}

function _browserSttSupported() {
  return typeof window !== 'undefined' && !!(window.SpeechRecognition || window.webkitSpeechRecognition);
}

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
    for (let index = event.resultIndex; index < event.results.length; index += 1) {
      if (!event.results[index].isFinal) continue;
      const part = _normalizeTranscript(event.results[index][0].transcript);
      if (!part) continue;
      _browserTranscript = _normalizeTranscript(`${_browserTranscript} ${part}`);
      _browserSpeechDetected = true;
      _markVoiceActivity();
      if (_loopActive) _scheduleLoopAutoStop();
    }
  };

  _recognition.onerror = (event) => {
    console.warn('Browser STT error:', event.error);
  };

  try { _recognition.start(); } catch (error) { console.warn('Browser STT start failed:', error); }
}

function stopBrowserSTT() {
  const transcript = _normalizeTranscript(_browserTranscript);
  if (_recognition) {
    try { _recognition.stop(); } catch (_) {}
    _recognition = null;
  }
  _browserTranscript = '';
  return transcript;
}

async function transcribeOnServer(audioBlob) {
  const formData = new FormData();
  formData.append('file', audioBlob, 'audio.webm');

  const response = await fetch('/api/stt/transcribe', {
    method: 'POST',
    credentials: 'same-origin',
    body: formData,
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail?.message || 'Transcription failed');
  }

  const data = await response.json();
  return _normalizeTranscript(data.text || '');
}

function insertTranscription(text, showToast, options = {}) {
  const transcript = _normalizeTranscript(text);
  if (!_hasFreshTranscript(transcript)) return false;

  const input = document.getElementById('message');
  if (!input) return false;

  if (options.replaceExisting) {
    input.value = transcript;
  } else {
    const existing = _normalizeTranscript(input.value);
    input.value = existing ? `${existing} ${transcript}` : transcript;
  }

  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
  if (showToast) showToast('Transcribed');
  return true;
}

function mountSttSettingsUi() {
  const defaultMessage = document.getElementById('set-defaultChatMsg');
  if (!defaultMessage) return false;

  const defaultCard = defaultMessage.closest('.admin-card');
  if (!defaultCard || !defaultCard.parentNode) return false;

  let card = document.getElementById('set-sttSettingsCard');
  if (!card) {
    card = document.createElement('div');
    card.className = 'admin-card';
    card.id = 'set-sttSettingsCard';
    card.innerHTML = `
      <h2 style="display:flex;align-items:center;gap:6px;">
        Speech to Text
        <span style="flex:1"></span>
        <label class="admin-switch" title="Transcribe microphone recordings into the chat input"><input type="checkbox" id="set-sttEnabledToggle" checked><span class="admin-slider"></span></label>
      </h2>
      <div class="admin-toggle-sub" style="margin-bottom:8px">Configure microphone transcription. Browser mode is enabled by default and falls back to attaching audio if speech recognition is unavailable.</div>
      <div id="set-sttConfigWrap" style="display:flex;flex-direction:column;gap:.5rem;">
        <div class="settings-row"><label class="settings-label">Provider</label><select id="set-sttProviderSelect" class="settings-select"><option value="browser">Browser (built-in)</option><option value="local">Local (Whisper)</option><option value="disabled">Disabled</option></select></div>
        <div id="set-sttModelRow" class="settings-row"><label class="settings-label">Model</label><select id="set-sttModelSelect" class="settings-select"><option value="tiny">tiny (fastest)</option><option value="base" selected>base (default)</option><option value="small">small</option><option value="medium">medium</option><option value="large-v3">large-v3</option></select><input id="set-sttModelInput" type="text" placeholder="whisper-1" style="flex:1;padding:5px;display:none;"></div>
        <div id="set-sttLangRow" class="settings-row"><label class="settings-label">Language</label><input id="set-sttLangInput" type="text" placeholder="Auto-detect or ISO code, e.g. en" class="settings-select" style="flex:1;"></div>
        <div class="settings-row"><label class="settings-label">Conversation loop</label><div style="flex:1;display:flex;align-items:center;gap:8px;justify-content:space-between;"><span class="admin-toggle-sub" style="margin:0;">Wait for speech, submit after silence, then listen again after each response.</span><label class="admin-switch"><input type="checkbox" id="set-sttConversationLoopToggle"><span class="admin-slider"></span></label></div></div>
        <div id="set-sttLoopTimingRows" style="display:flex;flex-direction:column;gap:.5rem;"><div class="settings-row"><label class="settings-label">Submit after silence</label><input id="set-sttLoopSubmitSeconds" type="number" min="1" max="60" step=".5" class="settings-select" style="width:120px;flex:0 0 auto;margin-left:auto;" value="3"><span class="admin-toggle-sub" style="margin:0 0 0 6px;">seconds</span></div><div class="settings-row"><label class="settings-label">Stop listening after</label><input id="set-sttLoopIdleTimeoutSeconds" type="number" min="1" max="300" step=".5" class="settings-select" style="width:120px;flex:0 0 auto;margin-left:auto;" value="5"><span class="admin-toggle-sub" style="margin:0 0 0 6px;">seconds idle</span></div></div>
        <div id="set-sttSettingsMsg" style="font-size:11px;color:color-mix(in srgb, var(--fg) 45%, transparent);"></div>
      </div>`;
  }

  const ttsToggle = document.getElementById('set-ttsEnabledToggle');
  const ttsCard = ttsToggle ? ttsToggle.closest('.admin-card') : null;
  const anchor = ttsCard || defaultCard;
  if (anchor.parentNode && anchor.nextSibling !== card) {
    anchor.parentNode.insertBefore(card, anchor.nextSibling);
  }

  bindSttSettingsUi(card).catch((error) => console.warn('Failed to bind STT settings UI', error));
  return true;
}

async function bindSttSettingsUi(card) {
  const providerSelect = document.getElementById('set-sttProviderSelect');
  const modelSelect = document.getElementById('set-sttModelSelect');
  const modelInput = document.getElementById('set-sttModelInput');
  const modelRow = document.getElementById('set-sttModelRow');
  const languageRow = document.getElementById('set-sttLangRow');
  const languageInput = document.getElementById('set-sttLangInput');
  const enabledToggle = document.getElementById('set-sttEnabledToggle');
  const configWrap = document.getElementById('set-sttConfigWrap');
  const loopToggle = document.getElementById('set-sttConversationLoopToggle');
  const loopTimingRows = document.getElementById('set-sttLoopTimingRows');
  const submitSecondsInput = document.getElementById('set-sttLoopSubmitSeconds');
  const idleSecondsInput = document.getElementById('set-sttLoopIdleTimeoutSeconds');
  const status = document.getElementById('set-sttSettingsMsg');

  if (!providerSelect || providerSelect.dataset.boundVoiceRecorderStt === '1') return;
  providerSelect.dataset.boundVoiceRecorderStt = '1';

  const isEndpoint = () => providerSelect.value.startsWith('endpoint:');
  const getModel = () => isEndpoint() ? modelInput.value.trim() : modelSelect.value;
  const effectiveProvider = () => enabledToggle && !enabledToggle.checked ? 'disabled' : providerSelect.value;
  const setStatus = (message, isError = false) => {
    if (!status) return;
    status.textContent = message || '';
    status.style.color = isError ? 'var(--red, #e55)' : 'var(--fg)';
  };

  const updateVisibility = () => {
    const provider = providerSelect.value;
    const showModel = provider === 'local' || provider.startsWith('endpoint:');
    const showLanguage = provider !== 'disabled';
    if (modelRow) modelRow.style.display = showModel ? 'flex' : 'none';
    if (languageRow) languageRow.style.display = showLanguage ? 'flex' : 'none';
    modelSelect.style.display = isEndpoint() ? 'none' : '';
    modelInput.style.display = isEndpoint() ? '' : 'none';

    const disabled = enabledToggle && !enabledToggle.checked;
    if (card) card.style.opacity = disabled ? '.45' : '';
    if (configWrap) configWrap.style.pointerEvents = disabled ? 'none' : '';
    const loopDisabled = !loopToggle || !loopToggle.checked;
    if (loopTimingRows) {
      loopTimingRows.style.opacity = loopDisabled ? '.5' : '';
      loopTimingRows.style.pointerEvents = loopDisabled ? 'none' : '';
    }

    if (!disabled && provider === 'browser' && !_browserSttSupported()) {
      setStatus('Browser speech recognition is not available here; recordings will fall back to audio attachments.', true);
    }
  };

  const loadEndpoints = async () => {
    try {
      const response = await fetch('/api/model-endpoints', { credentials: 'same-origin' });
      const endpoints = await response.json();
      if (!Array.isArray(endpoints)) return;
      endpoints.filter((endpoint) => endpoint.is_enabled).forEach((endpoint) => {
        const value = `endpoint:${endpoint.id}`;
        if (Array.from(providerSelect.options).some((option) => option.value === value)) return;
        const option = document.createElement('option');
        option.value = value;
        option.textContent = `${endpoint.name || endpoint.id} (API)`;
        providerSelect.appendChild(option);
      });
    } catch (error) {
      console.warn('Failed to load STT endpoints', error);
    }
  };

  const loadSettings = async () => {
    try {
      const response = await fetch('/api/auth/settings', { credentials: 'same-origin' });
      const settings = await response.json();
      const provider = settings.stt_provider || 'browser';
      if (!Array.from(providerSelect.options).some((option) => option.value === provider) && provider.startsWith('endpoint:')) {
        const option = document.createElement('option');
        option.value = provider;
        option.textContent = `${provider.replace('endpoint:', 'Endpoint ')} (API)`;
        providerSelect.appendChild(option);
      }
      if (Array.from(providerSelect.options).some((option) => option.value === provider)) providerSelect.value = provider;
      if (settings.stt_model) {
        modelSelect.value = settings.stt_model;
        modelInput.value = settings.stt_model;
      }
      languageInput.value = settings.stt_language || '';
      if (enabledToggle) enabledToggle.checked = settings.stt_enabled !== false;
      if (loopToggle) loopToggle.checked = settings.stt_conversation_loop === true;
      if (submitSecondsInput) submitSecondsInput.value = _clampNumber(settings.stt_loop_submit_seconds, 3, 1, 60);
      if (idleSecondsInput) idleSecondsInput.value = _clampNumber(settings.stt_loop_idle_timeout_seconds, 5, 1, 300);
      _applyLoopSettings(settings);
      _sttProvider = effectiveProvider();
      _sttLanguage = languageInput.value.trim();
    } catch (error) {
      console.warn('Failed to load STT settings', error);
    }
  };

  const saveSettings = async () => {
    try {
      const payload = {
        stt_enabled: enabledToggle ? enabledToggle.checked : true,
        stt_provider: providerSelect.value,
        stt_model: getModel() || 'base',
        stt_language: languageInput.value.trim(),
        stt_conversation_loop: loopToggle ? loopToggle.checked : false,
        stt_loop_submit_seconds: _clampNumber(submitSecondsInput?.value, 3, 1, 60),
        stt_loop_idle_timeout_seconds: _clampNumber(idleSecondsInput?.value, 5, 1, 300),
      };
      if (submitSecondsInput) submitSecondsInput.value = payload.stt_loop_submit_seconds;
      if (idleSecondsInput) idleSecondsInput.value = payload.stt_loop_idle_timeout_seconds;

      const response = await fetch('/api/auth/settings', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error('Settings save failed');

      _applyLoopSettings(payload);
      if (!_sttConversationLoop) stopConversationLoop('silent');
      _sttProvider = payload.stt_enabled ? payload.stt_provider : 'disabled';
      _sttLanguage = payload.stt_language;
      if (window.voiceRecorderModule) window.voiceRecorderModule._sttProvider = _sttProvider;
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
      setStatus('Saved');
    } catch (error) {
      setStatus('Failed to save STT settings', true);
    }
  };

  await loadEndpoints();
  await loadSettings();
  updateVisibility();

  providerSelect.addEventListener('change', () => { updateVisibility(); saveSettings(); });
  modelSelect.addEventListener('change', saveSettings);
  modelInput.addEventListener('change', saveSettings);
  languageInput.addEventListener('change', saveSettings);
  if (loopToggle) loopToggle.addEventListener('change', () => { updateVisibility(); saveSettings(); });
  if (submitSecondsInput) submitSecondsInput.addEventListener('change', saveSettings);
  if (idleSecondsInput) idleSecondsInput.addEventListener('change', saveSettings);
  if (enabledToggle) enabledToggle.addEventListener('change', () => { updateVisibility(); saveSettings(); });
}

function initSttSettingsUi() {
  if (typeof document === 'undefined') return;
  if (mountSttSettingsUi()) return;
  const observer = new MutationObserver(() => {
    if (mountSttSettingsUi()) observer.disconnect();
  });
  const start = () => {
    if (document.body) observer.observe(document.body, { childList: true, subtree: true });
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
}

function _continueLoopAfterNoSubmission() {
  if (!_loopActive) return;
  if (_loopRemainingMs() <= 250) {
    stopConversationLoop('idle-timeout');
    return;
  }
  _loopRestartTimer = setTimeout(_beginLoopListening, 100);
}

function _handleLoopTranscript(transcript, showToast, autoSubmit) {
  const inserted = insertTranscription(transcript, showToast, { replaceExisting: autoSubmit });
  if (!autoSubmit || !inserted) return { inserted, submitted: false };

  // onstop already called _releaseRecordingUi, so the click uses the normal
  // send flow instead of being rejected as an active microphone recording.
  const submitted = _submitCurrentTranscription(transcript);
  if (!submitted && showToast) showToast('Transcript ready — press Send to continue.');
  return { inserted, submitted };
}

export function startRecording(onFileCreated, showToast, showError, options = {}) {
  const fromConversationLoop = options.fromConversationLoop === true;
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
  if (!navigator.mediaDevices?.getUserMedia) {
    if (showError) showError('Microphone not supported in this browser.');
    stopConversationLoop('silent');
    _resetRecordingUI();
    return;
  }

  audioChunks = [];
  _recordingStopReason = 'manual';
  _resetSpeechState();
  const recordingId = ++_activeRecordingId;

  navigator.mediaDevices.getUserMedia({ audio: true })
    .then((stream) => {
      try {
        mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      } catch (_) {
        mediaRecorder = new MediaRecorder(stream);
      }

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunks.push(event.data);
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        _stopVoiceActivityDetection();

        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
        const provider = _sttProvider;
        const stopReason = _recordingStopReason;
        const autoSubmit = _loopActive && stopReason === 'loop-auto';
        const loopRecording = autoSubmit || stopReason === 'loop-timeout';
        const hadVoiceActivity = _speechDetected || _browserSpeechDetected || !_vadAvailable;

        // Critical ordering: clear the recorder's state before attempting the
        // normal send-button click. Previously the click was blocked because
        // the button still carried data-mode="recording".
        _releaseRecordingUi();

        let submitted = false;
        let inserted = false;

        if (provider === 'browser') {
          const transcript = stopBrowserSTT();
          const fresh = _hasFreshTranscript(transcript)
            && (_browserSpeechDetected || _speechDetected || !_vadAvailable);
          if (fresh) {
            const result = _handleLoopTranscript(transcript, showToast, autoSubmit);
            inserted = result.inserted;
            submitted = result.submitted;
          } else {
            if (showToast) showToast('No speech detected');
            if (!loopRecording) {
              const file = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
              if (onFileCreated) onFileCreated(file);
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
                const result = _handleLoopTranscript(transcript, showToast, autoSubmit);
                inserted = result.inserted;
                submitted = result.submitted;
              } else if (showToast) {
                showToast('No speech detected');
              }
            } catch (error) {
              console.error('STT transcription error:', error);
              if (showError) showError(`Transcription failed: ${error.message}`);
              if (!loopRecording) {
                const file = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
                if (onFileCreated) onFileCreated(file);
              }
            }
          }
        } else {
          const file = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
          if (onFileCreated) onFileCreated(file);
        }

        if (autoSubmit && _loopActive && !submitted) {
          // A valid transcript that could not be clicked is left in the input
          // for the user rather than risking a duplicate or stale resend.
          if (!inserted) _continueLoopAfterNoSubmission();
          else stopConversationLoop('silent');
        }
      };

      mediaRecorder.start();
      isRecording = true;
      _startVoiceActivityDetection(stream);
      if (_sttProvider === 'browser') startBrowserSTT(recordingId);
      if (_loopActive) _scheduleLoopAutoStop();
      if (showToast) showToast(`Recording...${_loopActive ? ' · waiting for speech' : ''}`);
    })
    .catch((error) => {
      console.error('Microphone access error:', error);
      if (showError) {
        if (error.name === 'NotAllowedError') showError('Microphone access denied. Check browser permissions.');
        else if (error.name === 'NotFoundError') showError('No microphone found.');
        else showError(`Microphone error: ${error.message}`);
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

export function stopRecording() {
  if (_loopActive) stopConversationLoop('manual');
  _stopRecordingInternal('manual');
}

export function getIsRecording() {
  return isRecording;
}

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
  set _sttProvider(value) { _sttProvider = value; },
  get _sttConversationLoop() { return _sttConversationLoop; },
};

window.voiceRecorderModule = voiceRecorderModule;
initSttSettingsUi();
_watchSendButtonForLoop();

export default voiceRecorderModule;
