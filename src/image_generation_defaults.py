"""Per-user image-generation endpoint selection.

A selected Image Default is an endpoint/model pair, not merely a model-name
hint.  The agent may still emit ``gpt-image-1`` or ``dall-e-3`` in a tool call,
but the user's selected endpoint must be used exactly.  This module validates
that selection and sends it directly to the selected endpoint's OpenAI-compatible
``/v1/images/generations`` route.

Keeping this logic here avoids making local Diffusers a mandatory Argos startup
dependency and preserves the existing cloud image path when no Image Default is
configured.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_IMAGE_MODEL_PREFIXES = ("gpt-image", "dall-e", "chatgpt-image")
_SIZE_RE = re.compile(r"^(\d{2,5})x(\d{2,5})$")


@dataclass(frozen=True)
class ConfiguredImageEndpoint:
    """Validated endpoint/model selected in AI Defaults for one request."""

    endpoint_id: str
    endpoint_name: str
    model: str
    base_url: str
    headers: dict[str, str]
    is_local: bool


def _replace_tool_model(content: str, model: str) -> str:
    """Replace the optional model line while preserving prompt/size/quality."""

    lines = str(content or "").split("\n")
    if not lines:
        lines = [""]
    while len(lines) < 2:
        lines.append("")
    lines[1] = model
    return "\n".join(lines)


def _cap_local_size(content: str, max_size: int = 512) -> str:
    """Clamp cloud-style default sizes to the conservative local profile cap."""

    lines = str(content or "").split("\n")
    if not lines:
        lines = [""]
    while len(lines) < 3:
        lines.append("")
    match = _SIZE_RE.match(lines[2].strip())
    if match:
        width, height = int(match.group(1)), int(match.group(2))
        if 64 <= width <= max_size and 64 <= height <= max_size and width % 8 == 0 and height % 8 == 0:
            return "\n".join(lines)
    lines[2] = f"{max_size}x{max_size}"
    return "\n".join(lines)


def _image_endpoint_id_for_owner(owner: Optional[str]) -> str:
    """Read the user-scoped endpoint id, with a global legacy fallback."""

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


def _is_loopback_or_local(base_url: str) -> bool:
    """Classify the local Diffusers service without trusting a display name."""

    from urllib.parse import urlparse

    try:
        host = (urlparse(base_url).hostname or "").lower()
    except Exception:
        return False
    return (
        host in {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
        or host.endswith(".local")
        or host.startswith("10.")
        or host.startswith("192.168.")
        or re.match(r"^172\.(1[6-9]|2\d|3[01])\.", host) is not None
    )


def resolve_configured_image_endpoint(owner: Optional[str]) -> Optional[ConfiguredImageEndpoint]:
    """Return an enabled image endpoint selected by the current user.

    Do not reject an explicit selected local model merely because cached model
    discovery is stale. A just-started local service can expose its model before
    Argos's endpoint cache refreshes, and the selected endpoint is the source of
    truth for that request.
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

            model_type = (getattr(endpoint, "model_type", "") or "llm").lower()
            is_known_cloud_image = model.lower().startswith(_IMAGE_MODEL_PREFIXES)
            if model_type != "image" and not is_known_cloud_image:
                return None

            base_url, api_key = resolve_endpoint_runtime(endpoint, owner=owner)
            is_local = model.lower().startswith("local-") or _is_loopback_or_local(base_url)
            return ConfiguredImageEndpoint(
                endpoint_id=str(endpoint.id),
                endpoint_name=str(getattr(endpoint, "name", "Image endpoint") or "Image endpoint"),
                model=model,
                base_url=base_url,
                headers=build_headers(api_key, base_url),
                is_local=is_local,
            )
        finally:
            db.close()
    except Exception:
        logger.debug("Could not resolve configured image endpoint", exc_info=True)
        return None


def _parse_image_request(content: str) -> tuple[str, str, str]:
    lines = str(content or "").strip().split("\n")
    prompt = lines[0].strip() if lines else ""
    size = lines[2].strip() if len(lines) > 2 and lines[2].strip() else "1024x1024"
    quality = lines[3].strip() if len(lines) > 3 and lines[3].strip() else "medium"
    return prompt, size, quality


def _images_url(base_url: str) -> str:
    """Build an OpenAI-style images route from a normalized endpoint base."""

    from src.endpoint_resolver import build_chat_url

    chat_url = build_chat_url(base_url).rstrip("/")
    suffix = "/chat/completions"
    if chat_url.endswith(suffix):
        return chat_url[: -len(suffix)] + "/images/generations"
    # Defensive fallback for an endpoint resolver with an unfamiliar chat path.
    base = base_url.rstrip("/")
    return (base if base.endswith("/v1") else base + "/v1") + "/images/generations"


def _save_gallery_image(filename: str, *, prompt: str, model: str, size: str, quality: str, session_id: Optional[str], owner: Optional[str]) -> str:
    try:
        from src.database import GalleryImage, SessionLocal

        image_id = str(uuid.uuid4())
        db = SessionLocal()
        try:
            db.add(GalleryImage(
                id=image_id,
                filename=filename,
                prompt=prompt,
                model=model,
                size=size,
                quality=quality,
                session_id=session_id,
                owner=owner,
            ))
            db.commit()
        finally:
            db.close()
        return image_id
    except Exception as exc:
        logger.warning("Failed to save local image Gallery record: %s", exc)
        return ""


