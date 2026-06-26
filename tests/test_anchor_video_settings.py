from src.anchor_video_settings import VIDEO_DEFAULTS, VideoSettingsError, resolve_generation_request, validate_video_config
from services.depth_parallax_renderer import camera_plan


def test_fixed_low_memory_profile_is_valid():
    result = validate_video_config(VIDEO_DEFAULTS, resolve_seed=False)
    assert result["video_width"] == 512
    assert result["video_height"] == 512
    assert result["video_duration_seconds"] == 10
    assert result["video_fps"] == 24
    assert result["video_source_fps"] == 6


def test_rejects_unsupported_duration():
    values = dict(VIDEO_DEFAULTS, video_duration_seconds=15)
    try:
        validate_video_config(values, resolve_seed=False)
    except VideoSettingsError as exc:
        assert "10-second" in str(exc)
    else:
        raise AssertionError("expected fixed duration validation failure")


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
