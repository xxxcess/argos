// Platform detection helpers shared by settings and keyboard modules.
export const IS_MAC =
  /Mac|iPhone|iPad/.test((typeof navigator !== 'undefined' && navigator.platform) || '') ||
  /Mac/.test((typeof navigator !== 'undefined' && navigator.userAgent) || '');

export function isAltGrEvent(e, isMac = IS_MAC) {
  return !isMac && !!e.ctrlKey && !!e.altKey && !!(e.getModifierState && e.getModifierState('AltGraph'));
}

if (typeof document !== 'undefined') {
  import('./video.js').catch(() => {});
  import('./videoAgentProgress.js').catch(() => {});
}
