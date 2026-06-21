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
    assert "_writeNewChatDraft({ url: url || '', modelId: modelId || '', endpointId: endpointId || '' });" in create_body
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
        "export async function materializePendingSession()",
        "export function hasPendingChat()",
    )
    assert "_clearNewChatDraft();" in materialize_body
