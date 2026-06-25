from src.anchor_video_settings import VIDEO_DEFAULTS, VideoSettingsError, resolve_generation_request, validate_video_config


def test_fixed_stability_profile_is_valid():
    config = validate_video_config(VIDEO_DEFAULTS, resolve_seed=False)
    assert config["video_width"] == 512
    assert config["video_height"] == 512
    assert config["video_num_frames"] == 241
    assert config["video_fps"] == 24


def test_generation_resolves_random_seed_and_preserves_intent_only_for_planning():
    config = resolve_generation_request(None, {"prompt": "A cat watches rain from a quiet window"})
    assert config["source_intent"] == "A cat watches rain from a quiet window"
    assert 0 <= config["video_seed"] <= 2_147_483_647


def test_non_fixed_size_is_rejected():
    try:
        validate_video_config({**VIDEO_DEFAULTS, "video_width": 768}, resolve_seed=False)
    except VideoSettingsError as exc:
        assert "512x512" in str(exc)
    else:
        raise AssertionError("non-fixed output size was accepted")


def test_invalid_seed_is_rejected():
    try:
        validate_video_config({**VIDEO_DEFAULTS, "video_seed": -5}, resolve_seed=False)
    except VideoSettingsError as exc:
        assert "Seed" in str(exc)
    else:
        raise AssertionError("invalid seed was accepted")
