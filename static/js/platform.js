// ============================================
// Platform detection + AltGr-keystroke helper
// ============================================
export const IS_MAC =
  /Mac|iPhone|iPad/.test((typeof navigator !== 'undefined' && navigator.platform) || '') ||
  /Mac/.test((typeof navigator !== 'undefined' && navigator.userAgent) || '');

export function isAltGrEvent(e, isMac = IS_MAC) {
  return !isMac && !!e.ctrlKey && !!e.altKey && !!(e.getModifierState && e.getModifierState('AltGraph'));
}

// Optional product modules mount when Settings/chat DOM is available. They are
// imported here because platform.js is already shared by the settings bundle.
if (typeof document !== 'undefined') {
  import('./video.js').catch(error => console.warn('[video] local settings unavailable', error));
  import('./videoAgentProgress.js').catch(error => console.warn('[video] agent progress unavailable', error));
}
