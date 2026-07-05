// Meeting Brief Library export actions.
//
// This module deliberately attaches to the existing Meeting Brief modal and
// Live Meeting Capture tab instead of owning recording or synthesis state.
// Both surfaces use the existing document API so exported Markdown joins the
// user's normal Library with the same ownership and visibility rules.

const DOCUMENT_ENDPOINT = '/api/document';
const BRIEF_ENDPOINT = '/api/meeting-briefs/generate';
const INITIAL_BRIEF_TEXT = 'Generate a brief to review decisions, actions, discussion highlights, and uncertainty.';

function cleanText(value) {
  return String(value || '').trim();
}

function isLiveCapturePage() {
  try {
    return new URLSearchParams(window.location.search).get('meeting-capture') === 'live';
  } catch (_) {
    return false;
  }
}

function isGeneratedBrief(value) {
  const text = cleanText(value);
  return text.length >= 80
    && text !== INITIAL_BRIEF_TEXT
    && !text.startsWith('Preparing a grounded meeting brief')
    && !text.startsWith('The brief could not be generated')
    && !text.startsWith('The model returned no brief');
}

async function createLibraryDocument(title, content) {
  const response = await fetch(DOCUMENT_ENDPOINT, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      session_id: null,
      title,
      language: 'markdown',
      content,
    }),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(result?.detail?.message || result?.detail || result?.message || 'Document export failed');
  }
  return result;
}

function syncParentButton(modal) {
  if (!modal) return;
  const button = modal.querySelector('#meeting-brief-export-library');
  const output = modal.querySelector('#meeting-brief-output');
  if (button) button.disabled = !isGeneratedBrief(output?.textContent);
}

function mountParentExport(modal) {
  if (!modal) return;
  if (modal.dataset.meetingBriefLibraryExportBound === '1') {
    syncParentButton(modal);
    return;
  }

  const generate = modal.querySelector('#meeting-brief-generate');
  const save = modal.querySelector('#meeting-brief-save');
  const output = modal.querySelector('#meeting-brief-output');
  const title = modal.querySelector('#meeting-brief-title');
  const status = modal.querySelector('#meeting-brief-status');
  if (!generate || !output || !title || !status) return;

  const button = document.createElement('button');
  button.type = 'button';
  button.id = 'meeting-brief-export-library';
  button.className = 'admin-btn-sm';
  button.textContent = 'Export brief to Library';
  button.disabled = true;
  if (save?.parentNode) save.parentNode.insertBefore(button, save.nextSibling);
  else generate.parentNode?.appendChild(button);

  button.addEventListener('click', async () => {
    const brief = cleanText(output.textContent);
    if (!isGeneratedBrief(brief)) {
      status.textContent = 'Generate a Meeting Brief before exporting it.';
      return;
    }
    const meetingTitle = cleanText(title.value) || 'Untitled meeting';
    button.disabled = true;
    status.textContent = 'Exporting Meeting Brief to your document library…';
    try {
      const document = await createLibraryDocument(`${meetingTitle} — Meeting Brief`, brief);
      status.textContent = `Exported “${document.title || `${meetingTitle} — Meeting Brief`}” to your document library.`;
      window.uiModule?.showToast?.('Meeting Brief exported to Library.');
    } catch (error) {
      status.textContent = `Meeting Brief export failed: ${error.message}`;
      window.uiModule?.showError?.(error.message || 'Meeting Brief export failed.');
    } finally {
      syncParentButton(modal);
    }
  });

  const outputObserver = new MutationObserver(() => syncParentButton(modal));
  outputObserver.observe(output, { childList: true, subtree: true, characterData: true });
  modal.dataset.meetingBriefLibraryExportBound = '1';
  syncParentButton(modal);
}

function liveTranscript() {
  return cleanText(document.querySelector('#meeting-capture-transcript .meeting-capture-final')?.textContent);
}

function liveStatus(message, isError = false) {
  const status = document.getElementById('meeting-capture-status');
  if (!status) return;
  status.textContent = message || '';
  status.dataset.error = isError ? 'true' : 'false';
}

function mountLiveCaptureExport() {
  const root = document.getElementById('meeting-capture-root');
  const actions = root?.querySelector('.meeting-capture-actions');
  if (!root || !actions || document.getElementById('meeting-capture-export-brief')) return;

  const button = document.createElement('button');
  button.type = 'button';
  button.id = 'meeting-capture-export-brief';
  button.textContent = 'Generate & export brief';
  actions.appendChild(button);

  button.addEventListener('click', async () => {
    const transcript = liveTranscript();
    if (!transcript) {
      liveStatus('Capture or receive transcript text before generating a Meeting Brief.', true);
      return;
    }
    const title = cleanText(document.getElementById('meeting-capture-title-input')?.value) || 'Untitled meeting';
    button.disabled = true;
    liveStatus('Generating a grounded Meeting Brief…');
    try {
      const briefResponse = await fetch(BRIEF_ENDPOINT, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title, transcript, focus: '' }),
      });
      const briefResult = await briefResponse.json().catch(() => ({}));
      if (!briefResponse.ok) {
        throw new Error(briefResult?.detail?.message || briefResult?.detail || briefResult?.message || 'Meeting Brief generation failed');
      }
      const brief = cleanText(briefResult.brief);
      if (!isGeneratedBrief(brief)) throw new Error('The Utility model returned an empty Meeting Brief');
      const document = await createLibraryDocument(`${title} — Meeting Brief`, brief);
      liveStatus(`Exported “${document.title || `${title} — Meeting Brief`}” to your document library.`);
      try {
        const message = {
          type: 'meeting-capture-brief-exported',
          source: 'argos-venture-live-meeting-capture',
          title,
          transcript,
          document_id: document.id || null,
        };
        window.opener?.postMessage?.(message, window.location.origin);
      } catch (_) {}
    } catch (error) {
      liveStatus(`Meeting Brief export failed: ${error.message}`, true);
    } finally {
      button.disabled = false;
    }
  });
}

function mountExports() {
  if (isLiveCapturePage()) mountLiveCaptureExport();
  else document.querySelectorAll('#meeting-brief-modal').forEach(mountParentExport);
}

function boot() {
  mountExports();
  const target = document.body || document.documentElement;
  if (!target || typeof MutationObserver === 'undefined') return;
  new MutationObserver(mountExports).observe(target, { childList: true, subtree: true });
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
else boot();
