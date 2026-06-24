"""Per-user image-generation endpoint selection.

The existing image tool accepts a model name emitted by an LLM.  That is useful
for ad-hoc requests, but a configured local image endpoint must win over a
hallucinated cloud model such as ``gpt-image-1``.  This module provides a small,
context-local override used by ``src.ai_interaction`` without changing its
Gallery persistence or provider response handling.
"""

from __future__ import annotations

import contextvars
import json
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ConfiguredImageEndpoint:
    """The validated endpoint/model selected in AI Defaults for this request."""

    endpoint_id: str
    model: str
    base_url: str
    headers: dict


_active_image_endpoint: contextvars.ContextVar[Optional[ConfiguredImageEndpoint]] = (
    contextvars.ContextVar("argos_active_image_endpoint", default=None)
)

_IMAGE_MODEL_PREFIXES = ("gpt-image", "dall-e", "chatgpt-image")


def _replace_tool_model(content: str, model: str) -> str:
    """Replace the optional second tool line while preserving prompt/size/quality."""

    lines = str(content or "").split("\n")
    if not lines:
        lines = [""]
    while len(lines) < 2:
        lines.append("")
    lines[1] = model
    return "\n".join(lines)


def _image_endpoint_id_for_owner(owner: Optional[str]) -> str:
    """Read the user-scoped endpoint id, with global config as a legacy fallback."""

    try:
        from routes.prefs_routes import _load_for_user

        prefs = _load_for_user(owner) or {}
        endpoint_id = str(prefs.get("image_endpoint_id") or "").strip()
        if endpoint_id:
            return endpoint_id
    except Exception:
        pass
    try:
        from src.settings import load_settings

        return str((load_settings() or {}).get("image_endpoint_id") or "").strip()
    except Exception:
        return ""


def resolve_configured_image_endpoint(owner: Optional[str]) -> Optional[ConfiguredImageEndpoint]:
    """Return the selected endpoint only when it is enabled and image-capable.

    A normal endpoint may be selected for a cloud image model (GPT Image/DALL-E)
    because those providers often expose chat and image models together.  Local
    arbitrary model IDs are only accepted from endpoints explicitly marked
    ``model_type='image'``.
    """

    try:
        from src.settings import get_user_setting

        enabled = bool(get_user_setting("image_gen_enabled", owner or "", False))
        model = str(get_user_setting("image_model", owner or "", "") or "").strip()
    except Exception:
        return None
    endpoint_id = _image_endpoint_id_for_owner(owner)
    if not enabled or not endpoint_id or not model:
        return None

    try:
        from src.auth_helpers import owner_filter
        from src.database import ModelEndpoint, SessionLocal
        from src.endpoint_resolver import build_headers, resolve_endpoint_runtime

        db = SessionLocal()
        try:
            query = db.query(ModelEndpoint).filter(
                ModelEndpoint.id == endpoint_id,
                ModelEndpoint.is_enabled == True,  # noqa: E712
            )
            if owner:
                query = owner_filter(query, ModelEndpoint, owner)
            endpoint = query.first()
            if endpoint is None:
                return None

            is_image_endpoint = (getattr(endpoint, "model_type", "") or "llm").lower() == "image"
            is_known_cloud_image = model.lower().startswith(_IMAGE_MODEL_PREFIXES)
            if not is_image_endpoint and not is_known_cloud_image:
                return None

            # A populated cache is authoritative enough to reject an accidental
            # stale setting. Empty caches remain "unknown" so a just-starting
            # local service can still become ready without forcing a settings edit.
            raw_models = getattr(endpoint, "cached_models", None)
            if raw_models:
                try:
                    known_models = json.loads(raw_models) if isinstance(raw_models, str) else raw_models
                except Exception:
                    known_models = []
                if isinstance(known_models, list) and known_models:
                    if model not in {str(item).strip() for item in known_models}:
                        return None

            base_url, api_key = resolve_endpoint_runtime(endpoint, owner=owner)
            return ConfiguredImageEndpoint(
                endpoint_id=str(endpoint.id),
                model=model,
                base_url=base_url,
                headers=build_headers(api_key, base_url),
            )
        finally:
            db.close()
    except Exception:
        return None


def install_image_generation_defaults(ai_interaction_module) -> None:
    """Install a one-time, request-local default-image-endpoint wrapper.

    The normal resolver stays untouched outside image calls.  During a configured
    image call the wrapper swaps only the resolver result for the selected model,
    so the original ``do_generate_image`` code continues to handle provider
    payloads, timeouts, response formats, Gallery storage, and URL safety.
    """

    if getattr(ai_interaction_module, "_local_image_defaults_installed", False):
        return

    original_generate = ai_interaction_module.do_generate_image
    original_resolve_model = ai_interaction_module._resolve_model

    def resolve_model_with_image_default(spec: str, owner: Optional[str] = None):
        selected = _active_image_endpoint.get()
        requested = str(spec or "").split("@", 1)[0].strip()
        if selected and requested.lower() == selected.model.lower():
            from src.endpoint_resolver import build_chat_url

            return build_chat_url(selected.base_url), selected.model, selected.headers
        return original_resolve_model(spec, owner=owner)

    async def generate_with_image_default(content: str, session_id: Optional[str] = None, owner: Optional[str] = None):
        selected = resolve_configured_image_endpoint(owner)
        token = _active_image_endpoint.set(selected)
        try:
            # Deliberately overwrite an LLM-provided model. A selected Image
            # Default is an explicit user preference, not merely a fallback.
            if selected:
                content = _replace_tool_model(content, selected.model)
            return await original_generate(content, session_id, owner=owner)
        finally:
            _active_image_endpoint.reset(token)

    ai_interaction_module._resolve_model = resolve_model_with_image_default
    ai_interaction_module.do_generate_image = generate_with_image_default
    ai_interaction_module._local_image_defaults_installed = True
