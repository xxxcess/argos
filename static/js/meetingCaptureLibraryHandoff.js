// Audio Capture export handoff.
//
// The capture surface owns microphone state. This small integration keeps the
// workflow safe at the workspace boundary: finish a recording before export,
// then leave the capture canvas when Document Library opens the newly-created
// Markdown document.

function setCaptureStatus(message, isError = false) {
  const status = document.getElementById('meeting-capture-status');
  if (!status) return;
  status.textContent = message;
  status.dataset.error = isError ? 'true' : 'false';
}

function recordingIsActive() {
  return !document.getElementById('meeting-capture-stop')?.disabled;
}

function wireCaptureGuard(root) {
  if (!root || root.dataset.libraryHandoffWired === '1') return;
  root.dataset.libraryHandoffWired = '1';
  root.addEventListener('click', event => {
    const exportButton = event.target.closest('#meeting-capture-export-transcript, #meeting-capture-export-brief');
    if (exportButton && recordingIsActive()) {
      event.preventDefault();
      event.stopImmediatePropagation();
      setCaptureStatus('Stop capture and wait for final transcription before exporting.', true);
      return;
    }

    const closeButton = event.target.closest('.meeting-capture-tab .workspace-tab-close');
    if (closeButton && recordingIsActive()) {
      event.preventDefault();
      event.stopImmediatePropagation();
      setCaptureStatus('Stop capture before closing this Audio Capture tab.', true);
    }
  }, true);
}

function wrapDocumentPanel() {
  const documents = window.documentModule;
  if (!documents || documents.__meetingCaptureRevealWrapped || typeof documents.openPanel !== 'function') return false;
  const openPanel = documents.openPanel.bind(documents);
  documents.openPanel = function openPanelAfterCapture(...args) {
    window.meetingCaptureModule?.deactivateCapture?.();
    return openPanel(...args);
  };
  documents.__meetingCaptureRevealWrapped = true;
  return true;
}

function install() {
  const root = document.getElementById('meeting-capture-workspace');
  if (root) wireCaptureGuard(root);
  wrapDocumentPanel();
}

function boot() {
  install();
  const timer = window.setInterval(() => {
    install();
    if (window.documentModule?.__meetingCaptureRevealWrapped) window.clearInterval(timer);
  }, 250);
  const target = document.body || document.documentElement;
  if (target && typeof MutationObserver !== 'undefined') {
    new MutationObserver(install).observe(target, { childList: true, subtree: true });
  }
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot, { once: true });
else boot();
