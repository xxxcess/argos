// static/js/voiceRecorder.js

/**
 * Voice recording with optional speech-to-text transcription.
 *
 * Providers:
 *   disabled       - attach audio as a file
 *   browser        - Web Speech API
 *   local          - server-side Whisper
 *   endpoint:<id>  - OpenAI-compatible transcription endpoint
 */

let mediaRecorder = null;
let audioChunks = [];
let isRecording = false;
let _recordingStopReason = 'manual';
let _activeRecordingId = 0;
let recordingSessionId = null;
let recordingRequestId = 0;
let recordingCancelled = false;

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

// STT settings
let _sttProvider = 'disabled';
let _sttLanguage = '';
let _sttConversationLoop = false;
let _sttLoopSubmitSeconds = 3;
let _sttLoopIdleTimeoutSeconds = 5;

// Conversation-loop state
let _loopActive = false;
let _loopWaitingForAssistant = false;
let _loopWaitingForTts = false;
let _loopInstructionDeadline = 0;
let _loopAutoStopTimer = null;
let _loopRestartTimer = null;
let _sendButtonObserver = null;
let _lastSendButtonMode = '';
let _lastAutoSubmittedText = '';
let _lastAutoSubmittedAt = 0;
let _ttsQuietSince = 0;
let _discardCurrentRecordingForTts = false;

const VAD_RMS_THRESHOLD = 0.018;
const VAD_MIN_SPEECH_MS = 250;
const MIN_RECORDING_MS = 400;
const MIN_TRANSCRIPT_CHARS = 2;
const DUPLICATE_SUBMIT_WINDOW_MS = 15000;
const TTS_POLL_MS = 200;
const TTS_STARTUP_GRACE_MS = 900;
const TTS_POST_PLAYBACK_COOLDOWN_MS = 900;

function _clampNumber(value, fallback, min, max) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.max(min, Math.min(max, number));
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

function _isTtsPlaybackActive() {
  const manager = window.aiTTSManager;
  const managerAudioActive = !!(
    manager && (
      manager.isPlaying ||
      manager._processing ||
      manager._streamActive ||
      (Array.isArray(manager._queue) && manager._queue.length > 0) ||
      (manager.currentAudio && !manager.currentAudio.paused && !manager.currentAudio.ended)
    )
  );

  const browserTtsActive = !!(
    window.speechSynthesis &&
    (window.speechSynthesis.speaking || window.speechSynthesis.pending)
  );

  return managerAudioActive || browserTtsActive;
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
  if (now - _speechStartedAt >= VAD_MIN_SPEECH_MS) _speechDetected = true;
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
    _vadAvailable = true;

    const samples = new Uint8Array(_audioAnalyser.fftSize);
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
  if (_loopAutoStopTimer) {
    clearTimeout(_loopAutoStopTimer);
    _loopAutoStopTimer = null;
  }

  const sendButton = document.querySelector('.send-btn');
  if (sendButton) {
    sendButton.classList.remove('recording');
    sendButton.dataset.mode = '';
  }
  if (window._updateSendBtnIcon) setTimeout(window._updateSendBtnIcon, 0);
}

function _resetRecordingUi() {
  _stopVoiceActivityDetection();
  _releaseRecordingUi();
}

