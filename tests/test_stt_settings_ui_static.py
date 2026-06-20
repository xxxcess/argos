from pathlib import Path


def test_stt_defaults_enable_browser_provider():
    source = Path('src/settings.py').read_text(encoding='utf-8')
    assert '"stt_enabled": True' in source
    assert '"stt_provider": "browser"' in source


def test_stt_conversation_loop_defaults_are_present():
    source = Path('src/settings.py').read_text(encoding='utf-8')
    assert '"stt_conversation_loop": False' in source
    assert '"stt_loop_submit_seconds": 3' in source
    assert '"stt_loop_idle_timeout_seconds": 5' in source


def test_voice_recorder_mounts_stt_settings_card():
    source = Path('static/js/voiceRecorder.js').read_text(encoding='utf-8')
    assert 'set-sttSettingsCard' in source
    assert 'Speech to Text' in source
    assert 'set-sttProviderSelect' in source
    assert 'stt_enabled' in source
    assert 'stt_provider' in source


def test_voice_recorder_wires_conversation_loop_controls():
    source = Path('static/js/voiceRecorder.js').read_text(encoding='utf-8')
    assert 'set-sttConversationLoopToggle' in source
    assert 'set-sttLoopSubmitSeconds' in source
    assert 'set-sttLoopIdleTimeoutSeconds' in source
    assert 'stt_conversation_loop' in source
    assert 'stt_loop_submit_seconds' in source
    assert 'stt_loop_idle_timeout_seconds' in source
    assert '_watchSendButtonForLoop' in source
    assert '_submitCurrentTranscription' in source


def test_voice_recorder_gates_loop_on_fresh_speech():
    source = Path('static/js/voiceRecorder.js').read_text(encoding='utf-8')
    assert '_startVoiceActivityDetection' in source
    assert '_speechDetected' in source
    assert '_browserSpeechDetected' in source
    assert '_hasFreshTranscript' in source
    assert 'if (!_speechDetected && !_browserSpeechDetected)' in source
    assert 'Never turn a timer into an instruction' in source


def test_voice_recorder_prevents_stale_prompt_loop_submits():
    source = Path('static/js/voiceRecorder.js').read_text(encoding='utf-8')
    assert '_clearMessageInputForLoop' in source
    assert 'text !== expected' in source
    assert '_activeRecordingId' in source
    assert 'recordingId !== _activeRecordingId' in source


def test_voice_recorder_releases_recorder_before_auto_submit():
    source = Path('static/js/voiceRecorder.js').read_text(encoding='utf-8')
    assert '_releaseRecordingUi' in source
    assert '_handleLoopTranscript' in source
    assert 'data-mode="recording"' in source
    onstop = source.index('mediaRecorder.onstop')
    assert source.index('_releaseRecordingUi();', onstop) < source.index('_handleLoopTranscript(', onstop)


def test_voice_recorder_prevents_duplicate_loop_submits():
    source = Path('static/js/voiceRecorder.js').read_text(encoding='utf-8')
    assert '_lastAutoSubmittedText' in source
    assert 'DUPLICATE_SUBMIT_WINDOW_MS' in source
    assert 'text !== expected' in source


def test_local_stt_dependency_is_installed():
    source = Path('requirements.txt').read_text(encoding='utf-8')
    assert 'faster-whisper' in source
