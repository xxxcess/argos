from pathlib import Path


SESSIONS_JS = Path("static/js/sessions.js")


def _slice(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_sidebar_session_click_routes_through_full_conversation_view():
    source = SESSIONS_JS.read_text(encoding="utf-8")

    click_handler = _slice(
        source,
        "div.addEventListener('click', (e) => {",
        "// Create a dropdown menu button",
    )
    helper = _slice(
        source,
        "function openSidebarSession(sessionId)",
        "// Session list keyboard navigation",
    )
    enter_handler = _slice(
        source,
        "if (e.key === 'Enter')",
        "// Initialize drag sorting",
    )

    assert "openSidebarSession(s.id);" in click_handler
    assert "window.sessionControlModule?.viewFullConversation" in helper
    assert "window.sessionControlModule.viewFullConversation(sessionId);" in helper
    assert "selectSession(sessionId);" in helper
    assert "openSidebarSession(sid);" in enter_handler
