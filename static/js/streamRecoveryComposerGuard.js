// Prevent legacy stream auto-recovery text from leaking raw tool markup into
// the user composer. chat.js writes a recovery message and calls sendBtn.click()
// when an SSE stream ends unexpectedly. Capture that synthetic click before the
// chat send handler reads #message, replace the recovery text with a terminal
// non-tool token, and clear it after the click completes.

const RECOVERY_PREFIXES = [
  'The stream dropped before you',
  'Your previous response was interrupted.',
];

function isRecoveryText(value) {
  const text = String(value || '').trimStart();
  return RECOVERY_PREFIXES.some(prefix => text.startsWith(prefix));
}

function install() {
  document.addEventListener('click', event => {
    const button = event.target?.closest?.('.send-btn');
    const composer = document.getElementById('message');
    // Only intercept the programmatic click used by auto-recovery. A user's
    // manually authored message must remain untouched.
    if (!button || event.isTrusted || !composer || !isRecoveryText(composer.value)) return;

    composer.dataset.streamRecoveryGuard = '1';
    composer.value = 'DONE';
    queueMicrotask(() => {
      if (composer.dataset.streamRecoveryGuard === '1' && composer.value === 'DONE') {
        composer.value = '';
      }
      delete composer.dataset.streamRecoveryGuard;
    });
  }, true);
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
else install();
