"""API for the guided local depth-parallax video workflow."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from services.anchor_video_generation import get_video_generation_service, runtime_status
from services.local_image_runtime import repair_log_tail, start_repair as start_image_runtime_repair, status as image_runtime_status
from services.video_runtime_setup import install_log_tail, start_install
from src.anchor_video_settings import VideoSettingsError, get_video_defaults, save_video_defaults
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


def _image_default(owner: str | None) -> dict[str, Any]:
    try:
        from src.image_generation_defaults import resolve_configured_image_endpoint
        endpoint = resolve_configured_image_endpoint(owner)
    except Exception:
        endpoint = None
    if endpoint is None:
        return {"ready": False, "message": "Select and enable an Image Default for the stable animation anchor."}
    result: dict[str, Any] = {
        "ready": True,
        "endpoint": endpoint.endpoint_name,
        "model": endpoint.model,
        "message": f"{endpoint.endpoint_name} · {endpoint.model}",
    }
    # The Cookbook Diffusers server shares the app's Python environment. Probe
    # the import boundary in a child process before accepting a local endpoint
    # as ready, so a lazy load failure does not surface later as an opaque 503.
    if endpoint.is_local:
        runtime = image_runtime_status()
        result["runtime"] = runtime
        if not runtime.get("available"):
            result["ready"] = False
            result["message"] = "Local Diffusers dependency mismatch: " + str(runtime.get("reason") or "repair required")[:340]
    return result


def _planner(owner: str | None) -> dict[str, Any]:
    try:
        from src.endpoint_resolver import resolve_endpoint
        url, model, _headers = resolve_endpoint("utility", owner=owner)
    except Exception:
        url, model = "", ""
    if not url or not model:
        return {"ready": False, "message": "Select a Utility Model or Default Chat Model for prompt planning."}
    return {"ready": True, "model": str(model), "message": str(model)}


def _setup(owner: str | None) -> dict[str, Any]:
    runtime = runtime_status()
    image = _image_default(owner)
    planner = _planner(owner)
    return {
        "ready": bool(runtime.get("available") and image["ready"] and planner["ready"]),
        "runtime": runtime,
        "image_default": image,
        "planner": planner,
        "profile": {
            "mode": "anchor_first_depth_parallax",
            "description": "Subtle depth-aware camera motion from a stable Gallery image anchor.",
            "width": 512,
            "height": 512,
            "duration_seconds": 10,
            "fps": 24,
            "audio": False,
        },
    }


def setup_video_routes() -> APIRouter:
    router = APIRouter(prefix="/api/video", tags=["video"])
    service = get_video_generation_service()

    @router.on_event("startup")
    async def start_video_worker() -> None:
        await service.start()

    @router.on_event("shutdown")
    async def stop_video_worker() -> None:
        await service.stop()

    @router.get("/setup")
    async def get_setup(request: Request) -> dict[str, Any]:
        return _setup(get_current_user(request))

    @router.post("/setup/install", status_code=202)
    async def install_engine(_request: Request) -> dict[str, Any]:
        result = start_install()
        if not result.get("accepted"):
            raise HTTPException(400, result.get("error") or "The local depth engine cannot be installed on this host.")
        return {"ok": True, **result}

    @router.get("/setup/install-log")
    async def get_install_log(_request: Request) -> dict[str, str]:
        return {"log": install_log_tail()}

    @router.post("/setup/repair-image-runtime", status_code=202)
    async def repair_image_runtime(_request: Request) -> dict[str, Any]:
        result = start_image_runtime_repair()
        if not result.get("accepted"):
            raise HTTPException(400, result.get("error") or "The local image runtime cannot be repaired on this host.")
        return {"ok": True, **result}

    @router.get("/setup/repair-image-runtime/log")
    async def get_image_repair_log(_request: Request) -> dict[str, str]:
        return {"log": repair_log_tail()}

    @router.post("/setup/test", status_code=202)
    async def run_guided_test(request: Request) -> dict[str, Any]:
        owner = get_current_user(request)
        state = _setup(owner)
        if not state["runtime"].get("available"):
            raise HTTPException(409, "Install and verify the local depth engine before running a test.")
        if not state["image_default"]["ready"] or not state["planner"]["ready"]:
            raise HTTPException(409, state["image_default"].get("message") or "Finish the Image Default and Utility Model checklist before running a test.")
        try:
            await service.start()
            job = service.create_job(owner, {
                "prompt": "A serene portrait beside a sunlit window, with a subtle cinematic camera drift through the quiet room.",
                "session_id": None,
            }, allow_disabled=True)
        except (VideoSettingsError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        except Exception as exc:
            logger.exception("Guided local video test could not start")
            raise HTTPException(500, f"Guided test could not start: {type(exc).__name__}: {str(exc)[:300]}") from exc
        return {"job_id": job.id, "status": job.status, "stage": job.stage, "test": True}

    @router.get("/defaults")
    async def get_defaults(request: Request) -> dict[str, Any]:
        return {"defaults": get_video_defaults(get_current_user(request))}

    @router.put("/defaults")
    async def put_defaults(request: Request) -> dict[str, Any]:
        body = await request.json()
        try:
            defaults = save_video_defaults(get_current_user(request), body)
        except VideoSettingsError as exc:
            raise HTTPException(400, str(exc))
        return {"ok": True, "defaults": defaults}

    @router.post("/generations", status_code=202)
    async def create_generation(request: Request) -> dict[str, Any]:
        body = await request.json()
        if not isinstance(body, dict):
            raise HTTPException(400, "Video generation request must be an object.")
        state = _setup(get_current_user(request))
        if not state["image_default"]["ready"]:
            raise HTTPException(409, state["image_default"].get("message") or "The Image Default is not ready.")
        try:
            await service.start()
            job = service.create_job(get_current_user(request), body)
        except (VideoSettingsError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        return {"job_id": job.id, "status": job.status, "stage": job.stage}

    @router.get("/generations/{job_id}")
    async def get_generation(job_id: str, request: Request) -> dict[str, Any]:
        job = service.get_job(job_id, get_current_user(request))
        if job is None:
            raise HTTPException(404, "Video generation not found")
        return service.serialize(job)

    @router.post("/generations/{job_id}/cancel")
    async def cancel_generation(job_id: str, request: Request) -> dict[str, Any]:
        job = service.cancel_job(job_id, get_current_user(request))
        if job is None:
            raise HTTPException(404, "Video generation not found")
        return {"ok": True, "job_id": job.id, "status": job.status, "stage": job.stage}

    return router
