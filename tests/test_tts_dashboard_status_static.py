from pathlib import Path


TTS_JS = Path("static/js/tts-ai.js")
SESSION_CONTROL_JS = Path("static/js/sessionControl.js")


def _slice(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_latest_final_tts_enqueues_immediately_without_debounce():
    source = TTS_JS.read_text(encoding="utf-8")
    body = _slice(
        source,
        "enqueueLatestFinal(text, button, resetFn, sessionId)",
        "async _processQueue()",
    )

    assert "setTimeout" not in body
    assert "_latestFinalDelayMs" not in source
    assert "this.enqueue(text, button, resetFn, sessionId);" in body


def test_tts_manager_emits_playback_state_for_dashboard_status():
    source = TTS_JS.read_text(encoding="utf-8")

    assert "odysseus:tts-playback-state" in source
    assert "_emitPlaybackState(true, sessionId)" in source
    assert "_emitPlaybackState(false, sessionId)" in source
    assert "this._playbackSessionId" in source


def test_session_control_overlays_speaking_status_from_tts_playback():
    source = SESSION_CONTROL_JS.read_text(encoding="utf-8")
    snapshot_body = _slice(
        source,
        "function snapshotFor(sessionId)",
        "function renderBubbleContent",
    )
    events_body = _slice(
        source,
        "document.addEventListener('odysseus:tts-playback-state'",
        "document.addEventListener('odysseus:sessions-updated'",
    )

    assert "speaking: 'Speaking'" in source
    assert "speakingSessions: new Set()" in source
    assert "state.speakingSessions.has(key)" in snapshot_body
    assert "status: 'speaking'" in snapshot_body
    assert "state.speakingSessions.add(key)" in events_body
    assert "state.speakingSessions.delete(key)" in events_body
