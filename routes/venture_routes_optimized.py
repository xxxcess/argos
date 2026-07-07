"""Performance-oriented Venture route facade.

Legacy routes still own the broad Quest API.  This facade replaces only the
high-churn reads and Artifact lifecycle endpoints so the browser does not
hydrate whole documents, whole memory ledgers, or detailed Bible selections on
every status tick.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import load_only

from routes import venture_routes_legacy as _legacy
from routes.venture_routes_legacy import *  # noqa: F401,F403
from src.quest_session_management import request_artifact_synthesis, request_memory_synthesis
from src.venture_artifact_quality_v2 import artifact_display_title, install_artifact_quality_contract, is_displayable_memory
from src.venture_synthesis_governor import enqueue_artifact_memory_synthesis, install_synthesis_governor

install_artifact_quality_contract()
install_synthesis_governor()


class QuestManagementRequest(BaseModel):
    action: Literal["synthesize_artifact", "synthesize_memory"]
    artifact_proposal_id: str | None = None


def _remove_route(router, path: str, method: str) -> None:
    router.routes[:] = [
        route for route in router.routes
        if not (getattr(route, "path", None) == path and method in (getattr(route, "methods", set()) or set()))
    ]


def _safe_job(job) -> dict:
    return {
        "id": job.id,
        "status": job.status,
        "trigger": job.trigger,
        "requested_at": job.requested_at.isoformat() + "Z" if job.requested_at else None,
        "started_at": job.started_at.isoformat() + "Z" if job.started_at else None,
        "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
        "safe_error_message": job.safe_error_message,
        "reason": (_legacy._json_loads(job.result_json, {}) or {}).get("reason"),
        "artifact_proposal_id": job.artifact_proposal_id,
    }


def _source_visible(role: str, source) -> bool:
    return role == "captain" or source.access_mode in {"shared_read", "shared_summaries"}


def _latest_source_jobs(db, source_ids: list[str]) -> dict[str, object]:
    if not source_ids:
        return {}
    rows = db.query(_legacy.QuestIndexJob).options(
        load_only(
            _legacy.QuestIndexJob.id,
            _legacy.QuestIndexJob.source_id,
            _legacy.QuestIndexJob.status,
            _legacy.QuestIndexJob.requested_at,
            _legacy.QuestIndexJob.progress_total,
            _legacy.QuestIndexJob.progress_completed,
            _legacy.QuestIndexJob.chunks_indexed,
            _legacy.QuestIndexJob.error_code,
            _legacy.QuestIndexJob.safe_error_message,
            _legacy.QuestIndexJob.next_retry_at,
        )
    ).filter(
        _legacy.QuestIndexJob.source_id.in_(source_ids),
    ).order_by(_legacy.QuestIndexJob.source_id.asc(), _legacy.QuestIndexJob.requested_at.desc()).all()
    result = {}
    for row in rows:
        result.setdefault(row.source_id, row)
    return result


def _source_chunk_counts(db, source_ids: list[str]) -> dict[str, int]:
    if not source_ids:
        return {}
    rows = db.query(
        _legacy.QuestEvidenceChunk.source_id,
        func.count(_legacy.QuestEvidenceChunk.id),
    ).filter(
        _legacy.QuestEvidenceChunk.source_id.in_(source_ids),
        _legacy.QuestEvidenceChunk.is_current == True,  # noqa: E712
    ).group_by(_legacy.QuestEvidenceChunk.source_id).all()
    return {source_id: int(count) for source_id, count in rows}


def _bible_state_counts(db, source_ids: list[str]) -> dict[str, dict[str, int]]:
    if not source_ids:
        return {}
    rows = db.query(
        _legacy.QuestBibleBookSelection.source_id,
        _legacy.QuestBibleBookSelection.state,
        func.count(_legacy.QuestBibleBookSelection.id),
    ).filter(
        _legacy.QuestBibleBookSelection.source_id.in_(source_ids),
        _legacy.QuestBibleBookSelection.active == True,  # noqa: E712
    ).group_by(
        _legacy.QuestBibleBookSelection.source_id,
        _legacy.QuestBibleBookSelection.state,
    ).all()
    result: dict[str, dict[str, int]] = {}
    for source_id, state, count in rows:
        result.setdefault(source_id, {})[state] = int(count)
    return result


def _status_from_counts(source, job, chunks: int, bible_counts: dict[str, int] | None = None) -> str:
    bible_counts = bible_counts or {}
    if job and job.status in {"queued", "running", "paused", "failed"}:
        return "indexing" if job.status == "running" else job.status
    if bible_counts.get("indexing"):
        return "indexing"
    if bible_counts.get("queued"):
        return "queued"
    if bible_counts.get("failed"):
        return "failed"
    if bible_counts.get("partial"):
        return "partial"
    if chunks:
        return "ready"
    return getattr(source, "status", None) or "not_indexed"


def _compact_source_payload(db, quest_id: str, role: str) -> dict:
    sources = db.query(_legacy.QuestSource).options(
        load_only(
            _legacy.QuestSource.id,
            _legacy.QuestSource.source_type,
            _legacy.QuestSource.display_name,
            _legacy.QuestSource.access_mode,
            _legacy.QuestSource.status,
            _legacy.QuestSource.last_processed_at,
        )
    ).filter(
        _legacy.QuestSource.session_id == quest_id,
        _legacy.QuestSource.status != "archived",
    ).all()
    sources = [source for source in sources if _source_visible(role, source)]
    source_ids = [source.id for source in sources]
    jobs = _latest_source_jobs(db, source_ids)
    chunks = _source_chunk_counts(db, source_ids)
    bible = _bible_state_counts(db, source_ids)
    payload = []
    for source in sources:
        job = jobs.get(source.id)
        state = _status_from_counts(source, job, chunks.get(source.id, 0), bible.get(source.id))
        payload.append({
            "id": source.id,
            "display_name": source.display_name,
            "access_mode": source.access_mode,
            "source_type": source.source_type,
            "index_state": state,
            "index_status": {
                "index_state": state,
                "chunk_count": chunks.get(source.id, 0),
                "book_counts": bible.get(source.id, {}),
                "has_active_job": bool(job and job.status in {"queued", "running"}),
                "warning": job.safe_error_message if job and job.status == "failed" else None,
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
            },
            "last_processed_at": source.last_processed_at.isoformat() + "Z" if source.last_processed_at else None,
        })
    counts = {"ready": 0, "indexing": 0, "attention": 0}
    for source in payload:
        state = source["index_state"]
        if state in {"ready", "indexed"}:
            counts["ready"] += 1
        elif state in {"queued", "running", "indexing"}:
            counts["indexing"] += 1
        elif state in {"failed", "partial"}:
            counts["attention"] += 1
    return {"sources": payload, "counts": counts}


def _detailed_source_payload(db, source, role: str) -> dict:
    compact = _compact_source_payload(db, source.session_id, role)
    status = next((row["index_status"] for row in compact["sources"] if row["id"] == source.id), {"index_state": "not_indexed"})
    config = _legacy._json_loads(source.configuration_json, {})
    if role != "captain" and source.access_mode == "shared_summaries":
        config = {"summary": config.get("summary", "") if isinstance(config, dict) else ""}
    if source.source_type == "bible":
        selections = db.query(_legacy.QuestBibleBookSelection).filter(
            _legacy.QuestBibleBookSelection.source_id == source.id,
            _legacy.QuestBibleBookSelection.active == True,  # noqa: E712
        ).order_by(_legacy.QuestBibleBookSelection.canonical_order.asc()).all()
        job_states = _legacy._bible_book_job_states(db, source.id)
        status["books"] = [
            _legacy._selection_to_dict(
                selection,
                job_states.get(selection.book_id) if selection.state in {"queued", "indexing", "paused", "failed"} else None,
            )
            for selection in selections
        ]
    return {
        "id": source.id,
        "session_id": source.session_id,
        "source_type": source.source_type,
        "source_mode": source.source_mode,
        "refresh_strategy": getattr(source, "refresh_strategy", None) or "manual",
        "display_name": source.display_name,
        "access_mode": source.access_mode,
        "configuration": config,
        "status": source.status,
        "index_state": status.get("index_state"),
        "index_status": status,
        "last_refreshed_at": source.last_refreshed_at.isoformat() + "Z" if source.last_refreshed_at else None,
        "last_processed_at": source.last_processed_at.isoformat() + "Z" if source.last_processed_at else None,
    }


def _visible_memory_count(db, quest_id: str, role: str) -> int:
    query = db.query(_legacy.QuestMemoryEntry).options(
        load_only(
            _legacy.QuestMemoryEntry.id,
            _legacy.QuestMemoryEntry.artifact_id,
            _legacy.QuestMemoryEntry.created_by,
            _legacy.QuestMemoryEntry.title,
            _legacy.QuestMemoryEntry.content,
        )
    ).filter(
        _legacy.QuestMemoryEntry.session_id == quest_id,
        _legacy.QuestMemoryEntry.state != "retired",
    )
    if role != "captain":
        query = query.filter(_legacy.QuestMemoryEntry.visibility == "quest_shared")
    return sum(1 for row in query.yield_per(100) if is_displayable_memory(row))


def _visible_memory_page(db, quest_id: str, role: str, limit: int) -> tuple[list, bool]:
    query = db.query(_legacy.QuestMemoryEntry).filter(
        _legacy.QuestMemoryEntry.session_id == quest_id,
        _legacy.QuestMemoryEntry.state != "retired",
    )
    if role != "captain":
        query = query.filter(_legacy.QuestMemoryEntry.visibility == "quest_shared")
    rows = query.order_by(
        _legacy.QuestMemoryEntry.pinned.desc(),
        _legacy.QuestMemoryEntry.updated_at.desc(),
    ).limit(limit + 1).all()
    filtered = [row for row in rows if is_displayable_memory(row)]
    return filtered[:limit], len(rows) > limit


def _artifact_payload(document, proposal) -> dict:
    title = artifact_display_title(document, proposal, _legacy._json_loads)
    synthesis = _legacy._json_loads(proposal.synthesis_json, {}) if proposal else {}
    points = synthesis.get("key_points") if isinstance(synthesis, dict) else []
    return {
        "id": document.id,
        "proposal_id": proposal.id if proposal else None,
        "artifact_type": proposal.artifact_type if proposal else "document",
        "title": title,
        "artifact_title": title,
        "status": proposal.status if proposal else "published",
        "key_point_count": len(points) if isinstance(points, list) else 0,
        "language": document.language,
        "updated_at": document.updated_at.isoformat() + "Z" if document.updated_at else None,
    }


def _management_snapshot(db, quest_id: str, role: str) -> dict:
    jobs = db.query(_legacy.QuestSynthesisJob).options(
        load_only(
            _legacy.QuestSynthesisJob.id,
            _legacy.QuestSynthesisJob.status,
            _legacy.QuestSynthesisJob.trigger,
            _legacy.QuestSynthesisJob.requested_at,
            _legacy.QuestSynthesisJob.started_at,
            _legacy.QuestSynthesisJob.finished_at,
            _legacy.QuestSynthesisJob.safe_error_message,
            _legacy.QuestSynthesisJob.result_json,
            _legacy.QuestSynthesisJob.artifact_proposal_id,
        )
    ).filter(
        _legacy.QuestSynthesisJob.quest_id == quest_id,
    ).order_by(_legacy.QuestSynthesisJob.requested_at.desc()).limit(5).all()
    artifacts = db.query(_legacy.QuestArtifactProposal).options(
        load_only(
            _legacy.QuestArtifactProposal.id,
            _legacy.QuestArtifactProposal.title,
            _legacy.QuestArtifactProposal.status,
            _legacy.QuestArtifactProposal.revision_number,
            _legacy.QuestArtifactProposal.published_at,
        )
    ).filter(
        _legacy.QuestArtifactProposal.session_id == quest_id,
        _legacy.QuestArtifactProposal.status == "published",
    ).order_by(_legacy.QuestArtifactProposal.published_at.desc()).limit(10).all()
    return {
        "actions": ["synthesize_artifact", "synthesize_memory"] if role == "captain" else [],
        "memory_count": _visible_memory_count(db, quest_id, role),
        "jobs": [_safe_job(job) for job in jobs],
        "published_artifacts": [
            {"id": artifact.id, "title": artifact.title, "revision_number": artifact.revision_number}
            for artifact in artifacts
        ],
    }


def setup_venture_routes(session_manager):
    router = _legacy.setup_venture_routes(session_manager)
    for path, method in (
        ("/api/quests/{quest_id}/sources", "GET"),
        ("/api/quests/{quest_id}/sources/{source_id}/index-status", "GET"),
        ("/api/quests/{quest_id}/argo-status", "GET"),
        ("/api/quests/{quest_id}/memory", "GET"),
        ("/api/quests/{quest_id}/memory/{memory_id}", "GET"),
        ("/api/quests/{quest_id}/artifacts", "GET"),
        ("/api/quests/{quest_id}/artifact-proposals/{proposal_id}/publish", "POST"),
    ):
        _remove_route(router, path, method)

    @router.get("/api/quests/{quest_id}/sources")
    def list_sources(request: Request, quest_id: str):
        _legacy.require_venture_runtime()
        user = _legacy.require_quest_member(request, quest_id)
        role = _legacy.get_quest_role(user, quest_id)
        db = _legacy.SessionLocal()
        try:
            sources = db.query(_legacy.QuestSource).filter(
                _legacy.QuestSource.session_id == quest_id,
                _legacy.QuestSource.status != "archived",
            ).all()
            return {"sources": [_detailed_source_payload(db, source, role) for source in sources if _source_visible(role, source)]}
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/sources/{source_id}/index-status")
    def source_index_status(request: Request, quest_id: str, source_id: str):
        _legacy.require_venture_runtime()
        user = _legacy.require_quest_member(request, quest_id)
        role = _legacy.get_quest_role(user, quest_id)
        db = _legacy.SessionLocal()
        try:
            source = db.query(_legacy.QuestSource).filter(
                _legacy.QuestSource.id == source_id,
                _legacy.QuestSource.session_id == quest_id,
            ).first()
            if not source or not _source_visible(role, source):
                raise HTTPException(404, "Quest source not found")
            return {"source_id": source.id, "index_status": _detailed_source_payload(db, source, role)["index_status"]}
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/live-status")
    def live_status(request: Request, quest_id: str):
        _legacy.require_venture_runtime()
        user = _legacy.require_quest_member(request, quest_id)
        role = _legacy.get_quest_role(user, quest_id)
        db = _legacy.SessionLocal()
        try:
            jobs = []
            if role == "captain":
                rows = db.query(_legacy.QuestSynthesisJob).options(
                    load_only(
                        _legacy.QuestSynthesisJob.id,
                        _legacy.QuestSynthesisJob.status,
                        _legacy.QuestSynthesisJob.trigger,
                        _legacy.QuestSynthesisJob.requested_at,
                        _legacy.QuestSynthesisJob.started_at,
                        _legacy.QuestSynthesisJob.finished_at,
                        _legacy.QuestSynthesisJob.safe_error_message,
                        _legacy.QuestSynthesisJob.result_json,
                        _legacy.QuestSynthesisJob.artifact_proposal_id,
                    )
                ).filter(
                    _legacy.QuestSynthesisJob.quest_id == quest_id,
                ).order_by(_legacy.QuestSynthesisJob.requested_at.desc()).limit(3).all()
                jobs = [_safe_job(job) for job in rows]
            return {
                **_compact_source_payload(db, quest_id, role),
                "argo": {"memory_count": _visible_memory_count(db, quest_id, role), "jobs": jobs},
            }
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/argo-status")
    def argo_status(request: Request, quest_id: str):
        _legacy.require_venture_runtime()
        user = _legacy.require_quest_member(request, quest_id)
        role = _legacy.get_quest_role(user, quest_id)
        db = _legacy.SessionLocal()
        try:
            snapshot = _management_snapshot(db, quest_id, role)
            return {"memory_count": snapshot["memory_count"], "jobs": snapshot["jobs"]}
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/memory")
    def list_memory(request: Request, quest_id: str, limit: int = 50):
        _legacy.require_venture_runtime()
        user = _legacy.require_quest_member(request, quest_id)
        role = _legacy.get_quest_role(user, quest_id)
        db = _legacy.SessionLocal()
        try:
            page, has_more = _visible_memory_page(db, quest_id, role, max(1, min(limit, 100)))
            return {"memory": [_legacy._memory_to_dict(row) for row in page], "has_more": has_more}
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/memory/{memory_id}")
    def get_memory(request: Request, quest_id: str, memory_id: str):
        _legacy.require_venture_runtime()
        user = _legacy.require_quest_member(request, quest_id)
        role = _legacy.get_quest_role(user, quest_id)
        db = _legacy.SessionLocal()
        try:
            row = db.query(_legacy.QuestMemoryEntry).filter(
                _legacy.QuestMemoryEntry.id == memory_id,
                _legacy.QuestMemoryEntry.session_id == quest_id,
            ).first()
            if not row or (role != "captain" and row.visibility != "quest_shared") or not is_displayable_memory(row):
                raise HTTPException(404, "Memory not found")
            return {"memory": _legacy._memory_to_dict(row)}
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/artifacts")
    def list_artifacts(request: Request, quest_id: str, limit: int = 50):
        _legacy.require_venture_runtime()
        user = _legacy.require_quest_member(request, quest_id)
        role = _legacy.get_quest_role(user, quest_id)
        db = _legacy.SessionLocal()
        try:
            documents = db.query(_legacy.Document).options(
                load_only(
                    _legacy.Document.id,
                    _legacy.Document.title,
                    _legacy.Document.language,
                    _legacy.Document.updated_at,
                    _legacy.Document.owner,
                    _legacy.Document.session_id,
                    _legacy.Document.archived,
                    _legacy.Document.is_active,
                )
            ).filter(
                _legacy.Document.session_id == quest_id,
                _legacy.Document.archived == False,  # noqa: E712
                _legacy.Document.is_active == True,  # noqa: E712
                _legacy.Document.owner == user,
            ).order_by(_legacy.Document.updated_at.desc()).limit(max(1, min(limit, 100))).all()
            proposals = db.query(_legacy.QuestArtifactProposal).options(
                load_only(
                    _legacy.QuestArtifactProposal.id,
                    _legacy.QuestArtifactProposal.document_id,
                    _legacy.QuestArtifactProposal.title,
                    _legacy.QuestArtifactProposal.status,
                    _legacy.QuestArtifactProposal.artifact_type,
                    _legacy.QuestArtifactProposal.synthesis_json,
                )
            ).filter(
                _legacy.QuestArtifactProposal.session_id == quest_id,
                _legacy.QuestArtifactProposal.status == "published",
            ).all()
            proposal_by_document = {proposal.document_id: proposal for proposal in proposals}
            if role != "captain":
                proposal_by_document.update({_legacy._artifact_copy_id(proposal.id, user): proposal for proposal in proposals})
            images = db.query(_legacy.GalleryImage).options(
                load_only(_legacy.GalleryImage.id, _legacy.GalleryImage.filename, _legacy.GalleryImage.prompt)
            ).filter(
                _legacy.GalleryImage.session_id == quest_id,
                _legacy.GalleryImage.is_active == True,  # noqa: E712
            ).order_by(_legacy.GalleryImage.updated_at.desc()).limit(50).all()
            return {
                "documents": [_artifact_payload(document, proposal_by_document.get(document.id)) for document in documents],
                "gallery": [{"id": image.id, "filename": image.filename, "prompt": image.prompt} for image in images],
            }
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/manage")
    def quest_management_status(request: Request, quest_id: str):
        _legacy.require_venture_runtime()
        user = _legacy.require_quest_member(request, quest_id)
        role = _legacy.get_quest_role(user, quest_id)
        db = _legacy.SessionLocal()
        try:
            return _management_snapshot(db, quest_id, role)
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/manage")
    def manage_quest(request: Request, quest_id: str, payload: QuestManagementRequest):
        _legacy.require_venture_runtime()
        captain = _legacy.require_quest_captain(request, quest_id)
        db = _legacy.SessionLocal()
        try:
            if payload.action == "synthesize_artifact":
                result = request_artifact_synthesis(db, _legacy, quest_id=quest_id, captain=captain)
            else:
                result = request_memory_synthesis(
                    db,
                    _legacy,
                    quest_id=quest_id,
                    captain=captain,
                    proposal_id=payload.artifact_proposal_id,
                )
            db.commit()
            return {"result": result.to_dict(), "management": _management_snapshot(db, quest_id, "captain")}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/artifact-proposals/{proposal_id}/publish")
    def publish_artifact_proposal(request: Request, quest_id: str, proposal_id: str):
        """Publish an Artifact, then queue its Revelation-only Memory extraction."""
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
            document = db.query(_legacy.Document).filter(_legacy.Document.id == proposal.document_id).first()
            if not document:
                raise HTTPException(404, "Artifact Draft not found")
            if proposal.status not in {"pending_review", "published"}:
                raise HTTPException(409, "Artifact proposal is not publishable")
            if proposal.status == "pending_review" and document.owner != captain:
                raise HTTPException(404, "Artifact Draft not found")

            if proposal.status == "pending_review":
                document.session_id = quest_id
                document.is_active = True
                document.title = artifact_display_title(document, proposal, _legacy._json_loads)
                proposal.title = document.title
                proposal.status = "published"
                proposal.visibility = "quest_shared"
                proposal.reviewed_at = proposal.published_at = _legacy.utcnow_naive()
                _legacy._ensure_member_artifact_copies(db, quest_id=quest_id, proposal=proposal, source_doc=document)
                db.add(_legacy.ChatMessage(
                    id=uuid.uuid4().hex,
                    session_id=quest_id,
                    role="system",
                    content=f"Quest Artifact published: {proposal.title}",
                    meta_data=_legacy._timeline_meta(
                        "artifact_published",
                        title="Artifact published",
                        event_id=f"artifact-published:{document.id}",
                        proposal_id=proposal.id,
                        document_id=document.id,
                        actor=captain,
                        subject=proposal.title,
                    ),
                ))
            job, queued = enqueue_artifact_memory_synthesis(
                db,
                quest_id=quest_id,
                proposal=proposal,
                created_by=captain,
                event_id=f"artifact-published:{proposal.id}:{proposal.revision_number}",
            )
            db.commit()
            return {
                "proposal": _legacy._proposal_to_dict(proposal),
                "document_id": document.id,
                "memory_synthesis_job_id": job.id,
                "memory_synthesis_queued": queued,
            }
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    return router
