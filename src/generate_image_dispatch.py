"""Bridge agent ``generate_image`` calls to the configured Image Default.

Normal agent image generation historically goes through the ``image_gen`` MCP
server. That is useful for a cloud-only setup, but it bypasses
``src.ai_interaction.do_generate_image`` and therefore bypasses a selected local
Image endpoint. This wrapper keeps MCP as the fallback when no Image Default is
configured, while routing an explicitly selected endpoint through the standard
Argos image pipeline first.
"""

from __future__ import annotations

from typing import Any, Optional


def _tool_description(content: str) -> str:
    prompt = str(content or "").split("\n", 1)[0].strip()
    return f"generate_image: {prompt[:80]}" if prompt else "generate_image"


def _format_direct_image_result(result: dict) -> dict:
    """Normalize a direct image result to the tool dispatcher contract."""

    if not isinstance(result, dict):
        return {"error": "Image generation returned an invalid result", "exit_code": 1}
    if result.get("error"):
        result.setdefault("exit_code", 1)
        return result
    result.setdefault("exit_code", 0)
    # Preserve the structured image fields consumed by the stream renderer, and
    # also give text-only callers a meaningful summary.
    result.setdefault("stdout", result.get("results", "Image generated"))
    return result


def install_generate_image_dispatch(tool_execution_module) -> None:
    """Wrap the legacy tool dispatcher once, without changing cloud behavior."""

    if getattr(tool_execution_module, "_image_default_dispatch_installed", False):
        return

    original_execute = tool_execution_module._execute_tool_block_impl

    async def execute_with_image_default(
        block: Any,
        session_id: Optional[str] = None,
        disabled_tools: Optional[set] = None,
        owner: Optional[str] = None,
        progress_cb=None,
        tool_policy=None,
    ):
        tool = getattr(block, "tool_type", "")
        if tool == "generate_image":
            try:
                from src.image_generation_defaults import resolve_configured_image_endpoint

                configured = resolve_configured_image_endpoint(owner)
            except Exception:
                configured = None
            if configured is not None:
                from src.ai_interaction import do_generate_image

                result = await do_generate_image(
                    getattr(block, "content", ""),
                    session_id=session_id,
                    owner=owner,
                )
                return _tool_description(getattr(block, "content", "")), _format_direct_image_result(result)

        return await original_execute(
            block,
            session_id=session_id,
            disabled_tools=disabled_tools,
            owner=owner,
            progress_cb=progress_cb,
            tool_policy=tool_policy,
        )

    tool_execution_module._execute_tool_block_impl = execute_with_image_default
    tool_execution_module._image_default_dispatch_installed = True
