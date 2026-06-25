"""Terminate an agent turn after a successful image-generation tool call.

A generated image is already a complete user-visible result. The generic agent
loop otherwise appends the tool output and asks the LLM for another round merely
to write a confirmation sentence. That extra request can fail at the selected
chat provider (for example with an HTTP 500) after the image was correctly saved
and rendered, misleading the user into thinking generation failed.

The base loop persists its tool event after emitting the tool bubble, then emits
``agent_step`` immediately before beginning that unnecessary next LLM round. This
wrapper waits for that boundary, so Gallery/history metadata remains intact while
the follow-up request is prevented.
"""

from __future__ import annotations

import functools
import json
import time
from typing import Any


def _sse_data(chunk: str) -> dict | None:
    if not isinstance(chunk, str) or not chunk.startswith("data: "):
        return None
    payload = chunk[6:].strip()
    if not payload or payload == "[DONE]":
        return None
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _successful_image_tool_event(chunk: str) -> bool:
    """Return true only for a completed image event with a concrete image URL."""

    data = _sse_data(chunk)
    if not data:
        return False
    if data.get("type") != "tool_output" or data.get("tool") != "generate_image":
        return False
    if data.get("exit_code") not in (None, 0):
        return False
    if data.get("error"):
        return False
    return bool(data.get("image_url"))


def _is_next_agent_step(chunk: str) -> bool:
    """Return true for the between-round event emitted after tool persistence."""

    data = _sse_data(chunk)
    return bool(data and data.get("type") == "agent_step")


def install_image_agent_terminal(agent_loop_module: Any) -> None:
    """Install the image-completion short circuit exactly once."""

    if getattr(agent_loop_module, "_image_agent_terminal_installed", False):
        return

    original_stream = agent_loop_module.stream_agent_loop

    @functools.wraps(original_stream)
    async def stream_with_terminal_image(*args, **kwargs):
        started = time.monotonic()
        source = original_stream(*args, **kwargs)
        terminal = False
        image_completed = False
        try:
            async for chunk in source:
                if _successful_image_tool_event(chunk):
                    image_completed = True
                    # Forward the bubble first. The base loop resumes afterward
                    # to persist its tool event and append the tool result.
                    yield chunk
                    continue

                if image_completed and _is_next_agent_step(chunk):
                    # `agent_step` is emitted after the base loop has recorded
                    # the image tool event, but before it requests another model
                    # round. Do not forward the spinner for a round we will skip.
                    closer = getattr(source, "aclose", None)
                    if callable(closer):
                        await closer()
                    terminal = True
                    confirmation = "\n\nImage generated."
                    yield "data: " + json.dumps({"delta": confirmation}) + "\n\n"
                    yield "data: " + json.dumps({
                        "type": "metrics",
                        "data": {
                            "response_time": round(time.monotonic() - started, 2),
                            "image_generation_complete": True,
                        },
                    }) + "\n\n"
                    yield "data: [DONE]\n\n"
                    return

                yield chunk
        finally:
            # Closing an already-closed async generator is harmless. On a
            # consumer disconnect this also propagates cancellation promptly.
            if not terminal:
                closer = getattr(source, "aclose", None)
                if callable(closer):
                    try:
                        await closer()
                    except (RuntimeError, StopAsyncIteration):
                        pass

    agent_loop_module.stream_agent_loop = stream_with_terminal_image
    agent_loop_module._image_agent_terminal_installed = True
