from __future__ import annotations

import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from services import remote_ltx_video as ltx
from src.anchor_video_settings import REMOTE_LTX_CONSENT_VERSION


def _box(name: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload) + 8) + name + payload


def _fake_mp4(frame_count: int = 240, duration_ms: int = 8000) -> bytes:
    mvhd = _box(
        b"mvhd",
        b"\x00\x00\x00\x00" + struct.pack(">IIII", 0, 0, 1000, duration_ms) + b"\x00" * 80,
    )
    stsz = _box(
        b"stsz",
        b"\x00\x00\x00\x00" + struct.pack(">II", 0, frame_count) + (b"\x00\x00\x00\x01" * frame_count),
    )
    stbl = _box(b"stbl", stsz)
    minf = _box(b"minf", stbl)
    mdia = _box(b"mdia", minf)
    trak = _box(b"trak", mdia)
    moov = _box(b"moov", mvhd + trak)
    ftyp = _box(b"ftyp", b"isom\x00\x00\x02\x00isomiso2avc1mp41")
    return ftyp + moov + _box(b"mdat", b"\x00" * 2048)


def _schema() -> dict:
    return {
        "named_endpoints": {
            "/image_to_video": {
                "parameters": [
                    {"parameter_name": "prompt", "component": "Textbox", "label": "Prompt"},
                    {"parameter_name": "negative_prompt", "component": "Textbox", "label": "Negative prompt"},
                    {"parameter_name": "image", "component": "Image", "label": "Input image"},
                    {"parameter_name": "duration", "component": "Slider", "label": "Duration seconds"},
                    {"parameter_name": "seed", "component": "Number", "label": "Seed"},
                    {"parameter_name": "width", "component": "Number", "label": "Width"},
                    {"parameter_name": "height", "component": "Number", "label": "Height"},
                    {"parameter_name": "fps", "component": "Number", "label": "FPS"},
                    {"parameter_name": "mode", "component": "Dropdown", "label": "Mode"},
                ]
            }
        }
    }


def _live_ltx_schema() -> dict:
    return {
        "named_endpoints": {
            "/text_to_video": {
                "parameters": [
                    {"parameter_name": "prompt", "component": "Textbox", "label": "Prompt", "python_type": {"type": "str"}},
                    {"parameter_name": "negative_prompt", "component": "Textbox", "label": "Negative prompt", "python_type": {"type": "str"}},
                    {"parameter_name": "input_image_filepath", "component": "Textbox", "label": "image_n", "python_type": {"type": "str"}},
                    {"parameter_name": "height_ui", "component": "Slider", "label": "Height"},
                    {"parameter_name": "width_ui", "component": "Slider", "label": "Width"},
                    {"parameter_name": "mode", "component": "Dropdown", "label": "task"},
                    {"parameter_name": "duration_ui", "component": "Slider", "label": "Video Duration (seconds)"},
                    {"parameter_name": "ui_frames_to_use", "component": "Slider", "label": "Frames to use from input video"},
                    {"parameter_name": "seed_ui", "component": "Number", "label": "Seed"},
                    {"parameter_name": "randomize_seed", "component": "Checkbox", "label": "Randomize Seed"},
                    {"parameter_name": "ui_guidance_scale", "component": "Slider", "label": "Guidance Scale (CFG)"},
                    {"parameter_name": "improve_texture_flag", "component": "Checkbox", "label": "Improve Texture (multi-scale)"},
                ]
            },
            "/image_to_video": {
                "parameters": [
                    {"parameter_name": "prompt", "component": "Textbox", "label": "Prompt"},
                    {"parameter_name": "input_image_filepath", "component": "Image", "label": "Input image"},
                    {"parameter_name": "mode", "component": "Dropdown", "label": "task"},
                ]
            },
        }
    }


def test_remote_schema_rejects_unknown_endpoint():
    with pytest.raises(ltx.RemoteLtxCompatibilityError):
        ltx.inspect_remote_ltx_schema({"named_endpoints": {"/predict": {"parameters": [{"parameter_name": "text"}]}}})


def test_remote_schema_finds_image_to_video_endpoint():
    signature = ltx.inspect_remote_ltx_schema(_schema())
    assert signature.api_name == "/image_to_video"
    assert signature.prompt_param == "prompt"
    assert signature.image_param == "image"
    assert signature.image_transport == "file_data"
    assert signature.duration_param == "duration"


def test_remote_schema_prefers_filepath_string_endpoint_for_public_ltx_shape():
    signature = ltx.inspect_remote_ltx_schema(_live_ltx_schema())
    assert signature.api_name == "/text_to_video"
    assert signature.prompt_param == "prompt"
    assert signature.image_param == "input_image_filepath"
    assert signature.image_transport == "uploaded_path"
    assert signature.mode_param == "mode"
    assert signature.randomize_seed_param == "randomize_seed"
    assert signature.frames_to_use_param == "ui_frames_to_use"
    assert signature.guidance_scale_param == "ui_guidance_scale"
    assert signature.improve_texture_param == "improve_texture_flag"


def test_remote_schema_handles_duplicate_scored_endpoint_names():
    api_info = {
        "endpoints": [
            {
                "api_name": "/image_to_video",
                "parameters": [
                    {"parameter_name": "prompt", "label": "Prompt"},
                    {"parameter_name": "image", "label": "Input image"},
                ],
            },
            {
                "api_name": "/image_to_video",
                "parameters": [
                    {"parameter_name": "prompt", "label": "Prompt"},
                    {"parameter_name": "image", "label": "Input image"},
                ],
            },
        ]
    }
    signature = ltx.inspect_remote_ltx_schema(api_info)
    assert signature.api_name == "/image_to_video"


