"""Owner-scoped API for the guided anchor-first local video workflow."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.anchor_video_generation import get_video_generation_service, runtime_status
from services.video_runtime_setup import install_log_tail, prepare_runtime, start_install, status as setup_runtime_status
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


def _image_default_status(owner: str | None) -> dict[str, Any]:
    try:
        from src.image_generation_defaults import resolve_configured_image_endpoint
        selected = resolve_configured_image_endpoint(owner)
    except Exception:
        selected = None
    if selected is None:
        return {
            "ready": False,
            "message": "Choose an enabled Image Default for the animation anchor.",
        }
    return {
        "ready": True,
        "endpoint_id": selected.endpoint_id,
        "endpoint_name": selected.endpoint_name,
        "model": selected.model,
        "message": f"{selected.endpoint_name} · {selected.model}",
    }


def _planner_status(owner: str | None) -> dict[str, Any]:
    try:
        from src.endpoint_resolver import resolve_endpoint
        url, model, _headers = resolve_endpoint("utility", owner=owner)
    except Exception:
        url, model = "", ""
    if not url or not model:
        return {
            "ready": False,
            "message": "Choose a Utility Model or Default Chat Model to plan anchor and motion prompts.",
        }
    return {"ready": True, "model": str(model), "message": str(model)}


def _setup_payload(owner: str | None) -> dict[str, Any]:
    # Set MLX_VIDEO_BIN to the managed wrapper before asking the existing worker
    # about availability. This is what makes the installation survive a restart
    # without requiring a user-created environment variable.
    prepare_runtime()
    managed_runtime = setup_runtime_status()
    worker_runtime = runtime_status()
    ready = bool(managed_runtime.get("available") and worker_runtime.get("available"))
    return {
        "ready": ready,
        "runtime": {**managed_runtime, "worker": worker_runtime},
        "image_default": _image_default_status(owner),
        "planner": _planner_status(owner),
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


def setup_video_routes() -> APIRouter:
    router = APIRouter(prefix="/api/video", tags=["video"])
    service = get_video_generation_service()

    @router.on_event("startup")
    async def start_video_worker() -> None:
        prepare_runtime()
        await service.start()

    @router.on_event("shutdown")
    async def stop_video_worker() -> None:
        await service.stop()

    @router.get("/runtime")
    async def get_runtime(request: Request) -> dict[str, Any]:
        require_privilege(request, "can_generate_videos")
        prepare_runtime()
        return runtime_status()

    @router.get("/setup")
    async def get_setup(request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        return _setup_payload(user)

    @router.post("/setup/install", status_code=202)
    async def install_runtime(request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        if not _admin(request, user):
            raise HTTPException(403, "An administrator must install the local video engine.")
        result = start_install()
        if not result.get("accepted"):
            raise HTTPException(400, result.get("error") or "The video engine cannot be installed on this host.")
        return {"ok": True, **result}

    @router.get("/setup/install-log")
    async def get_install_log(request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        if not _admin(request, user):
            raise HTTPException(403, "Admin only")
        return {"log": install_log_tail()}

    @router.post("/setup/test", status_code=202)
    async def run_setup_test(request: Request) -> dict[str, Any]:
        user = require_privilege(request, "can_generate_videos")
        setup = _setup_payload(user)
        if not setup["runtime"].get("available"):
            raise HTTPException(409, "Install the local video engine before running a test.")
        if not setup["image_default"].get("ready") or not setup["planner"].get("ready"):
            raise HTTPException(409, "Finish the Image Default and Utility Model setup before running a test.")
        try:
            await service.start()
            job = service.create_job(user, {
                "prompt": "A calm portrait scene with a person standing beside a window as afternoon light shifts softly across the room.",
                "session_id": None,
            })
        except (VideoSettingsError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        return {"job_id": job.id, "status": job.status, "stage": job.stage, "test": True}

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
            prepare_runtime()
            await service.start()
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
