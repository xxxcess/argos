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

// Browser STT state
let _recognition = null;
let _browserTranscript = '';

// Cached STT provider — refreshed on settings change
let _sttProvider = 'disabled';
let _sttLanguage = '';

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
      // Notify the send button to update its icon
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
    }
  } catch (e) {
    console.warn('Failed to fetch STT stats:', e);
  }
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
  if (recordingInterval) {
    clearInterval(recordingInterval);
    recordingInterval = null;
  }
  // Reset send button via global callback
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
function startBrowserSTT() {
  const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SpeechRecognition) return;

  _browserTranscript = '';
  _recognition = new SpeechRecognition();
  _recognition.continuous = true;
  _recognition.interimResults = false;
  _recognition.lang = _sttLanguage || '';

  _recognition.onresult = (event) => {
    for (let i = event.resultIndex; i < event.results.length; i++) {
      if (event.results[i].isFinal) {
        _browserTranscript += event.results[i][0].transcript + ' ';
      }
    }
  };

  _recognition.onerror = (e) => {
    console.warn('Browser STT error:', e.error);
  };

  _recognition.start();
}

function stopBrowserSTT() {
  if (_recognition) {
    try { _recognition.stop(); } catch (e) { /* ignore */ }
    _recognition = null;
  }
  return _browserTranscript.trim();
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
  return data.text || '';
}

/**
 * Insert transcribed text into the chat input
 */
function insertTranscription(text, showToast) {
  if (!text) return;
  const input = document.getElementById('message');
  if (!input) return;

  const existing = input.value.trim();
  input.value = existing ? existing + ' ' + text : text;

  // Trigger auto-resize and icon update
  input.dispatchEvent(new Event('input', { bubbles: true }));
  input.focus();

  if (showToast) showToast('Transcribed');
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
        setMsg('Active: ' + label + (stats.model ? ' · ' + stats.model : ''), stats.provider === 'disabled');
      }
      if (window._updateSendBtnIcon) window._updateSendBtnIcon();
    } catch (_) {}
  }

  async function saveSTT() {
    try {
      const enabled = sttEnabledToggle ? sttEnabledToggle.checked : true;
      const payload = {
        stt_enabled: enabled,
        stt_provider: provSel.value,
        stt_model: getModel() || 'base',
        stt_language: langInput.value.trim()
      };
      const res = await fetch('/api/auth/settings', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error('Settings save failed');
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
export function startRecording(onFileCreated, showToast, showError) {
  // Check for secure context (getUserMedia requires HTTPS or localhost)
  if (!window.isSecureContext) {
    if (showError) showError('Microphone requires HTTPS. Use a reverse proxy with SSL or access via localhost.');
    _resetRecordingUI();
    return;
  }

  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    if (showError) showError('Microphone not supported in this browser.');
    _resetRecordingUI();
    return;
  }

  audioChunks = [];

  navigator.mediaDevices.getUserMedia({ audio: true })
    .then(stream => {
      mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });

      mediaRecorder.ondataavailable = event => {
        if (event.data.size > 0) {
          audioChunks.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach(track => track.stop());

        const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
        const provider = _sttProvider;

        if (provider === 'browser') {
          const transcript = stopBrowserSTT();
          if (transcript) {
            insertTranscription(transcript, showToast);
          } else {
            if (showToast) showToast('No speech detected');
            const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
            if (onFileCreated) onFileCreated(audioFile);
          }
        } else if (provider === 'local' || provider.startsWith('endpoint:')) {
          // Show "Transcribing..." feedback
          if (showToast) showToast('Transcribing...', 5000);
          try {
            const transcript = await transcribeOnServer(audioBlob);
            if (transcript) {
              insertTranscription(transcript, showToast);
            } else {
              if (showToast) showToast('No speech detected');
            }
          } catch (e) {
            console.error('STT transcription error:', e);
            if (showError) showError('Transcription failed: ' + e.message);
            // Fallback: attach as file
            const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
            if (onFileCreated) onFileCreated(audioFile);
          }
        } else {
          // STT disabled — attach audio file
          const audioFile = new File([audioBlob], `voice-message-${Date.now()}.webm`, { type: 'audio/webm' });
          if (onFileCreated) onFileCreated(audioFile);
        }

        _resetRecordingUI();
      };

      mediaRecorder.start();
      isRecording = true;
      recordingStartTime = new Date();

      // Start browser STT if that's the provider
      if (_sttProvider === 'browser') {
        startBrowserSTT();
      }

      if (showToast) {
        showToast('Recording...');
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
      _resetRecordingUI();
    });
}

/**
 * Stop voice recording
 */
export function stopRecording() {
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    // isRecording will be set to false in _resetRecordingUI called from onstop
  } else {
    _resetRecordingUI();
  }
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
}

const voiceRecorderModule = {
  startRecording,
  stopRecording,
  getIsRecording,
  init,
  refreshSttProvider,
  get _sttProvider() { return _sttProvider; },
  set _sttProvider(v) { _sttProvider = v; },
};

window.voiceRecorderModule = voiceRecorderModule;
initSttSettingsUi();

export default voiceRecorderModule;
