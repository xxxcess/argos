from src.video_settings import (
    VideoSettingsError,
    VIDEO_DEFAULTS,
    resolve_generation_request,
    validate_video_config,
)


def test_default_video_configuration_is_valid():
    config = validate_video_config(VIDEO_DEFAULTS)
    assert config["video_model"] == "Lightricks/LTX-2"
    assert config["video_pipeline"] == "distilled"
    assert config["video_num_frames"] == 33


def test_ltx_frame_counts_must_follow_one_plus_eight_k():
    try:
        validate_video_config({**VIDEO_DEFAULTS, "video_num_frames": 34})
    except VideoSettingsError as exc:
        assert "Frame count" in str(exc)
    else:
        raise AssertionError("invalid LTX frame count was accepted")


def test_resolution_is_limited_to_supported_presets():
    try:
        validate_video_config({**VIDEO_DEFAULTS, "video_width": 640, "video_height": 512})
    except VideoSettingsError as exc:
        assert "resolution" in str(exc).lower()
    else:
        raise AssertionError("unsupported resolution was accepted")


def test_generation_resolves_a_reproducible_random_seed():
    config = resolve_generation_request(None, {"prompt": "A quiet cinematic lake at sunrise"})
    assert config["video_seed"] is not None
    assert 0 <= config["video_seed"] <= 2_147_483_647


def test_unknown_generation_overrides_are_ignored_not_forwarded_to_cli():
    config = resolve_generation_request(None, {"prompt": "A paper boat", "output_path": "/tmp/nope.mp4"})
    assert "output_path" not in config
