"""Validated defaults and request normalization for local video generation.

This module intentionally keeps video settings separate from generic ModelEndpoint
configuration: mlx-video is a local Apple Silicon runtime, not a remote model API.
"""
from __future__ import annotations

import secrets
from copy import deepcopy
from typing import Any, Mapping


VIDEO_DEFAULTS: dict[str, Any] = {
    "video_gen_enabled": False,
    "video_provider": "mlx_video",
    "video_model": "Lightricks/LTX-2",
    "video_pipeline": "distilled",
    "video_width": 512,
    "video_height": 512,
    "video_num_frames": 33,
    "video_fps": 24,
    "video_seed": None,
    "video_tiling": "auto",
    "video_enhance_prompt": False,
}

VIDEO_SETTING_KEYS = frozenset(VIDEO_DEFAULTS)
RESOLUTION_PRESETS = ((512, 512), (768, 512), (512, 768))
FRAME_PRESETS = (33, 49, 97)
FPS_PRESETS = (24,)
PIPELINES = ("distilled",)
TILING_MODES = ("auto", "enabled", "disabled")
MAX_PROMPT_CHARS = 4_000
MAX_SESSION_ID_CHARS = 128


class VideoSettingsError(ValueError):
    """A user-correctable video configuration error."""


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _global_video_settings() -> dict[str, Any]:
    """Read saved app settings while allowing video defaults on older installs.

    Video settings deliberately have an independent schema for this feature so
    the runtime works before all deployments have rewritten data/settings.json.
    """
    try:
        from src.settings import load_settings
        saved = load_settings() or {}
    except Exception:
        saved = {}
    result = deepcopy(VIDEO_DEFAULTS)
    for key in VIDEO_SETTING_KEYS:
        if key in saved and saved[key] is not None:
            result[key] = saved[key]
    return result


def get_video_defaults(owner: str | None = None) -> dict[str, Any]:
    """Return global defaults overlaid by an authenticated user's preferences."""
    result = _global_video_settings()
    if owner:
        try:
            from routes.prefs_routes import _load_for_user
            prefs = _load_for_user(owner) or {}
            for key in VIDEO_SETTING_KEYS:
                if key in prefs and prefs[key] is not None:
                    result[key] = prefs[key]
        except Exception:
            pass
    return validate_video_config(result, allow_random_seed=True)


def validate_video_config(
    values: Mapping[str, Any], *, allow_random_seed: bool = True
) -> dict[str, Any]:
    """Validate and normalize a video settings/request mapping.

    LTX's distilled two-stage mode requires width/height divisible by 64 and
    frame counts of ``1 + 8*k``. V1 intentionally exposes only known-good
    presets, which avoids accepting requests that look valid but fail after a
    model has loaded.
    """
    base = deepcopy(VIDEO_DEFAULTS)
    for key in VIDEO_SETTING_KEYS:
        if key in values and values[key] is not None:
            base[key] = values[key]

    provider = str(base["video_provider"] or "").strip()
    if provider != "mlx_video":
        raise VideoSettingsError("Only the local mlx-video provider is supported.")

    model = str(base["video_model"] or "").strip()
    if model != "Lightricks/LTX-2":
        raise VideoSettingsError("Only Lightricks/LTX-2 is supported in this release.")

    pipeline = str(base["video_pipeline"] or "").strip().lower()
    if pipeline not in PIPELINES:
        raise VideoSettingsError("Unsupported video pipeline.")

    width = _as_int(base["video_width"], 512)
    height = _as_int(base["video_height"], 512)
    if (width, height) not in RESOLUTION_PRESETS:
        raise VideoSettingsError("Choose one of the supported video resolution presets.")
    if width % 64 or height % 64:
        raise VideoSettingsError("LTX-2 distilled dimensions must be divisible by 64.")

    frames = _as_int(base["video_num_frames"], 33)
    if frames not in FRAME_PRESETS or frames < 1 or (frames - 1) % 8:
        raise VideoSettingsError("Frame count must be a supported value of 1 + 8*k.")

    fps = _as_int(base["video_fps"], 24)
    if fps not in FPS_PRESETS:
        raise VideoSettingsError("Unsupported frames-per-second value.")

    raw_seed = base.get("video_seed")
    if raw_seed in (None, "", "random"):
        seed = None if allow_random_seed else secrets.randbelow(2_147_483_647)
    else:
        seed = _as_int(raw_seed, -1)
        if not 0 <= seed <= 2_147_483_647:
            raise VideoSettingsError("Seed must be between 0 and 2147483647.")

    tiling = str(base.get("video_tiling") or "auto").strip().lower()
    if tiling not in TILING_MODES:
        raise VideoSettingsError("Unsupported tiling mode.")

    return {
        "video_gen_enabled": _as_bool(base.get("video_gen_enabled"), False),
        "video_provider": provider,
        "video_model": model,
        "video_pipeline": pipeline,
        "video_width": width,
        "video_height": height,
        "video_num_frames": frames,
        "video_fps": fps,
        "video_seed": seed,
        "video_tiling": tiling,
        "video_enhance_prompt": _as_bool(base.get("video_enhance_prompt"), False),
    }


def resolve_generation_request(owner: str | None, body: Mapping[str, Any]) -> dict[str, Any]:
    """Merge a strictly-whitelisted request over the caller's saved defaults."""
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise VideoSettingsError("A video prompt is required.")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise VideoSettingsError(f"Prompt must be at most {MAX_PROMPT_CHARS} characters.")

    defaults = get_video_defaults(owner)
    allowed = {
        "video_pipeline", "video_width", "video_height", "video_num_frames",
        "video_fps", "video_seed", "video_tiling", "video_enhance_prompt",
    }
    overrides = {key: body[key] for key in allowed if key in body}
    config = validate_video_config({**defaults, **overrides}, allow_random_seed=False)
    config["prompt"] = prompt
    session_id = body.get("session_id")
    if session_id is not None:
        session_id = str(session_id).strip()
        if len(session_id) > MAX_SESSION_ID_CHARS:
            raise VideoSettingsError("Session id is too long.")
    config["session_id"] = session_id or None
    return config


def validate_defaults_patch(patch: Mapping[str, Any]) -> dict[str, Any]:
    """Return a normalized partial patch suitable for persisted defaults."""
    unknown = set(patch) - VIDEO_SETTING_KEYS
    if unknown:
        raise VideoSettingsError("Unsupported video setting: " + ", ".join(sorted(unknown)))
    merged = {**VIDEO_DEFAULTS, **dict(patch)}
    normalized = validate_video_config(merged, allow_random_seed=True)
    return {key: normalized[key] for key in patch}


def save_video_defaults(owner: str | None, patch: Mapping[str, Any], *, global_scope: bool = False) -> dict[str, Any]:
    """Persist validated defaults globally or as an owner-scoped preference."""
    clean = validate_defaults_patch(patch)
    if global_scope:
        from src.settings import load_settings, save_settings
        settings = load_settings()
        settings.update(clean)
        save_settings(settings)
        return get_video_defaults(None)

    from routes.prefs_routes import _load_for_user, _save_for_user
    prefs = _load_for_user(owner) or {}
    prefs.update(clean)
    _save_for_user(owner, prefs)
    return get_video_defaults(owner)
