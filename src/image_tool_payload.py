"""Normalize image-tool arguments across Argos's two tool encodings.

Legacy internal image calls are newline-delimited::

    prompt\nmodel\nsize\nquality

Agent/MCP calls may instead carry a JSON object such as::

    {"prompt":"cat","model":"gpt-image-1","size":"1024x1024","quality":"high"}

Local image routing must never use that entire JSON string as the Diffusers
prompt. This module converts the JSON representation at the shared boundary,
while retaining the legacy format for existing callers.
"""

from __future__ import annotations

import json
from typing import Any


def normalize_image_tool_content(content: Any) -> str:
    """Return Argos's canonical newline-delimited image request representation.

    Invalid or non-object JSON is kept as legacy prompt content. Prompt newlines
    are flattened because newline delimiters are structural in the legacy format.
    The model remains present for cloud fallback diagnostics, but a configured
    local Image Default replaces it downstream.
    """

    if isinstance(content, dict):
        payload = content
    else:
        raw = str(content or "").strip()
        if not raw:
            return ""
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            return raw
        if not isinstance(decoded, dict):
            return raw
        payload = decoded

    prompt = str(payload.get("prompt") or "").replace("\r", " ").replace("\n", " ").strip()
    model = str(payload.get("model") or "").strip()
    size = str(payload.get("size") or "").strip()
    quality = str(payload.get("quality") or "").strip()
    return "\n".join((prompt, model, size, quality))
