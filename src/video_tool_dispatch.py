"""Integration adapters for the local ``generate_video`` agent tool."""
from __future__ import annotations

import json
import re


def _agent_enabled(owner: str | None) -> bool:
    try:
        from routes.prefs_routes import _load_for_user
        return (_load_for_user(owner) or {}).get("video_agent_enabled", True) is not False
    except Exception:
        return True


def install_video_agent_tags(module) -> None:
    if getattr(module, "_depth_video_tags_installed", False):
        return
    module.TOOL_TAGS.add("generate_video")
    try:
        from src import tool_parsing
        tool_parsing._TOOL_BLOCK_RE = re.compile(r"```(" + "|".join(module.TOOL_TAGS) + r")\s*\n([\s\S]*?)```", re.IGNORECASE)
        tool_parsing._TOOL_NAME_MAP["generate_video"] = "generate_video"
    except Exception:
        pass
    try:
        from src import tool_security
        tool_security._PLAN_MODE_KNOWN_MUTATORS.add("generate_video")
    except Exception:
        pass
    module._depth_video_tags_installed = True


def install_video_tool_schema(module) -> None:
    if any((entry.get("function") or {}).get("name") == "generate_video" for entry in module.FUNCTION_TOOL_SCHEMAS):
        return
    module.FUNCTION_TOOL_SCHEMAS.append({
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": "Create a local silent 10-second depth-aware parallax video. Argos first creates a Gallery image anchor through the user's Image Default, then applies subtle camera motion locally. This does not invent new object motion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "High-level video intent used only to plan an image anchor and camera motion."},
                    "seed": {"type": "integer", "description": "Optional reproducibility seed."},
                },
                "required": ["prompt"],
            },
        },
    })


def _request(content: str, session_id: str | None) -> dict:
    raw = str(content or "").strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            result = {"prompt": str(data.get("prompt") or data.get("intent") or "").strip(), "session_id": session_id}
            if data.get("seed") is not None:
                result["video_seed"] = data["seed"]
            return result
    except (TypeError, ValueError):
        pass
    return {"prompt": raw.split("\n", 1)[0].strip(), "session_id": session_id}


def install_video_generation_dispatch(module) -> None:
    if getattr(module, "_depth_video_dispatch_installed", False):
        return
    original = module._execute_tool_block_impl

    async def wrapped(block, session_id=None, disabled_tools=None, owner=None, progress_cb=None, tool_policy=None):
        if getattr(block, "tool_type", "") != "generate_video":
            return await original(block, session_id=session_id, disabled_tools=disabled_tools, owner=owner, progress_cb=progress_cb, tool_policy=tool_policy)
        if disabled_tools and "generate_video" in disabled_tools:
            return await original(block, session_id=session_id, disabled_tools=disabled_tools, owner=owner, progress_cb=progress_cb, tool_policy=tool_policy)
        if tool_policy and tool_policy.blocks("generate_video"):
            return await original(block, session_id=session_id, disabled_tools=disabled_tools, owner=owner, progress_cb=progress_cb, tool_policy=tool_policy)
        if not _agent_enabled(owner):
            return "generate_video", {"error": "Generate video is disabled in Built-in Agent Tools.", "exit_code": 1}
        try:
            from services.anchor_video_generation import get_video_generation_service
            service = get_video_generation_service()
            await service.start()
            job = service.create_job(owner, _request(getattr(block, "content", ""), session_id))
            return "generate_video", {
                "output": (
                    "Queued a local depth-parallax video. Argos will create a Gallery anchor first, then render subtle depth-aware camera motion. "
                    f"[video-job:{job.id}]"
                ),
                "exit_code": 0,
                "video_job_id": job.id,
                "video_status": job.status,
                "video_stage": job.stage,
            }
        except Exception as exc:
            return "generate_video", {"error": str(exc), "exit_code": 1}

    module._execute_tool_block_impl = wrapped
    module._depth_video_dispatch_installed = True
