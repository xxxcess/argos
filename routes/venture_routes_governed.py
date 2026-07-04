"""Final Venture route guards for reliable Quest background work."""

from __future__ import annotations

import asyncio
import json

from fastapi import BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

import routes.venture_routes_optimized as _optimized
from routes import venture_routes_legacy as _legacy
from routes.venture_routes_performance_patch import install_route_performance_patch
from src.quest_bible_workflow import execute_quest_bible_action
from src.quest_indexing import job_to_dict, start_quest_index_worker, stop_quest_index_worker
from src.quest_session_management import request_artifact_synthesis, request_memory_synthesis
from src.venture_bible_refresh_guard import (
    install_bible_refresh_guard,
    queue_selected_bible_reindex,
    recover_obsolete_bible_jobs,
)
from src.venture_db_indexes import ensure_venture_indexes
from src.venture_synthesis import process_synthesis_job, start_quest_synthesis_worker, stop_quest_synthesis_worker
from src.venture_synthesis_execution_guard import install_synthesis_execution_guard
from src.venture_synthesis_reliability import (
    install_synthesis_reliability_guard,
    recover_stale_synthesis_jobs,
)
from src.venture_synthesis_validation_fallback import install_synthesis_validation_fallback

# Install low-level guards before routes create or display work. The workers
# remain idempotent: both legacy startup hooks and this facade may call start.
ensure_venture_indexes()
install_bible_refresh_guard()
install_synthesis_reliability_guard()
install_synthesis_validation_fallback()
install_synthesis_execution_guard()
install_route_performance_patch()


class QuestBibleRequest(BaseModel):
    reference: str
    source_id: str | None = None


class QuestManagementRequest(BaseModel):
    action: str
    artifact_proposal_id: str | None = None


def _remove_route(router, path: str, method: str) -> None:
    router.routes[:] = [
        route for route in router.routes
        if not (getattr(route, "path", None) == path and method in (getattr(route, "methods", set()) or set()))
    ]


async def _recover_then_process_synthesis(job_id: str) -> None:
    """Reclaim interrupted work before the guarded immediate attempt."""
    await asyncio.to_thread(recover_stale_synthesis_jobs)
    await process_synthesis_job(job_id)


def _schedule_synthesis(background_tasks: BackgroundTasks | None, job_id: str | None) -> None:
    """Nudge a queued or previously queued job after its commit.

    The persistent worker remains the queue owner. The process function is
    guarded by an atomic queued-to-running claim, so duplicate nudges are safe.
    """
    if background_tasks is not None and job_id:
        background_tasks.add_task(_recover_then_process_synthesis, job_id)


def _run_bible_action(
    request: Request,
    quest_id: str,
    payload: dict,
    background_tasks: BackgroundTasks | None = None,
):
    _legacy.require_venture_runtime()
    user = _legacy.require_quest_member(request, quest_id)
    result = execute_quest_bible_action(
        json.dumps({**payload, "quest_id": quest_id}),
        owner=user,
        session_id=quest_id,
    )
    if result.get("exit_code") != 0:
        raise HTTPException(400, result.get("error") or "Quest Bible action failed")
    synthesis = result.get("synthesis_job") if isinstance(result.get("synthesis_job"), dict) else {}
    _schedule_synthesis(background_tasks, synthesis.get("id"))
    return result


def _review_queue_payload(db, quest_id: str, captain: str) -> list[dict]:
    """Return Captain-owned pending Artifact drafts, including inactive documents."""
    rows = db.query(_legacy.QuestArtifactProposal, _legacy.Document).join(
        _legacy.Document,
        _legacy.Document.id == _legacy.QuestArtifactProposal.document_id,
    ).filter(
        _legacy.QuestArtifactProposal.session_id == quest_id,
        _legacy.QuestArtifactProposal.captain_username == captain,
        _legacy.QuestArtifactProposal.status.in_(("pending_review", "draft")),
        _legacy.Document.archived == False,  # noqa: E712
    ).order_by(_legacy.QuestArtifactProposal.created_at.desc()).limit(20).all()
    payload = []
    for proposal, document in rows:
        item = _optimized._artifact_payload(document, proposal)
        item["document_id"] = document.id
        item["review_state"] = "pending_review"
        payload.append(item)
    return payload


