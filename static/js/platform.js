// ============================================
// Platform detection + AltGr-keystroke helper
// ============================================
// Shared by the keybind code: root keyboard-shortcuts.js, the editor's
// keyboard-shortcuts.js, and settings.js. Single source of truth so the three
// guards can't drift.

// AltGr (right Alt on AZERTY/QWERTZ and most non-US layouts, used to type
// @ # { } [ ] | \ and €) is reported by browsers as Ctrl+Alt. macOS is the
// exception: there the Option key — a normal part of Mac shortcuts — also sets
// the AltGraph modifier state, so it must NOT be treated as AltGr.
export const IS_MAC =
  /Mac|iPhone|iPad/.test((typeof navigator !== 'undefined' && navigator.platform) || '') ||
  /Mac/.test((typeof navigator !== 'undefined' && navigator.userAgent) || '');

// True when `e` is an AltGr keystroke we should ignore for Ctrl+Alt shortcut
// purposes. getModifierState('AltGraph') is true for AltGr but false for a
// genuine left Ctrl+Alt. Always false on macOS, where Option legitimately sets
// AltGraph.
export function isAltGrEvent(e, isMac = IS_MAC) {
  return (
    !isMac &&
    !!e.ctrlKey &&
    !!e.altKey &&
    !!(e.getModifierState && e.getModifierState('AltGraph'))
  );
}

// settings.js already imports this module. The optional video module self-mounts
// a sibling card inside the existing AI Defaults panel.
if (typeof window !== 'undefined') {
  import('./video.js').catch(error => console.warn('[video] settings UI unavailable', error));
}
