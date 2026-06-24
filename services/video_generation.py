"""Durable, single-concurrency local mlx-video generation service.

V1 deliberately uses a subprocess rather than importing the model pipeline into
Argos's process.  That keeps MLX allocations, CLI failures, and optional runtime
dependencies isolated from the web server and lets unified memory be released
when a generation finishes.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import secrets
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text

from core.database import Base, GalleryImage, SessionLocal, engine
from src.constants import DATA_DIR, GENERATED_IMAGES_DIR
from src.video_settings import resolve_generation_request

logger = logging.getLogger(__name__)


class VideoGenerationJob(Base):
    """Persisted prompt-to-video request; jobs survive browser refreshes/restarts."""

    __tablename__ = "video_generation_jobs"

    id = Column(String, primary_key=True, index=True)
    owner = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, default="queued", index=True)
    prompt = Column(Text, nullable=False)
    request_config = Column(JSON, nullable=False, default=dict)
    stage = Column(String, nullable=False, default="Queued")
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    gallery_image_id = Column(String, ForeignKey("gallery_images.id", ondelete="SET NULL"), nullable=True, index=True)
    error_summary = Column(String(500), nullable=True)
    log_path = Column(String, nullable=True)
    cancellation_requested = Column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_video_generation_jobs_owner_status_created", "owner", "status", "created_at"),
    )


class VideoMediaMetadata(Base):
    """Additive sidecar metadata for video without breaking legacy GalleryImage rows."""

    __tablename__ = "video_media_metadata"

    id = Column(String, primary_key=True)
    gallery_image_id = Column(String, ForeignKey("gallery_images.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    duration_seconds = Column(Integer, nullable=True)
    fps = Column(Integer, nullable=False)
    frame_count = Column(Integer, nullable=False)
    seed = Column(Integer, nullable=False)
    generation_params = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


VIDEO_JOB_LOG_DIR = Path(DATA_DIR) / "logs" / "video_jobs"
VIDEO_TMP_DIR = Path(DATA_DIR) / "video_jobs"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_video_tables() -> None:
    """Create just the feature's additive tables; safe and idempotent."""
    Base.metadata.create_all(bind=engine, tables=[VideoGenerationJob.__table__, VideoMediaMetadata.__table__])


def recover_interrupted_jobs() -> int:
    """A subprocess cannot be resumed after a process restart, so mark it honestly."""
    db = SessionLocal()
    try:
        rows = db.query(VideoGenerationJob).filter(VideoGenerationJob.status == "running").all()
        for job in rows:
            job.status = "interrupted"
            job.stage = "Interrupted by server restart"
            job.error_summary = "Generation was interrupted by a server restart."
            job.finished_at = _utcnow()
        if rows:
            db.commit()
        return len(rows)
    finally:
        db.close()


def _runtime_binary() -> str:
    return (os.getenv("MLX_VIDEO_BIN") or "mlx_video.ltx_2.generate").strip()


def runtime_status() -> dict[str, Any]:
    """Return a safe availability payload; never leak paths/secrets."""
    if platform.system() != "Darwin":
        return {"available": False, "reason": "Video generation requires native macOS on Apple Silicon."}
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        return {"available": False, "reason": "Video generation requires Apple Silicon."}
    if sys.version_info < (3, 11):
        return {"available": False, "reason": "Video generation requires Python 3.11 or newer."}
    binary = _runtime_binary()
    if os.path.sep in binary:
        found = Path(binary).is_file() and os.access(binary, os.X_OK)
    else:
        found = shutil.which(binary) is not None
    if not found:
        return {"available": False, "reason": "mlx-video is not installed or configured for this native environment."}
    return {
        "available": True,
        "provider": "mlx_video",
        "models": ["Lightricks/LTX-2"],
        "pipelines": ["distilled"],
        "resolutions": [[512, 512], [768, 512], [512, 768]],
        "frame_counts": [33, 49, 97],
        "fps": [24],
        "audio": False,
    }


