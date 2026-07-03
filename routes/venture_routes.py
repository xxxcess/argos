"""Argos Venture Quest APIs with low-churn panel reads and artifact-first memory.

The legacy Quest API surface remains intact. This facade replaces the live panel
reads and the Artifact endpoints whose behavior must remain aligned with the
Artifact-to-Voyage-Memory lifecycle.
"""

from __future__ import annotations

import uuid

from fastapi import BackgroundTasks, HTTPException, Request

from routes import venture_routes_legacy as _legacy
from routes.venture_routes_legacy import *  # noqa: F401,F403


def _source_index_status(db, row: _legacy.QuestSource) -> dict:
    """Build source status using the request-owned session."""
    if row.source_type == "bible":
        selections = db.query(_legacy.QuestBibleBookSelection).filter(
            _legacy.QuestBibleBookSelection.source_id == row.id,
            _legacy.QuestBibleBookSelection.active == True,  # noqa: E712
        ).order_by(_legacy.QuestBibleBookSelection.canonical_order.asc()).all()
        job_states = _legacy._bible_book_job_states(db, row.id)
        visible_books = [
            _legacy._selection_to_dict(
                selection,
                job_states.get(selection.book_id)
                if selection.state in {"queued", "indexing", "paused", "failed"}
                else None,
            )
            for selection in selections
        ]
        aggregate = _legacy._bible_aggregate(selections)
        if any(book["state"] == "indexing" for book in visible_books):
            aggregate = "indexing"
        active_jobs = db.query(_legacy.QuestIndexJob).filter(
            _legacy.QuestIndexJob.source_id == row.id,
            _legacy.QuestIndexJob.status.in_(("running", "queued", "failed", "paused")),
        ).order_by(_legacy.QuestIndexJob.requested_at.asc()).all()
        job = (
            next((candidate for candidate in active_jobs if candidate.status == "running"), None)
            or next((candidate for candidate in active_jobs if candidate.status == "queued" and not candidate.error_code), None)
            or next((candidate for candidate in active_jobs if candidate.status == "queued"), None)
            or next((candidate for candidate in active_jobs if candidate.status == "paused"), None)
            or next((candidate for candidate in active_jobs if candidate.status == "failed"), None)
        ) or (
            db.query(_legacy.QuestIndexJob)
            .filter(_legacy.QuestIndexJob.source_id == row.id)
            .order_by(_legacy.QuestIndexJob.requested_at.desc())
            .first()
        )
        chunks = db.query(_legacy.QuestEvidenceChunk).filter(
            _legacy.QuestEvidenceChunk.source_id == row.id,
            _legacy.QuestEvidenceChunk.is_current == True,  # noqa: E712
        ).count()
        return {
            "index_state": aggregate,
            "last_successful_index_at": row.last_processed_at.isoformat() + "Z" if row.last_processed_at else None,
            "current_job": {
                "id": job.id,
                "status": job.status,
                "progress_total": 1,
                "progress_completed": 0 if job.status in {"queued", "running"} else 1,
                "chunks_indexed": job.chunks_indexed,
                "safe_error_message": job.safe_error_message,
                "error_code": job.error_code,
                "next_retry_at": job.next_retry_at.isoformat() + "Z" if job.next_retry_at else None,
            } if job else None,
            "has_active_job": bool(job and job.status in {"queued", "running"}),
            "artifact_count": sum(1 for selection in selections if selection.state == "indexed"),
            "chunk_count": chunks,
            "freshness": aggregate,
            "warning": job.safe_error_message if job and job.status == "failed" else None,
            "books": visible_books,
        }

    job = db.query(_legacy.QuestIndexJob).filter(
        _legacy.QuestIndexJob.source_id == row.id,
    ).order_by(_legacy.QuestIndexJob.requested_at.desc()).first()
    artifacts = db.query(_legacy.QuestSourceArtifact).filter(
        _legacy.QuestSourceArtifact.source_id == row.id,
        _legacy.QuestSourceArtifact.is_current == True,  # noqa: E712
    ).count()
    chunks = db.query(_legacy.QuestEvidenceChunk).filter(
        _legacy.QuestEvidenceChunk.source_id == row.id,
        _legacy.QuestEvidenceChunk.is_current == True,  # noqa: E712
    ).count()
    active = job and job.status in {"queued", "running"}
    return {
        "index_state": getattr(row, "index_state", None) or (job.status if job else "not_indexed"),
        "last_successful_index_at": row.last_processed_at.isoformat() + "Z" if row.last_processed_at else None,
        "current_job": {
            "id": job.id,
            "status": job.status,
            "progress_total": job.progress_total,
            "progress_completed": job.progress_completed,
            "chunks_indexed": job.chunks_indexed,
            "safe_error_message": job.safe_error_message,
            "error_code": job.error_code,
            "next_retry_at": job.next_retry_at.isoformat() + "Z" if job.next_retry_at else None,
        } if job else None,
        "has_active_job": bool(active),
        "artifact_count": artifacts,
        "chunk_count": chunks,
        "freshness": "ready" if chunks else (job.status if job else "not_indexed"),
        "warning": job.safe_error_message if job and job.status == "failed" else None,
    }


