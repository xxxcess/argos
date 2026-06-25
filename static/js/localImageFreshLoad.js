// Rehydrate Local Diffusers controls after a normal page refresh.
//
// Settings initializes several asynchronous cards at startup. The original
// local-image add-on could run before the stock Image Generation card finished
// rebuilding its select options, then never ran again. This guard refreshes the
// endpoint-aware controls when the Settings modal becomes visible or the AI tab
// is selected.

import { refreshImageDefaults } from './localImageIntegration.js';

let timer = null;
let installed = false;

function hasImageControls() {
  return !!document.getElementById('set-imgModelSelect');
}

function scheduleRefresh(delay = 0) {
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => {
    timer = null;
    if (!hasImageControls()) return;
    refreshImageDefaults().catch(error => {
      console.debug('[local-image] settings rehydrate skipped', error);
    });
  }, delay);
}

function scheduleSettledRefreshes() {
  // The stock card calls /api/models and /api/auth/settings asynchronously.
  // Reapply after each likely completion window so its old inpaint-only list
  // cannot overwrite the endpoint-aware local model list.
  scheduleRefresh(0);
  setTimeout(() => scheduleRefresh(250), 250);
  setTimeout(() => scheduleRefresh(900), 900);
  setTimeout(() => scheduleRefresh(1800), 1800);
}

function isSettingsTrigger(target) {
  return !!target?.closest?.(
    '#settings-btn, #tool-settings-btn, [data-open-settings], [data-settings-tab="ai"], #settings-modal'
  );
}

function install() {
  if (installed) return;
  installed = true;

  document.addEventListener('click', event => {
    if (isSettingsTrigger(event.target)) scheduleSettledRefreshes();
  });
  window.addEventListener('ge:model-endpoints-updated', scheduleSettledRefreshes);
  window.addEventListener('load', scheduleSettledRefreshes, { once: true });

  // Covers settings markup that is re-mounted by a plugin/theme path and modal
  // class changes that reveal the AI tab without a direct click on its tab.
  const observer = new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'attributes' && record.target?.id === 'settings-modal') {
        scheduleSettledRefreshes();
        return;
      }
      if (record.type === 'childList' && hasImageControls()) {
        const changed = [...record.addedNodes].some(node => {
          if (!(node instanceof Element)) return false;
          return node.id === 'set-imgModelSelect' || !!node.querySelector?.('#set-imgModelSelect');
        });
        if (changed) {
          scheduleSettledRefreshes();
          return;
        }
      }
    }
  });
  observer.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });

  scheduleSettledRefreshes();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
else install();
