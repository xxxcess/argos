"""Fallbacks that keep grounded Captain-requested Artifacts reviewable.

A Quest synthesis request must not remain invisible merely because a model emits
incomplete citation JSON or decides there is "no durable insight" after the
Captain explicitly requested a reviewable Artifact. The fallback uses only the
existing conversation/source evidence and still has to satisfy the Artifact
validation contract.
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
    original_persist = synthesis.persist_synthesis_result

    def grounded_fallback(labels: dict):
        return original_validate(synthesis._fallback_synthesis(_fallback_pack(labels)), labels)

    def validate(data, labels):
        try:
            result = original_validate(data, labels)
        except ValueError:
            if not _can_fallback(labels):
                raise
            return grounded_fallback(labels)

        if result.get("should_create") is False and result.get("reason") in _FALLBACK_REASONS and _can_fallback(labels):
            return grounded_fallback(labels)
        return result

    def persist(db, job, synth, labels):
        # A Captain deliberately asked for a draft. When enough cited material
        # exists, a local evidence-only draft is preferable to silently marking
        # the job no_insight because a model chose a conservative false branch.
        if (
            job.trigger == "captain_requested"
            and not synth.get("should_create")
            and _can_fallback(labels)
        ):
            synth = grounded_fallback(labels)
        return original_persist(db, job, synth, labels)

    synthesis.validate_synthesis_json = validate
    synthesis.persist_synthesis_result = persist
    synthesis._validation_fallback_installed = True
