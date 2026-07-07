"""Resource governor for Venture synthesis.

The Venture worker is intentionally single-owner. This module is installed by
``routes.venture_routes`` during application import and patches the existing
synthesis module without changing its public contract. It prevents duplicate
synthesis jobs, atomically claims work, and bounds conversation/source hydration
before an LLM request is assembled.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import load_only


MAX_HEAD_MESSAGES = 12
MAX_TAIL_MESSAGES = 36
MAX_SOURCE_CHUNKS = 4
MAX_SOURCE_PREVIEW_CHARS = 900


def _json_loads(synthesis, value: str | None, fallback):
    return synthesis._loads(value, fallback)


def _bounded_conversation_evidence(synthesis, db, quest):
    """Load at most 48 messages instead of hydrating an entire Voyage Log."""
    memberships = {
        row.username: row.role
        for row in db.query(synthesis.QuestMember)
        .options(load_only(synthesis.QuestMember.username, synthesis.QuestMember.role))
        .filter(synthesis.QuestMember.session_id == quest.id)
        .all()
        if row.username
    }
    columns = (
        synthesis.ChatMessage.id,
        synthesis.ChatMessage.role,
        synthesis.ChatMessage.content,
        synthesis.ChatMessage.timestamp,
        synthesis.ChatMessage.meta_data,
    )
    head = (
        db.query(synthesis.ChatMessage)
        .options(load_only(*columns))
        .filter(synthesis.ChatMessage.session_id == quest.id)
        .order_by(synthesis.ChatMessage.timestamp.asc(), synthesis.ChatMessage.id.asc())
        .limit(MAX_HEAD_MESSAGES)
        .all()
    )
    tail = (
        db.query(synthesis.ChatMessage)
        .options(load_only(*columns))
        .filter(synthesis.ChatMessage.session_id == quest.id)
        .order_by(synthesis.ChatMessage.timestamp.desc(), synthesis.ChatMessage.id.desc())
        .limit(MAX_TAIL_MESSAGES)
        .all()
    )
    rows_by_id = {row.id: row for row in [*head, *tail]}
    rows = sorted(rows_by_id.values(), key=lambda row: (row.timestamp, row.id))
    candidates = [row for row in rows if synthesis._is_substantive_message(row)]
    if not candidates:
        return [], {}

    first, last = candidates[0], candidates[-1]
    middle = max(candidates[1:-1], key=synthesis._message_score, default=None)
    path_ids = {first.id, last.id}
    if middle:
        path_ids.add(middle.id)

    selected_ids = set(path_ids)
    for row in candidates:
        text = row.content or ""
        is_question = "?" in text or any(marker in text.lower() for marker in ("open question", "what should", "should we", "can we", "do we"))
        if is_question and len(selected_ids) < synthesis.MAX_CONVERSATION_OBSERVATIONS + synthesis.MAX_CONVERSATION_QUESTIONS:
            selected_ids.add(row.id)
    selected = [row for row in candidates if row.id in selected_ids]

    labels: dict[str, dict[str, Any]] = {}
    items: list[dict[str, Any]] = []
    path_sequence = 0
    for row in selected:
        label = f"C{len(items) + 1}"
        is_path = row.id in path_ids
        if is_path:
            path_sequence += 1
        metadata = synthesis._message_metadata(row)
        item = {
            "label": label,
            "kind": "conversation",
            "speaker": synthesis._conversation_speaker(row, quest, memberships),
            "message_role": row.role,
            "text": " ".join((row.content or "").split())[:1200],
            "locator": f"Voyage Log · message {len(items) + 1}",
            "message_id": row.id,
            "captured_at": row.timestamp.isoformat() + "Z" if row.timestamp else None,
            "is_conversation_path": is_path,
            "path_sequence": path_sequence if is_path else None,
            "metadata": metadata,
        }
        labels[label] = item
        items.append(item)
    return items, labels


def _bounded_evidence_pack(synthesis, db, job):
    """Build synthesis input with SQL-side text previews and no N+1 source reads."""
    quest = db.query(synthesis.DbSession).filter(synthesis.DbSession.id == job.quest_id).first()
    bearing = db.query(synthesis.QuestBearing).filter(synthesis.QuestBearing.session_id == job.quest_id).first()
    if not quest or not bearing:
        raise ValueError("quest_not_found")

    conversation_items, labels = _bounded_conversation_evidence(synthesis, db, quest)
    run = None
    if job.retrieval_run_id:
        run = db.query(synthesis.QuestRetrievalRun).filter(
            synthesis.QuestRetrievalRun.id == job.retrieval_run_id,
            synthesis.QuestRetrievalRun.quest_id == job.quest_id,
        ).first()
    requested_ids = _json_loads(synthesis, run.chunk_ids_json if run else None, [])

    base = (
        db.query(
            synthesis.QuestEvidenceChunk,
            synthesis.QuestSource.display_name,
            synthesis.QuestSource.source_type,
            synthesis.QuestSourceVersion.captured_at,
            func.substr(synthesis.QuestSourceArtifact.normalized_text_path_or_text, 1, MAX_SOURCE_PREVIEW_CHARS).label("preview"),
        )
        .join(synthesis.QuestSource, synthesis.QuestSource.id == synthesis.QuestEvidenceChunk.source_id)
        .outerjoin(synthesis.QuestSourceVersion, synthesis.QuestSourceVersion.id == synthesis.QuestEvidenceChunk.source_version_id)
        .outerjoin(synthesis.QuestSourceArtifact, synthesis.QuestSourceArtifact.id == synthesis.QuestEvidenceChunk.artifact_id)
        .filter(
            synthesis.QuestEvidenceChunk.quest_id == job.quest_id,
            synthesis.QuestEvidenceChunk.is_current == True,  # noqa: E712
        )
    )
    source_rows = []
    if requested_ids:
        by_id = {
            chunk.id: (chunk, source_name, source_type, captured_at, preview)
            for chunk, source_name, source_type, captured_at, preview in base.filter(
                synthesis.QuestEvidenceChunk.id.in_(requested_ids)
            ).all()
        }
        source_rows = [by_id[chunk_id] for chunk_id in requested_ids if chunk_id in by_id][:MAX_SOURCE_CHUNKS]
    elif job.trigger == "captain_requested":
        source_rows = base.order_by(synthesis.QuestEvidenceChunk.created_at.desc()).limit(MAX_SOURCE_CHUNKS).all()

    source_items = []
    for chunk, source_name, source_type, captured_at, preview in source_rows:
        text = " ".join((preview or "").split())[:MAX_SOURCE_PREVIEW_CHARS]
        if not text:
            continue
        label = f"S{len(source_items) + 1}"
        item = {
            "label": label,
            "kind": "source",
            "source_name": source_name,
            "source_type": source_type,
            "locator": chunk.locator,
            "text": text,
            "chunk_id": chunk.id,
            "source_version_id": chunk.source_version_id,
            "captured_at": captured_at.isoformat() + "Z" if captured_at else None,
        }
        labels[label] = item
        source_items.append(item)

    return {
        "quest_title": quest.name,
        "current_bearing": {
            "title": bearing.title,
            "exploration_goal": bearing.exploration_goal,
            "current_summary": bearing.current_summary,
            "next_bearing": bearing.next_bearing,
        },
        "conversation_items": [{key: value for key, value in item.items() if key not in {"message_id", "metadata"}} for item in conversation_items],
        "source_chunks": [{key: value for key, value in item.items() if key not in {"chunk_id", "source_version_id"}} for item in source_items],
    }, labels


def _atomic_claim_next_job(synthesis) -> str | None:
    """Claim only one queued job even when multiple processes poll the table."""
    db = synthesis.SessionLocal()
    try:
        now = synthesis.utcnow_naive()
        stale_before = now - timedelta(minutes=30)
        db.query(synthesis.QuestSynthesisJob).filter(
            synthesis.QuestSynthesisJob.status == "running",
            synthesis.QuestSynthesisJob.started_at < stale_before,
        ).update({"status": "queued", "next_retry_at": now}, synchronize_session=False)
        db.commit()
        candidates = db.query(synthesis.QuestSynthesisJob.id).filter(
            synthesis.QuestSynthesisJob.status == "queued",
            (synthesis.QuestSynthesisJob.next_retry_at == None) | (synthesis.QuestSynthesisJob.next_retry_at <= now),  # noqa: E711
        ).order_by(synthesis.QuestSynthesisJob.requested_at.asc()).limit(8).all()
        for (job_id,) in candidates:
            claimed = db.query(synthesis.QuestSynthesisJob).filter(
                synthesis.QuestSynthesisJob.id == job_id,
                synthesis.QuestSynthesisJob.status == "queued",
            ).update({"status": "running", "started_at": now}, synchronize_session=False)
            if claimed:
                db.commit()
                return job_id
            db.rollback()
        return None
    finally:
        db.close()


def _deduped_enqueue(synthesis, original, db, *, quest_id: str, trigger: str, created_by: str | None, triggering_user_message_id: str | None = None, triggering_assistant_message_id: str | None = None, retrieval_run_id: str | None = None, priority_time=None):
    trigger = trigger if trigger in synthesis.JOB_TRIGGERS else "grounded_response"
    if trigger != "artifact_revision":
        existing = db.query(synthesis.QuestSynthesisJob).filter(
            synthesis.QuestSynthesisJob.quest_id == quest_id,
            synthesis.QuestSynthesisJob.trigger.in_(("grounded_response", "captain_requested")),
            synthesis.QuestSynthesisJob.status.in_(("queued", "running")),
        ).order_by(synthesis.QuestSynthesisJob.requested_at.desc()).first()
        if existing:
            return existing
    return original(
        db,
        quest_id=quest_id,
        trigger=trigger,
        created_by=created_by,
        triggering_user_message_id=triggering_user_message_id,
        triggering_assistant_message_id=triggering_assistant_message_id,
        retrieval_run_id=retrieval_run_id,
        priority_time=priority_time,
    )


def enqueue_artifact_memory_synthesis(db, *, quest_id: str, proposal, created_by: str, event_id: str) -> tuple[Any, bool]:
    """Queue one Memory extraction job per published Artifact revision."""
    import src.venture_synthesis as synthesis

    rows = db.query(synthesis.QuestSynthesisJob).filter(
        synthesis.QuestSynthesisJob.quest_id == quest_id,
        synthesis.QuestSynthesisJob.trigger == "artifact_revision",
        synthesis.QuestSynthesisJob.artifact_proposal_id == proposal.id,
        synthesis.QuestSynthesisJob.status.in_(("queued", "running", "completed")),
    ).order_by(synthesis.QuestSynthesisJob.requested_at.desc()).all()
    for existing in rows:
        # An active job consumes the latest published content when it executes,
        # so queueing another one only increases contention. A completed job
        # blocks only the same revision; a later revision must be extractable.
        if existing.status in {"queued", "running"}:
            return existing, False
        target_revision = _json_loads(synthesis, existing.result_json, {}).get("artifact_revision")
        if existing.status == "completed" and target_revision == proposal.revision_number:
            return existing, False
    job = synthesis.QuestSynthesisJob(
        id=uuid.uuid4().hex,
        quest_id=quest_id,
        trigger="artifact_revision",
        status="queued",
        created_by=created_by,
        artifact_proposal_id=proposal.id,
        requested_at=synthesis.utcnow_naive(),
        result_json=synthesis._dumps({
            "event_type": "memory_synthesis",
            "artifact_id": proposal.document_id,
            "artifact_revision": proposal.revision_number,
            "trigger_event_id": event_id,
        }),
    )
    db.add(job)
    return job, True


def install_synthesis_governor() -> None:
    """Install once; the existing worker resolves these globals at runtime."""
    import src.venture_synthesis as synthesis

    if getattr(synthesis, "_resource_governor_installed", False):
        return
    original_enqueue = synthesis.enqueue_synthesis_job

    def enqueue(db, **kwargs):
        return _deduped_enqueue(synthesis, original_enqueue, db, **kwargs)

    def build_evidence_pack(db, job):
        return _bounded_evidence_pack(synthesis, db, job)

    synthesis.enqueue_synthesis_job = enqueue
    synthesis.build_evidence_pack = build_evidence_pack
    synthesis._conversation_evidence = lambda db, quest: _bounded_conversation_evidence(synthesis, db, quest)
    synthesis._claim_next_job = lambda: _atomic_claim_next_job(synthesis)
    synthesis._resource_governor_installed = True
