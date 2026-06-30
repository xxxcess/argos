"""Video render provider abstractions for anchor-first jobs."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from services.depth_parallax_renderer import render_depth_parallax_video
from services.remote_ltx_video import RemoteLtxProvider
from services.video_runtime_setup import prepare_runtime
from src.anchor_video_settings import VIDEO_PROVIDER_DEPTH, VIDEO_PROVIDER_REMOTE_LTX

LOCAL_DURATION_SECONDS = 8
LOCAL_SOURCE_FPS = 6
LOCAL_TARGET_FPS = 24
LOCAL_FRAME_COUNT = LOCAL_DURATION_SECONDS * LOCAL_TARGET_FPS


@dataclass(frozen=True)
class RenderedVideo:
    path: Path
    provider_id: str
    model: str
    quality: str
    requested_duration_seconds: float
    actual_duration_seconds: float
    actual_fps: float
    actual_frame_count: int
    generation_params: dict[str, Any] = field(default_factory=dict)


class VideoRenderProvider(Protocol):
    provider_id: str

    def status(self) -> dict[str, Any]: ...

    def validate_request(self, config: dict[str, Any]) -> None: ...

    def render(
        self,
        *,
        anchor_path: Path,
        motion_prompt: str,
        seed: int,
        progress_callback: Callable[[str], None],
        cancelled: Callable[[], bool],
        work_dir: Path,
    ) -> RenderedVideo: ...


class DepthParallaxProvider:
    provider_id = VIDEO_PROVIDER_DEPTH

    def status(self) -> dict[str, Any]:
        prepared = prepare_runtime()
        return {
            **prepared,
            "provider": self.provider_id,
            "mode": "local_motion",
            "label": "Local Motion",
            "description": "Private eight-second depth-aware camera movement.",
            "width": 512,
            "height": 512,
            "duration_seconds": LOCAL_DURATION_SECONDS,
            "fps": LOCAL_TARGET_FPS,
            "source_fps": LOCAL_SOURCE_FPS,
            "frame_count": LOCAL_FRAME_COUNT,
            "audio": False,
            "message": (
                "Local Motion ready. Private depth-aware camera movement."
                if prepared.get("available")
                else str(prepared.get("reason") or "Install required")
            ),
        }

    def validate_request(self, _config: dict[str, Any]) -> None:
        status = self.status()
        if not status.get("available"):
            raise RuntimeError(status.get("reason") or "The local depth-video engine is unavailable.")

    def render(
        self,
        *,
        anchor_path: Path,
        motion_prompt: str,
        seed: int,
        progress_callback: Callable[[str], None],
        cancelled: Callable[[], bool],
        work_dir: Path,
    ) -> RenderedVideo:
        status = self.status()
        if not status.get("available"):
            raise RuntimeError(status.get("reason") or "The local depth-video engine is unavailable.")
        draft = work_dir / "video.mp4"
        progress_callback("rendering_depth_parallax")
        render_depth_parallax_video(
            anchor_path=anchor_path,
            output_path=draft,
            model_path=Path(str(status["model_path"])),
            ffmpeg_path=str(status["ffmpeg"]),
            motion_prompt=motion_prompt,
            seed=int(seed),
            source_fps=LOCAL_SOURCE_FPS,
            duration_seconds=LOCAL_DURATION_SECONDS,
            target_fps=LOCAL_TARGET_FPS,
            cancelled=cancelled,
        )
        return RenderedVideo(
            path=draft,
            provider_id=self.provider_id,
            model="local-depth-parallax:Depth-Anything-V2-Small",
            quality="local-motion",
            requested_duration_seconds=float(LOCAL_DURATION_SECONDS),
            actual_duration_seconds=float(LOCAL_DURATION_SECONDS),
            actual_fps=float(LOCAL_TARGET_FPS),
            actual_frame_count=LOCAL_FRAME_COUNT,
            generation_params={
                "provider": self.provider_id,
                "provider_label": "Local Motion",
                "source_fps": LOCAL_SOURCE_FPS,
                "depth_model": "Depth-Anything-V2-Small",
                "requested_duration_seconds": float(LOCAL_DURATION_SECONDS),
                "actual_duration_seconds": float(LOCAL_DURATION_SECONDS),
                "actual_fps": float(LOCAL_TARGET_FPS),
                "actual_frame_count": LOCAL_FRAME_COUNT,
            },
        )


class RemoteLtxRenderProvider(RemoteLtxProvider):
    def render(self, **kwargs: Any) -> RenderedVideo:
        result = super().render(**kwargs)
        return RenderedVideo(
            path=result.path,
            provider_id=self.provider_id,
            model="remote-ltx:Lightricks/ltx-video-distilled",
            quality="remote-ltx",
            requested_duration_seconds=8.0,
            actual_duration_seconds=result.actual_duration_seconds,
            actual_fps=result.actual_fps,
            actual_frame_count=result.actual_frame_count,
            generation_params={
                **result.generation_params,
                "actual_duration_seconds": result.actual_duration_seconds,
                "actual_fps": result.actual_fps,
                "actual_frame_count": result.actual_frame_count,
            },
        )


def provider_for(provider_id: str) -> VideoRenderProvider:
    if provider_id == VIDEO_PROVIDER_REMOTE_LTX:
        return RemoteLtxRenderProvider()
    return DepthParallaxProvider()


def provider_status(provider_id: str) -> dict[str, Any]:
    return provider_for(provider_id).status()
