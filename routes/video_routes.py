"""Owner-scoped API for anchor-first local video generation."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.anchor_video_generation import get_video_generation_service, runtime_status
from src.anchor_video_settings import VideoSettingsError, get_video_defaults, save_video_defaults
from src.auth_helpers import require_privilege


def _admin(request: Request, user: str) -> bool:
    if not user:
        return True
    manager = getattr(request.app.state, "auth_manager", None)
    try:
        return bool(manager and manager.is_admin(user))
    except Exception:
        return False


def setup_video_routes() -> APIRouter:
    router = APIRouter(prefix="/api/video", tags=["video"])
    service = get_video_generation_service()

    @router.on_event("startup")
    async def start_video_worker() -> None:
        await service.start()

    @router.on_event("shutdown")
    async def stop_video_worker() -> None:
        await service.stop()

    @router.get("/runtime")
    async def get_runtime(request: Request) -> dict[str, Any]:
        require_privilege(request, "can_generate_videos")
        return runtime_status()

    @router.get("/defaults")
    async def get_defaults(request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        return {
            "defaults": get_video_defaults(user),
            "global_defaults": get_video_defaults(None) if _admin(request, user) else None,
            "profile": {
                "mode": "anchor_first_i2v",
                "width": 512,
                "height": 512,
                "frames": 241,
                "fps": 24,
                "duration_seconds": 10,
                "audio": False,
            },
        }

    @router.put("/defaults")
    async def put_defaults(request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Video defaults must be an object.")
        global_scope = bool(body.pop("global", False))
        if global_scope and not _admin(request, user):
            raise HTTPException(403, "Admin only")
        try:
            defaults = save_video_defaults(user, body, global_scope=global_scope)
        except VideoSettingsError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True, "defaults": defaults, "scope": "global" if global_scope else "user"}

    @router.post("/generations", status_code=202)
    async def create_generation(request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Video generation request must be an object.")
        try:
            job = service.create_job(user, body)
        except VideoSettingsError as exc:
            raise HTTPException(400, str(exc))
        except ValueError as exc:
            raise HTTPException(403, str(exc))
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        return {"job_id": job.id, "status": job.status, "stage": job.stage}

    @router.get("/generations/{job_id}")
    async def get_generation(job_id: str, request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        job = service.get_job(job_id, user)
        if job is None:
            raise HTTPException(404, "Video generation not found")
        return service.serialize(job)

    @router.post("/generations/{job_id}/cancel")
    async def cancel_generation(job_id: str, request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        job = service.cancel_job(job_id, user)
        if job is None:
            raise HTTPException(404, "Video generation not found")
        return {"ok": True, "job_id": job.id, "status": job.status, "stage": job.stage}

    return router
