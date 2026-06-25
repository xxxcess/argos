"""Terminate an agent turn after a successful image-generation tool call.

A generated image is already a complete user-visible result. The generic agent
loop otherwise appends the tool output and asks the LLM for another round merely
to write a confirmation sentence. That extra request can fail at the selected
chat provider (for example with an HTTP 500) after the image was correctly saved
and rendered, misleading the user into thinking generation failed.

This lightweight wrapper closes the source agent stream immediately after its
successful ``generate_image`` tool event, emits a deterministic confirmation and
terminal SSE events, and lets the ordinary chat-route persistence path save the
assistant message.
"""

from __future__ import annotations

import functools
import json
import time
from typing import Any


def _successful_image_tool_event(chunk: str) -> bool:
    """Return true only for a completed image event with a concrete image URL."""

    if not isinstance(chunk, str) or not chunk.startswith("data: "):
        return False
    payload = chunk[6:].strip()
    if not payload or payload == "[DONE]":
        return False
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    if data.get("type") != "tool_output" or data.get("tool") != "generate_image":
        return False
    if data.get("exit_code") not in (None, 0):
        return False
    if data.get("error"):
        return False
    return bool(data.get("image_url"))


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
        try:
            async for chunk in source:
                yield chunk
                if not _successful_image_tool_event(chunk):
                    continue

                # The source generator is paused immediately after yielding the
                # tool event. Closing it here prevents its next LLM continuation
                # request, which is unnecessary and may fail independently.
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
