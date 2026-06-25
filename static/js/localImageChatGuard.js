// Keep endpoints explicitly marked as image-only out of the normal chat picker.
// models.js already tags these rows with data-model-type="image"; this small
// observer covers both initial rendering and later endpoint refreshes without
// changing the chat picker implementation.

function hideImageRows() {
  document.querySelectorAll('#models [data-model-type="image"]').forEach(row => {
    row.style.display = 'none';
    row.setAttribute('aria-hidden', 'true');
  });
}

function install() {
  const box = document.getElementById('models');
  if (!box || box.dataset.localImageGuard === '1') return;
  box.dataset.localImageGuard = '1';
  new MutationObserver(hideImageRows).observe(box, { childList: true, subtree: true });
  hideImageRows();
  window.addEventListener('ge:model-endpoints-updated', () => setTimeout(hideImageRows, 100));
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
else install();
