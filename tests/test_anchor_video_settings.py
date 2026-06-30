from src.anchor_video_settings import (
    REMOTE_LTX_CONSENT_VERSION,
    VIDEO_DEFAULTS,
    VIDEO_PROVIDER_DEPTH,
    VIDEO_PROVIDER_REMOTE_LTX,
    VideoSettingsError,
    get_generation_defaults,
    get_video_defaults,
    resolve_generation_request,
    save_video_defaults,
    validate_video_config,
)
from services.anchor_video_generation import _derived_prompt
from services.depth_parallax_renderer import camera_plan


def test_fixed_low_memory_profile_is_valid():
    result = validate_video_config(VIDEO_DEFAULTS, resolve_seed=False)
    assert result["video_width"] == 512
    assert result["video_height"] == 512
    assert result["video_duration_seconds"] == 8
    assert result["video_fps"] == 24
    assert result["video_source_fps"] == 6


def test_rejects_unsupported_duration():
    values = dict(VIDEO_DEFAULTS, video_duration_seconds=15)
    try:
        validate_video_config(values, resolve_seed=False)
    except VideoSettingsError as exc:
        assert "eight-second" in str(exc)
    else:
        raise AssertionError("expected fixed duration validation failure")


def test_remote_provider_requires_consent():
    values = dict(VIDEO_DEFAULTS, video_provider=VIDEO_PROVIDER_REMOTE_LTX)
    try:
        validate_video_config(values, resolve_seed=False)
    except VideoSettingsError as exc:
        assert "requires public-provider acknowledgement" in str(exc)
    else:
        raise AssertionError("expected remote consent validation failure")


def test_remote_profile_is_about_eight_seconds_with_consent():
    values = dict(
        VIDEO_DEFAULTS,
        video_provider=VIDEO_PROVIDER_REMOTE_LTX,
        video_remote_ltx_consent_version=REMOTE_LTX_CONSENT_VERSION,
    )
    result = validate_video_config(values, resolve_seed=False)
    assert result["video_provider"] == VIDEO_PROVIDER_REMOTE_LTX
    assert result["video_duration_seconds"] == 8.0
    assert result["video_fps"] == 30
    assert result["video_source_fps"] is None


def test_remote_provider_selection_persists_after_consent(monkeypatch):
    prefs = {}
    monkeypatch.setattr("src.anchor_video_settings._prefs", lambda owner: dict(prefs))
    monkeypatch.setattr("src.anchor_video_settings._save_prefs", lambda owner, values: prefs.update(values))

    consented = save_video_defaults("owner", {"video_remote_ltx_consent_version": REMOTE_LTX_CONSENT_VERSION})
    assert consented["video_remote_ltx_consent_version"] == REMOTE_LTX_CONSENT_VERSION

    saved = save_video_defaults("owner", {"video_provider": VIDEO_PROVIDER_REMOTE_LTX})
    assert saved["video_provider"] == VIDEO_PROVIDER_REMOTE_LTX

    reloaded = get_video_defaults("owner")
    assert reloaded["video_provider"] == VIDEO_PROVIDER_REMOTE_LTX
    assert reloaded["video_duration_seconds"] == 8.0
    assert reloaded["video_fps"] == 30


def test_settings_hides_remote_pref_without_consent_and_generation_rejects(monkeypatch):
    prefs = {"video_provider": VIDEO_PROVIDER_REMOTE_LTX}
    monkeypatch.setattr("src.anchor_video_settings._prefs", lambda owner: dict(prefs))

    settings = get_video_defaults("owner")
    assert settings["video_provider"] == VIDEO_PROVIDER_DEPTH

    generation = get_generation_defaults("owner")
    assert generation["video_provider"] == VIDEO_PROVIDER_REMOTE_LTX
    try:
        resolve_generation_request("owner", {"prompt": "A cinematic portrait"})
    except VideoSettingsError as exc:
        assert "requires public-provider acknowledgement" in str(exc)
    else:
        raise AssertionError("expected remote consent validation failure")


def test_camera_plan_uses_motion_prompt_not_raw_intent():
    plan = camera_plan("A gentle pan left through the scene.", 42)
    assert plan["direction"] == "left"
    assert camera_plan("A quiet scene.", 42) == camera_plan("A quiet scene.", 42)


def test_generation_request_preserves_only_approved_controls(monkeypatch):
    monkeypatch.setattr("src.anchor_video_settings.get_video_defaults", lambda owner: dict(VIDEO_DEFAULTS, video_gen_enabled=True))
    result = resolve_generation_request("owner", {"prompt": "A cinematic portrait", "output_path": "/tmp/no.mp4"})
    assert result["source_intent"] == "A cinematic portrait"
    assert "output_path" not in result
    assert isinstance(result["video_seed"], int)


def test_generation_request_uses_saved_provider_not_request_override(monkeypatch):
    monkeypatch.setattr("src.anchor_video_settings.get_video_defaults", lambda owner: dict(VIDEO_DEFAULTS, video_gen_enabled=True))
    result = resolve_generation_request("owner", {"prompt": "A cinematic portrait", "video_provider": VIDEO_PROVIDER_REMOTE_LTX})
    assert result["video_provider"] == "depth_parallax"


def test_remote_motion_prompt_rejects_raw_user_intent():
    raw = (
        "A dragon beats its wings and flies over a mountain ridge while low clouds move "
        "around the castle and the camera tracks forward."
    )
    try:
        _derived_prompt(raw, raw, "remote_motion")
    except RuntimeError as exc:
        assert "repeated the raw intent" in str(exc)
    else:
        raise AssertionError("expected raw intent rejection")
