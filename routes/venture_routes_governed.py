"""Final Venture route guards for legacy-compatible controls."""

from __future__ import annotations

from fastapi import Request

from routes import venture_routes_legacy as _legacy
from routes.venture_routes_optimized import setup_venture_routes as _optimized_setup
from routes.venture_routes_performance_patch import install_route_performance_patch
from src.quest_session_management import request_artifact_synthesis
from src.venture_db_indexes import ensure_venture_indexes
from src.venture_synthesis_execution_guard import install_synthesis_execution_guard

# The optimized facade installs bounded evidence and atomic queue claiming.
# These guards reject stale direct execution and remove remaining N+1/count
# hydration before the application starts its background workers.
ensure_venture_indexes()
install_synthesis_execution_guard()
install_route_performance_patch()


def _remove_route(router, path: str, method: str) -> None:
    router.routes[:] = [
        route for route in router.routes
        if not (getattr(route, "path", None) == path and method in (getattr(route, "methods", set()) or set()))
    ]


def setup_venture_routes(session_manager):
    router = _optimized_setup(session_manager)
    _remove_route(router, "/api/quests/{quest_id}/argo-synthesis/run", "POST")

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
