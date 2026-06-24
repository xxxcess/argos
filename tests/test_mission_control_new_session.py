from pathlib import Path


APP_JS = Path("static/app.js")
SESSION_CONTROL_JS = Path("static/js/sessionControl.js")
STYLE_CSS = Path("static/style.css")


def _slice(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_mission_control_init_enters_new_session_draft():
    source = SESSION_CONTROL_JS.read_text(encoding="utf-8")
    init_body = _slice(
        source,
        "export function init()",
        "const sessionControlModule",
    )

    assert "startNewMissionDraft();" in init_body
    assert "setDashboardVisible(true);" not in init_body


def test_mission_control_exposes_new_session_mode_guard():
    source = SESSION_CONTROL_JS.read_text(encoding="utf-8")
    guard_body = _slice(
        source,
        "function isComposingNewSession()",
        "function focusMission",
    )

    assert "state.dashboardVisible" in guard_body
    assert "state.mode === 'new-mission'" in guard_body
    assert "!state.isActiveMissionOpen" in guard_body
    assert "!state.activePanelSessionId" in guard_body
    assert "isComposingNewSession" in source[source.index("const sessionControlModule"):]


def test_mission_control_exposes_full_view_new_chat_draft():
    source = SESSION_CONTROL_JS.read_text(encoding="utf-8")
    draft_body = _slice(
        source,
        "function viewNewChatDraft()",
        "function closeActiveMission",
    )

    assert "setDashboardVisible(false);" in draft_body
    assert "chatModule?.showWelcomeScreen" in draft_body
    assert "viewNewChatDraft" in source[source.index("const sessionControlModule"):]


def test_full_conversation_navigation_closes_dashboard_active_panel():
    source = SESSION_CONTROL_JS.read_text(encoding="utf-8")
    helper_body = _slice(
        source,
        "function closeActivePanelForFullConversation()",
        "function viewFullConversation",
    )
    full_view_body = _slice(
        source,
        "function viewFullConversation(sessionId)",
        "function viewNewChatDraft",
    )

    assert "state.activePanelSessionId = null;" in helper_body
    assert "state.isActiveMissionOpen = false;" in helper_body
    assert "els.activePanel.hidden = true;" in helper_body
    assert "closeActivePanelForFullConversation();" in full_view_body
    assert full_view_body.index("closeActivePanelForFullConversation();") < full_view_body.index("setDashboardVisible(false);")


def test_mission_control_exposes_post_delete_dashboard_navigation():
    source = SESSION_CONTROL_JS.read_text(encoding="utf-8")
    helper_body = _slice(
        source,
        "function showDashboardAfterSessionDelete",
        "function closeActiveMission",
    )

    assert "startNewMissionDraft();" in helper_body
    assert "focusMission(" not in helper_body
    assert "showDashboardAfterSessionDelete" in source[source.index("const sessionControlModule"):]


def test_mission_control_moves_attachment_strip_with_dashboard_composer():
    source = SESSION_CONTROL_JS.read_text(encoding="utf-8")
    move_body = _slice(
        source,
        "function moveComposerIntoCommandBar()",
        "function restoreComposerToChat()",
    )
    restore_body = _slice(
        source,
        "function restoreComposerToChat()",
        "function buildShell()",
    )

    assert "let _attachmentHome = null;" in source
    assert "document.getElementById('attach-strip')" in move_body
    assert "els.composerSlot.appendChild(attachStrip);" in move_body
    assert "_attachmentHome.parent.insertBefore(attachStrip" in restore_body
    assert "_attachmentHome.parent.appendChild(attachStrip);" in restore_body


def test_submit_clears_stale_session_id_for_mission_new_session_mode():
    source = APP_JS.read_text(encoding="utf-8")
    submit_body = _slice(
        source,
        "function handleSubmit(e)",
        "chatForm.onsubmit = handleSubmit;",
    )

    guard_pos = submit_body.index("sessionControlModule?.isComposingNewSession?.()")
    original_submit_pos = submit_body.index("return originalSubmit.call(chatModule, e);")
    assert guard_pos < original_submit_pos
    assert "sessionModule.setCurrentSessionId(null);" in submit_body


def test_mission_active_panel_header_uses_icon_controls_and_scroll_lock_toggle():
    source = SESSION_CONTROL_JS.read_text(encoding="utf-8")
    render_body = _slice(
        source,
        "function renderActivePanel()",
        "function operationsItems()",
    )

    assert "mission-panel-controls" in render_body
    assert 'id="mission-scroll-bottom"' in render_body
    assert 'aria-pressed="true"' in render_body
    assert "toggleActivePanelAutoScroll" in render_body
    assert "updateActivePanelBottomButton" in render_body
    assert "View Full Conversation" not in render_body
    assert "Back to Bottom" not in render_body
    assert "Close Session" not in render_body

    toggle_body = _slice(
        source,
        "function toggleActivePanelAutoScroll()",
        "function renderActivePanel()",
    )
    assert "state.activePanelAutoScroll = !state.activePanelAutoScroll;" in toggle_body
    assert "scrollActivePanelToBottom();" in toggle_body

    scroll_body = _slice(
        source,
        "function scrollActivePanelToBottom(options = {})",
        "function updateActivePanelBottomButton()",
    )
    assert "setTimeout(run, delayMs)" in scroll_body
    assert "clearActivePanelScrollTimer();" in scroll_body
    assert "delayMs: 1000" in render_body


def test_mission_dashboard_recent_activity_sits_before_recent_chats_and_opens_tasks_activity():
    source = SESSION_CONTROL_JS.read_text(encoding="utf-8")
    shell_body = _slice(
        source,
        "function buildShell()",
        "topBar.insertAdjacentElement",
    )
    activity_pos = shell_body.index("mission-recent-activity-section")
    chats_pos = shell_body.index("mission-recent-title")
    assert activity_pos < chats_pos
    assert 'id="mission-activity-feed"' in shell_body

    render_body = _slice(
        source,
        "function renderRecentActivity()",
        "function clearActivePanelScrollTimer()",
    )
    assert "/api/tasks/runs/recent?limit=8" in source
    assert "window.tasksModule?.openActivity" in render_body
    assert "runId: row.dataset.runId" in render_body
    assert "taskId: row.dataset.taskId" in render_body


def test_recent_chat_cards_put_status_and_icon_open_action_in_header():
    source = SESSION_CONTROL_JS.read_text(encoding="utf-8")
    render_chats = _slice(
        source,
        "function renderChats()",
        "async function loadRecentActivity",
    )

    card_top = _slice(
        render_chats,
        '<div class="mission-card-top">',
        '<div class="mission-card-preview"',
    )
    assert "mission-card-actions" in card_top
    assert "mission-open-chat" in card_top
    assert 'aria-label="Open chat"' in card_top
    assert "Open Chat" not in render_chats
    assert "memory-toolbar-btn mission-open-chat" not in render_chats


def test_mission_thinking_mirror_collapses_full_chat_state():
    source = SESSION_CONTROL_JS.read_text(encoding="utf-8")
    strip_body = _slice(
        source,
        "function stripIds",
        "function isTerminalStatus",
    )

    assert "content.classList.remove('expanded');" in strip_body
    assert "toggle.classList.remove('expanded');" in strip_body
    assert "header.setAttribute('aria-expanded', 'false');" in strip_body


def test_mission_dashboard_hides_global_scroll_button_and_uses_full_width_replies():
    css = STYLE_CSS.read_text(encoding="utf-8")
    hide_rule = _slice(
        css,
        "body.mission-dashboard-visible #scroll-bottom-btn",
        ".mission-control {",
    )
    assert "display: none !important;" in hide_rule

    ai_rule = _slice(
        css,
        ".mission-panel-msg.msg-ai",
        ".mission-panel-chat .agent-thread",
    )
    assert "width: min(80vw, 100%);" in ai_rule
    assert "max-width: min(80vw, 100%);" in ai_rule
    assert "align-self: center;" in ai_rule

    agent_rule = _slice(
        css,
        ".mission-panel-chat .agent-thread",
        ".mission-panel-chat .agent-thinking-dots",
    )
    assert "width: 100%;" in agent_rule
    assert "max-width: 100%;" in agent_rule
    assert "overflow: hidden;" in agent_rule

    chat_rule = _slice(
        css,
        ".mission-panel-chat {",
        ".mission-panel-msg {",
    )
    assert "overflow-x: hidden;" in chat_rule

    subtree_rule = _slice(
        css,
        ".mission-panel-chat *",
        ".mission-panel-msg .body > .thinking-section:first-child:last-child",
    )
    assert "max-width: 100%;" in subtree_rule
    assert "overflow-wrap: anywhere;" in subtree_rule

    thinking_rule = _slice(
        css,
        ".mission-panel-chat .thinking-section",
        ".mission-latest-block",
    )
    assert "min-height: 24px;" in thinking_rule
    assert "max-height: 120px;" in thinking_rule

    activity_rule = _slice(
        css,
        ".mission-activity-feed",
        ".mission-chat-card,"
    )
    assert ".mission-activity-row" in activity_rule
    assert "grid-template-columns: auto minmax(0, 1fr) auto;" in activity_rule


def test_mission_dashboard_attachment_strip_anchors_to_command_input():
    css = STYLE_CSS.read_text(encoding="utf-8")
    composer_rule = _slice(
        css,
        "#mission-composer-slot {",
        "body.mission-dashboard-visible .mission-control .model-picker-menu",
    )

    assert "#mission-composer-slot .attach-strip" in composer_rule
    assert "order: 0;" in composer_rule
    assert "#mission-composer-slot:has(.attach-strip .thumb-image)" in composer_rule
    assert "flex-direction: row;" in composer_rule
    assert "#mission-composer-slot .attach-strip:has(.thumb-image)" in composer_rule
    assert "order: 0;" in composer_rule
    assert "margin: 4px 5px 4px 0;" in composer_rule
    assert "padding: 8px 8px 8px 2px;" in composer_rule
    assert "max-width: min(34vw, 240px);" in composer_rule
