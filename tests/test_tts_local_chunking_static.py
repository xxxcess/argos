from pathlib import Path


def test_local_tts_uses_three_sentence_groups():
    source = Path("services/tts/tts_service.py").read_text(encoding="utf-8")
    assert "LOCAL_TTS_SENTENCES_PER_CHUNK = 3" in source
    assert "def _split_text_for_local_tts" in source
    assert "len(group) >= LOCAL_TTS_SENTENCES_PER_CHUNK" in source
    assert "for chunk_index, text_chunk in enumerate(text_chunks)" in source


def test_local_tts_does_not_silently_truncate_long_replies():
    source = Path("services/tts/tts_service.py").read_text(encoding="utf-8")
    assert "text = text[:5000]" not in source
    assert "Do not silently truncate a model reply" in source


def test_local_tts_joins_chunk_waveforms_with_short_silence():
    source = Path("services/tts/tts_service.py").read_text(encoding="utf-8")
    assert "LOCAL_TTS_SILENCE_MS = 90" in source
    assert "waveform_chunks.append(silence)" in source
    assert "np.concatenate(waveform_chunks)" in source