def _source_to_dict(db, row: _legacy.QuestSource, access: str | None = None) -> dict:
    config = _legacy._json_loads(row.configuration_json, {})
    if access == "summary":
        config = {"summary": config.get("summary", "") if isinstance(config, dict) else ""}
    elif access is None:
        config = {}
    index_status = _source_index_status(db, row)
    return {
        "id": row.id,
        "session_id": row.session_id,
        "source_type": row.source_type,
        "source_mode": row.source_mode,
        "refresh_strategy": getattr(row, "refresh_strategy", None) or "manual",
        "display_name": row.display_name,
        "access_mode": row.access_mode,
        "configuration": config,
        "status": row.status,
        "index_state": index_status.get("index_state"),
        "index_status": index_status,
        "last_refreshed_at": row.last_refreshed_at.isoformat() + "Z" if row.last_refreshed_at else None,
        "last_processed_at": row.last_processed_at.isoformat() + "Z" if row.last_processed_at else None,
    }


def _remove_route(router, path: str, method: str) -> None:
    router.routes[:] = [
        route for route in router.routes
        if not (getattr(route, "path", None) == path and method in (getattr(route, "methods", set()) or set()))
    ]


def _key_point_count(proposal: _legacy.QuestArtifactProposal | None) -> int:
    if not proposal:
        return 0
    synthesis = _legacy._json_loads(proposal.synthesis_json, {})
    points = synthesis.get("key_points") if isinstance(synthesis, dict) else []
    return len(points) if isinstance(points, list) else 0


def _artifact_document_payload(document, proposal: _legacy.QuestArtifactProposal | None) -> dict:
    title = (proposal.title if proposal and proposal.title else document.title) or "Untitled Artifact"
    return {
        "id": document.id,
        "proposal_id": proposal.id if proposal else None,
        "artifact_type": proposal.artifact_type if proposal else "document",
        "title": title,
        "artifact_title": title,
        "status": proposal.status if proposal else "published",
        "key_point_count": _key_point_count(proposal),
        "language": document.language,
        "updated_at": document.updated_at.isoformat() + "Z" if document.updated_at else None,
    }


def _queue_artifact_memory_job(db, *, quest_id: str, proposal: _legacy.QuestArtifactProposal, captain: str, publish_event_id: str) -> str | None:
    existing = db.query(_legacy.QuestSynthesisJob).filter(
        _legacy.QuestSynthesisJob.quest_id == quest_id,
        _legacy.QuestSynthesisJob.trigger == "artifact_revision",
        _legacy.QuestSynthesisJob.artifact_proposal_id == proposal.id,
    ).order_by(_legacy.QuestSynthesisJob.requested_at.desc()).first()
    if existing and existing.status in {"queued", "running", "completed"}:
        return existing.id
    job = _legacy.QuestSynthesisJob(
        id=uuid.uuid4().hex,
        quest_id=quest_id,
        trigger="artifact_revision",
        status="queued",
        created_by=captain,
        artifact_proposal_id=proposal.id,
        requested_at=_legacy.utcnow_naive(),
        result_json=_legacy._json_dumps({
            "event_type": "memory_synthesis",
            "artifact_id": proposal.document_id,
            "trigger_event_id": publish_event_id,
        }),
    )
    db.add(job)
    db.add(_legacy.ChatMessage(
        id=uuid.uuid4().hex,
        session_id=quest_id,
        role="system",
        content=f"Memory synthesis queued for Artifact: {proposal.title}",
        meta_data=_legacy._json_dumps({
            "event_type": "memory_synthesis",
            "presentation": "background_task",
            "event_id": f"memory-synthesis:artifact:{proposal.document_id}",
            "task_id": job.id,
            "status": "queued",
            "title": "Memory synthesis",
            "subject": proposal.title,
            "artifact_id": proposal.document_id,
            "quest_id": quest_id,
            "trigger_event_id": publish_event_id,
        }),
    ))
    return job.id


