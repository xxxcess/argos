from pathlib import Path


def test_stt_defaults_enable_browser_provider():
    source = Path('src/settings.py').read_text(encoding='utf-8')
    assert '"stt_enabled": True' in source
    assert '"stt_provider": "browser"' in source


def test_stt_settings_ui_and_loop_controls_exist():
    source = Path('static/js/voiceRecorder.js').read_text(encoding='utf-8')
    for token in (
        'set-sttSettingsCard',
        'set-sttProviderSelect',
        'set-sttConversationLoopToggle',
        'set-sttLoopSubmitSeconds',
        'set-sttLoopIdleTimeoutSeconds',
        'stt_conversation_loop',
        'stt_loop_submit_seconds',
        'stt_loop_idle_timeout_seconds',
    ):
        assert token in source


def test_stt_loop_requires_fresh_user_speech():
    source = Path('static/js/voiceRecorder.js').read_text(encoding='utf-8')
    for token in (
        '_startVoiceActivityDetection',
        '_speechDetected',
        '_browserSpeechDetected',
        '_hasFreshTranscript',
        '_clearMessageInputForLoop',
        '_activeRecordingId',
        'recordingId !== _activeRecordingId',
    ):
        assert token in source


def test_stt_loop_waits_for_tts_and_discards_playback_capture():
    source = Path('static/js/voiceRecorder.js').read_text(encoding='utf-8')
    for token in (
        '_isTtsPlaybackActive',
        '_waitForTtsThenRestart',
        'TTS_POST_PLAYBACK_COOLDOWN_MS',
        '_discardCurrentRecordingForTts',
        "_stopRecordingInternal('loop-tts')",
        'discardForTts',
    ):
        assert token in source


def test_local_stt_dependency_is_installed():
    source = Path('requirements.txt').read_text(encoding='utf-8')
    assert 'faster-whisper' in source
