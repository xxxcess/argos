"""Argos Venture Quest APIs with low-churn panel read endpoints.

The compatibility module keeps the existing Quest API surface intact. This facade
replaces only the high-frequency side-panel reads so they do not open an extra
SQLAlchemy session per source while the request session is already checked out.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from routes import venture_routes_legacy as _legacy
from routes.venture_routes_legacy import *  # noqa: F401,F403


def _source_index_status(db, row: _legacy.QuestSource) -> dict:
    """Build source status using the request-owned session.

    The legacy implementation created a nested ``SessionLocal`` for every
    source it serialized. A single Quest panel refresh can therefore acquire
    N+1 connections. Keeping all reads in the request session removes that
    pool pressure and gives the panel a consistent snapshot.
    """
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

    job = (
        db.query(_legacy.QuestIndexJob)
        .filter(_legacy.QuestIndexJob.source_id == row.id)
        .order_by(_legacy.QuestIndexJob.requested_at.desc())
        .first()
    )
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


def _remove_get_route(router, path: str) -> None:
    router.routes[:] = [
        route for route in router.routes
        if not (getattr(route, "path", None) == path and "GET" in (getattr(route, "methods", set()) or set()))
    ]


def setup_venture_routes(session_manager):
    router = _legacy.setup_venture_routes(session_manager)
    _remove_get_route(router, "/api/quests/{quest_id}/sources")
    _remove_get_route(router, "/api/quests/{quest_id}/sources/{source_id}/index-status")

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
        """Compact, permission-aware payload for the live Argo Status card."""
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

    return router