def setup_venture_routes(session_manager):
    router = _legacy.setup_venture_routes(session_manager)
    _remove_route(router, "/api/quests/{quest_id}/sources", "GET")
    _remove_route(router, "/api/quests/{quest_id}/sources/{source_id}/index-status", "GET")
    _remove_route(router, "/api/quests/{quest_id}/artifacts", "GET")
    _remove_route(router, "/api/quests/{quest_id}/artifact-proposals/{proposal_id}/publish", "POST")

    @router.get("/api/quests/{quest_id}/sources")
    def list_sources(request: Request, quest_id: str):
        _legacy.require_venture_runtime()
        user = _legacy.require_quest_member(request, quest_id)
        role = _legacy.get_quest_role(user, quest_id)
        db = _legacy.SessionLocal()
        try:
            rows = db.query(_legacy.QuestSource).filter(
                _legacy.QuestSource.session_id == quest_id,
                _legacy.QuestSource.status != "archived",
            ).all()
            out = []
            for row in rows:
                if role == "captain" or row.access_mode == "shared_read":
                    out.append(_source_to_dict(db, row, "raw"))
                elif row.access_mode == "shared_summaries":
                    out.append(_source_to_dict(db, row, "summary"))
            return {"sources": out}
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/sources/{source_id}/index-status")
    def source_index_status(request: Request, quest_id: str, source_id: str):
        _legacy.require_venture_runtime()
        db = _legacy.SessionLocal()
        try:
            row = db.query(_legacy.QuestSource).filter(
                _legacy.QuestSource.id == source_id,
                _legacy.QuestSource.session_id == quest_id,
            ).first()
            if not row:
                raise HTTPException(404, "Quest source not found")
            access = _legacy.require_quest_source_read_access(request, row)
            if access != "raw" and row.access_mode == "captain_only":
                raise HTTPException(404, "Quest source not found")
            return {"source_id": row.id, "index_status": _source_index_status(db, row)}
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/argo-status")
    def argo_status(request: Request, quest_id: str):
        _legacy.require_venture_runtime()
        user = _legacy.require_quest_member(request, quest_id)
        role = _legacy.get_quest_role(user, quest_id)
        db = _legacy.SessionLocal()
        try:
            memory = db.query(_legacy.QuestMemoryEntry).filter(
                _legacy.QuestMemoryEntry.session_id == quest_id,
                _legacy.QuestMemoryEntry.state != "retired",
            )
            if role != "captain":
                memory = memory.filter(_legacy.QuestMemoryEntry.visibility == "quest_shared")
            jobs = []
            if role == "captain":
                rows = db.query(_legacy.QuestSynthesisJob).filter(
                    _legacy.QuestSynthesisJob.quest_id == quest_id,
                ).order_by(_legacy.QuestSynthesisJob.requested_at.desc()).limit(5).all()
                jobs = [_legacy._synthesis_job_to_safe_dict(row) for row in rows]
            return {"memory_count": memory.count(), "jobs": jobs}
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/artifacts")
    def list_artifacts(request: Request, quest_id: str):
        _legacy.require_venture_runtime()
        user = _legacy.require_quest_member(request, quest_id)
        role = _legacy.get_quest_role(user, quest_id)
        db = _legacy.SessionLocal()
        try:
            documents_query = db.query(_legacy.Document).filter(
                _legacy.Document.session_id == quest_id,
                _legacy.Document.archived == False,  # noqa: E712
                _legacy.Document.is_active == True,  # noqa: E712
            ).filter(_legacy.Document.owner == user)
            documents = documents_query.all()
            if role != "captain" and not documents:
                documents = db.query(_legacy.Document).filter(
                    _legacy.Document.session_id == quest_id,
                    _legacy.Document.archived == False,  # noqa: E712
                    _legacy.Document.is_active == True,  # noqa: E712
                ).all()
            proposals = db.query(_legacy.QuestArtifactProposal).filter(
                _legacy.QuestArtifactProposal.session_id == quest_id,
                _legacy.QuestArtifactProposal.status == "published",
            ).all()
            proposal_by_document = {}
            for proposal in proposals:
                proposal_by_document[proposal.document_id] = proposal
                if role != "captain":
                    proposal_by_document[_legacy._artifact_copy_id(proposal.id, user)] = proposal
            images = db.query(_legacy.GalleryImage).filter(
                _legacy.GalleryImage.session_id == quest_id,
                _legacy.GalleryImage.is_active == True,  # noqa: E712
            ).all()
            return {
                "documents": [
                    _artifact_document_payload(document, proposal_by_document.get(document.id))
                    for document in documents
                ],
                "gallery": [{"id": image.id, "filename": image.filename, "prompt": image.prompt} for image in images],
            }
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/artifact-proposals/{proposal_id}/publish")
    def publish_artifact_proposal(request: Request, quest_id: str, proposal_id: str, background_tasks: BackgroundTasks):
        """Publish an Artifact, then create memory exclusively from its key points."""
        _legacy.require_venture_runtime()
        captain = _legacy.require_quest_captain(request, quest_id)
        db = _legacy.SessionLocal()
        try:
            proposal = db.query(_legacy.QuestArtifactProposal).filter(
                _legacy.QuestArtifactProposal.id == proposal_id,
                _legacy.QuestArtifactProposal.session_id == quest_id,
            ).with_for_update().first()
            if not proposal:
                raise HTTPException(404, "Artifact proposal not found")
            document = db.query(_legacy.Document).filter(
                _legacy.Document.id == proposal.document_id,
            ).first()
            if not document:
                raise HTTPException(404, "Artifact Draft not found")

            if proposal.status == "published":
                _legacy._ensure_member_artifact_copies(db, quest_id=quest_id, proposal=proposal, source_doc=document)
                job_id = _queue_artifact_memory_job(
                    db,
                    quest_id=quest_id,
                    proposal=proposal,
                    captain=captain,
                    publish_event_id=f"artifact_published:{document.id}",
                )
                db.commit()
                if job_id:
                    background_tasks.add_task(_legacy._run_synthesis_job_background, job_id)
                return {"proposal": _legacy._proposal_to_dict(proposal), "already_published": True, "memory_synthesis_job_id": job_id}
            if proposal.status != "pending_review":
                raise HTTPException(409, "Artifact proposal is not pending review")
            if document.owner != captain:
                raise HTTPException(404, "Artifact Draft not found")

            document.session_id = quest_id
            document.is_active = True
            document.title = proposal.title or document.title
            proposal.status = "published"
            proposal.visibility = "quest_shared"
            proposal.reviewed_at = proposal.published_at = _legacy.utcnow_naive()
            copied_for = _legacy._ensure_member_artifact_copies(db, quest_id=quest_id, proposal=proposal, source_doc=document)
            publish_event_id = f"artifact_published:{document.id}"
            db.add(_legacy.ChatMessage(
                id=uuid.uuid4().hex,
                session_id=quest_id,
                role="system",
                content=f"Quest Artifact published: {proposal.title}",
                meta_data=_legacy._timeline_meta(
                    "artifact_published",
                    title="Artifact published",
                    event_id=publish_event_id,
                    proposal_id=proposal.id,
                    document_id=document.id,
                    actor=captain,
                    subject=proposal.title,
                    copied_for=copied_for,
                ),
            ))
            job_id = _queue_artifact_memory_job(
                db,
                quest_id=quest_id,
                proposal=proposal,
                captain=captain,
                publish_event_id=publish_event_id,
            )
            members = db.query(_legacy.QuestMember).filter(
                _legacy.QuestMember.session_id == quest_id,
                _legacy.QuestMember.role == "shipmate",
            ).all()
            quest = db.query(_legacy.DbSession).filter(_legacy.DbSession.id == quest_id).first()
            for member in members:
                _legacy._notify(
                    db,
                    member.username,
                    f"artifact-published:{proposal_id}:{member.username}",
                    category="inbox",
                    state="unread",
                    title=f"Captain {captain} published '{proposal.title}'",
                    message=f"Captain {captain} published '{proposal.title}' to {quest.name if quest else 'the Quest'}.",
                    resource_type="quest_artifact",
                    resource_id=document.id,
                    actions=[],
                )
            _legacy._notify(
                db,
                captain,
                f"artifact-proposal:{proposal.id}",
                state="resolved",
                title="Artifact Draft published",
                message=f"Published '{proposal.title}' to the Quest.",
                actions=[],
            )
            db.commit()
            if job_id:
                background_tasks.add_task(_legacy._run_synthesis_job_background, job_id)
            return {"proposal": _legacy._proposal_to_dict(proposal), "document_id": document.id, "memory_synthesis_job_id": job_id}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    return router
