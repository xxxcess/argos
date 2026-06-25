"""Durable anchor-first prompt-to-video generation.

The user supplies an intent. A utility model derives two distinct prompts: one
for Argos's configured Image Default (the stable anchor) and another for local
LTX image-to-video animation. The raw intent is never passed directly to either
downstream generator.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text

from core.database import Base, GalleryImage, SessionLocal, engine
from src.anchor_video_settings import VideoSettingsError, resolve_generation_request
from src.constants import DATA_DIR, GENERATED_IMAGES_DIR

logger = logging.getLogger(__name__)


class VideoGenerationJob(Base):
    __tablename__ = "video_generation_jobs"

    id = Column(String, primary_key=True, index=True)
    owner = Column(String, nullable=True, index=True)
    session_id = Column(String, nullable=True, index=True)
    status = Column(String, nullable=False, default="queued", index=True)
    stage = Column(String, nullable=False, default="queued")
    source_intent = Column(Text, nullable=False)
    request_config = Column(JSON, nullable=False, default=dict)
    anchor_gallery_image_id = Column(String, ForeignKey("gallery_images.id", ondelete="SET NULL"), nullable=True, index=True)
    anchor_prompt = Column(Text, nullable=True)
    motion_prompt = Column(Text, nullable=True)
    planner_model = Column(String, nullable=True)
    gallery_image_id = Column(String, ForeignKey("gallery_images.id", ondelete="SET NULL"), nullable=True, index=True)
    cancellation_requested = Column(Boolean, nullable=False, default=False)
    error_summary = Column(String(500), nullable=True)
    log_path = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_video_jobs_owner_status_created", "owner", "status", "created_at"),)


class VideoMediaMetadata(Base):
    """Video-specific provenance without changing legacy GalleryImage columns."""
    __tablename__ = "video_media_metadata"

    id = Column(String, primary_key=True)
    gallery_image_id = Column(String, ForeignKey("gallery_images.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    anchor_gallery_image_id = Column(String, ForeignKey("gallery_images.id", ondelete="SET NULL"), nullable=True, index=True)
    anchor_prompt = Column(Text, nullable=False)
    motion_prompt = Column(Text, nullable=False)
    planner_model = Column(String, nullable=True)
    duration_seconds = Column(Integer, nullable=False, default=10)
    fps = Column(Integer, nullable=False, default=24)
    frame_count = Column(Integer, nullable=False, default=241)
    seed = Column(Integer, nullable=False)
    generation_params = Column(JSON, nullable=False, default=dict)


_TMP_ROOT = Path(DATA_DIR) / "video_jobs"
_LOG_ROOT = Path(DATA_DIR) / "logs" / "video_jobs"
_ALLOWED_ANCHOR_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def ensure_video_tables() -> None:
    Base.metadata.create_all(bind=engine, tables=[VideoGenerationJob.__table__, VideoMediaMetadata.__table__])


def recover_interrupted_jobs() -> int:
    db = SessionLocal()
    try:
        jobs = db.query(VideoGenerationJob).filter(VideoGenerationJob.status == "running").all()
        for job in jobs:
            job.status = "interrupted"
            job.stage = "interrupted"
            job.error_summary = "Generation was interrupted by a server restart."
            job.finished_at = _now()
        if jobs:
            db.commit()
        return len(jobs)
    finally:
        db.close()


def _binary() -> str:
    return (os.getenv("MLX_VIDEO_BIN") or "mlx_video.ltx_2.generate").strip()


def runtime_status() -> dict[str, Any]:
    if platform.system() != "Darwin":
        return {"available": False, "reason": "Video generation requires native macOS on Apple Silicon."}
    if platform.machine().lower() not in {"arm64", "aarch64"}:
        return {"available": False, "reason": "Video generation requires Apple Silicon."}
    if sys.version_info < (3, 11):
        return {"available": False, "reason": "Video generation requires Python 3.11 or newer."}
    command = _binary()
    found = (Path(command).is_file() and os.access(command, os.X_OK)) if os.path.sep in command else bool(shutil.which(command))
    if not found:
        return {"available": False, "reason": "mlx-video is not installed in this native environment."}
    return {
        "available": True,
        "provider": "mlx_video",
        "mode": "anchor_first_i2v",
        "model": "Lightricks/LTX-2",
        "pipeline": "distilled",
        "width": 512,
        "height": 512,
        "frames": 241,
        "fps": 24,
        "duration_seconds": 10,
        "audio": False,
    }


def _safe_error(exc: Exception | str) -> str:
    value = re.sub(r"\s+", " ", str(exc or "Video generation failed.")).strip()
    return (value or "Video generation failed.")[:500]


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _validate_derived_prompt(raw_intent: str, prompt: str, label: str) -> str:
    prompt = re.sub(r"\s+", " ", str(prompt or "")).strip().strip('"')
    if len(prompt) < 80:
        raise RuntimeError(f"The {label} prompt planner returned an incomplete result.")
    raw = _normalize(raw_intent)
    normalized = _normalize(prompt)
    if normalized == raw or (raw and raw in normalized):
        raise RuntimeError(f"The {label} prompt planner repeated the raw video intent instead of deriving a production prompt.")
    return prompt[:3000]


async def _derive_prompt(owner: str | None, source_intent: str, kind: str, anchor_prompt: str | None = None) -> tuple[str, str]:
    """Use the selected Utility Model, with its standard chat fallback behavior."""
    from src.endpoint_resolver import resolve_endpoint
    from src.llm_core import llm_call_async

    url, model, headers = resolve_endpoint("utility", owner=owner)
    if not url or not model:
        raise RuntimeError("Configure a Utility Model or Default Chat Model to plan anchor-first video prompts.")

    if kind == "anchor":
        system = (
            "You are a film art director. Transform a user's high-level video intent into a detailed still-image prompt for the first frame of a cinematic clip. "
            "Specify subject identity, wardrobe, materials, environment, composition, camera angle, lens feel, framing, lighting, palette, atmosphere, and image quality. "
            "Do not mention movement, duration, sound, cuts, captions, or explain your work. Do not copy the user's wording. Return only the finished image prompt."
        )
        user = "Video intent to reinterpret:\n" + source_intent
    else:
        system = (
            "You are an image-to-video director. Produce one detailed motion instruction for a supplied anchor frame. Preserve the anchor's identity, wardrobe, composition, lighting, and environment. "
            "Describe subtle subject motion, controlled camera movement, timing, natural physics, and one continuous cinematic shot. "
            "Do not introduce a new scene, cuts, audio, subtitles, or explanations. Do not copy the user's wording. Return only the finished animation prompt."
        )
        user = "Original video intent:\n" + source_intent + "\n\nAnchor art direction:\n" + (anchor_prompt or "")

    response = await llm_call_async(
        url,
        model,
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        headers=headers,
        timeout=120,
    )
    return _validate_derived_prompt(source_intent, response, kind), str(model)


def _job_dirs(job_id: str) -> tuple[Path, Path]:
    work = _TMP_ROOT / job_id
    work.mkdir(parents=True, exist_ok=True)
    _LOG_ROOT.mkdir(parents=True, exist_ok=True)
    return work, _LOG_ROOT / f"{job_id}.log"


def _build_command(anchor_path: Path, motion_prompt: str, config: dict[str, Any], output_path: Path) -> list[str]:
    """Build a strictly fixed, no-audio image-to-video command."""
    return [
        _binary(),
        "--image", str(anchor_path),
        "--prompt", motion_prompt,
        "--pipeline", "distilled",
        "--width", "512",
        "--height", "512",
        "--num-frames", "241",
        "--fps", "24",
        "--seed", str(config["video_seed"]),
        "--output", str(output_path),
    ]


def _ffprobe_has_audio(path: Path) -> bool | None:
    tool = shutil.which("ffprobe")
    if not tool:
        return None
    try:
        output = subprocess.check_output([tool, "-v", "error", "-show_entries", "stream=codec_type", "-of", "json", str(path)], text=True, timeout=10, stderr=subprocess.DEVNULL)
        streams = json.loads(output).get("streams") or []
        return any((stream or {}).get("codec_type") == "audio" for stream in streams)
    except Exception:
        return None


class AnchorVideoGenerationService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def start(self) -> None:
        await asyncio.to_thread(ensure_video_tables)
        await asyncio.to_thread(recover_interrupted_jobs)
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._worker_loop(), name="anchor-video-worker")

    async def stop(self) -> None:
        self._stop.set()
        for process in tuple(self._processes.values()):
            if process.returncode is None:
                process.terminate()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=8)
            except asyncio.TimeoutError:
                self._task.cancel()

    def create_job(self, owner: str | None, body: dict[str, Any]) -> VideoGenerationJob:
        config = resolve_generation_request(owner, body)
        if not config["video_gen_enabled"]:
            raise ValueError("Video generation is disabled in AI Defaults.")
        if not runtime_status().get("available"):
            raise RuntimeError(runtime_status().get("reason") or "Video runtime is unavailable.")
        from src.image_generation_defaults import resolve_configured_image_endpoint
        if resolve_configured_image_endpoint(owner) is None:
            raise ValueError("Configure and enable an Image Default before generating anchor-first video.")
        job = VideoGenerationJob(
            id=uuid.uuid4().hex,
            owner=owner or None,
            session_id=config["session_id"],
            status="queued",
            stage="queued",
            source_intent=config["source_intent"],
            request_config=config,
        )
        db = SessionLocal()
        try:
            db.add(job)
            db.commit()
            db.refresh(job)
            db.expunge(job)
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
            if job:
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
                job.status, job.stage, job.finished_at = "cancelled", "cancelled", _now()
            elif job.status == "running":
                job.cancellation_requested, job.stage = True, "cancelling"
            db.commit()
            db.refresh(job)
            db.expunge(job)
            return job
        finally:
            db.close()

    def _update(self, job_id: str, **fields: Any) -> None:
        db = SessionLocal()
        try:
            job = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job_id).first()
            if job:
                for key, value in fields.items():
                    setattr(job, key, value)
                db.commit()
        finally:
            db.close()

    def _cancelled(self, job_id: str) -> bool:
        db = SessionLocal()
        try:
            job = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job_id).first()
            return job is None or bool(job.cancellation_requested) or job.status == "cancelled"
        finally:
            db.close()

    def _claim_next(self) -> VideoGenerationJob | None:
        db = SessionLocal()
        try:
            job = db.query(VideoGenerationJob).filter(VideoGenerationJob.status == "queued", VideoGenerationJob.cancellation_requested == False).order_by(VideoGenerationJob.created_at.asc()).first()  # noqa: E712
            if not job:
                return None
            job.status, job.stage, job.started_at = "running", "planning_anchor", _now()
            db.commit()
            db.refresh(job)
            db.expunge(job)
            return job
        finally:
            db.close()

    def _copy_anchor(self, job_id: str, anchor_id: str, owner: str | None, work: Path) -> Path:
        db = SessionLocal()
        try:
            query = db.query(GalleryImage).filter(GalleryImage.id == anchor_id)
            if owner:
                query = query.filter(GalleryImage.owner == owner)
            image = query.first()
            if image is None:
                raise RuntimeError("The generated animation anchor is no longer available.")
            filename = str(image.filename or "")
        finally:
            db.close()
        name = Path(filename).name
        suffix = Path(name).suffix.lower()
        if name != filename or suffix not in _ALLOWED_ANCHOR_EXTS:
            raise RuntimeError("The generated anchor has an unsupported image format.")
        root = Path(GENERATED_IMAGES_DIR).resolve()
        source = (root / name).resolve()
        if root not in source.parents or not source.is_file():
            raise RuntimeError("The generated anchor file is unavailable.")
        target = work / f"anchor{suffix}"
        shutil.copy2(source, target)
        return target

    def _finalize(self, job_id: str, output: Path, log_path: Path) -> None:
        if not output.is_file() or output.stat().st_size <= 0 or output.suffix.lower() != ".mp4":
            raise RuntimeError("mlx-video did not create a usable MP4 output.")
        if _ffprobe_has_audio(output) is True:
            raise RuntimeError("The video output contains audio, but this feature requires muted clips.")
        digest = hashlib.sha256()
        with output.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        file_hash = digest.hexdigest()
        root = Path(GENERATED_IMAGES_DIR)
        root.mkdir(parents=True, exist_ok=True)
        final = root / f"{file_hash}.mp4"
        if final.exists() and final.stat().st_size > 0:
            output.unlink(missing_ok=True)
        else:
            os.replace(output, final)

        db = SessionLocal()
        try:
            job = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job_id).first()
            if job is None:
                raise RuntimeError("Video job disappeared before finalization.")
            if job.cancellation_requested:
                raise RuntimeError("Video generation was cancelled.")
            gallery_id = uuid.uuid4().hex
            config = dict(job.request_config or {})
            db.add(GalleryImage(
                id=gallery_id,
                filename=final.name,
                prompt=job.motion_prompt or "",
                model="mlx-video:LTX-2-distilled",
                size="512x512",
                quality="distilled",
                owner=job.owner,
                session_id=job.session_id,
                file_hash=file_hash,
                file_size=final.stat().st_size,
                width=512,
                height=512,
            ))
            db.add(VideoMediaMetadata(
                id=uuid.uuid4().hex,
                gallery_image_id=gallery_id,
                anchor_gallery_image_id=job.anchor_gallery_image_id,
                anchor_prompt=job.anchor_prompt or "",
                motion_prompt=job.motion_prompt or "",
                planner_model=job.planner_model,
                seed=int(config["video_seed"]),
                generation_params={k: v for k, v in config.items() if k not in {"source_intent", "session_id"}},
            ))
            job.gallery_image_id = gallery_id
            job.status, job.stage, job.finished_at = "succeeded", "succeeded", _now()
            job.log_path = str(log_path)
            db.commit()
        finally:
            db.close()

    async def _worker_loop(self) -> None:
        while not self._stop.is_set():
            if not runtime_status().get("available"):
                await asyncio.sleep(3)
                continue
            job = await asyncio.to_thread(self._claim_next)
            if job is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=0.75)
                except asyncio.TimeoutError:
                    pass
                continue
            await self._run_job(job.id)

    async def _run_job(self, job_id: str) -> None:
        db = SessionLocal()
        try:
            job = db.query(VideoGenerationJob).filter(VideoGenerationJob.id == job_id).first()
            if not job:
                return
            source_intent, owner, session_id, config = job.source_intent, job.owner, job.session_id, dict(job.request_config or {})
        finally:
            db.close()
        work, log_path = _job_dirs(job_id)
        try:
            if await asyncio.to_thread(self._cancelled, job_id):
                await asyncio.to_thread(self._update, job_id, status="cancelled", stage="cancelled", finished_at=_now())
                return
            anchor_prompt, planner_model = await _derive_prompt(owner, source_intent, "anchor")
            await asyncio.to_thread(self._update, job_id, anchor_prompt=anchor_prompt, planner_model=planner_model, stage="generating_anchor")
            if await asyncio.to_thread(self._cancelled, job_id):
                await asyncio.to_thread(self._update, job_id, status="cancelled", stage="cancelled", finished_at=_now())
                return

            from src.image_generation_defaults import generate_configured_image, resolve_configured_image_endpoint
            selected = resolve_configured_image_endpoint(owner)
            if selected is None:
                raise RuntimeError("Image Default is no longer configured for this account.")
            # This is the only prompt sent to image generation; it is derived above.
            anchor_result = await generate_configured_image(
                f"{anchor_prompt}\n{selected.model}\n512x512\nhigh",
                selected,
                session_id=session_id,
                owner=owner,
            )
            if anchor_result.get("error"):
                raise RuntimeError(str(anchor_result["error"]))
            anchor_id = str(anchor_result.get("image_id") or "")
            if not anchor_id:
                raise RuntimeError("Image generation completed without a Gallery anchor.")
            await asyncio.to_thread(self._update, job_id, anchor_gallery_image_id=anchor_id, stage="anchor_ready")
            # The client detects anchor_gallery_image_id, refreshes Gallery, and
            # notifies the user before the same durable job proceeds to animation.
            await asyncio.sleep(0)
            if await asyncio.to_thread(self._cancelled, job_id):
                await asyncio.to_thread(self._update, job_id, status="cancelled", stage="cancelled", finished_at=_now())
                return

            await asyncio.to_thread(self._update, job_id, stage="planning_motion")
            motion_prompt, planner_model = await _derive_prompt(owner, source_intent, "motion", anchor_prompt)
            await asyncio.to_thread(self._update, job_id, motion_prompt=motion_prompt, planner_model=planner_model, stage="animating")
            anchor_path = await asyncio.to_thread(self._copy_anchor, job_id, anchor_id, owner, work)
            output = work / "video.mp4"
            command = _build_command(anchor_path, motion_prompt, config, output)
            with log_path.open("wb") as log_file:
                process = await asyncio.create_subprocess_exec(*command, cwd=str(work), stdout=log_file, stderr=asyncio.subprocess.STDOUT)
                self._processes[job_id] = process
                cancelled = False
                while process.returncode is None:
                    if await asyncio.to_thread(self._cancelled, job_id):
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
            self._processes.pop(job_id, None)
            if cancelled or await asyncio.to_thread(self._cancelled, job_id):
                await asyncio.to_thread(self._update, job_id, status="cancelled", stage="cancelled", finished_at=_now())
                return
            if process.returncode != 0:
                raise RuntimeError("mlx-video failed while animating the anchor. Check the private job log for details.")
            await asyncio.to_thread(self._update, job_id, stage="finalizing")
            await asyncio.to_thread(self._finalize, job_id, output, log_path)
        except Exception as exc:
            if await asyncio.to_thread(self._cancelled, job_id):
                await asyncio.to_thread(self._update, job_id, status="cancelled", stage="cancelled", finished_at=_now())
            else:
                logger.warning("Anchor video job %s failed: %s", job_id, exc, exc_info=True)
                await asyncio.to_thread(self._update, job_id, status="failed", stage="failed", error_summary=_safe_error(exc), finished_at=_now())
        finally:
            self._processes.pop(job_id, None)
            shutil.rmtree(work, ignore_errors=True)

    def serialize(self, job: VideoGenerationJob) -> dict[str, Any]:
        value = {
            "id": job.id,
            "status": job.status,
            "stage": job.stage,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
            "error": job.error_summary,
            "anchor_gallery_id": job.anchor_gallery_image_id,
            "anchor_prompt": job.anchor_prompt,
            "motion_prompt": job.motion_prompt,
            "planner_model": job.planner_model,
            "gallery_id": job.gallery_image_id,
            "anchor_ready": bool(job.anchor_gallery_image_id),
        }
        db = SessionLocal()
        try:
            if job.anchor_gallery_image_id:
                anchor = db.query(GalleryImage).filter(GalleryImage.id == job.anchor_gallery_image_id).first()
                if anchor:
                    value["anchor_url"] = f"/api/generated-image/{anchor.filename}"
            if job.gallery_image_id:
                video = db.query(GalleryImage).filter(GalleryImage.id == job.gallery_image_id).first()
                if video:
                    value.update({"video_url": f"/api/generated-image/{video.filename}", "video_model": video.model, "video_size": video.size, "video_fps": 24, "video_frames": 241, "video_seed": (job.request_config or {}).get("video_seed")})
        finally:
            db.close()
        return value


_service: AnchorVideoGenerationService | None = None


def get_video_generation_service() -> AnchorVideoGenerationService:
    global _service
    if _service is None:
        _service = AnchorVideoGenerationService()
    return _service