async def generate_configured_image(
    content: str,
    selected: ConfiguredImageEndpoint,
    *,
    session_id: Optional[str] = None,
    owner: Optional[str] = None,
) -> dict[str, Any]:
    """Generate through the explicitly selected endpoint without model lookup."""

    import httpx

    from src.constants import GENERATED_IMAGES_DIR
    from src.url_safety import check_outbound_url

    prompt, size, quality = _parse_image_request(content)
    if not prompt:
        return {"error": "Image prompt is required (line 1)"}

    # A selected local profile takes precedence over LLM-provided cloud defaults.
    # The shipped SD Turbo service is intentionally capped at 512px for M2/8 GB.
    if selected.is_local:
        normalized = _cap_local_size(_replace_tool_model(content, selected.model))
        prompt, size, quality = _parse_image_request(normalized)

    model_id = selected.model
    model_lower = model_id.lower()
    is_gpt_image = "gpt-image" in model_lower
    is_dalle = "dall-e" in model_lower
    images_url = _images_url(selected.base_url)

    valid_gpt_sizes = {"1024x1024", "1024x1536", "1536x1024", "auto"}
    valid_dalle3_sizes = {"1024x1024", "1024x1792", "1792x1024"}
    if is_gpt_image and size not in valid_gpt_sizes:
        size = "1024x1024"
    elif is_dalle and size not in valid_dalle3_sizes:
        size = "1024x1024"

    payload: dict[str, Any] = {"model": model_id, "prompt": prompt, "n": 1, "size": size}
    if not is_dalle:
        payload["quality"] = quality if quality in {"low", "medium", "high", "auto"} else "medium"

    logger.info(
        "Configured image generation: endpoint=%s model=%s size=%s quality=%s prompt=%s",
        selected.endpoint_name,
        model_id,
        size,
        payload.get("quality", "medium"),
        prompt[:80],
    )

    try:
        timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(images_url, json=payload, headers=selected.headers)
        if response.status_code != 200:
            error_text = response.text[:500]
            try:
                error_data = response.json()
                value = error_data.get("error", error_text) if isinstance(error_data, dict) else error_text
                error_text = value.get("message", error_text) if isinstance(value, dict) else str(value)
            except Exception:
                pass
            return {
                "error": (
                    f"Image generation failed ({response.status_code}) from "
                    f"'{selected.endpoint_name}': {error_text}"
                )
            }

        data = response.json()
        images = data.get("data") or []
        if not images:
            return {"error": f"'{selected.endpoint_name}' returned no images"}
        image = images[0] if isinstance(images[0], dict) else {}

        image_url = ""
        image_id = ""
        if image.get("b64_json"):
            try:
                raw = base64.b64decode(image["b64_json"], validate=True)
            except Exception:
                return {"error": f"'{selected.endpoint_name}' returned invalid base64 image data"}
            image_dir = Path(GENERATED_IMAGES_DIR)
            image_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{uuid.uuid4().hex[:12]}.png"
            (image_dir / filename).write_bytes(raw)
            image_url = f"/api/generated-image/{filename}"
            image_id = _save_gallery_image(
                filename,
                prompt=prompt,
                model=model_id,
                size=size,
                quality=payload.get("quality", "medium"),
                session_id=session_id,
                owner=owner,
            )
        elif image.get("url"):
            result_url = str(image["url"])
            ok, reason = check_outbound_url(
                result_url,
                block_private=os.getenv("IMAGE_BLOCK_PRIVATE_IPS", "false").lower() == "true",
            )
            if not ok:
                return {"error": f"Image endpoint returned unsafe image URL: {reason}"}
            try:
                download = httpx.get(result_url, timeout=60)
                if download.status_code == 200:
                    image_dir = Path(GENERATED_IMAGES_DIR)
                    image_dir.mkdir(parents=True, exist_ok=True)
                    filename = f"{uuid.uuid4().hex[:12]}.png"
                    (image_dir / filename).write_bytes(download.content)
                    image_url = f"/api/generated-image/{filename}"
                    image_id = _save_gallery_image(
                        filename,
                        prompt=prompt,
                        model=model_id,
                        size=size,
                        quality=payload.get("quality", "medium"),
                        session_id=session_id,
                        owner=owner,
                    )
                else:
                    image_url = result_url
            except Exception as exc:
                logger.warning("Could not download image returned by %s: %s", selected.endpoint_name, exc)
                image_url = result_url
        else:
            return {"error": f"'{selected.endpoint_name}' returned an unexpected image format"}

        return {
            "results": f"Generated image for: {prompt[:100]}",
            "image_url": image_url,
            "image_id": image_id,
            "image_prompt": prompt,
            "image_model": model_id,
            "image_size": size,
            "image_quality": payload.get("quality", "medium"),
        }
    except httpx.TimeoutException:
        return {"error": f"Image generation timed out at '{selected.endpoint_name}' (300s)."}
    except Exception as exc:
        logger.exception("Configured image generation failed")
        return {"error": f"Image generation error from '{selected.endpoint_name}': {exc}"}


def install_image_generation_defaults(ai_interaction_module) -> None:
    """Install one direct endpoint wrapper around Argos's legacy image function."""

    if getattr(ai_interaction_module, "_local_image_defaults_installed", False):
        return

    original_generate = ai_interaction_module.do_generate_image

    async def generate_with_image_default(
        content: str,
        session_id: Optional[str] = None,
        owner: Optional[str] = None,
    ) -> dict[str, Any]:
        selected = resolve_configured_image_endpoint(owner)
        if selected is not None:
            return await generate_configured_image(
                content,
                selected,
                session_id=session_id,
                owner=owner,
            )
        return await original_generate(content, session_id, owner=owner)

    ai_interaction_module.do_generate_image = generate_with_image_default
    ai_interaction_module._local_image_defaults_installed = True