def _safe_error(exc: Exception | str) -> str:
    text = str(exc or "Video generation failed.").replace("\n", " ").strip()
    return (text or "Video generation failed.")[:500]


def _job_log_path(job_id: str) -> Path:
    VIDEO_JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return VIDEO_JOB_LOG_DIR / f"{job_id}.log"


def _job_tmp_dir(job_id: str) -> Path:
    path = VIDEO_TMP_DIR / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _build_command(config: dict[str, Any], output_path: Path) -> list[str]:
    """Build a no-shell, no-audio CLI invocation from validated config only."""
    command = [
        _runtime_binary(),
        "--prompt", config["prompt"],
        "--pipeline", config["video_pipeline"],
        "--width", str(config["video_width"]),
        "--height", str(config["video_height"]),
        "--num-frames", str(config["video_num_frames"]),
        "--fps", str(config["video_fps"]),
        "--seed", str(config["video_seed"]),
        "--output-path", str(output_path),
    ]
    # mlx-video's current LTX CLI supports optional tiling. Keep it explicit and
    # strict; never allow arbitrary client-supplied CLI parameters.
    if config.get("video_tiling") == "enabled":
        command.append("--tiled")
    return command


def _ffprobe_metadata(path: Path) -> tuple[int | None, bool | None]:
    """Best-effort duration/audio inspection. Missing ffprobe is not fatal."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None, None
    try:
        payload = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_entries", "format=duration:stream=codec_type", "-of", "json", str(path)],
            stderr=subprocess.DEVNULL,
            timeout=10,
            text=True,
        )
        data = json.loads(payload)
        duration = data.get("format", {}).get("duration")
        duration_seconds = int(round(float(duration))) if duration is not None else None
        has_audio = any((stream or {}).get("codec_type") == "audio" for stream in (data.get("streams") or []))
        return duration_seconds, has_audio
    except Exception:
        return None, None


class VideoGenerationService:
    """One local worker serializing durable jobs through mlx-video."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def start(self) -> None:
        await asyncio.to_thread(ensure_video_tables)
        interrupted = await asyncio.to_thread(recover_interrupted_jobs)
        if interrupted:
            logger.info("Marked %s interrupted video job(s) after restart", interrupted)
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._worker_loop(), name="mlx-video-worker")

    async def stop(self) -> None:
        self._stop.set()
        for process in list(self._processes.values()):
            if process.returncode is None:
                process.terminate()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=8)
            except asyncio.TimeoutError:
                self._task.cancel()
            except Exception:
                logger.debug("Video worker shutdown failed", exc_info=True)

    def create_job(self, owner: str | None, body: dict[str, Any]) -> VideoGenerationJob:
        config = resolve_generation_request(owner, body)
        if not config["video_gen_enabled"]:
            raise ValueError("Video generation is disabled in AI Defaults.")
        status = runtime_status()
        if not status.get("available"):
            raise RuntimeError(status.get("reason") or "Video generation runtime is unavailable.")
        job = VideoGenerationJob(
            id=uuid.uuid4().hex,
            owner=owner or None,
            session_id=config.get("session_id"),
            status="queued",
            prompt=config["prompt"],
            request_config=config,
            stage="Queued",
        )
        db = SessionLocal()
        try:
            db.add(job)
            db.commit()
            db.refresh(job)
            return job
        finally:
            db.close()

    def get_job(self, job_id: str, owner: str | None) -> VideoGenerationJob | None:
        db = SessionLocal()
        try:
            query = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job_id)
            if owner:
                query = query.filter(VideoGenerationJob.owner == owner)
            job = query.first()
            if job is not None:
                db.expunge(job)
            return job
        finally:
            db.close()

    def cancel_job(self, job_id: str, owner: str | None) -> VideoGenerationJob | None:
        db = SessionLocal()
        try:
            query = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job_id)
            if owner:
                query = query.filter(VideoGenerationJob.owner == owner)
            job = query.first()
            if not job:
                return None
            if job.status == "queued":
                job.status = "cancelled"
                job.stage = "Cancelled"
                job.finished_at = _utcnow()
            elif job.status == "running":
                job.cancellation_requested = True
                job.stage = "Cancelling"
            db.commit()
            db.refresh(job)
            db.expunge(job)
            return job
        finally:
            db.close()

    async def _worker_loop(self) -> None:
        while not self._stop.is_set():
            status = runtime_status()
            if not status.get("available"):
                await asyncio.sleep(3)
                continue
            job = await asyncio.to_thread(self._claim_next_job)
            if job is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=0.75)
                except asyncio.TimeoutError:
                    pass
                continue
            await self._run_job(job.id)

    def _claim_next_job(self) -> VideoGenerationJob | None:
        db = SessionLocal()
        try:
            job = (
                db.query(VideoGenerationJob)
                .filter(VideoGenerationJob.status == "queued", VideoGenerationJob.cancellation_requested == False)  # noqa: E712
                .order_by(VideoGenerationJob.created_at.asc())
                .first()
            )
            if not job:
                return None
            job.status = "running"
            job.stage = "Preparing model"
            job.started_at = _utcnow()
            db.commit()
            db.refresh(job)
            db.expunge(job)
            return job
        finally:
            db.close()

    def _is_cancelled(self, job_id: str) -> bool:
        db = SessionLocal()
        try:
            job = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job_id).first()
            return not job or job.cancellation_requested or job.status == "cancelled"
        finally:
            db.close()

    def _set_stage(self, job_id: str, stage: str) -> None:
        db = SessionLocal()
        try:
            job = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job_id).first()
            if job:
                job.stage = stage
                db.commit()
        finally:
            db.close()

    def _finish_cancelled(self, job_id: str) -> None:
        db = SessionLocal()
        try:
            job = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job_id).first()
            if job:
                job.status = "cancelled"
                job.stage = "Cancelled"
                job.finished_at = _utcnow()
                db.commit()
        finally:
            db.close()

    def _finish_failed(self, job_id: str, error: Exception | str) -> None:
        db = SessionLocal()
        try:
            job = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job_id).first()
            if job:
                job.status = "failed"
                job.stage = "Failed"
                job.error_summary = _safe_error(error)
                job.finished_at = _utcnow()
                db.commit()
        finally:
            db.close()

    def _finalize_success(self, job_id: str, output: Path, config: dict[str, Any], log_path: Path) -> None:
        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError("mlx-video did not produce a usable MP4 output.")
        if output.suffix.lower() != ".mp4":
            raise RuntimeError("mlx-video output was not an MP4 file.")

        duration, has_audio = _ffprobe_metadata(output)
        if has_audio is True:
            raise RuntimeError("Generated output contains audio; silent video is required.")

        digest = hashlib.sha256()
        with output.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        file_hash = digest.hexdigest()
        media_root = Path(GENERATED_IMAGES_DIR)
        media_root.mkdir(parents=True, exist_ok=True)
        final_path = media_root / f"{file_hash}.mp4"
        if final_path.exists() and final_path.stat().st_size > 0:
            output.unlink(missing_ok=True)
        else:
            os.replace(output, final_path)

        db = SessionLocal()
        try:
            job = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job_id).first()
            if not job:
                raise RuntimeError("Video job disappeared before finalization.")
            if job.cancellation_requested:
                raise RuntimeError("Video generation was cancelled.")
            gallery_id = uuid.uuid4().hex
            gallery = GalleryImage(
                id=gallery_id,
                filename=final_path.name,
                prompt=config["prompt"],
                model="mlx-video:LTX-2-distilled",
                size=f"{config['video_width']}x{config['video_height']}",
                quality=config["video_pipeline"],
                owner=job.owner,
                session_id=job.session_id,
                file_hash=file_hash,
                file_size=final_path.stat().st_size,
                width=config["video_width"],
                height=config["video_height"],
            )
            db.add(gallery)
            db.add(VideoMediaMetadata(
                id=uuid.uuid4().hex,
                gallery_image_id=gallery_id,
                duration_seconds=duration,
                fps=config["video_fps"],
                frame_count=config["video_num_frames"],
                seed=config["video_seed"],
                generation_params={key: value for key, value in config.items() if key not in {"prompt", "session_id"}},
            ))
            job.gallery_image_id = gallery_id
            job.status = "succeeded"
            job.stage = "Complete"
            job.finished_at = _utcnow()
            job.log_path = str(log_path)
            db.commit()
        finally:
            db.close()

    async def _run_job(self, job_id: str) -> None:
        db = SessionLocal()
        try:
            job = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job_id).first()
            if not job:
                return
            config = dict(job.request_config or {})
        finally:
            db.close()
        if await asyncio.to_thread(self._is_cancelled, job_id):
            await asyncio.to_thread(self._finish_cancelled, job_id)
            return

        temp_dir = _job_tmp_dir(job_id)
        output = temp_dir / "output.mp4"
        log_path = _job_log_path(job_id)
        try:
            await asyncio.to_thread(self._set_stage, job_id, "Generating frames")
            command = _build_command(config, output)
            with log_path.open("wb") as log_file:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                    cwd=str(temp_dir),
                )
                self._processes[job_id] = process
                cancelled = False
                while process.returncode is None:
                    if await asyncio.to_thread(self._is_cancelled, job_id):
                        cancelled = True
                        process.terminate()
                        try:
                            await asyncio.wait_for(process.wait(), timeout=8)
                        except asyncio.TimeoutError:
                            process.kill()
                            await process.wait()
                        break
                    try:
                        await asyncio.wait_for(process.wait(), timeout=0.5)
                    except asyncio.TimeoutError:
                        continue
                return_code = process.returncode
            self._processes.pop(job_id, None)
            if cancelled or await asyncio.to_thread(self._is_cancelled, job_id):
                await asyncio.to_thread(self._finish_cancelled, job_id)
                return
            if return_code != 0:
                raise RuntimeError(f"mlx-video exited with code {return_code}. Check the job log for details.")
            await asyncio.to_thread(self._set_stage, job_id, "Finalizing video")
            await asyncio.to_thread(self._finalize_success, job_id, output, config, log_path)
        except Exception as exc:
            if await asyncio.to_thread(self._is_cancelled, job_id):
                await asyncio.to_thread(self._finish_cancelled, job_id)
            else:
                logger.warning("Video job %s failed: %s", job_id, exc, exc_info=True)
                await asyncio.to_thread(self._finish_failed, job_id, exc)
        finally:
            self._processes.pop(job_id, None)
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

    def serialize_job(self, job: VideoGenerationJob) -> dict[str, Any]:
        result = {
            "id": job.id,
            "status": job.status,
            "stage": job.stage,
            "prompt": job.prompt,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "error": job.error_summary,
            "gallery_id": job.gallery_image_id,
            "config": job.request_config,
        }
        if job.status == "succeeded" and job.gallery_image_id:
            db = SessionLocal()
            try:
                image = db.query(GalleryImage).filter(GalleryImage.id == job.gallery_image_id).first()
                meta = db.query(VideoMediaMetadata).filter(VideoMediaMetadata.gallery_image_id == job.gallery_image_id).first()
                if image:
                    result.update({
                        "video_url": f"/api/generated-image/{image.filename}",
                        "video_model": image.model,
                        "video_size": image.size,
                        "video_fps": meta.fps if meta else None,
                        "video_frames": meta.frame_count if meta else None,
                        "video_seed": meta.seed if meta else None,
                        "duration_seconds": meta.duration_seconds if meta else None,
                    })
            finally:
                db.close()
        return result


_service: VideoGenerationService | None = None


def get_video_generation_service() -> VideoGenerationService:
    global _service
    if _service is None:
        _service = VideoGenerationService()
    return _service
