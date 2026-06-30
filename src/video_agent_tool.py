"""Canonical helpers for the agent-facing video generation tool."""

from __future__ import annotations

import json
import logging
from typing import Any

from src.anchor_video_settings import VIDEO_PROVIDER_REMOTE_LTX, VideoSettingsError

logger = logging.getLogger(__name__)

VIDEO_AGENT_DISABLED_MESSAGE = "Generate video is disabled in Built-in Agent Tools."
LOCAL_VIDEO_AGENT_ACK = "Creating the image anchor, then rendering local depth-aware camera motion."
REMOTE_VIDEO_AGENT_ACK = "Creating the image anchor, then submitting remote LTX motion generation."


def is_video_agent_enabled(owner: str | None) -> bool:
    """Return the per-user Built-in Agent Tools preference for generate_video."""

    try:
        from routes.prefs_routes import _load_for_user

        return (_load_for_user(owner) or {}).get("video_agent_enabled", True) is not False
    except Exception:
        return True


def set_video_agent_enabled(owner: str | None, enabled: bool) -> None:
    """Persist the per-user Built-in Agent Tools preference for generate_video."""

    from routes.prefs_routes import _load_for_user, _save_for_user

    prefs = dict(_load_for_user(owner) or {})
    prefs["video_agent_enabled"] = bool(enabled)
    _save_for_user(owner, prefs)


def _optional_seed(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        seed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Video seed must be a whole number.") from exc
    if seed < 0 or seed > 2_147_483_647:
        raise ValueError("Video seed must be between 0 and 2147483647.")
    return seed


def normalize_generate_video_request(content: Any) -> dict[str, Any]:
    """Normalize native and fenced calls to ``prompt`` plus optional ``seed``."""

    data: dict[str, Any] | None = None
    if isinstance(content, dict):
        data = content
    else:
        raw = str(content or "").strip()
        if raw:
            try:
                decoded = json.loads(raw)
            except (TypeError, ValueError):
                decoded = None
            if isinstance(decoded, dict):
                data = decoded
            else:
                first_line = raw.split("\n", 1)[0].strip()
                data = {"prompt": first_line}
        else:
            data = {"prompt": ""}

    prompt = str((data or {}).get("prompt") or (data or {}).get("intent") or "").strip()
    request: dict[str, Any] = {"prompt": prompt}
    seed = _optional_seed((data or {}).get("seed", (data or {}).get("video_seed")))
    if seed is not None:
        request["seed"] = seed
    return request


def generation_body_from_agent_request(request: dict[str, Any], session_id: str | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "prompt": str(request.get("prompt") or "").strip(),
        "session_id": session_id,
    }
    if request.get("seed") is not None:
        body["video_seed"] = request["seed"]
    return body


def safe_agent_video_error(exc: Exception) -> str:
    """Return a chat-safe startup error without paths or repair commands."""

    if isinstance(exc, (VideoSettingsError, ValueError)):
        text = str(exc).strip()
        return text or "Video generation request is invalid."
    if isinstance(exc, RuntimeError):
        logger.warning("Agent video generation could not start: %s", exc)
        return str(exc)[:240] if str(exc).strip() else "The selected video provider is unavailable or not fully configured."
    logger.exception("Agent video generation could not start")
    return "Video generation could not be started."


async def dispatch_agent_video_generation(
    content: Any,
    *,
    session_id: str | None,
    owner: str | None,
) -> tuple[str, dict[str, Any]]:
    """Queue one durable local video job for an agent tool call."""

    if not is_video_agent_enabled(owner):
        return "generate_video", {"error": VIDEO_AGENT_DISABLED_MESSAGE, "exit_code": 1}

    try:
        request = normalize_generate_video_request(content)
        body = generation_body_from_agent_request(request, session_id)
        from services.anchor_video_generation import get_video_generation_service

        service = get_video_generation_service()
        await service.start()
        job = service.create_job(owner, body, allow_disabled=True)
    except Exception as exc:
        return "generate_video", {"error": safe_agent_video_error(exc), "exit_code": 1}

    provider = str((getattr(job, "request_config", None) or {}).get("video_provider") or "depth_parallax")
    ack = REMOTE_VIDEO_AGENT_ACK if provider == VIDEO_PROVIDER_REMOTE_LTX else LOCAL_VIDEO_AGENT_ACK
    return "generate_video", {
        "kind": "video_generation",
        "job_id": job.id,
        "provider": provider,
        "status": "queued",
        "stage": "planning_anchor",
        "terminal_media_job": True,
        "assistant_ack": ack,
        "output": ack,
        "exit_code": 0,
        "video_job_id": job.id,
        "video_status": "queued",
        "video_stage": "planning_anchor",
    }
