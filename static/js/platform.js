// Platform detection + AltGr-keystroke helper.
// Shared by keyboard-shortcuts.js, the editor, and settings.js.

export const IS_MAC =
  /Mac|iPhone|iPad/.test((typeof navigator !== 'undefined' && navigator.platform) || '') ||
  /Mac/.test((typeof navigator !== 'undefined' && navigator.userAgent) || '');

export function isAltGrEvent(e, isMac = IS_MAC) {
  return (
    !isMac &&
    !!e.ctrlKey &&
    !!e.altKey &&
    !!(e.getModifierState && e.getModifierState('AltGraph'))
  );
}

// settings.js already imports this shared module. The optional feature module
// installs its card when the AI Defaults panel is present.
if (typeof window !== 'undefined') {
  import('./videoGeneration.js').catch((error) => {
    console.warn('[video] unable to load video settings UI', error);
  });
}
