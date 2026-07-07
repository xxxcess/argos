from src.venture_synthesis_validation_fallback import _can_fallback, _fallback_pack


def test_fallback_requires_a_conversation_path_and_three_grounded_items():
    labels = {
        "C1": {"label": "C1", "is_conversation_path": True, "text": "Captain asked a focused question."},
        "S1": {"label": "S1", "text": "Source evidence one."},
        "S2": {"label": "S2", "text": "Source evidence two."},
    }
    assert _can_fallback(labels)


def test_fallback_does_not_run_without_a_chronological_conversation_anchor():
    labels = {
        "C1": {"label": "C1", "is_conversation_path": False, "text": "Conversation note."},
        "S1": {"label": "S1", "text": "Source evidence one."},
        "S2": {"label": "S2", "text": "Source evidence two."},
    }
    assert not _can_fallback(labels)


def test_fallback_pack_preserves_conversation_and_source_provenance():
    labels = {
        "C1": {"label": "C1", "is_conversation_path": True, "text": "Captain asked a focused question."},
        "S1": {"label": "S1", "text": "Source evidence one."},
    }
    pack = _fallback_pack(labels)
    assert [item["label"] for item in pack["conversation_items"]] == ["C1"]
    assert [item["label"] for item in pack["source_chunks"]] == ["S1"]
