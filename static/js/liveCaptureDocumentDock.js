// Live Capture document-dock geometry.
//
// The normal document editor is mounted inside the chat layout. Live Capture is
// a separate fixed workspace surface, so hiding the entire chat container also
// hides or clips the document editor. Keep the chat layout available for the
// document pane, then reserve exactly the dock's rendered width for capture.

const ACTIVE_CLASS = 'workspace-live-capture-active';
const ROOT_SELECTOR = '#live-capture-workspace';
const PANE_SELECTOR = '#doc-editor-pane';
const DIVIDER_SELECTOR = '#doc-divider';

let queued = false;
let paneObserver = null;
let bodyObserver = null;
let resizeObserver = null;

function isVisible(element) {
  if (!element || !document.body.contains(element)) return false;
  const style = window.getComputedStyle(element);
  const rect = element.getBoundingClientRect();
  return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 80 && rect.height > 80;
}

function resetCaptureBounds(root) {
  root.style.setProperty('left', '0px', 'important');
  root.style.setProperty('right', '0px', 'important');
}

function syncDocumentDock() {
  const root = document.querySelector(ROOT_SELECTOR);
  if (!root || !document.body.classList.contains(ACTIVE_CLASS)) return;

  const pane = document.querySelector(PANE_SELECTOR);
  if (!isVisible(pane) || window.innerWidth <= 820) {
    resetCaptureBounds(root);
    return;
  }

  const viewportWidth = window.innerWidth;
  const paneRect = pane.getBoundingClientRect();
  const dividerRect = document.querySelector(DIVIDER_SELECTOR)?.getBoundingClientRect();
  const dividerLeft = dividerRect && dividerRect.width > 0 ? dividerRect.left : paneRect.left;
  const dividerRight = dividerRect && dividerRect.width > 0 ? dividerRect.right : paneRect.right;

  // The editor is normally a right dock. Reserve the divider as well so the
  // Live Capture background never sits above its resize/collapse controls.
  if (paneRect.left >= viewportWidth * 0.25) {
    const dockStart = Math.max(0, Math.min(paneRect.left, dividerLeft));
    root.style.setProperty('left', '0px', 'important');
    root.style.setProperty('right', `${Math.max(0, Math.ceil(viewportWidth - dockStart))}px`, 'important');
    return;
  }

  // Fullscreen/left-docked editor support. This keeps the capture canvas from
  // flowing beneath an editor that was expanded using its divider control.
  if (paneRect.right <= viewportWidth * 0.75) {
    const dockEnd = Math.min(viewportWidth, Math.max(paneRect.right, dividerRight));
    root.style.setProperty('left', `${Math.max(0, Math.floor(dockEnd))}px`, 'important');
    root.style.setProperty('right', '0px', 'important');
    return;
  }

  // A fullscreen document owns the workspace; do not leave a narrow, clipped
  // Live Capture sliver behind it.
  root.style.setProperty('left', '0px', 'important');
  root.style.setProperty('right', '0px', 'important');
}

function queueSync(frames = 1) {
  if (queued) return;
  queued = true;
  const step = () => {
    if (--frames > 0) {
      requestAnimationFrame(step);
      return;
    }
    queued = false;
    syncDocumentDock();
  };
  requestAnimationFrame(step);
}

function observePane() {
  const pane = document.querySelector(PANE_SELECTOR);
  if (paneObserver?.target === pane) return;
  paneObserver?.observer?.disconnect();
  paneObserver = null;
  if (!pane || !window.ResizeObserver) return;
  const observer = new ResizeObserver(() => queueSync(2));
  observer.observe(pane);
  paneObserver = { target: pane, observer };
}

function installStyles() {
  if (document.getElementById('live-capture-document-dock-style')) return;
  const style = document.createElement('style');
  style.id = 'live-capture-document-dock-style';
  style.textContent = `
    /* The document dock is a sibling surface inside the normal chat layout. */
    body.workspace-live-capture-active #chat-container {
      visibility: visible !important;
      pointer-events: none;
    }
    body.workspace-live-capture-active #doc-editor-pane,
    body.workspace-live-capture-active #doc-divider {
      visibility: visible !important;
      pointer-events: auto !important;
      z-index: 70 !important;
    }
    body.workspace-live-capture-active #live-capture-workspace {
      overflow: hidden;
      z-index: 40;
    }
    @media (max-width: 820px) {
      body.workspace-live-capture-active #live-capture-workspace {
        left: 0 !important;
        right: 0 !important;
      }
    }
  `;
  document.head.appendChild(style);
}

function init() {
  installStyles();
  resizeObserver = window.ResizeObserver ? new ResizeObserver(() => queueSync(2)) : null;
  if (resizeObserver) resizeObserver.observe(document.documentElement);

  bodyObserver = new MutationObserver(() => {
    observePane();
    queueSync(3);
  });
  bodyObserver.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'style'],
  });

  window.addEventListener('resize', () => queueSync(2), { passive: true });
  window.addEventListener('transitionend', event => {
    if (event.target?.matches?.(`${PANE_SELECTOR}, ${DIVIDER_SELECTOR}`)) queueSync(2);
  }, true);
  document.addEventListener('argos:live-capture-document-dock-sync', () => queueSync(2));
  observePane();
  queueSync(2);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
else init();
