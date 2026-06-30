from pathlib import Path


APP_JS = Path("static/app.js")
MODEL_PICKER_JS = Path("static/js/modelPicker.js")
KEYBOARD_SHORTCUTS_JS = Path("static/js/keyboard-shortcuts.js")


def _slice(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_new_chat_prefers_user_pending_then_default_before_previous_session():
    source = APP_JS.read_text(encoding="utf-8")
    helper = _slice(
        source,
        "async function _createDirectChatFromPreferredModel()",
        "// ============================================",
    )

    default_pos = helper.index("const dc = await _refreshDefaultChat();")
    pending_pos = helper.index("sessionModule.getPendingChat")
    assert pending_pos < default_pos
    assert "pending.source !== 'auto' && pending.source !== 'default'" in helper
    assert default_pos < helper.index("current.endpoint_url")
    assert default_pos < helper.index("const withModel = sessions.filter")


def test_empty_new_chat_button_uses_default_aware_helper():
    source = APP_JS.read_text(encoding="utf-8")
    handler = _slice(
        source,
        "// New chat mode — empty input, no attachments, no STT",
        "// If input is empty and STT is enabled, start recording",
    )

    assert "await _createDirectChatFromPreferredModel()" in handler
    assert "current.endpoint_url" not in handler


def test_model_picker_only_user_pending_overrides_settings_default():
    source = MODEL_PICKER_JS.read_text(encoding="utf-8")
    updater = _slice(
        source,
        "export function updateModelPicker()",
        "const displayName = modelId ? modelId.split('/').pop() : 'Select model';",
    )

    assert "_pendingChat.source === 'user'" in updater
    assert "const dc = _defaultChatFromCache();" in updater
    assert "source: 'auto'" in updater


def test_desktop_new_chat_actions_use_shared_preference_helper():
    source = APP_JS.read_text(encoding="utf-8")

    rail_handler = _slice(
        source,
        "// New session button on icon rail",
        "// Mobile new chat button",
    )
    brand_handler = _slice(
        source,
        "// Logo click \u2192 new chat",
        "// Delete session button on icon rail",
    )

    assert "if (await _createDirectChatFromPreferredModel()) {" in rail_handler
    assert "if (await _createDirectChatFromPreferredModel()) {" in brand_handler
    assert "sessionControlModule?.viewNewChatDraft?.();" in rail_handler
    assert "sessionControlModule?.viewNewChatDraft?.();" in brand_handler
    assert "const dc = await _refreshDefaultChat();" not in rail_handler
    assert "const dc = await _refreshDefaultChat();" not in brand_handler


def test_new_chat_actions_exit_session_control_dashboard():
    source = APP_JS.read_text(encoding="utf-8")
    rail_handler = _slice(
        source,
        "// New session button on icon rail",
        "// Mobile new chat button",
    )
    mobile_handler = _slice(
        source,
        "// Mobile new chat button",
        "// Logo click \u2192 new chat",
    )
    brand_handler = _slice(
        source,
        "// Logo click \u2192 new chat",
        "// Delete session button on icon rail",
    )

    assert "sessionControlModule?.viewNewChatDraft?.();" in rail_handler
    assert "sessionControlModule?.viewNewChatDraft?.();" in mobile_handler
    assert "sessionControlModule?.viewNewChatDraft?.();" in brand_handler


def test_sidebar_new_chat_option_removed():
    source = APP_JS.read_text(encoding="utf-8")
    index = Path("static/index.html").read_text(encoding="utf-8")

    assert "sidebar-new-chat-btn" not in source
    assert "sidebar-new-chat-btn" not in index
    assert 'data-ui-key="sidebar-new-chat"' not in index


def test_new_session_shortcut_opens_full_conversation_when_session_control_exists():
    source = KEYBOARD_SHORTCUTS_JS.read_text(encoding="utf-8")
    shortcut_handler = _slice(
        source,
        "if (_matchesCombo(e, kb.new_session))",
        "if (_matchesCombo(e, kb.cancel))",
    )

    assert "window.sessionControlModule?.viewFullConversation" in shortcut_handler
    assert "window.sessionControlModule.viewFullConversation(data.id);" in shortcut_handler
    assert "await sessionModule.selectSession(data.id);" in shortcut_handler
