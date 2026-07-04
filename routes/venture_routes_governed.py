"""Final Venture route guards for legacy-compatible controls."""

from __future__ import annotations

import json

from fastapi import HTTPException, Request

import routes.venture_routes_optimized as _optimized
from routes import venture_routes_legacy as _legacy
from routes.venture_routes_performance_patch import install_route_performance_patch
from src.quest_bible_workflow import execute_quest_bible_action
from src.quest_session_management import request_artifact_synthesis
from src.venture_db_indexes import ensure_venture_indexes
from src.venture_synthesis import start_quest_synthesis_worker, stop_quest_synthesis_worker
from src.venture_synthesis_execution_guard import install_synthesis_execution_guard
from src.venture_synthesis_validation_fallback import install_synthesis_validation_fallback

# The optimized facade installs bounded evidence and atomic queue claiming.
# These guards reject stale direct execution and remove remaining N+1/count
# hydration before the application starts its background workers.
ensure_venture_indexes()
install_synthesis_validation_fallback()
install_synthesis_execution_guard()
install_route_performance_patch()


class QuestBibleRequest(_legacy.BaseModel):
    reference: str
    source_id: str | None = None


def _remove_route(router, path: str, method: str) -> None:
    router.routes[:] = [
        route for route in router.routes
        if not (getattr(route, "path", None) == path and method in (getattr(route, "methods", set()) or set()))
    ]


def _run_bible_action(request: Request, quest_id: str, payload: dict):
    _legacy.require_venture_runtime()
    user = _legacy.require_quest_member(request, quest_id)
    result = execute_quest_bible_action(
        json.dumps({**payload, "quest_id": quest_id}),
        owner=user,
        session_id=quest_id,
    )
    if result.get("exit_code") != 0:
        raise HTTPException(400, result.get("error") or "Quest Bible action failed")
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


def setup_venture_routes(session_manager):
    router = _optimized.setup_venture_routes(session_manager)
    _remove_route(router, "/api/quests/{quest_id}/argo-synthesis/run", "POST")
    _remove_route(router, "/api/quests/{quest_id}/artifacts", "GET")

    @router.on_event("startup")
    async def start_venture_synthesis_worker() -> None:
        # Route import happens before an event loop exists; startup is the first
        # safe point to create the worker task. Without this, queued jobs remain
        # queued forever and no draft can reach the review queue.
        start_quest_synthesis_worker(concurrency=1)

    @router.on_event("shutdown")
    async def stop_venture_synthesis_worker() -> None:
        await stop_quest_synthesis_worker()

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
    def remember_bible_passage(request: Request, quest_id: str, payload: QuestBibleRequest):
        return _run_bible_action(request, quest_id, {
            "action": "remember_bible_passage",
            "reference": payload.reference,
            "source_id": payload.source_id,
        })

    @router.post("/api/quests/{quest_id}/argo-synthesis/run")
    def run_argo_synthesis(request: Request, quest_id: str):
        """Compatibility endpoint; queue work for the single synthesis worker."""
        _legacy.require_venture_runtime()
        captain = _legacy.require_quest_captain(request, quest_id)
        db = _legacy.SessionLocal()
        try:
            result = request_artifact_synthesis(db, _legacy, quest_id=quest_id, captain=captain)
            db.commit()
            return {"result": result.to_dict(), "job_id": result.job_id}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    return router