def _queue_source_work(db, source, *, captain: str, trigger: str):
    """Route every Bible action through a selected-book scope."""
    if source.source_type == "bible":
        try:
            job, created = queue_selected_bible_reindex(db, source, captain=captain)
        except ValueError as exc:
            message = {
                "bible_book_scope_required": "Select a Bible book before reindexing this source.",
                "bible_source_inactive": "Bible source is not active.",
            }.get(str(exc), "Bible source scope is invalid.")
            raise HTTPException(400, message) from exc
        return job, created
    from src.quest_indexing import enqueue_index_job
    return enqueue_index_job(db, source, trigger=trigger, created_by=captain), True


def setup_venture_routes(session_manager):
    router = _optimized.setup_venture_routes(session_manager)
    for path, method in (
        ("/api/quests/{quest_id}/argo-synthesis/run", "POST"),
        ("/api/quests/{quest_id}/artifacts", "GET"),
        ("/api/quests/{quest_id}/manage", "POST"),
        ("/api/quests/{quest_id}/sources/{source_id}/refresh", "POST"),
        ("/api/quests/{quest_id}/sources/{source_id}/reindex", "POST"),
        ("/api/quests/{quest_id}/sources/{source_id}/retry", "POST"),
    ):
        _remove_route(router, path, method)

    @router.on_event("startup")
    async def start_venture_workers() -> None:
        # Reclaim old work before polling. This makes an interrupted previous
        # run visible again without waiting for the normal stale lease.
        recover_obsolete_bible_jobs()
        recover_stale_synthesis_jobs()
        start_quest_index_worker(concurrency=1)
        start_quest_synthesis_worker(concurrency=1)

    @router.on_event("shutdown")
    async def stop_venture_workers() -> None:
        await stop_quest_synthesis_worker()
        await stop_quest_index_worker()

    @router.get("/api/quests/{quest_id}/artifacts")
    def list_artifacts(request: Request, quest_id: str, limit: int = 50):
        """Return published shelf items plus Captain-visible drafts awaiting review."""
        _legacy.require_venture_runtime()
        user = _legacy.require_quest_member(request, quest_id)
        role = _legacy.get_quest_role(user, quest_id)
        db = _legacy.SessionLocal()
        try:
            documents = db.query(_legacy.Document).filter(
                _legacy.Document.session_id == quest_id,
                _legacy.Document.archived == False,  # noqa: E712
                _legacy.Document.is_active == True,  # noqa: E712
                _legacy.Document.owner == user,
            ).order_by(_legacy.Document.updated_at.desc()).limit(max(1, min(limit, 100))).all()
            published = db.query(_legacy.QuestArtifactProposal).filter(
                _legacy.QuestArtifactProposal.session_id == quest_id,
                _legacy.QuestArtifactProposal.status == "published",
            ).all()
            proposal_by_document = {proposal.document_id: proposal for proposal in published}
            if role != "captain":
                proposal_by_document.update({_legacy._artifact_copy_id(proposal.id, user): proposal for proposal in published})
            gallery = db.query(_legacy.GalleryImage).filter(
                _legacy.GalleryImage.session_id == quest_id,
                _legacy.GalleryImage.is_active == True,  # noqa: E712
            ).order_by(_legacy.GalleryImage.updated_at.desc()).limit(50).all()
            return {
                "documents": [
                    _optimized._artifact_payload(document, proposal_by_document.get(document.id))
                    for document in documents
                ],
                "review_queue": _review_queue_payload(db, quest_id, user) if role == "captain" else [],
                "gallery": [
                    {"id": image.id, "filename": image.filename, "prompt": image.prompt}
                    for image in gallery
                ],
            }
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/sources/{source_id}/refresh")
    def refresh_source(request: Request, quest_id: str, source_id: str):
        _legacy.require_venture_runtime()
        captain = _legacy.require_quest_captain(request, quest_id)
        db = _legacy.SessionLocal()
        try:
            source = db.query(_legacy.QuestSource).filter(
                _legacy.QuestSource.id == source_id,
                _legacy.QuestSource.session_id == quest_id,
            ).first()
            if not source:
                raise HTTPException(404, "Quest source not found")

            # Preserve the legacy dynamic-email preflight. Bible sources are the
            # only special case: they must queue selected book work, not a
            # generic source refresh.
            email_poll = None
            if source.source_type == "email":
                try:
                    from src.venture_email import poll_email_source
                    email_poll = poll_email_source(db, source)
                except Exception:
                    email_poll = {"changed": False, "error": "email_poll_unavailable"}

            job, created = _queue_source_work(db, source, captain=captain, trigger="manual_refresh")
            db.commit()
            payload = {
                "accepted": True,
                "reindexed_selected_books": source.source_type == "bible",
                "already_active": not created,
                "source": _legacy._source_to_dict(source, "raw"),
                "index_job": job_to_dict(job),
            }
            if email_poll is not None:
                payload["email_poll"] = email_poll
                payload["version_id"] = email_poll.get("version_id")
            return payload
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/sources/{source_id}/reindex")
    def reindex_source(request: Request, quest_id: str, source_id: str):
        return refresh_source(request, quest_id, source_id)

    @router.post("/api/quests/{quest_id}/sources/{source_id}/retry")
    def retry_source(request: Request, quest_id: str, source_id: str):
        return refresh_source(request, quest_id, source_id)

    @router.get("/api/quests/{quest_id}/bible/lookup")
    def lookup_bible(request: Request, quest_id: str, reference: str | None = None, query: str | None = None, source_id: str | None = None):
        if reference:
            return _run_bible_action(request, quest_id, {
                "action": "retrieve_bible_passage",
                "reference": reference,
                "source_id": source_id,
            })
        if query:
            return _run_bible_action(request, quest_id, {
                "action": "search_bible",
                "query": query,
                "source_id": source_id,
            })
        raise HTTPException(400, "Provide reference or query")

    @router.post("/api/quests/{quest_id}/bible/request")
    def request_bible_passage(request: Request, quest_id: str, payload: QuestBibleRequest):
        return _run_bible_action(request, quest_id, {
            "action": "request_bible_passage",
            "reference": payload.reference,
            "source_id": payload.source_id,
        })

    @router.post("/api/quests/{quest_id}/bible/remember")
    def remember_bible_passage(request: Request, quest_id: str, payload: QuestBibleRequest, background_tasks: BackgroundTasks):
        return _run_bible_action(request, quest_id, {
            "action": "remember_bible_passage",
            "reference": payload.reference,
            "source_id": payload.source_id,
        }, background_tasks)

    @router.post("/api/quests/{quest_id}/manage")
    async def manage_quest(request: Request, quest_id: str, payload: QuestManagementRequest, background_tasks: BackgroundTasks):
        _legacy.require_venture_runtime()
        captain = _legacy.require_quest_captain(request, quest_id)
        if payload.action not in {"synthesize_artifact", "synthesize_memory"}:
            raise HTTPException(400, "Unsupported Quest management action")
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
            # A duplicate request may return an existing queued job. Re-nudge it
            # rather than reporting progress that cannot resume until a restart.
            _schedule_synthesis(background_tasks, result.job_id)
            return {"result": result.to_dict(), "management": _optimized._management_snapshot(db, quest_id, "captain")}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/argo-synthesis/run")
    async def run_argo_synthesis(request: Request, quest_id: str, background_tasks: BackgroundTasks):
        """Compatibility endpoint; queue and nudge the guarded worker once."""
        _legacy.require_venture_runtime()
        captain = _legacy.require_quest_captain(request, quest_id)
        db = _legacy.SessionLocal()
        try:
            result = request_artifact_synthesis(db, _legacy, quest_id=quest_id, captain=captain)
            db.commit()
            _schedule_synthesis(background_tasks, result.job_id)
            return {"result": result.to_dict(), "job_id": result.job_id}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    return router
