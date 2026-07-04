"""Fallback for incomplete model synthesis output.

A Quest synthesis request must not remain invisible merely because a model emits
valid JSON with too few cited fields.  This guard falls back only to the
existing deterministic, evidence-only Artifact builder and only when the
conversation/source window can satisfy the Artifact contract.
"""

from __future__ import annotations


_FALLBACK_REASONS = {
    "insufficient_grounded_conversation",
    "insufficient_grounded_key_points",
}


def _fallback_pack(labels: dict) -> dict:
    conversations = []
    sources = []
    for label, item in labels.items():
        if not isinstance(item, dict):
            continue
        if label.startswith("C"):
            conversations.append(dict(item))
        elif label.startswith("S"):
            sources.append(dict(item))
    return {
        "conversation_items": conversations,
        "source_chunks": sources,
    }


def _can_fallback(labels: dict) -> bool:
    conversation_path = any(
        label.startswith("C") and isinstance(item, dict) and item.get("is_conversation_path")
        for label, item in labels.items()
    )
    grounded_items = sum(1 for label in labels if label.startswith(("C", "S")))
    return conversation_path and grounded_items >= 3


def install_synthesis_validation_fallback() -> None:
    import src.venture_synthesis as synthesis

    if getattr(synthesis, "_validation_fallback_installed", False):
        return

    original_validate = synthesis.validate_synthesis_json

    def validate(data, labels):
        try:
            result = original_validate(data, labels)
        except ValueError:
            if not _can_fallback(labels):
                raise
            fallback = synthesis._fallback_synthesis(_fallback_pack(labels))
            return original_validate(fallback, labels)

        if result.get("should_create") is False and result.get("reason") in _FALLBACK_REASONS and _can_fallback(labels):
            fallback = synthesis._fallback_synthesis(_fallback_pack(labels))
            return original_validate(fallback, labels)
        return result

    synthesis.validate_synthesis_json = validate
    synthesis._validation_fallback_installed = True
