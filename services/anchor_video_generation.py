"""Durable anchor-first local depth/parallax video generation.

A planner derives two production prompts from the user's intent. The first goes
to the selected Image Default and produces a stable Gallery anchor. The second
is used only to choose a subtle deterministic camera path for the local renderer.
The raw intent never reaches image generation or rendering unchanged.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, JSON, String, Text

from core.database import Base, GalleryImage, SessionLocal, engine
from services.depth_parallax_renderer import RenderCancelled, render_depth_parallax_video
from services.video_runtime_setup import prepare_runtime
from src.anchor_video_settings import VideoSettingsError, resolve_generation_request
from src.constants import DATA_DIR, GENERATED_IMAGES_DIR

logger = logging.getLogger(__name__)
_TMP_ROOT = Path(DATA_DIR) / "video_jobs"
_ALLOWED_ANCHOR_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


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
    error_summary = Column(String(700), nullable=True)
    created_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("ix_video_generation_owner_status_created", "owner", "status", "created_at"),)


class VideoMediaMetadata(Base):
    __tablename__ = "video_media_metadata"

    id = Column(String, primary_key=True)
    gallery_image_id = Column(String, ForeignKey("gallery_images.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    anchor_gallery_image_id = Column(String, ForeignKey("gallery_images.id", ondelete="SET NULL"), nullable=True, index=True)
    anchor_prompt = Column(Text, nullable=False)
    motion_prompt = Column(Text, nullable=False)
    planner_model = Column(String, nullable=True)
    duration_seconds = Column(Integer, nullable=False, default=10)
    fps = Column(Integer, nullable=False, default=24)
    frame_count = Column(Integer, nullable=False, default=240)
    seed = Column(Integer, nullable=False)
    generation_params = Column(JSON, nullable=False, default=dict)


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


def runtime_status() -> dict[str, Any]:
    prepared = prepare_runtime()
    return {
        **prepared,
        "mode": "anchor_first_depth_parallax",
        "width": 512,
        "height": 512,
        "duration_seconds": 10,
        "fps": 24,
        "audio": False,
    }


def _safe_error(error: Exception | str) -> str:
    return (re.sub(r"\s+", " ", str(error or "Video generation failed.")).strip() or "Video generation failed.")[:700]


def _normalized(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _derived_prompt(source: str, candidate: str, label: str) -> str:
    value = re.sub(r"\s+", " ", str(candidate or "")).strip().strip('"')
    if len(value) < 80:
        raise RuntimeError(f"The {label} planner returned an incomplete production prompt.")
    original = _normalized(source)
    if original and (original == _normalized(value) or original in _normalized(value)):
        raise RuntimeError(f"The {label} planner repeated the raw intent instead of deriving a production prompt.")
    return value[:3000]


async def _plan(owner: str | None, source: str, kind: str, anchor_prompt: str | None = None) -> tuple[str, str]:
    from src.endpoint_resolver import resolve_endpoint
    from src.llm_core import llm_call_async

    url, model, headers = resolve_endpoint("utility", owner=owner)
    if not url or not model:
        raise RuntimeError("Configure a Utility Model or Default Chat Model for video planning.")
    if kind == "anchor":
        system = (
            "You are a film art director. Rewrite a user's high-level video intent as one detailed still-image art-direction prompt. "
            "Specify subject identity, composition, environment, lens feel, framing, lighting, materials, palette, and atmosphere. "
            "Do not mention motion, duration, audio, captions, or cuts. Do not copy the user's wording. Return only the prompt."
        )
        user = "Video intent to reinterpret:\n" + source
    else:
        system = (
            "You are a cinematographer planning subtle depth-aware camera movement from an existing still image. "
            "Return one detailed continuous-shot instruction that preserves the anchor's subject, composition, environment, lighting, and identity. "
            "Use only a gentle pan, tilt, push-in, pull-back, or drift. Do not introduce new action, cuts, audio, text, or a new scene. "
            "Do not copy the user's wording. Return only the motion direction."
        )
        user = "Original intent:\n" + source + "\n\nAnchor art direction:\n" + (anchor_prompt or "")
    response = await llm_call_async(url, model, [{"role": "system", "content": system}, {"role": "user", "content": user}], headers=headers, timeout=120)
    return _derived_prompt(source, response, kind), str(model)


def _work_dir(job_id: str) -> Path:
    path = _TMP_ROOT / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _anchor_file(anchor_id: str, owner: str | None, work: Path) -> Path:
    db = SessionLocal()
    try:
        query = db.query(GalleryImage).filter(GalleryImage.id == anchor_id)
        if owner:
            query = query.filter(GalleryImage.owner == owner)
        anchor = query.first()
        if anchor is None:
            raise RuntimeError("The generated animation anchor is no longer available.")
        filename = Path(str(anchor.filename or "")).name
    finally:
        db.close()
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_ANCHOR_EXTS:
        raise RuntimeError("The generated animation anchor is not a supported image file.")
    source = Path(GENERATED_IMAGES_DIR) / filename
    if not source.is_file():
        raise RuntimeError("The generated animation anchor file is missing.")
    destination = work / f"anchor{suffix}"
    shutil.copy2(source, destination)
    return destination


def _gallery_filename(image_id: str | None, owner: str | None) -> str:
    if not image_id:
        return ""
    db = SessionLocal()
    try:
        query = db.query(GalleryImage).filter(GalleryImage.id == image_id)
        if owner:
            query = query.filter(GalleryImage.owner == owner)
        image = query.first()
        return Path(str(image.filename or "")).name if image else ""
    finally:
        db.close()


class AnchorVideoGenerationService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        await asyncio.to_thread(ensure_video_tables)
        await asyncio.to_thread(recover_interrupted_jobs)
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._worker(), name="depth-parallax-video-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=8)
            except asyncio.TimeoutError:
                self._task.cancel()

    def create_job(self, owner: str | None, body: dict[str, Any], *, allow_disabled: bool = False) -> VideoGenerationJob:
        config = resolve_generation_request(owner, body)
        if not allow_disabled and not config["video_gen_enabled"]:
            raise ValueError("Video generation is disabled in AI Defaults.")
        status = runtime_status()
        if not status.get("available"):
            raise RuntimeError(status.get("reason") or "The local depth-video engine is unavailable.")
        from src.image_generation_defaults import resolve_configured_image_endpoint
        if resolve_configured_image_endpoint(owner) is None:
            raise ValueError("Configure and enable an Image Default before generating video anchors.")
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
            db.commit(); db.refresh(job); db.expunge(job)
            return job
        finally:
            db.close()

    def serialize(self, job: VideoGenerationJob) -> dict[str, Any]:
        anchor_filename = _gallery_filename(job.anchor_gallery_image_id, job.owner)
        video_filename = _gallery_filename(job.gallery_image_id, job.owner)
        return {
            "job_id": job.id,
            "status": job.status,
            "stage": job.stage,
            "error": job.error_summary,
            "anchor_ready": bool(job.anchor_gallery_image_id),
            "anchor_url": f"/api/generated-image/{anchor_filename}" if anchor_filename else None,
            "video_url": f"/api/generated-image/{video_filename}" if video_filename else None,
            "anchor_prompt": job.anchor_prompt,
            "motion_prompt": job.motion_prompt,
            "planner_model": job.planner_model,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        }

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

    def _claim(self) -> VideoGenerationJob | None:
        db = SessionLocal()
        try:
            job = db.query(VideoGenerationJob).filter(VideoGenerationJob.status == "queued", VideoGenerationJob.cancellation_requested == False).order_by(VideoGenerationJob.created_at.asc()).first()  # noqa: E712
            if not job:
                return None
            job.status, job.stage, job.started_at = "running", "planning_anchor", _now()
            db.commit(); db.refresh(job); db.expunge(job)
            return job
        finally:
            db.close()

    async def _worker(self) -> None:
        while not self._stop.is_set():
            job = await asyncio.to_thread(self._claim)
            if job is None:
                await asyncio.sleep(0.7)
                continue
            await self._run(job)

    async def _run(self, job: VideoGenerationJob) -> None:
        work = _work_dir(job.id)
        try:
            if self._cancelled(job.id):
                self._update(job.id, status="cancelled", stage="cancelled", finished_at=_now())
                return
            anchor_prompt, planner_model = await _plan(job.owner, job.source_intent, "anchor")
            self._update(job.id, stage="generating_anchor", anchor_prompt=anchor_prompt, planner_model=planner_model)
            from src.image_generation_defaults import generate_configured_image, resolve_configured_image_endpoint
            endpoint = resolve_configured_image_endpoint(job.owner)
            if endpoint is None:
                raise RuntimeError("The Image Default is no longer configured.")
            image = await generate_configured_image(
                f"{anchor_prompt}\n{endpoint.model}\n512x512\nhigh",
                endpoint,
                session_id=job.session_id,
                owner=job.owner,
            )
            if image.get("error") or not image.get("image_id"):
                raise RuntimeError(image.get("error") or "The Image Default did not return a Gallery anchor.")
            anchor_id = str(image["image_id"])
            self._update(job.id, stage="anchor_ready", anchor_gallery_image_id=anchor_id)
            if self._cancelled(job.id):
                self._update(job.id, status="cancelled", stage="cancelled", finished_at=_now())
                return
            motion_prompt, motion_model = await _plan(job.owner, job.source_intent, "motion", anchor_prompt)
            self._update(job.id, stage="rendering_depth_parallax", motion_prompt=motion_prompt, planner_model=motion_model)
            prepared = prepare_runtime()
            if not prepared.get("available"):
                raise RuntimeError(prepared.get("reason") or "The local depth-video engine is unavailable.")
            anchor_path = await asyncio.to_thread(_anchor_file, anchor_id, job.owner, work)
            draft = work / "video.mp4"
            await asyncio.to_thread(
                render_depth_parallax_video,
                anchor_path=anchor_path,
                output_path=draft,
                model_path=Path(str(prepared["model_path"])),
                ffmpeg_path=str(prepared["ffmpeg"]),
                motion_prompt=motion_prompt,
                seed=int(job.request_config["video_seed"]),
                source_fps=int(job.request_config["video_source_fps"]),
                duration_seconds=int(job.request_config["video_duration_seconds"]),
                target_fps=int(job.request_config["video_fps"]),
                cancelled=lambda: self._cancelled(job.id),
            )
            if self._cancelled(job.id):
                self._update(job.id, status="cancelled", stage="cancelled", finished_at=_now())
                return
            self._update(job.id, stage="saving_gallery")
            digest = hashlib.sha256(draft.read_bytes()).hexdigest()[:24]
            filename = f"{digest}.mp4"
            target = Path(GENERATED_IMAGES_DIR) / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(draft, target)
            gallery_id = str(uuid.uuid4())
            db = SessionLocal()
            try:
                db.add(GalleryImage(
                    id=gallery_id,
                    filename=filename,
                    prompt=motion_prompt,
                    model="local-depth-parallax:Depth-Anything-V2-Small",
                    size="512x512",
                    quality="depth-parallax",
                    session_id=job.session_id,
                    owner=job.owner,
                ))
                db.add(VideoMediaMetadata(
                    id=str(uuid.uuid4()),
                    gallery_image_id=gallery_id,
                    anchor_gallery_image_id=anchor_id,
                    anchor_prompt=anchor_prompt,
                    motion_prompt=motion_prompt,
                    planner_model=motion_model,
                    duration_seconds=10,
                    fps=24,
                    frame_count=240,
                    seed=int(job.request_config["video_seed"]),
                    generation_params={"provider": "depth_parallax", "source_fps": 6, "depth_model": "Depth-Anything-V2-Small"},
                ))
                db.commit()
            finally:
                db.close()
            self._update(job.id, status="succeeded", stage="succeeded", gallery_image_id=gallery_id, finished_at=_now())
        except RenderCancelled:
            self._update(job.id, status="cancelled", stage="cancelled", finished_at=_now())
        except Exception as exc:
            logger.exception("Depth-parallax video job %s failed", job.id)
            self._update(job.id, status="failed", stage="failed", error_summary=_safe_error(exc), finished_at=_now())
        finally:
            shutil.rmtree(work, ignore_errors=True)


_service: AnchorVideoGenerationService | None = None


def get_video_generation_service() -> AnchorVideoGenerationService:
    global _service
    if _service is None:
        _service = AnchorVideoGenerationService()
    return _service
