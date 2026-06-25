// Shared bridge for local-image actions that run through Cookbook.
//
// Do not POST /api/model/serve directly from a feature panel. The Cookbook
// launcher stores the returned session as a tracked task; that state is what
// creates the Active tab and keeps logs/status/stop controls available.

function nextFrame() {
  return new Promise(resolve => requestAnimationFrame(() => resolve()));
}

async function openRunningTab(renderRunningTab) {
  // Render first: the Active tab is created only when there is at least one
  // tracked task. Calling open({tab:'Running'}) before this used to make the
  // tab selector a no-op because the tab did not exist yet.
  renderRunningTab();

  if (window.cookbookModule?.open) {
    await window.cookbookModule.open({ tab: 'Running' });
    return;
  }

  // Defensive fallback for early/module-order startup. The ordinary app entry
  // assigns window.cookbookModule, but opening via the sidebar still works if
  // a feature panel is clicked before that assignment is observed.
  document.getElementById('tool-cookbook-btn')?.click();
  await nextFrame();
  renderRunningTab();
  document.querySelector('#cookbook-modal .cookbook-tab[data-backend="Running"]')?.click();
}

/**
 * Launch an image-related command as a first-class Cookbook task.
 *
 * The returned task remains visible in Cookbook → Active with the standard
 * output polling, error diagnosis, and stop/clear controls.
 */
export async function launchTrackedLocalImageTask({ name, repoId, cmd, fields = {} }) {
  // Dynamic import avoids pulling Cookbook into the UI bootstrap's module graph
  // until the user actually launches a local image action.
  const cookbook = await import('./cookbook.js');
  const before = new Set(cookbook._loadTasks().map(task => task.sessionId));

  // Explicit empty host makes these actions local even when the user has a
  // remote Cookbook server selected for LLM work. The local Diffusers endpoint
  // is intentionally loopback-only.
  await cookbook._launchServeTask(name, repoId, cmd, fields, '');

  const task = cookbook._loadTasks().find(candidate =>
    !before.has(candidate.sessionId)
    && candidate.type === 'serve'
    && candidate.payload?._cmd === cmd
  );
  if (!task) {
    throw new Error('Cookbook did not create a task. Check the launch error notification.');
  }

  await openRunningTab(cookbook._renderRunningTab);
  return task;
}