def test_remote_submit_uses_derived_prompt_and_uploaded_anchor_path(monkeypatch, tmp_path):
    video = tmp_path / "result.mp4"
    video.write_bytes(_fake_mp4())
    calls = []
    stages = []

    class Job:
        def __init__(self):
            self._done = False
            self._ticks = 0

        def done(self):
            self._ticks += 1
            self._done = self._ticks > 2
            return self._done

        def status(self):
            return SimpleNamespace(code="IN_QUEUE" if self._ticks == 1 else "PROCESSING")

        def result(self):
            return str(video)

    class Client:
        def view_api(self, return_format="dict"):
            return _live_ltx_schema()

        def submit(self, **kwargs):
            calls.append(kwargs)
            return Job()

    monkeypatch.setattr(ltx, "_client", lambda work_dir=None: Client())
    monkeypatch.setattr(ltx, "_upload_anchor_path", lambda _client, _path: "/tmp/gradio/uploaded-anchor.png")
    monkeypatch.setattr(ltx.time, "sleep", lambda _seconds: None)
    anchor = tmp_path / "anchor.png"
    anchor.write_bytes(b"png")

    result = ltx.RemoteLtxProvider().render(
        anchor_path=anchor,
        motion_prompt="Derived dragon wing motion that preserves the anchor identity and composition.",
        seed=123,
        progress_callback=stages.append,
        cancelled=lambda: False,
        work_dir=tmp_path,
    )

    submitted = calls[0]
    assert submitted["api_name"] == "/text_to_video"
    assert submitted["prompt"].startswith("Derived dragon wing motion")
    assert submitted["input_image_filepath"] == "/tmp/gradio/uploaded-anchor.png"
    assert submitted["duration_ui"] == 8.0
    assert submitted["ui_frames_to_use"] == 9
    assert submitted["seed_ui"] == 123
    assert submitted["randomize_seed"] is False
    assert submitted["ui_guidance_scale"] == 1.0
    assert submitted["improve_texture_flag"] is False
    assert submitted["mode"] == "image-to-video"
    assert "waiting_remote_queue" in stages
    assert "generating_remote_ltx" in stages
    assert result.actual_frame_count == 240
    assert round(result.actual_duration_seconds, 1) == 8.0


def test_remote_cancel_requests_job_cancel(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "gradio_client", SimpleNamespace(handle_file=lambda path: {"path": path}))
    cancelled = {"value": False, "called": False}

    class Job:
        def done(self):
            cancelled["value"] = True
            return False

        def status(self):
            return SimpleNamespace(code="IN_QUEUE")

        def cancel(self):
            cancelled["called"] = True

    class Client:
        def view_api(self, return_format="dict"):
            return _schema()

        def submit(self, **_kwargs):
            return Job()

    monkeypatch.setattr(ltx, "_client", lambda work_dir=None: Client())
    monkeypatch.setattr(ltx.time, "sleep", lambda _seconds: None)
    anchor = tmp_path / "anchor.png"
    anchor.write_bytes(b"png")

    with pytest.raises(ltx.RenderCancelled):
        ltx.RemoteLtxProvider().render(
            anchor_path=anchor,
            motion_prompt="Derived motion prompt that preserves the anchor identity and composition.",
            seed=123,
            progress_callback=lambda _stage: None,
            cancelled=lambda: cancelled["value"],
            work_dir=tmp_path,
        )
    assert cancelled["called"] is True


def test_remote_provider_requires_consent(monkeypatch):
    monkeypatch.setattr(ltx, "remote_ltx_status", lambda: {"available": True})
    with pytest.raises(ltx.RemoteLtxError):
        ltx.RemoteLtxProvider().validate_request({"video_provider": "remote_ltx"})
    ltx.RemoteLtxProvider().validate_request({
        "video_provider": "remote_ltx",
        "video_remote_ltx_consent_version": REMOTE_LTX_CONSENT_VERSION,
    })


@pytest.mark.parametrize("content", [b"", b"<html>nope</html>", b"not an mp4" * 200])
def test_remote_video_validation_rejects_invalid_content(tmp_path, content):
    path = tmp_path / "bad.mp4"
    path.write_bytes(content)
    with pytest.raises(ltx.RemoteLtxError):
        ltx.validate_remote_video(path)


def test_remote_video_validation_rejects_malformed_mp4_cleanly(tmp_path):
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"....ftyp" + b"x" * 1024 + b"moov" + b"x" * 32 + b"mdat")
    with pytest.raises(ltx.RemoteLtxError, match="invalid MP4|playable timing metadata"):
        ltx.validate_remote_video(path)


def test_remote_download_rejects_html_mime(monkeypatch, tmp_path):
    class Response:
        status_code = 200
        headers = {"content-type": "text/html"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_bytes(self, _size):
            yield b"<html>nope</html>"

    monkeypatch.setattr(ltx.httpx, "stream", lambda *args, **kwargs: Response())

    with pytest.raises(ltx.RemoteLtxError):
        ltx._download_url("https://example.invalid/video", tmp_path / "video.mp4")


def test_remote_download_rejects_oversized_response(monkeypatch, tmp_path):
    class Response:
        status_code = 200
        headers = {"content-type": "video/mp4"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_bytes(self, _size):
            yield b"12345"

    monkeypatch.setattr(ltx, "MAX_REMOTE_VIDEO_BYTES", 4)
    monkeypatch.setattr(ltx.httpx, "stream", lambda *args, **kwargs: Response())

    with pytest.raises(ltx.RemoteLtxError):
        ltx._download_url("https://example.invalid/video", tmp_path / "video.mp4")
