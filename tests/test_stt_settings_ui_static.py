from pathlib import Path


def test_stt_defaults_enable_browser_provider():
    source = Path('src/settings.py').read_text(encoding='utf-8')
    assert '"stt_enabled": True' in source
    assert '"stt_provider": "browser"' in source


def test_voice_recorder_mounts_stt_settings_card():
    source = Path('static/js/voiceRecorder.js').read_text(encoding='utf-8')
    assert 'set-sttSettingsCard' in source
    assert 'Speech to Text' in source
    assert 'set-sttProviderSelect' in source
    assert 'stt_enabled' in source
    assert 'stt_provider' in source


def test_local_stt_dependency_is_installed():
    source = Path('requirements.txt').read_text(encoding='utf-8')
    assert 'faster-whisper' in source
