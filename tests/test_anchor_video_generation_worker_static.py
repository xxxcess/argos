from __future__ import annotations

from pathlib import Path

from src.generated_images import GENERATED_IMAGE_RE


_REPO = Path(__file__).resolve().parent.parent
_SERVICE = (_REPO / "services" / "anchor_video_generation.py").read_text(encoding="utf-8")


def test_anchor_worker_passes_expected_anchor_file_arguments():
    assert "asyncio.to_thread(_anchor_file, anchor_id, job.owner, work)" in _SERVICE
    assert "asyncio.to_thread(_anchor_file, job.id, anchor_id" not in _SERVICE


def test_video_gallery_filename_matches_generated_media_route():
    assert 'filename = f"{digest}.mp4"' in _SERVICE
    assert "depth-parallax-{digest}.mp4" not in _SERVICE
    assert GENERATED_IMAGE_RE.fullmatch("72fe6fc23594b0ed0d8ed94d.mp4")
