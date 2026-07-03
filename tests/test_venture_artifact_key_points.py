from types import SimpleNamespace

import src.venture_synthesis as synthesis
from src.venture_artifact_quality_v2 import (
    install_artifact_quality_contract,
    is_displayable_memory,
)

install_artifact_quality_contract()


def _labels():
    return {
        "C1": {
            "label": "C1",
            "kind": "conversation",
            "speaker": "Captain",
            "locator": "Voyage Log · message 1",
            "text": "Please identify why staged imports do not advance after the first completed item.",
            "captured_at": "2026-07-03T12:00:00Z",
        },
        "C2": {
            "label": "C2",
            "kind": "conversation",
            "speaker": "Shipmate: alex",
            "locator": "Voyage Log · message 2",
            "text": "The later selections stay queued even though the first selection reports complete.",
            "captured_at": "2026-07-03T12:01:00Z",
        },
        "C3": {
            "label": "C3",
            "kind": "conversation",
            "speaker": "Argo",
            "locator": "Voyage Log · message 3",
            "text": "The scheduler examines active work before the terminal status is flushed.",
            "captured_at": "2026-07-03T12:02:00Z",
        },
        "S1": {
            "label": "S1",
            "kind": "source",
            "source_name": "Index worker",
            "source_type": "document",
            "locator": "queue handoff",
            "text": "A completed job must be flushed before the next queued item is considered.",
            "captured_at": "2026-07-03T12:03:00Z",
        },
    }


def _payload():
    return {
        "should_create": True,
        "title": "Queue handoff needs durable completion before scheduling",
        "claim_key": "queue-handoff-durable-completion",
        "conversation_observations": [
            {
                "speaker": "Captain",
                "kind": "request",
                "text": "The Captain asked why staged imports do not advance after the first completed item.",
                "citations": ["C1"],
            },
            {
                "speaker": "Shipmate: alex",
                "kind": "observation",
                "text": "A shipmate observed that later selections remain queued after the first item reports complete.",
                "citations": ["C2"],
            },
            {
                "speaker": "Argo",
                "kind": "observation",
                "text": "Argo traced the issue to terminal state visibility during queue handoff.",
                "citations": ["C3"],
            },
        ],
        "questions_raised": [
            {
                "text": "Should the next queued item be scheduled only after terminal state is durably flushed?",
                "citations": ["C1", "C3"],
            }
        ],
        "key_points": [
            {
                "title": "Durable completion controls queue progression",
                "revelation": "Later selections can remain queued when scheduling checks active work before the completed state is flushed.",
                "category": "finding",
                "confidence": "high",
                "citations": ["C3", "S1"],
            },
            {
                "title": "Staged processing remains the intended model",
                "revelation": "The repair can preserve serialized processing while changing the handoff order that releases the next selected item.",
                "category": "decision",
                "confidence": "medium",
                "citations": ["C1", "C2"],
            },
            {
                "title": "Queue regression coverage is required",
                "revelation": "A regression test should prove that later selected items advance only after the prior item reaches durable completion.",
                "category": "next_step",
                "confidence": "medium",
                "citations": ["C1", "S1"],
            },
        ],
        "interpretation": "The evidence points to a handoff-order defect rather than a selection or permission failure.",
        "recommended_next_bearing": ["Add a flush-before-active-check regression test."],
    }


def test_validation_accepts_cited_revelations_and_chronological_path():
    result = synthesis.validate_synthesis_json(_payload(), _labels())

    assert result["should_create"] is True
    assert len(result["conversation_observations"]) == 3
    assert len(result["key_points"]) == 3
    assert all(point["revelation"] for point in result["key_points"])


def test_numeric_or_generic_artifact_title_is_replaced_with_a_key_point_title():
    payload = _payload()
    payload["title"] = "1."

    result = synthesis.validate_synthesis_json(payload, _labels())

    assert result["title"] == "Durable completion controls queue progression"


def test_memory_is_extracted_only_from_explicit_revelation_lines():
    result = synthesis.validate_synthesis_json(_payload(), _labels())
    result["quest_title"] = "Queue reliability"
    result["current_bearing"] = "Stabilize staged processing"
    document = synthesis.render_artifact_markdown(result, _labels())
    document += "\n## Why this matters\nThis extra evidence-backed paragraph must not become a memory. [C1]\n"
    proposal = SimpleNamespace(document=SimpleNamespace(current_content=document))

    candidates = synthesis._extract_artifact_key_point_candidates(proposal)

    assert len(candidates) == 3
    assert candidates[0]["content"] == _payload()["key_points"][0]["revelation"]
    assert candidates[0]["citations"] == ["C3", "S1"]


def test_raw_numbered_evidence_cannot_become_a_revelation_or_visible_memory():
    payload = _payload()
    payload["key_points"][0]["revelation"] = "1. He entered the city. 2. They brought him a man lying on a bed. 3. Jesus said their sins were forgiven."

    result = synthesis.validate_synthesis_json(payload, _labels())

    assert result["should_create"] is False
    legacy_dump = SimpleNamespace(
        artifact_id="artifact-1",
        created_by="argo",
        title="1",
        content="1. He entered the city. 2. They brought him a man lying on a bed. 3. Jesus said their sins were forgiven.",
    )
    assert is_displayable_memory(legacy_dump) is False
