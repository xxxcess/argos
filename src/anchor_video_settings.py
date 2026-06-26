"""User-scoped fixed-profile settings for local depth-parallax video.

The renderer intentionally has a small, stable profile so it stays practical on
Apple-Silicon machines with 8 GB unified memory.  It creates depth-aware camera
motion from a generated image anchor; it is not a heavyweight generative video
model.
"""
from __future__ import annotations

import secrets
from typing import Any


class VideoSettingsError(ValueError):
    """Raised when a video request does not match the supported local profile."""


VIDEO_DEFAULTS: dict[str, Any] = {
    "video_gen_enabled": False,
    "video_provider": "depth_parallax",
    "video_width": 512,
    "video_height": 512,
    "video_duration_seconds": 10,
    "video_fps": 24,
    "video_source_fps": 6,
    "video_seed": None,
}

_FIXED_KEYS = {
    "video_provider": "depth_parallax",
    "video_width": 512,
    "video_height": 512,
    "video_duration_seconds": 10,
    "video_fps": 24,
    "video_source_fps": 6,
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


def get_video_defaults(owner: str | None) -> dict[str, Any]:
    values = dict(VIDEO_DEFAULTS)
    values.update({key: value for key, value in _prefs(owner).items() if key in VIDEO_DEFAULTS})
    # The public UI has no unsafe performance knobs; always normalize old prefs.
    values.update(_FIXED_KEYS)
    values["video_gen_enabled"] = bool(values.get("video_gen_enabled"))
    seed = values.get("video_seed")
    values["video_seed"] = _validate_seed(seed) if seed not in (None, "") else None
    return values


def _validate_seed(seed: Any) -> int:
    try:
        value = int(seed)
    except (TypeError, ValueError) as exc:
        raise VideoSettingsError("Video seed must be a whole number.") from exc
    if value < 0 or value > 2_147_483_647:
        raise VideoSettingsError("Video seed must be between 0 and 2147483647.")
    return value


def validate_video_config(values: dict[str, Any], *, resolve_seed: bool) -> dict[str, Any]:
    values = {**VIDEO_DEFAULTS, **(values or {})}
    for key, expected in _FIXED_KEYS.items():
        actual = values.get(key, expected)
        if actual != expected:
            if key in {"video_width", "video_height"}:
                raise VideoSettingsError("Local depth-parallax video uses a fixed 512x512 profile.")
            if key == "video_duration_seconds":
                raise VideoSettingsError("Local depth-parallax video uses a fixed 10-second profile.")
            if key == "video_fps":
                raise VideoSettingsError("Local depth-parallax video uses a fixed 24 FPS profile.")
            raise VideoSettingsError("Unsupported local video profile.")
        values[key] = expected
    seed = values.get("video_seed")
    values["video_seed"] = (secrets.randbelow(2_147_483_648) if resolve_seed and seed in (None, "") else (None if seed in (None, "") else _validate_seed(seed)))
    values["video_gen_enabled"] = bool(values.get("video_gen_enabled"))
    return values


def save_video_defaults(owner: str | None, updates: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(updates, dict):
        raise VideoSettingsError("Video defaults must be an object.")
    allowed = {"video_gen_enabled", "video_seed"}
    unknown = set(updates) - allowed
    if unknown:
        raise VideoSettingsError("Only video enablement and seed can be changed for the local profile.")
    current = get_video_defaults(owner)
    if "video_gen_enabled" in updates:
        current["video_gen_enabled"] = bool(updates["video_gen_enabled"])
    if "video_seed" in updates:
        current["video_seed"] = None if updates["video_seed"] in (None, "") else _validate_seed(updates["video_seed"])
    current = validate_video_config(current, resolve_seed=False)
    prefs = _prefs(owner)
    prefs.update({key: current[key] for key in VIDEO_DEFAULTS})
    _save_prefs(owner, prefs)
    return current


def resolve_generation_request(owner: str | None, body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise VideoSettingsError("Video generation request must be an object.")
    source_intent = str(body.get("prompt") or body.get("intent") or "").strip()
    if not source_intent:
        raise VideoSettingsError("Describe the video you want to create.")
    if len(source_intent) > 4000:
        raise VideoSettingsError("Video intent must be 4000 characters or fewer.")
    values = get_video_defaults(owner)
    if "video_seed" in body and body["video_seed"] not in (None, ""):
        values["video_seed"] = _validate_seed(body["video_seed"])
    values = validate_video_config(values, resolve_seed=True)
    values["source_intent"] = source_intent
    values["session_id"] = str(body.get("session_id") or "").strip() or None
    return values
