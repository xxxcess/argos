"""Settings helpers for the fixed anchor-first local video workflow."""
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
    # 1 + 8*30; 241 / 24 FPS is approximately ten seconds.
    "video_num_frames": 241,
    "video_fps": 24,
    "video_seed": None,
}
VIDEO_SETTING_KEYS = frozenset(VIDEO_DEFAULTS)
MAX_INTENT_CHARS = 4000
MAX_SESSION_ID_CHARS = 128


class VideoSettingsError(ValueError):
    pass


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


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


def _global_values() -> dict[str, Any]:
    try:
        from src.settings import load_settings
        saved = load_settings() or {}
    except Exception:
        saved = {}
    values = deepcopy(VIDEO_DEFAULTS)
    for key in VIDEO_SETTING_KEYS:
        if key in saved and saved[key] is not None:
            values[key] = saved[key]
    return values


def validate_video_config(values: Mapping[str, Any], *, resolve_seed: bool) -> dict[str, Any]:
    base = deepcopy(VIDEO_DEFAULTS)
    for key in VIDEO_SETTING_KEYS:
        if key in values and values[key] is not None:
            base[key] = values[key]

    if str(base["video_provider"] or "").strip() != "mlx_video":
        raise VideoSettingsError("Only the local mlx-video provider is supported.")
    if str(base["video_model"] or "").strip() != "Lightricks/LTX-2":
        raise VideoSettingsError("Only Lightricks/LTX-2 is supported in this release.")
    if str(base["video_pipeline"] or "").strip().lower() != "distilled":
        raise VideoSettingsError("Only the LTX-2 distilled pipeline is supported.")
    if (_as_int(base["video_width"], 512), _as_int(base["video_height"], 512)) != (512, 512):
        raise VideoSettingsError("Video generation is fixed at 512x512 for stability.")
    frames = _as_int(base["video_num_frames"], 241)
    if frames != 241 or (frames - 1) % 8:
        raise VideoSettingsError("Video generation is fixed at 241 frames for a ten-second clip.")
    if _as_int(base["video_fps"], 24) != 24:
        raise VideoSettingsError("Video generation is fixed at 24 FPS.")

    raw_seed = base.get("video_seed")
    if raw_seed in (None, "", "random"):
        seed = secrets.randbelow(2_147_483_647) if resolve_seed else None
    else:
        seed = _as_int(raw_seed, -1)
        if not 0 <= seed <= 2_147_483_647:
            raise VideoSettingsError("Seed must be between 0 and 2147483647.")

    return {
        "video_gen_enabled": _as_bool(base.get("video_gen_enabled"), False),
        "video_provider": "mlx_video",
        "video_model": "Lightricks/LTX-2",
        "video_pipeline": "distilled",
        "video_width": 512,
        "video_height": 512,
        "video_num_frames": 241,
        "video_fps": 24,
        "video_seed": seed,
    }


def get_video_defaults(owner: str | None = None) -> dict[str, Any]:
    values = _global_values()
    if owner:
        try:
            from routes.prefs_routes import _load_for_user
            prefs = _load_for_user(owner) or {}
            for key in VIDEO_SETTING_KEYS:
                if key in prefs and prefs[key] is not None:
                    values[key] = prefs[key]
        except Exception:
            pass
    return validate_video_config(values, resolve_seed=False)


def resolve_generation_request(owner: str | None, body: Mapping[str, Any]) -> dict[str, Any]:
    intent = str(body.get("prompt") or body.get("intent") or "").strip()
    if not intent:
        raise VideoSettingsError("A video intent is required.")
    if len(intent) > MAX_INTENT_CHARS:
        raise VideoSettingsError("Video intent is too long.")
    config = get_video_defaults(owner)
    if "video_seed" in body:
        config["video_seed"] = body["video_seed"]
    config = validate_video_config(config, resolve_seed=True)
    session_id = str(body.get("session_id") or "").strip() or None
    if session_id and len(session_id) > MAX_SESSION_ID_CHARS:
        raise VideoSettingsError("Session id is too long.")
    config["source_intent"] = intent
    config["session_id"] = session_id
    return config


def save_video_defaults(owner: str | None, patch: Mapping[str, Any], *, global_scope: bool = False) -> dict[str, Any]:
    unknown = set(patch) - VIDEO_SETTING_KEYS
    if unknown:
        raise VideoSettingsError("Unsupported video setting: " + ", ".join(sorted(unknown)))
    normalized = validate_video_config({**VIDEO_DEFAULTS, **dict(patch)}, resolve_seed=False)
    clean = {key: normalized[key] for key in patch}
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
