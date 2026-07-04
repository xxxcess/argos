// Argos Venture Meeting Brief — transcript-first, CookBook-aware meeting workflow.
// Loaded as a storage.js side effect so it is available on the workspace home tab
// without adding a separate frontend bundle or parallel app shell.

const API_BASE = window.location.origin;
let _lastBrief = '';
let _openModal = null;

function notify(message, isError = false) {
  const ui = window.uiModule;
  if (isError && ui?.showError) ui.showError(message);
  else if (ui?.showToast) ui.showToast(message);
  else window.alert(message);
}

function svg(path) {
  return `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${path}</svg>`;
}

function meetingIcon() {
  return svg('<rect x="3" y="4" width="18" height="15" rx="2"/><path d="M7 21h10"/><path d="M9 8h6M8 12h8M8 16h4"/>');
}

function homeIsActive() {
  try {
    return document.body.classList.contains('workspace-home-active')
      || !window.sessionModule?.getCurrentSessionId?.();
  } catch (_) {
    return document.body.classList.contains('workspace-home-active');
  }
}

function syncHomeVisibility() {
  const visible = homeIsActive();
  for (const id of ['tool-meeting-brief-btn', 'rail-meeting-brief']) {
    const el = document.getElementById(id);
    if (el) el.style.display = visible ? '' : 'none';
  }
}

function wireLauncher(el) {
  if (!el || el.dataset.meetingBriefWired === '1') return;
  el.dataset.meetingBriefWired = '1';
  const open = () => openMeetingBrief();
  el.addEventListener('click', open);
  el.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      open();
    }
  });
}

function mountLaunchers() {
  const rail = document.getElementById('icon-rail');
  if (rail && !document.getElementById('rail-meeting-brief')) {
    const button = document.createElement('button');
    button.type = 'button';
    button.id = 'rail-meeting-brief';
    button.className = 'icon-rail-btn';
    button.title = 'Meeting Brief';
    button.setAttribute('aria-label', 'Open Meeting Brief');
    button.innerHTML = meetingIcon();
    const notes = document.getElementById('rail-notes');
    rail.insertBefore(button, notes || null);
    wireLauncher(button);
  }

  const tools = document.getElementById('tools-section');
  if (tools && !document.getElementById('tool-meeting-brief-btn')) {
    const row = document.createElement('div');
    row.id = 'tool-meeting-brief-btn';
    row.className = 'list-item';
    row.tabIndex = 0;
    row.setAttribute('role', 'button');
    row.setAttribute('aria-label', 'Open Meeting Brief');
    row.innerHTML = `${meetingIcon()}<span class="grow">Meeting Brief</span>`;
    const notes = document.getElementById('tool-notes-btn');
    tools.insertBefore(row, notes || null);
    wireLauncher(row);
  }

  syncHomeVisibility();
}

function setStatus(el, text) {
  if (el) el.textContent = text || '';
}

function cleanTitle(value) {
  return (value || '').trim() || 'Untitled meeting';
}

function makeButton(label, className = 'admin-btn-add') {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = className;
  button.textContent = label;
  return button;
}

