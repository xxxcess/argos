from pathlib import Path


APP_JS = Path("static/app.js")
SESSION_CONTROL_JS = Path("static/js/sessionControl.js")


def _slice(source, start_marker, end_marker):
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


def test_send_button_stop_paths_run_before_new_chat_and_mic_modes():
    source = APP_JS.read_text(encoding="utf-8")
    body = _slice(
        source,
        "sendBtn.addEventListener('click'",
        "// Otherwise, send message",
    )

    recording_pos = body.index("sendBtn.dataset.mode === 'recording'")
    streaming_pos = body.index("sendBtn.dataset.mode === 'streaming'")
    current_stream_pos = body.index("chatModule?.hasActiveStream?.(currentStreamingSessionId)")
    mission_stream_pos = body.index("sessionControlModule?.stopActiveGeneration?.()")
    new_chat_pos = body.index("sendBtn.dataset.mode === 'newchat'")
    mic_pos = body.index("if (!hasText && !hasFiles && _isSttEnabled())")

    assert recording_pos < streaming_pos < current_stream_pos < mission_stream_pos < new_chat_pos < mic_pos
    assert "voiceRecorderModule.stopRecording();" in body
    assert "voiceRecorderModule.stopConversationLoop?.('manual');" in body
    assert "handleSubmit(e);" in body
    assert "chatModule.stopSessionGeneration?.(currentStreamingSessionId);" in body


def test_mission_control_exposes_active_generation_stop_for_composer_button():
    source = SESSION_CONTROL_JS.read_text(encoding="utf-8")
    body = _slice(
        source,
        "function stopActiveGeneration()",
        "function endVoiceLoop",
    )

    assert "state.activePanelSessionId" in body
    assert "snap?.status !== 'streaming'" in body
    assert "chatModule.hasActiveStream?.(sessionId)" in body
    assert "stopGeneration(sessionId);" in body
    assert "stopActiveGeneration" in source[source.index("const sessionControlModule"):]
