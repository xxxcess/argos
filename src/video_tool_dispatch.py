"""Adapters for the anchor-first ``generate_video`` agent tool."""
from __future__ import annotations

import json
import re


def _agent_video_enabled(owner) -> bool:
    """Read the same user-facing Built-in Tools toggle shown in Settings."""
    try:
        from routes.prefs_routes import _load_for_user
        return (_load_for_user(owner) or {}).get("video_agent_enabled", True) is not False
    except Exception:
        return True


def install_video_agent_tags(module):
    if getattr(module, "_video_tool_tags_installed", False):
        return
    module.TOOL_TAGS.add("generate_video")
    try:
        from src import tool_parsing
        tool_parsing._TOOL_BLOCK_RE = re.compile(
            r"```(" + "|".join(module.TOOL_TAGS) + r")\s*\n([\s\S]*?)```",
            re.IGNORECASE,
        )
        tool_parsing._TOOL_NAME_MAP["generate_video"] = "generate_video"
    except Exception:
        pass
    # Video creation mutates Gallery/storage, so plan mode must fail closed just
    # like generate_image. Keep the static policy set current without changing
    # unrelated legacy policy code.
    try:
        from src import tool_security
        tool_security._PLAN_MODE_KNOWN_MUTATORS.add("generate_video")
    except Exception:
        pass
    module._video_tool_tags_installed = True


def install_video_tool_schema(module):
    if any((item.get("function") or {}).get("name") == "generate_video" for item in module.FUNCTION_TOOL_SCHEMAS):
        return
    module.FUNCTION_TOOL_SCHEMAS.append({
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": "Generate a muted ten-second anchor-first video. Argos uses the user's configured Image Default to create and save a stable Gallery anchor, then derives a separate motion prompt and animates that anchor locally.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "High-level video intent. It is used only for planning and is never sent unchanged to downstream image or video generation."},
                    "seed": {"type": "integer", "description": "Optional reproducibility seed."},
                },
                "required": ["prompt"],
            },
        },
    })


def _request(content, session_id):
    raw = str(content or "").strip()
    try:
        value = json.loads(raw)
        if isinstance(value, dict):
            result = {"prompt": str(value.get("prompt") or value.get("intent") or "").strip(), "session_id": session_id}
            if "seed" in value:
                result["video_seed"] = value["seed"]
            return result
    except (ValueError, TypeError):
        pass
    return {"prompt": raw.split("\n", 1)[0].strip(), "session_id": session_id}


def install_video_generation_dispatch(module):
    if getattr(module, "_video_generation_dispatch_installed", False):
        return
    original = module._execute_tool_block_impl

    async def wrapped(block, session_id=None, disabled_tools=None, owner=None, progress_cb=None, tool_policy=None):
        if getattr(block, "tool_type", "") == "generate_video":
            if disabled_tools and "generate_video" in disabled_tools:
                return await original(block, session_id=session_id, disabled_tools=disabled_tools, owner=owner, progress_cb=progress_cb, tool_policy=tool_policy)
            if tool_policy and tool_policy.blocks("generate_video"):
                return await original(block, session_id=session_id, disabled_tools=disabled_tools, owner=owner, progress_cb=progress_cb, tool_policy=tool_policy)
            if not _agent_video_enabled(owner):
                return "generate_video", {"error": "Generate video is disabled in Built-in Agent Tools.", "exit_code": 1}
            try:
                # The setup route normally prepares this wrapper. Ensure direct
                # agent execution follows the same managed runtime path.
                from services.video_runtime_setup import prepare_runtime
                prepare_runtime()
                from services.anchor_video_generation import get_video_generation_service
                service = get_video_generation_service()
                await service.start()
                job = service.create_job(owner, _request(getattr(block, "content", ""), session_id))
                return "generate_video", {
                    "output": (
                        "Queued anchor-first video generation. Argos will create a Gallery anchor, "
                        "show it when ready, then animate it into a muted clip. "
                        f"[video-job:{job.id}]"
                    ),
                    "exit_code": 0,
                    "video_job_id": job.id,
                    "video_status": job.status,
                    "video_stage": job.stage,
                }
            except Exception as exc:
                return "generate_video", {"error": str(exc), "exit_code": 1}
        return await original(block, session_id=session_id, disabled_tools=disabled_tools, owner=owner, progress_cb=progress_cb, tool_policy=tool_policy)

    module._execute_tool_block_impl = wrapped
    module._video_generation_dispatch_installed = True