function openMeetingBrief() {
  if (_openModal?.isConnected) {
    _openModal.querySelector('#meeting-brief-title')?.focus();
    return;
  }

  const modal = document.createElement('div');
  modal.id = 'meeting-brief-modal';
  modal.className = 'modal';
  modal.innerHTML = `
    <div class="modal-content" role="dialog" aria-modal="true" aria-labelledby="meeting-brief-heading">
      <div class="modal-header">
        <h4 id="meeting-brief-heading">${meetingIcon()} Meeting Brief</h4>
        <button type="button" class="close-btn" aria-label="Close Meeting Brief">✖</button>
      </div>
      <div class="modal-body">
        <div class="settings-col">
          <div class="settings-row">
            <label class="settings-label" for="meeting-brief-title">Title</label>
            <input id="meeting-brief-title" class="settings-select" maxlength="180" placeholder="Weekly project review" />
          </div>
          <div class="settings-row">
            <label class="settings-label" for="meeting-brief-audio">Audio</label>
            <input id="meeting-brief-audio" type="file" accept="audio/*,video/*" />
            <button type="button" class="admin-btn-sm" id="meeting-brief-transcribe">Transcribe</button>
          </div>
          <div class="settings-row" style="align-items:flex-start">
            <label class="settings-label" for="meeting-brief-transcript">Transcript</label>
            <textarea id="meeting-brief-transcript" class="settings-select" rows="10" placeholder="Paste a transcript, or select a recording and transcribe it with the configured STT provider."></textarea>
          </div>
          <div class="settings-row" style="align-items:flex-start">
            <label class="settings-label" for="meeting-brief-focus">Focus</label>
            <textarea id="meeting-brief-focus" class="settings-select" rows="2" placeholder="Optional: decisions, risks, next steps, or a question to resolve"></textarea>
          </div>
          <div class="settings-row">
            <span class="settings-label">Model</span>
            <span id="meeting-brief-model" style="opacity:.65">Uses the configured Utility model</span>
          </div>
          <div class="settings-row">
            <button type="button" class="admin-btn-add" id="meeting-brief-generate">Generate brief</button>
            <button type="button" class="admin-btn-sm" id="meeting-brief-save" disabled>Save to Notes</button>
            <span id="meeting-brief-status" style="font-size:11px;opacity:.7"></span>
          </div>
          <div class="settings-col">
            <label class="settings-label" for="meeting-brief-output">Brief</label>
            <pre id="meeting-brief-output" class="settings-select" tabindex="0" aria-live="polite" style="white-space:pre-wrap;min-height:180px;overflow:auto">Generate a brief to review decisions, actions, and uncertainty.</pre>
          </div>
        </div>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  _openModal = modal;

  const close = () => {
    if (!modal.isConnected) return;
    modal.remove();
    _openModal = null;
    document.removeEventListener('keydown', onKeydown);
  };
  const onKeydown = event => {
    if (event.key === 'Escape') close();
  };
  document.addEventListener('keydown', onKeydown);
  modal.addEventListener('click', event => {
    if (event.target === modal) close();
  });
  modal.querySelector('.close-btn')?.addEventListener('click', close);

  const title = modal.querySelector('#meeting-brief-title');
  const audio = modal.querySelector('#meeting-brief-audio');
  const transcript = modal.querySelector('#meeting-brief-transcript');
  const focus = modal.querySelector('#meeting-brief-focus');
  const model = modal.querySelector('#meeting-brief-model');
  const status = modal.querySelector('#meeting-brief-status');
  const output = modal.querySelector('#meeting-brief-output');
  const transcribe = modal.querySelector('#meeting-brief-transcribe');
  const generate = modal.querySelector('#meeting-brief-generate');
  const save = modal.querySelector('#meeting-brief-save');

  transcribe?.addEventListener('click', async () => {
    const file = audio?.files?.[0];
    if (!file) {
      notify('Choose an audio or video file first.', true);
      return;
    }
    transcribe.disabled = true;
    setStatus(status, 'Transcribing…');
    try {
      const data = new FormData();
      data.append('file', file, file.name);
      const response = await fetch(`${API_BASE}/api/stt/transcribe`, { method: 'POST', body: data });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result?.detail?.message || result?.message || 'Transcription failed');
      transcript.value = result.text || '';
      setStatus(status, transcript.value ? 'Transcript ready.' : 'No speech was detected.');
    } catch (error) {
      setStatus(status, '');
      notify(error.message || 'Transcription failed.', true);
    } finally {
      transcribe.disabled = false;
    }
  });

  generate?.addEventListener('click', async () => {
    const text = (transcript?.value || '').trim();
    if (!text) {
      notify('Paste a transcript or transcribe a recording first.', true);
      return;
    }
    generate.disabled = true;
    if (save) save.disabled = true;
    output.textContent = 'Preparing a grounded meeting brief…';
    setStatus(status, 'Using the configured Utility model…');
    try {
      const response = await fetch(`${API_BASE}/api/meeting-briefs/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: cleanTitle(title?.value),
          transcript: text,
          focus: (focus?.value || '').trim(),
        }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result?.detail?.message || result?.message || 'Could not generate a meeting brief');
      _lastBrief = result.brief || '';
      output.textContent = _lastBrief || 'The model returned no brief.';
      model.textContent = result.model ? `Utility model: ${result.model}` : 'Uses the configured Utility model';
      setStatus(status, result.chunk_count > 1 ? `Merged ${result.chunk_count} transcript segments.` : 'Brief ready.');
      if (save) save.disabled = !_lastBrief;
    } catch (error) {
      output.textContent = 'The brief could not be generated.';
      setStatus(status, '');
      notify(error.message || 'Could not generate a meeting brief.', true);
    } finally {
      generate.disabled = false;
    }
  });

  save?.addEventListener('click', async () => {
    if (!_lastBrief) return;
    save.disabled = true;
    setStatus(status, 'Saving to Notes…');
    try {
      const response = await fetch(`${API_BASE}/api/meeting-briefs/save-to-notes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: cleanTitle(title?.value),
          brief: _lastBrief,
          transcript: (transcript?.value || '').trim(),
        }),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result?.detail?.message || result?.message || 'Could not save the brief');
      setStatus(status, 'Saved to Notes.');
      notify('Meeting brief saved to Notes.');
    } catch (error) {
      setStatus(status, '');
      notify(error.message || 'Could not save the meeting brief.', true);
    } finally {
      save.disabled = false;
    }
  });

  title?.focus();
}

function boot() {
  mountLaunchers();
  document.addEventListener('odysseus:workspace-tab-activated', syncHomeVisibility);
  new MutationObserver(syncHomeVisibility).observe(document.body, {
    attributes: true,
    attributeFilter: ['class'],
  });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
else boot();
