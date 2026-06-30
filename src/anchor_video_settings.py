"""User-scoped settings for anchor-first video generation.

Argos always creates a private image anchor through the configured Image
Default. The selected render provider then turns that anchor into a short video.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Any


class VideoSettingsError(ValueError):
    """Raised when a video request does not match the supported profile."""


VIDEO_PROVIDER_DEPTH = "depth_parallax"
VIDEO_PROVIDER_REMOTE_LTX = "remote_ltx"
VIDEO_PROVIDERS = {VIDEO_PROVIDER_DEPTH, VIDEO_PROVIDER_REMOTE_LTX}
REMOTE_LTX_CONSENT_VERSION = "public-ltx-v1"


VIDEO_DEFAULTS: dict[str, Any] = {
    "video_gen_enabled": False,
    "video_provider": VIDEO_PROVIDER_DEPTH,
    "video_width": 512,
    "video_height": 512,
    "video_duration_seconds": 8,
    "video_fps": 24,
    "video_source_fps": 6,
    "video_seed": None,
    "video_remote_ltx_consent_version": None,
    "video_remote_ltx_consent_at": None,
}

_LOCAL_FIXED_KEYS = {
    "video_provider": VIDEO_PROVIDER_DEPTH,
    "video_width": 512,
    "video_height": 512,
    "video_duration_seconds": 8,
    "video_fps": 24,
    "video_source_fps": 6,
}

_REMOTE_FIXED_KEYS = {
    "video_provider": VIDEO_PROVIDER_REMOTE_LTX,
    "video_width": 512,
    "video_height": 512,
    "video_duration_seconds": 8.0,
    "video_fps": 30,
    "video_source_fps": None,
}


def _prefs(owner: str | None) -> dict[str, Any]:
    try:
        from routes.prefs_routes import _load_for_user
        return dict(_load_for_user(owner) or {})
    except Exception:
        return {}


def _save_prefs(owner: str | None, values: dict[str, Any]) -> None:
    from routes.prefs_routes import _save_for_user
    _save_for_user(owner, values)


def _stored_video_defaults(owner: str | None) -> dict[str, Any]:
    values = dict(VIDEO_DEFAULTS)
    values.update({key: value for key, value in _prefs(owner).items() if key in VIDEO_DEFAULTS})
    provider = str(values.get("video_provider") or VIDEO_PROVIDER_DEPTH)
    if provider not in VIDEO_PROVIDERS:
        provider = VIDEO_PROVIDER_DEPTH
    values.update(_REMOTE_FIXED_KEYS if provider == VIDEO_PROVIDER_REMOTE_LTX else _LOCAL_FIXED_KEYS)
    values["video_gen_enabled"] = bool(values.get("video_gen_enabled"))
    seed = values.get("video_seed")
    values["video_seed"] = _validate_seed(seed) if seed not in (None, "") else None
    values["video_remote_ltx_consent_version"] = (
        REMOTE_LTX_CONSENT_VERSION
        if values.get("video_remote_ltx_consent_version") == REMOTE_LTX_CONSENT_VERSION
        else None
    )
    consent_at = str(values.get("video_remote_ltx_consent_at") or "").strip()
    values["video_remote_ltx_consent_at"] = consent_at or None
    return values


def get_video_defaults(owner: str | None) -> dict[str, Any]:
    values = _stored_video_defaults(owner)
    if values["video_provider"] == VIDEO_PROVIDER_REMOTE_LTX and not has_remote_ltx_consent(values):
        values["video_provider"] = VIDEO_PROVIDER_DEPTH
        values.update(_LOCAL_FIXED_KEYS)
    return values


def get_generation_defaults(owner: str | None) -> dict[str, Any]:
    return _stored_video_defaults(owner)


def _validate_seed(seed: Any) -> int:
    try:
        value = int(seed)
    except (TypeError, ValueError) as exc:
        raise VideoSettingsError("Video seed must be a whole number.") from exc
    if value < 0 or value > 2_147_483_647:
        raise VideoSettingsError("Video seed must be between 0 and 2147483647.")
    return value


def validate_video_config(values: dict[str, Any], *, resolve_seed: bool) -> dict[str, Any]:
    raw_values = dict(values or {})
    values = {**VIDEO_DEFAULTS, **raw_values}
    provider = str(values.get("video_provider") or VIDEO_PROVIDER_DEPTH)
    if provider not in VIDEO_PROVIDERS:
        raise VideoSettingsError("Unsupported video render provider.")
    if provider == VIDEO_PROVIDER_REMOTE_LTX and not has_remote_ltx_consent(values):
        raise VideoSettingsError("Remote LTX Video requires public-provider acknowledgement before use.")
    fixed = _REMOTE_FIXED_KEYS if provider == VIDEO_PROVIDER_REMOTE_LTX else _LOCAL_FIXED_KEYS
    for key, expected in fixed.items():
        actual = raw_values.get(key, expected)
        if actual != expected:
            if provider == VIDEO_PROVIDER_REMOTE_LTX and key in VIDEO_DEFAULTS and actual == VIDEO_DEFAULTS[key]:
                values[key] = expected
                continue
            if key in {"video_width", "video_height"}:
                raise VideoSettingsError("Video generation uses a fixed 512x512 anchor profile.")
            if key == "video_duration_seconds":
                raise VideoSettingsError("Video generation uses a fixed eight-second profile.")
            if key == "video_fps":
                raise VideoSettingsError("The selected video provider uses a fixed output FPS profile.")
            raise VideoSettingsError("Unsupported video profile.")
        values[key] = expected
    seed = values.get("video_seed")
    values["video_seed"] = (secrets.randbelow(2_147_483_648) if resolve_seed and seed in (None, "") else (None if seed in (None, "") else _validate_seed(seed)))
    values["video_gen_enabled"] = bool(values.get("video_gen_enabled"))
    values["video_remote_ltx_consent_version"] = (
        REMOTE_LTX_CONSENT_VERSION
        if values.get("video_remote_ltx_consent_version") == REMOTE_LTX_CONSENT_VERSION
        else None
    )
    values["video_remote_ltx_consent_at"] = str(values.get("video_remote_ltx_consent_at") or "").strip() or None
    return values


def save_video_defaults(owner: str | None, updates: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(updates, dict):
        raise VideoSettingsError("Video defaults must be an object.")
    allowed = {
        "video_gen_enabled",
        "video_seed",
        "video_provider",
        "video_remote_ltx_consent_version",
    }
    unknown = set(updates) - allowed
    if unknown:
        raise VideoSettingsError("Unsupported video defaults update.")
    current = _stored_video_defaults(owner)
    if updates.get("video_remote_ltx_consent_version") == REMOTE_LTX_CONSENT_VERSION:
        current["video_remote_ltx_consent_version"] = REMOTE_LTX_CONSENT_VERSION
        current["video_remote_ltx_consent_at"] = datetime.now(timezone.utc).isoformat()
    if "video_provider" in updates:
        provider = str(updates["video_provider"] or "").strip()
        if provider not in VIDEO_PROVIDERS:
            raise VideoSettingsError("Unsupported video render provider.")
        if provider == VIDEO_PROVIDER_REMOTE_LTX and not has_remote_ltx_consent(current):
            raise VideoSettingsError("Remote LTX Video requires public-provider acknowledgement before use.")
        current["video_provider"] = provider
    if "video_gen_enabled" in updates:
        current["video_gen_enabled"] = bool(updates["video_gen_enabled"])
    if "video_seed" in updates:
        current["video_seed"] = None if updates["video_seed"] in (None, "") else _validate_seed(updates["video_seed"])
    current = validate_video_config(current, resolve_seed=False)
    prefs = _prefs(owner)
    prefs.update({key: current[key] for key in VIDEO_DEFAULTS})
    _save_prefs(owner, prefs)
    return current


def has_remote_ltx_consent(values: dict[str, Any] | None) -> bool:
    values = values or {}
    return values.get("video_remote_ltx_consent_version") == REMOTE_LTX_CONSENT_VERSION


def resolve_generation_request(owner: str | None, body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise VideoSettingsError("Video generation request must be an object.")
    source_intent = str(body.get("prompt") or body.get("intent") or "").strip()
    if not source_intent:
        raise VideoSettingsError("Describe the video you want to create.")
    if len(source_intent) > 4000:
        raise VideoSettingsError("Video intent must be 4000 characters or fewer.")
    values = get_generation_defaults(owner)
    if "video_seed" in body and body["video_seed"] not in (None, ""):
        values["video_seed"] = _validate_seed(body["video_seed"])
    values = validate_video_config(values, resolve_seed=True)
    values["source_intent"] = source_intent
    values["session_id"] = str(body.get("session_id") or "").strip() or None
    return values
