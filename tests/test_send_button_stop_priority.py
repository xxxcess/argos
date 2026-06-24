from pathlib import Path


APP_JS = Path("static/app.js")
SESSION_CONTROL_JS = Path("static/js/sessionControl.js")
VOICE_JS = Path("static/js/voiceRecorder.js")


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
    assert "voiceRecorderModule.stopRecording({" in body
    assert "submitLoopTranscript: voiceRecorderModule.isConversationLoopActive?.() === true" in body
    assert "if (hasReadyText)" in body
    assert "voiceRecorderModule.stopConversationLoop?.('manual');" in body
    assert "handleSubmit(e);" in body
    assert "chatModule.stopSessionGeneration?.(currentStreamingSessionId);" in body


def test_conversation_loop_manual_recording_stop_submits_current_turn():
    source = VOICE_JS.read_text(encoding="utf-8")
    stop_body = _slice(
        source,
        "export function stopRecording(options = {})",
        "export function isConversationLoopActive()",
    )
    recorder_stop_block = _slice(
        source,
        "mediaRecorder.onstop = async () => {",
        "mediaRecorder.start();",
    )

    assert "stopReason === 'loop-manual-submit'" in recorder_stop_block
    assert "options && options.submitLoopTranscript === true" in stop_body
    assert "_stopRecordingInternal('loop-manual-submit');" in stop_body
    assert "isConversationLoopActive" in source[source.index("const voiceRecorderModule"):]


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
