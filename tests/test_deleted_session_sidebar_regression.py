from pathlib import Path


APP_JS = Path("static/app.js")
SESSIONS_JS = Path("static/js/sessions.js")
KEYBOARD_JS = Path("static/js/keyboard-shortcuts.js")


def test_rail_delete_uses_hard_delete_endpoint():
    source = APP_JS.read_text()
    rail_block = source[source.index("const railDelete = el('rail-delete-session');"):]
    rail_block = rail_block[:rail_block.index("// Textarea auto-resize")]

    assert "fetch(`${API_BASE}/api/session/${currentId}`, { method: 'DELETE' })" in rail_block
    assert "api/session/${currentId}/archive" not in rail_block
    assert "sessionControlModule?.showDashboardAfterSessionDelete?.();" in rail_block
    assert "showDashboardAfterSessionDelete?.(nextSession" not in rail_block
    assert "sessionModule.selectSession(nextSession.id)" not in rail_block


def test_confirmed_session_deletes_return_to_session_control_dashboard():
    sessions_source = SESSIONS_JS.read_text()
    delete_menu_block = sessions_source[sessions_source.index("deleteItem.addEventListener('click', async () => {"):]
    delete_menu_block = delete_menu_block[:delete_menu_block.index("archiveItem.addEventListener")]
    focused_delete_block = sessions_source[sessions_source.index("if (e.key === 'Delete' || e.key === 'Backspace')"):]
    focused_delete_block = focused_delete_block[:focused_delete_block.index("if (e.key === 'Enter')")]
    keyboard_source = KEYBOARD_JS.read_text()
    shortcut_block = keyboard_source[keyboard_source.index("if (_matchesCombo(e, kb.delete_session))"):]
    shortcut_block = shortcut_block[:shortcut_block.index("if (_matchesCombo(e, kb.new_session))")]

    assert "window.sessionControlModule?.showDashboardAfterSessionDelete?.();" in delete_menu_block
    assert "window.sessionControlModule?.showDashboardAfterSessionDelete?.();" in focused_delete_block
    assert "window.sessionControlModule?.showDashboardAfterSessionDelete?.();" in shortcut_block
    assert "dashboardTargetId" not in delete_menu_block
    assert "dashboardTargetId" not in focused_delete_block
    assert "showDashboardAfterSessionDelete?.(nextSession" not in shortcut_block


def test_deleted_sessions_are_pruned_from_local_sidebar_state():
    source = SESSIONS_JS.read_text()

    assert "function _removeSessionFromLocalState(sid)" in source
    assert "sessions = sessions.filter(s => String(s.id) !== id);" in source
    assert "Storage.set('session-order', JSON.stringify(orderIds.filter(x => String(x) !== id)))" in source
    assert "_removeSessionFromLocalState(s.id);" in source


def test_session_fetch_normalizes_duplicate_ids_before_render():
    source = SESSIONS_JS.read_text()

    assert "function _normalizeSessionsList(fetched)" in source
    assert "if (seen.has(id)) continue;" in source
    assert "sessions = _sortSessionsByActivity(_normalizeSessionsList(fetched));" in source
