"""HTTP API for durable, local prompt-to-video jobs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from src.auth_helpers import require_privilege
from src.video_settings import (
    VIDEO_DEFAULTS,
    VideoSettingsError,
    get_video_defaults,
    save_video_defaults,
    validate_defaults_patch,
)
from services.video_generation import get_video_generation_service, runtime_status


def _is_admin(request: Request, user: str) -> bool:
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
    async def _start_video_worker() -> None:
        await service.start()

    @router.on_event("shutdown")
    async def _stop_video_worker() -> None:
        await service.stop()

    @router.get("/runtime")
    async def video_runtime(request: Request) -> dict[str, Any]:
        # Runtime availability is intentionally readable to signed-in users so
        # the AI Defaults card can explain why a local feature is disabled.
        require_privilege(request, "can_generate_videos")
        return runtime_status()

    @router.get("/defaults")
    async def video_defaults(request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        return {
            "defaults": get_video_defaults(user),
            "global_defaults": get_video_defaults(None) if _is_admin(request, user) else None,
            "preset": {
                "resolutions": [{"width": 512, "height": 512}, {"width": 768, "height": 512}, {"width": 512, "height": 768}],
                "frames": [33, 49, 97],
                "fps": [24],
                "pipelines": ["distilled"],
                "tiling": ["auto", "enabled", "disabled"],
                "audio": False,
            },
        }

    @router.put("/defaults")
    async def update_video_defaults(request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Video settings must be an object.")
        global_scope = bool(body.pop("global", False))
        if global_scope and not _is_admin(request, user):
            raise HTTPException(403, "Admin only")
        try:
            defaults = save_video_defaults(user, body, global_scope=global_scope)
        except VideoSettingsError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True, "defaults": defaults, "scope": "global" if global_scope else "user"}

    @router.post("/generations", status_code=202)
    async def create_video_generation(request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Generation request must be an object.")
        try:
            job = service.create_job(user, body)
        except VideoSettingsError as exc:
            raise HTTPException(400, str(exc))
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        except ValueError as exc:
            raise HTTPException(403, str(exc))
        return {"job_id": job.id, "status": job.status, "stage": job.stage}

    @router.get("/generations/{job_id}")
    async def get_video_generation(job_id: str, request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        job = service.get_job(job_id, user)
        if not job:
            # Do not reveal whether another user owns a guessed job id.
            raise HTTPException(404, "Video generation not found")
        return service.serialize_job(job)

    @router.post("/generations/{job_id}/cancel")
    async def cancel_video_generation(job_id: str, request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        job = service.cancel_job(job_id, user)
        if not job:
            raise HTTPException(404, "Video generation not found")
        return {"ok": True, "job_id": job.id, "status": job.status, "stage": job.stage}

    return router
