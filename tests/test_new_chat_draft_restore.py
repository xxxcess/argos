from pathlib import Path


SESSIONS_JS = Path("static/js/sessions.js")


def _slice(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_load_sessions_restores_new_chat_draft_before_auto_selecting_previous_chat():
    source = SESSIONS_JS.read_text(encoding="utf-8")
    body = _slice(
        source,
        "export async function loadSessions()",
        "export async function selectSession",
    )

    draft_pos = body.index("const persistedDraft = _readNewChatDraft();")
    target_pos = body.index("let targetId = null;")
    auto_select_pos = body.index("} else if (!_skipAutoSelect && _realSessions.length > 0) {")

    assert draft_pos < target_pos < auto_select_pos
    assert "const hasNewChatDraft = !!_pendingChat || restoringNewChatDraft;" in body
    assert "if (hasNewChatDraft)" in body
    assert "if (!targetId && hasNewChatDraft)" in body
    assert "_showNewChatUi({ focus: window.innerWidth > 768 });" in body


def test_new_chat_paths_persist_and_clear_draft_marker():
    source = SESSIONS_JS.read_text(encoding="utf-8")

    create_body = _slice(
        source,
        "export function createDirectChat",
        "/** Actually create the session in the DB.",
    )
    assert "_writeNewChatDraft({" in create_body
    assert "url: url || ''," in create_body
    assert "modelId: modelId || ''," in create_body
    assert "endpointId: endpointId || ''," in create_body
    assert "source: _pendingChat.source || ''," in create_body
    assert "Storage.remove('lastSessionId');" in create_body
    assert "_showNewChatUi();" in create_body

    setter_body = _slice(
        source,
        "export function setCurrentSessionId",
        "// Session list keyboard navigation",
    )
    assert "_writeNewChatDraft({});" in setter_body
    assert "_clearNewChatDraft();" in setter_body

    materialize_body = _slice(
        source,
        "export async function materializePendingSession(options = {})",
        "export function hasPendingChat()",
    )
    assert "_clearNewChatDraft();" in materialize_body
    assert "openInFullView: !!options.openInFullView" in materialize_body


def test_select_session_applies_ai_default_chat_model_before_history_load():
    source = SESSIONS_JS.read_text(encoding="utf-8")
    helper_body = _slice(
        source,
        "async function _applyDefaultChatModelToSession",
        "function _showNewChatUi",
    )
    default_helper = _slice(
        source,
        "async function _getDefaultChatConfig",
        "async function _applyDefaultChatModelToSession",
    )
    select_body = _slice(
        source,
        "export async function selectSession",
        "// Guard: if the fetched history is empty",
    )

    assert "fetch(`${API_BASE}/api/default-chat`" in default_helper
    assert "const dc = await _getDefaultChatConfig();" in helper_body
    assert "fetch(`${API_BASE}/api/session/${sessionId}`" in helper_body
    assert "method: 'PATCH'" in helper_body
    assert "fd.append('model', dc.model);" in helper_body
    assert "fd.append('endpoint_url', dc.endpoint_url);" in helper_body
    assert "sMeta.model = dc.model;" in helper_body

    default_pos = select_body.index("const defaultChat = await _applyDefaultChatModelToSession(id, meta);")
    picker_pos = select_body.index("updateModelPicker();")
    history_pos = select_body.index("const res = await fetch(`${API_BASE}/api/history/${id}`);")
    assert default_pos < picker_pos < history_pos
    assert "modelName = defaultChat?.model || data.model || null;" in select_body