function stopConversationLoop(reason) {
  const wasActive = _loopActive || _loopWaitingForAssistant || _loopWaitingForTts;
  _loopActive = false;
  _loopWaitingForAssistant = false;
  _loopWaitingForTts = false;
  _loopInstructionDeadline = 0;
  _ttsQuietSince = 0;
  _discardCurrentRecordingForTts = false;
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

function _waitForTtsThenRestart(options = {}) {
  if (!_loopActive) return;
  if (_loopRestartTimer) clearTimeout(_loopRestartTimer);

  const initialGrace = options.initialGrace === true;
  const check = () => {
    if (!_loopActive) return;

    if (_isTtsPlaybackActive()) {
      _loopWaitingForTts = true;
      _ttsQuietSince = 0;
      _loopRestartTimer = setTimeout(check, TTS_POLL_MS);
      return;
    }

    const now = Date.now();
    if (!_ttsQuietSince) _ttsQuietSince = now;
    const quietRequired = _loopWaitingForTts || !initialGrace
      ? TTS_POST_PLAYBACK_COOLDOWN_MS
      : TTS_STARTUP_GRACE_MS;

    if (now - _ttsQuietSince < quietRequired) {
      _loopRestartTimer = setTimeout(check, TTS_POLL_MS);
      return;
    }

    _loopRestartTimer = null;
    _loopWaitingForTts = false;
    _loopWaitingForAssistant = false;
    _ttsQuietSince = 0;
    _beginLoopListening();
  };

  check();
}

function _submitCurrentTranscription(expectedText) {
  if (_isTtsPlaybackActive()) {
    _waitForTtsThenRestart();
    return false;
  }

  const input = document.getElementById('message');
  const text = _normalizeTranscript(input?.value || '');
  const expected = _normalizeTranscript(expectedText || text);
  if (!text || !expected || text !== expected) return false;

  const now = Date.now();
  if (text === _lastAutoSubmittedText && now - _lastAutoSubmittedAt < DUPLICATE_SUBMIT_WINDOW_MS) {
    return false;
  }

  const sendButton = document.querySelector('.send-btn');
  if (!sendButton || sendButton.disabled || sendButton.dataset.mode === 'streaming') return false;

  sendButton.classList.remove('recording');
  sendButton.dataset.mode = '';
  _lastAutoSubmittedText = text;
  _lastAutoSubmittedAt = now;
  _loopWaitingForAssistant = true;
  _lastSendButtonMode = '';

  setTimeout(() => {
    if (_loopWaitingForAssistant && !isRecording && !_isTtsPlaybackActive()) {
      sendButton.click();
    }
  }, 0);
  return true;
}

function _scheduleLoopAutoStop() {
  if (!_loopActive || !isRecording) return;
  if (_loopAutoStopTimer) clearTimeout(_loopAutoStopTimer);

  // Speaker playback is not user speech. Stop and discard this turn instead
  // of allowing browser STT to transcribe the assistant's TTS response.
  if (_isTtsPlaybackActive()) {
    _discardCurrentRecordingForTts = true;
    _stopRecordingInternal('loop-tts');
    return;
  }

  const remaining = _loopRemainingMs();
  if (remaining <= 0) {
    stopConversationLoop('idle-timeout');
    _stopRecordingInternal('loop-timeout');
    return;
  }

  // Never turn a timer into an instruction. Keep listening until actual user
  // speech is detected or the no-speech deadline expires.
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
  if (!_loopActive || isRecording || _loopWaitingForAssistant || _loopWaitingForTts) return;
  if (!_conversationLoopAvailable()) {
    stopConversationLoop('silent');
    return;
  }
  if (_isTtsPlaybackActive()) {
    _waitForTtsThenRestart();
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
  _loopWaitingForAssistant = true;
  _ttsQuietSince = 0;
  _waitForTtsThenRestart({ initialGrace: true });
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
  const observer = new MutationObserver(() => {
    if (attach()) observer.disconnect();
  });
  if (document.body) observer.observe(document.body, { childList: true, subtree: true });
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

  _recognition.onerror = (event) => console.warn('Browser STT error:', event.error);
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
  const requestId = recordingRequestId;
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
  if (recordingCancelled || requestId !== recordingRequestId) return '';
  return _normalizeTranscript(data.text || '');
}

function insertTranscription(text, showToast, options = {}) {
  if (recordingCancelled) return false;
  const transcript = _normalizeTranscript(text);
  if (!_hasFreshTranscript(transcript)) return false;

  const input = document.getElementById('message');
  if (!input) return false;
  if (options.replaceExisting) input.value = transcript;
  else input.value = _normalizeTranscript(input.value) ? `${_normalizeTranscript(input.value)} ${transcript}` : transcript;

  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();
  if (showToast) showToast('Transcribed');
  return true;
}

function _handleLoopTranscript(transcript, showToast, autoSubmit) {
  const inserted = insertTranscription(transcript, showToast, { replaceExisting: autoSubmit });
  if (!autoSubmit || !inserted) return { inserted, submitted: false };
  const submitted = _submitCurrentTranscription(transcript);
  if (!submitted && showToast && !_isTtsPlaybackActive()) {
    showToast('Transcript ready — press Send to continue.');
  }
  return { inserted, submitted };
}

function _continueLoopAfterNoSubmission() {
  if (!_loopActive) return;
  if (_isTtsPlaybackActive()) {
    _waitForTtsThenRestart();
    return;
  }
  if (_loopRemainingMs() <= 250) {
    stopConversationLoop('idle-timeout');
    return;
  }
  _loopRestartTimer = setTimeout(_beginLoopListening, 100);
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
      <h2 style="display:flex;align-items:center;gap:6px;">Speech to Text<span style="flex:1"></span><label class="admin-switch"><input type="checkbox" id="set-sttEnabledToggle" checked><span class="admin-slider"></span></label></h2>
      <div class="admin-toggle-sub" style="margin-bottom:8px">Configure microphone transcription. Conversation loop waits for user speech and pauses while TTS plays.</div>
      <div id="set-sttConfigWrap" style="display:flex;flex-direction:column;gap:.5rem;">
        <div class="settings-row"><label class="settings-label">Provider</label><select id="set-sttProviderSelect" class="settings-select"><option value="browser">Browser (built-in)</option><option value="local">Local (Whisper)</option><option value="disabled">Disabled</option></select></div>
        <div id="set-sttModelRow" class="settings-row"><label class="settings-label">Model</label><select id="set-sttModelSelect" class="settings-select"><option value="tiny">tiny (fastest)</option><option value="base" selected>base (default)</option><option value="small">small</option><option value="medium">medium</option><option value="large-v3">large-v3</option></select><input id="set-sttModelInput" type="text" placeholder="whisper-1" class="settings-select" style="display:none;flex:1"></div>
        <div id="set-sttLangRow" class="settings-row"><label class="settings-label">Language</label><input id="set-sttLangInput" type="text" placeholder="Auto-detect or ISO code, e.g. en" class="settings-select" style="flex:1"></div>
        <div class="settings-row"><label class="settings-label">Conversation loop</label><div style="flex:1;display:flex;align-items:center;gap:8px;justify-content:space-between"><span class="admin-toggle-sub" style="margin:0">Submit after speech ends, then wait for the assistant and TTS before listening again.</span><label class="admin-switch"><input type="checkbox" id="set-sttConversationLoopToggle"><span class="admin-slider"></span></label></div></div>
        <div id="set-sttLoopTimingRows" style="display:flex;flex-direction:column;gap:.5rem"><div class="settings-row"><label class="settings-label">Submit after silence</label><input id="set-sttLoopSubmitSeconds" type="number" min="1" max="60" step=".5" value="3" class="settings-select" style="width:120px;margin-left:auto"><span class="admin-toggle-sub" style="margin:0 0 0 6px">seconds</span></div><div class="settings-row"><label class="settings-label">Stop listening after</label><input id="set-sttLoopIdleTimeoutSeconds" type="number" min="1" max="300" step=".5" value="5" class="settings-select" style="width:120px;margin-left:auto"><span class="admin-toggle-sub" style="margin:0 0 0 6px">seconds idle</span></div></div>
        <div id="set-sttSettingsMsg" style="font-size:11px;color:color-mix(in srgb,var(--fg) 45%,transparent)"></div>
      </div>`;
  }

  const ttsToggle = document.getElementById('set-ttsEnabledToggle');
  const ttsCard = ttsToggle ? ttsToggle.closest('.admin-card') : null;
  const anchor = ttsCard || defaultCard;
  if (anchor.parentNode && anchor.nextSibling !== card) anchor.parentNode.insertBefore(card, anchor.nextSibling);
  bindSttSettingsUi(card).catch((error) => console.warn('Failed to bind STT settings', error));
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
  const loopToggle = document.getElementById('set-sttConversationLoopToggle');
  const loopTimingRows = document.getElementById('set-sttLoopTimingRows');
  const submitSecondsInput = document.getElementById('set-sttLoopSubmitSeconds');
  const idleSecondsInput = document.getElementById('set-sttLoopIdleTimeoutSeconds');
  const status = document.getElementById('set-sttSettingsMsg');
  const configWrap = document.getElementById('set-sttConfigWrap');
  if (!providerSelect || providerSelect.dataset.boundVoiceRecorderStt === '1') return;
  providerSelect.dataset.boundVoiceRecorderStt = '1';

  const isEndpoint = () => providerSelect.value.startsWith('endpoint:');
  const effectiveProvider = () => enabledToggle && !enabledToggle.checked ? 'disabled' : providerSelect.value;
  const setStatus = (message, isError = false) => {
    if (!status) return;
    status.textContent = message || '';
    status.style.color = isError ? 'var(--red, #e55)' : 'var(--fg)';
  };

  const updateVisibility = () => {
    const provider = providerSelect.value;
    const showModel = provider === 'local' || provider.startsWith('endpoint:');
    if (modelRow) modelRow.style.display = showModel ? 'flex' : 'none';
    if (languageRow) languageRow.style.display = provider !== 'disabled' ? 'flex' : 'none';
    modelSelect.style.display = isEndpoint() ? 'none' : '';
    modelInput.style.display = isEndpoint() ? '' : 'none';
    const disabled = enabledToggle && !enabledToggle.checked;
    if (card) card.style.opacity = disabled ? '.45' : '';
    if (configWrap) configWrap.style.pointerEvents = disabled ? 'none' : '';
    const loopOff = !loopToggle || !loopToggle.checked;
    if (loopTimingRows) {
      loopTimingRows.style.opacity = loopOff ? '.5' : '';
      loopTimingRows.style.pointerEvents = loopOff ? 'none' : '';
    }
    if (!disabled && provider === 'browser' && !_browserSttSupported()) {
      setStatus('Browser speech recognition is unavailable; recordings will fall back to attachments.', true);
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
    } catch (_) {}
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
      modelSelect.value = settings.stt_model || 'base';
      modelInput.value = settings.stt_model || 'base';
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
        stt_model: isEndpoint() ? (modelInput.value.trim() || 'whisper-1') : modelSelect.value,
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
      _sttProvider = payload.stt_enabled ? payload.stt_provider : 'disabled';
      _sttLanguage = payload.stt_language;
      if (!_sttConversationLoop) stopConversationLoop('silent');
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
      setStatus('Saved');
    } catch (_) {
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
  if (enabledToggle) enabledToggle.addEventListener('change', () => { updateVisibility(); saveSettings(); });
  if (loopToggle) loopToggle.addEventListener('change', () => { updateVisibility(); saveSettings(); });
  if (submitSecondsInput) submitSecondsInput.addEventListener('change', saveSettings);
  if (idleSecondsInput) idleSecondsInput.addEventListener('change', saveSettings);
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

export function startRecording(onFileCreated, showToast, showError, options = {}) {
  recordingSessionId = options.sessionId || window.sessionModule?.getCurrentSessionId?.() || null;
  recordingCancelled = false;
  const requestId = ++recordingRequestId;
  const fromConversationLoop = options.fromConversationLoop === true;
  if (!fromConversationLoop && _conversationLoopAvailable()) {
    _loopActive = true;
    _loopWaitingForAssistant = false;
    _clearMessageInputForLoop();
    _loopInstructionDeadline = Date.now() + (_sttLoopIdleTimeoutSeconds * 1000);
  }

  if (_loopActive && _isTtsPlaybackActive()) {
    _waitForTtsThenRestart({ initialGrace: false });
    return;
  }

  if (!window.isSecureContext) {
    if (showError) showError('Microphone requires HTTPS. Use SSL or localhost.');
    stopConversationLoop('silent');
    _resetRecordingUi();
    return;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    if (showError) showError('Microphone not supported in this browser.');
    stopConversationLoop('silent');
    _resetRecordingUi();
    return;
  }

  audioChunks = [];
  _recordingStopReason = 'manual';
  _discardCurrentRecordingForTts = false;
  _resetSpeechState();
  const recordingId = ++_activeRecordingId;

  navigator.mediaDevices.getUserMedia({ audio: true })
    .then((stream) => {
      if (recordingCancelled || requestId !== recordingRequestId) {
        try { stream.getTracks().forEach((track) => track.stop()); } catch (_) {}
        _resetRecordingUi();
        return;
      }
      try { mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' }); }
      catch (_) { mediaRecorder = new MediaRecorder(stream); }

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunks.push(event.data);
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop());
        _stopVoiceActivityDetection();
        if (recordingCancelled || requestId !== recordingRequestId) {
          stopBrowserSTT();
          audioChunks = [];
          _releaseRecordingUi();
          return;
        }
        const stopReason = _recordingStopReason;
        const autoSubmit = _loopActive && (stopReason === 'loop-auto' || stopReason === 'loop-manual-submit');
        const discardForTts = _discardCurrentRecordingForTts || stopReason === 'loop-tts';
        const loopRecording = autoSubmit || stopReason === 'loop-timeout' || discardForTts;
        const hadVoiceActivity = _speechDetected || _browserSpeechDetected || !_vadAvailable;
        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });

        // Clear recording mode before inserting/sending any transcription.
        _releaseRecordingUi();

        if (discardForTts) {
          stopBrowserSTT();
          _discardCurrentRecordingForTts = false;
          _waitForTtsThenRestart();
          return;
        }

        let inserted = false;
        let submitted = false;

        if (providerForCurrentTurn() === 'browser') {
          const transcript = stopBrowserSTT();
          const fresh = _hasFreshTranscript(transcript) && (_browserSpeechDetected || _speechDetected || !_vadAvailable);
          if (fresh) {
            const result = _handleLoopTranscript(transcript, showToast, autoSubmit);
            inserted = result.inserted;
            submitted = result.submitted;
          } else {
            if (showToast) showToast('No speech detected');
            if (!loopRecording && onFileCreated) {
              onFileCreated(new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' }));
            }
          }
        } else if (providerForCurrentTurn() === 'local' || providerForCurrentTurn().startsWith('endpoint:')) {
          if (autoSubmit && !hadVoiceActivity) {
            if (showToast) showToast('No speech detected');
          } else {
            if (showToast) showToast('Transcribing...', 5000);
            try {
              const transcript = await transcribeOnServer(audioBlob);
              if (_loopActive && _isTtsPlaybackActive()) {
                _waitForTtsThenRestart();
                return;
              }
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
              if (!loopRecording && onFileCreated) {
                onFileCreated(new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' }));
              }
            }
          }
        } else if (onFileCreated) {
          onFileCreated(new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' }));
        }

        if (autoSubmit && _loopActive && !submitted) {
          if (!inserted) _continueLoopAfterNoSubmission();
          else stopConversationLoop('silent');
        }
      };

      mediaRecorder.start();
      isRecording = true;
      _startVoiceActivityDetection(stream);
      if (providerForCurrentTurn() === 'browser') startBrowserSTT(recordingId);
      if (_loopActive) _scheduleLoopAutoStop();
      if (showToast) showToast(`Recording...${_loopActive ? ' · waiting for speech' : ''}`);
    })
    .catch((error) => {
      if (recordingCancelled || requestId !== recordingRequestId) {
        _resetRecordingUi();
        return;
      }
      console.error('Microphone access error:', error);
      if (showError) showError(`Microphone error: ${error.message}`);
      stopConversationLoop('silent');
      _resetRecordingUi();
    });
}

function providerForCurrentTurn() {
  return _sttProvider || 'disabled';
}

function _stopRecordingInternal(reason) {
  _recordingStopReason = reason || 'manual';
  if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop();
  else _resetRecordingUi();
}

export function stopRecording(options = {}) {
  const submitLoopTranscript = options && options.submitLoopTranscript === true;
  if (_loopActive && submitLoopTranscript) {
    _stopRecordingInternal('loop-manual-submit');
    return;
  }
  if (_loopActive) stopConversationLoop('manual');
  _stopRecordingInternal('manual');
}

export function isConversationLoopActive() {
  return _loopActive;
}

export function cancelForMissionClose(sessionId) {
  if (sessionId != null && recordingSessionId != null && String(sessionId) !== String(recordingSessionId)) {
    return;
  }
  recordingCancelled = true;
  recordingRequestId += 1;
  stopConversationLoop('silent');
  stopBrowserSTT();
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    try { mediaRecorder.stop(); } catch (_) {}
  }
  try {
    if (mediaRecorder && mediaRecorder.stream) {
      mediaRecorder.stream.getTracks().forEach((track) => track.stop());
    }
  } catch (_) {}
  audioChunks = [];
  recordingSessionId = null;
  _resetRecordingUi();
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
  cancelForMissionClose,
  stopConversationLoop,
  isConversationLoopActive,
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
