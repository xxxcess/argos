import subprocess
import textwrap
from pathlib import Path


def test_workspace_tab_controller_state_transitions():
    js = textwrap.dedent(
        """
        import assert from 'node:assert/strict';
        import {
          MAX_SESSION_TABS,
          canonicalSessionTabId,
          closeTabById,
          createInitialTabState,
          normalizeTabState,
          upsertSessionTab,
        } from './static/js/workspaceTabs.js';

        let state = createInitialTabState();
        state = upsertSessionTab(state, { id: 'a', name: 'Alpha' });
        state = upsertSessionTab(state, { id: 'a', name: 'Alpha renamed' });
        assert.deepEqual(state.order, ['home', 'session:a']);
        assert.equal(state.selected, 'session:a');
        assert.equal(state.tabs['session:a'].title, 'Alpha renamed');

        state.tabs['session:a'].view = { draft: 'hello', scrollTop: 42 };
        state = upsertSessionTab(state, { id: 'b', name: 'Beta' });
        assert.equal(state.tabs['session:a'].view.draft, 'hello');

        state = closeTabById(state, 'session:b');
        assert.equal(state.selected, 'session:a');
        state = closeTabById(state, 'home');
        assert.ok(state.tabs.home.pinned);

        state = normalizeTabState(state, [{ id: 'a', name: 'Authoritative A' }]);
        assert.equal(state.tabs[canonicalSessionTabId('a')].title, 'Authoritative A');

        const fallback = closeTabById(state, 'session:a');
        assert.equal(fallback.selected, 'home');

        for (let i = 0; i < MAX_SESSION_TABS + 3; i++) {
          state = upsertSessionTab(state, { id: 'extra-' + i, name: 'Extra ' + i });
        }
        assert.equal(state.order[0], 'home');
        assert.equal(state.order.length, MAX_SESSION_TABS + 1);
        assert.equal(new Set(state.order).size, state.order.length);
        """
    )
    subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        text=True,
        check=True,
        cwd=".",
    )


def test_workspace_account_menu_uses_single_settings_entry():
    source = Path("static/js/workspaceShell.js").read_text(encoding="utf-8")

    assert 'data-account-action="settings">Settings</button>' in source
    assert 'data-account-action="theme">Theme</button>' in source
    assert "Account settings" not in source
    assert "Preferences" not in source


def test_workspace_home_and_plus_use_durable_session_paths():
    source = Path("static/js/workspaceShell.js").read_text(encoding="utf-8")

    assert "startNewMissionDraft" in source
    assert "materializePendingSession({ source: 'workspace-plus', openInFullView: true })" in source
    assert "viewFullConversation(tab.sessionId)" in source
    assert "await _activateTab(canonicalSessionTabId(sid));" in source
    assert "createDirectChatFromPreferredModel" in source
    assert "const shouldSelect = detail.openInFullView || !window.sessionControlModule?.isComposingNewSession?.();" in source


def test_workspace_tabs_expose_chat_controls_next_to_session_title():
    shell = Path("static/js/workspaceShell.js").read_text(encoding="utf-8")
    index = Path("static/index.html").read_text(encoding="utf-8")

    assert "workspace-tab-menu-btn" in shell
    assert "odysseus:open-chat-controls" in shell
    assert "odysseus:request-session-tab-rename" in shell
    assert 'id="export-delete-btn"' in index
    assert "Delete chat session" in index
