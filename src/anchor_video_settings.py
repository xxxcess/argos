"""Settings helpers for anchor-first local video generation."""

VIDEO_DEFAULTS = {
    "video_gen_enabled": False,
    "video_provider": "mlx_video",
    "video_model": "Lightricks/LTX-2",
    "video_pipeline": "distilled",
    "video_width": 512,
    "video_height": 512,
    "video_num_frames": 241,
    "video_fps": 24,
    "video_seed": None,
}
