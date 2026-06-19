from pathlib import Path


def test_tts_manager_skips_rendered_thinking_by_default():
    source = Path('static/js/tts-ai.js').read_text(encoding='utf-8')
    assert 'includeThinkingSections = readStoredIncludeThinking()' in source
    assert "temp.querySelectorAll('.thinking-section').forEach(el => el.remove())" in source
    assert "temp.querySelectorAll('.thinking-header, .thinking-toggle').forEach(el => el.remove())" in source


def test_tts_settings_ui_adds_read_thinking_toggle():
    source = Path('static/js/tts-ai.js').read_text(encoding='utf-8')
    assert 'set-ttsIncludeThinkingToggle' in source
    assert 'Read thinking' in source
    assert 'tts_include_thinking' in source
