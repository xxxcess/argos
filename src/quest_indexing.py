"""Durable asynchronous Quest datasource indexing worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from datetime import timedelta
from typing import Any

from core.database import (
    ChatMessage,
    QuestEvidenceChunk,
    QuestIndexJob,
    QuestBibleBookSelection,
    QuestSource,
    QuestSourceArtifact,
    QuestSourceVersion,
    SessionLocal,
    utcnow_naive,
)
from src.quest_source_adapters import (
    UNSUPPORTED_PERMANENT,
    NormalizedTextUnit,
    adapter_for_source,
    content_hash,
    safe_excerpt,
)
from src.quest_vector_store import deterministic_chunk_doc_id, ensure_quest_vector_store_available, index_evidence_chunks, delete_where

logger = logging.getLogger(__name__)

JOB_STATUSES = {"queued", "running", "ready", "partial", "stale", "failed", "paused", "cancelled"}
JOB_TRIGGERS = {
    "source_connected", "manual_refresh", "scheduled_poll", "document_changed",
    "answer_verification", "retry",
}
TERMINAL_STATUSES = {"ready", "partial", "failed", "paused", "cancelled"}
_worker_task: asyncio.Task | None = None
_worker_stop: asyncio.Event | None = None


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True)


def _json_loads(value: str | None, fallback: Any = None) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else ({} if fallback is None else fallback)
    except Exception:
        return {} if fallback is None else fallback


def _event(db, *, quest_id: str, source: QuestSource, job: QuestIndexJob | None, status: str, message: str, counts: dict[str, Any] | None = None, error_code: str | None = None) -> None:
    if source.access_mode == "captain_only":
        visibility = "captain_private"
    else:
        visibility = "quest_shared"
    db.add(ChatMessage(
        id=uuid.uuid4().hex,
        session_id=quest_id,
        role="system",
        content=message,
        meta_data=_json_dumps({
            "event_type": "quest_indexing",
            "presentation": "background_task",
            "event_id": f"index-job:{job.id}" if job else f"index-job:source:{source.id}",
            "task_id": job.id if job else None,
            "title": "Source indexing",
            "subject": source.display_name,
            "quest_id": quest_id,
            "source_id": source.id,
            "source_version_id": job.source_version_id if job else None,
            "job_id": job.id if job else None,
            "status": status,
            "progress": {
                "completed": job.progress_completed if job else 0,
                "total": job.progress_total if job else 0,
                "unit": "chunks",
            },
            "result": counts or {},
            "started_at": job.started_at.isoformat() + "Z" if job and job.started_at else None,
            "completed_at": job.finished_at.isoformat() + "Z" if job and job.finished_at else None,
            "visibility": visibility,
            "safe_error": "Source indexing failed safely." if error_code else None,
            "safe_error_code": error_code,
            "source_access_mode": source.access_mode,
        }),
    ))


def enqueue_index_job(
    db,
    source: QuestSource,
    *,
    trigger: str = "manual_refresh",
    created_by: str | None = None,
    priority: int = 100,
    scope: dict[str, Any] | None = None,
) -> QuestIndexJob:
    trigger = trigger if trigger in JOB_TRIGGERS else "manual_refresh"
    job = QuestIndexJob(
        id=uuid.uuid4().hex,
        quest_id=source.session_id,
        source_id=source.id,
        status="queued",
        trigger=trigger,
        priority=priority,
        created_by=created_by,
        requested_at=utcnow_naive(),
        scope_json=_json_dumps(scope or {}),
    )
    source.index_state = "queued"
    db.add(job)
    _event(db, quest_id=source.session_id, source=source, job=job, status="queued", message=f'Argo queued indexing for "{source.display_name}".')
    return job


def has_active_bible_book_job(db, source_id: str) -> bool:
    return db.query(QuestIndexJob).filter(
        QuestIndexJob.source_id == source_id,
        QuestIndexJob.status.in_(("queued", "running")),
        QuestIndexJob.scope_json.like('%"kind": "bible_book_import"%'),
    ).count() > 0


def enqueue_bible_book_job(
    db,
    source: QuestSource,
    selection: QuestBibleBookSelection,
    *,
    created_by: str | None = None,
    selection_mode: str = "manual_selection",
    priority: int = 80,
) -> QuestIndexJob:
    return enqueue_index_job(
        db,
        source,
        trigger="manual_refresh",
        created_by=created_by,
        priority=priority,
        scope={
            "kind": "bible_book_import",
            "selection_id": selection.id,
            "translation": selection.translation,
            "testament": selection.testament,
            "book_id": selection.book_id,
            "selection_mode": selection_mode,
        },
    )


def enqueue_next_bible_book_job_if_idle(db, source: QuestSource) -> QuestIndexJob | None:
    if source.source_type != "bible" or has_active_bible_book_job(db, source.id):
        return None
    selection = db.query(QuestBibleBookSelection).filter(
        QuestBibleBookSelection.source_id == source.id,
        QuestBibleBookSelection.active == True,  # noqa: E712
        QuestBibleBookSelection.state == "queued",
    ).order_by(QuestBibleBookSelection.canonical_order.asc()).first()
    if not selection:
        return None
    return enqueue_bible_book_job(db, source, selection, created_by=source.captain_username)


def job_to_dict(job: QuestIndexJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "quest_id": job.quest_id,
        "source_id": job.source_id,
        "source_version_id": job.source_version_id,
        "status": job.status,
        "trigger": job.trigger,
        "attempt_count": job.attempt_count,
        "priority": job.priority,
        "requested_at": job.requested_at.isoformat() + "Z" if job.requested_at else None,
        "started_at": job.started_at.isoformat() + "Z" if job.started_at else None,
        "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
        "next_retry_at": job.next_retry_at.isoformat() + "Z" if job.next_retry_at else None,
        "progress_total": job.progress_total,
        "progress_completed": job.progress_completed,
        "records_discovered": job.records_discovered,
        "records_changed": job.records_changed,
        "artifacts_created": job.artifacts_created,
        "chunks_created": job.chunks_created,
        "chunks_indexed": job.chunks_indexed,
        "chunks_deduplicated": job.chunks_deduplicated,
        "error_code": job.error_code,
        "safe_error_message": job.safe_error_message,
        "scope": json.loads(job.scope_json or "{}") if getattr(job, "scope_json", None) else {},
    }


def _chunk_text(unit: NormalizedTextUnit, target: int = 1000, overlap: int = 200) -> list[tuple[str, str]]:
    text = re.sub(r"\n{3,}", "\n\n", (unit.content or "").strip())
    if not text:
        return []
    if getattr(unit, "preserve_boundaries", False):
        return [(text, unit.locator)]
    if len(text) <= target:
        return [(text, unit.locator)]
    if unit.artifact_kind == "youtube":
        target = 1800
        overlap = 250
    parts: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        end = min(len(text), pos + target)
        if end < len(text):
            cut = max(text.rfind("\n\n", pos, end), text.rfind(". ", pos, end), text.rfind("\n", pos, end))
            if cut > pos + 300:
                end = cut + 1
        chunk = text[pos:end].strip()
        if chunk:
            parts.append((chunk, unit.locator))
        if end >= len(text):
            break
        pos = max(end - overlap, pos + 1)
    return parts


def _safe_error(exc: Exception) -> tuple[str, str, bool]:
    raw = str(exc) or type(exc).__name__
    if "UNIQUE constraint failed:" in raw:
        constraint = raw.split("UNIQUE constraint failed:", 1)[1].split("\n", 1)[0].strip()
        code = f"unique_constraint_failed:{constraint}"
    else:
        code = raw.split(":", 1)[0].strip() or type(exc).__name__
    code = re.sub(r"[^a-zA-Z0-9_ -]", "_", code)[:80].lower().replace(" ", "_")
    permanent = code in UNSUPPORTED_PERMANENT or code in {"no_readable_text", "needs_browser", "transcript_unavailable"}
    messages = {
        "unsupported_file_type": "This file type is not supported for Quest indexing.",
        "encrypted_file": "This file appears to be encrypted or password protected.",
        "no_readable_text": "No readable text was found.",
        "needs_browser": "This page needs browser-rendered capture before it can be indexed.",
        "invalid_url": "The URL is not valid for server-side capture.",
        "unconstrained_email_scope": "Email source scope is too broad.",
        "transcript_unavailable": "No usable transcript is available for this video.",
        "bible_rate_limited": "The Bible provider is rate limiting requests. Retry later.",
        "bible_unavailable": "The Bible provider is temporarily unavailable.",
        "bible_invalid_response": "The Bible provider returned an invalid chapter response.",
        "bible_chapter_not_found": "The requested Bible chapter was not found.",
        "bible_scope_invalid": "The Bible source scope is invalid.",
        "bulk_import_disabled": "Full-testament Bible imports are disabled by this installation.",
        "database_source_unsupported": "Database/API Quest indexing is not enabled for this connector.",
        "vector_store_unavailable": "Quest vector storage is unavailable.",
        "upload_not_found": "The uploaded file could not be found on the server. Upload it again and retry.",
        "invalid_upload_id": "The file upload reference is invalid. Upload it again and retry.",
        "upload_outside_root": "The uploaded file reference failed storage safety checks.",
        "pdf_extract_failed": "PDF text extraction failed for this file.",
    }
    if code.startswith("unique_constraint_failed"):
        return code, "Quest indexing hit a duplicate ledger row while retrying. Retry after refreshing the source.", True
    if code.startswith("pdf_extract_failed"):
        return code, messages["pdf_extract_failed"], True
    return code, messages.get(code, "Quest indexing failed. Review the source configuration and retry."), permanent


async def process_index_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.query(QuestIndexJob).filter(QuestIndexJob.id == job_id).first()
        if not job or job.status not in {"queued", "running"}:
            return
        source = db.query(QuestSource).filter(QuestSource.id == job.source_id, QuestSource.session_id == job.quest_id).first()
        if not source:
            job.status = "failed"
            job.error_code = "source_missing"
            job.safe_error_message = "Quest source was removed."
            db.commit()
            return
        if source.status == "paused":
            job.status = "paused"
            source.index_state = "paused"
            db.commit()
            return

        now = utcnow_naive()
        job.status = "running"
        job.started_at = job.started_at or now
        job.attempt_count = (job.attempt_count or 0) + 1
        source.index_state = "running"
        db.commit()
        _event(db, quest_id=source.session_id, source=source, job=job, status="running", message=f'Argo started indexing "{source.display_name}".')
        db.commit()

        adapter = adapter_for_source(source)
        scope = json.loads(job.scope_json or "{}") if getattr(job, "scope_json", None) else {}
        refs = await adapter.discover(source, getattr(source, "checkpoint", None), job=job)
        job.records_discovered = len(refs)
        job.progress_total = max(1, len(refs))
        db.commit()
        if not refs:
            raise ValueError("no_records_discovered")
        ensure_quest_vector_store_available(source.session_id, adapter.visibility_lane(source))

        fp = hashlib.sha256(_json_dumps({"source": source.id, "refs": [r.key for r in refs], "at": now.isoformat()}).encode()).hexdigest()
        version = QuestSourceVersion(
            id=uuid.uuid4().hex,
            quest_source_id=source.id,
            version_label=f"v{len(source.versions) + 1}",
            source_fingerprint=fp,
            provenance_json=_json_dumps({"trigger": job.trigger, "source_type": source.source_type, "scope": scope}),
            captured_at=now,
        )
        db.add(version)
        db.flush()
        job.source_version_id = version.id

        append_only = source.source_type == "bible" and _json_loads(source.configuration_json, {}).get("versioning_mode") == "append_only"
        if append_only:
            book_id = str(scope.get("book_id") or "").upper()
            translation = str(scope.get("translation") or "web").upper()
            stale_artifacts = db.query(QuestSourceArtifact).filter(
                QuestSourceArtifact.source_id == source.id,
                QuestSourceArtifact.external_locator.like(f"BIBLE|{translation}|{book_id}|%"),
                QuestSourceArtifact.is_current == True,  # noqa: E712
            ).all()
            stale_ids = [a.id for a in stale_artifacts]
            if stale_ids:
                db.query(QuestSourceArtifact).filter(QuestSourceArtifact.id.in_(stale_ids)).update({"is_current": False}, synchronize_session=False)
                db.query(QuestEvidenceChunk).filter(QuestEvidenceChunk.artifact_id.in_(stale_ids)).update({"is_current": False}, synchronize_session=False)
                delete_where(source.session_id, {"$and": [{"source_id": source.id}, {"translation": str(scope.get("translation") or "web").lower()}, {"book_id": book_id}]})
            db.commit()
        else:
            db.query(QuestSourceArtifact).filter(QuestSourceArtifact.source_id == source.id, QuestSourceArtifact.is_current == True).update({"is_current": False}, synchronize_session=False)
            db.query(QuestEvidenceChunk).filter(QuestEvidenceChunk.source_id == source.id, QuestEvidenceChunk.is_current == True).update({"is_current": False}, synchronize_session=False)

        vector_rows: list[dict[str, Any]] = []
        for idx, ref in enumerate(refs):
            artifact = await adapter.capture(source, ref, job=job)
            artifact_id = uuid.uuid4().hex
            raw_ref = dict(artifact.raw_reference or {})
            raw_ref.pop("password", None)
            raw_ref.pop("token", None)
            text_for_hash = artifact.content or artifact.external_locator
            art = QuestSourceArtifact(
                id=artifact_id,
                quest_id=source.session_id,
                source_id=source.id,
                source_version_id=version.id,
                artifact_kind=artifact.artifact_kind,
                external_locator=artifact.external_locator,
                title=artifact.title,
                content_hash=content_hash(text_for_hash),
                captured_at=artifact.captured_at,
                updated_at=now,
                extraction_method=None,
                extraction_confidence=None,
                status=artifact.status,
                raw_reference_json=_json_dumps(raw_ref),
                normalized_text_path_or_text=None,
                visibility_lane=adapter.visibility_lane(source),
                is_current=False,
            )
            db.add(art)
            if artifact.status not in {"captured", "ready"}:
                job.progress_completed = idx + 1
                continue
            units = await adapter.extract(source, artifact, version.id, artifact_id, job=job)
            if units:
                art.status = "ready"
                art.extraction_method = units[0].extraction_method
                art.extraction_confidence = units[0].extraction_confidence
                art.normalized_text_path_or_text = units[0].content[:6000]
                art.visibility_lane = units[0].visibility_lane
            chunk_index = 0
            for unit in units:
                for chunk_text, locator in _chunk_text(unit):
                    chroma_id = deterministic_chunk_doc_id(source.session_id, source.id, version.id, artifact_id, chunk_index)
                    chash = content_hash(chunk_text)
                    chunk = QuestEvidenceChunk(
                        id=uuid.uuid4().hex,
                        quest_id=source.session_id,
                        source_id=source.id,
                        source_version_id=version.id,
                        artifact_id=artifact_id,
                        chunk_index=chunk_index,
                        chroma_document_id=chroma_id,
                        content_hash=chash,
                        locator=locator,
                        visibility_lane=unit.visibility_lane,
                        is_current=False,
                    )
                    db.add(chunk)
                    vector_rows.append({
                        "quest_id": source.session_id,
                        "visibility_lane": unit.visibility_lane,
                        "chroma_document_id": chroma_id,
                        "content": chunk_text,
                        "metadata": {
                            "quest_hash": hashlib.sha256(source.session_id.encode()).hexdigest()[:24],
                            "quest_id_hash": hashlib.sha256(source.session_id.encode()).hexdigest()[:24],
                            "source_id": source.id,
                            "source_version_id": version.id,
                            "artifact_id": artifact_id,
                            "chunk_id": chunk.id,
                            "locator": locator,
                            "title": unit.title,
                            "visibility_lane": unit.visibility_lane,
                            "artifact_kind": unit.artifact_kind,
                            "extraction_method": unit.extraction_method,
                            "is_current": True,
                            "excerpt": safe_excerpt(chunk_text),
                        } | dict(unit.raw_reference or {}),
                    })
                    job.chunks_created += 1
                    chunk_index += 1
            job.artifacts_created += 1
            job.records_changed += 1
            job.progress_completed = idx + 1
            if source.source_type == "bible" and scope.get("selection_id"):
                sel = db.query(QuestBibleBookSelection).filter(QuestBibleBookSelection.id == scope.get("selection_id")).first()
                if sel:
                    sel.state = "indexing"
                    sel.completed_chapters = min(idx + 1, sel.total_chapters or job.progress_total or idx + 1)
            db.commit()

        if not vector_rows:
            raise ValueError("no_readable_text")
        write_result = index_evidence_chunks(vector_rows)
        if write_result.unavailable and not write_result.indexed:
            raise RuntimeError(write_result.error or "vector_store_unavailable")
        job.chunks_indexed = write_result.indexed
        job.chunks_deduplicated = write_result.deduplicated

        db.query(QuestEvidenceChunk).filter(QuestEvidenceChunk.source_version_id == version.id).update({"is_current": True}, synchronize_session=False)
        db.query(QuestSourceArtifact).filter(QuestSourceArtifact.source_version_id == version.id).update({"is_current": True}, synchronize_session=False)
        source.current_version_id = version.id
        source.index_state = "ready"
        source.last_refreshed_at = now
        source.last_processed_at = now
        job.status = "ready"
        job.finished_at = utcnow_naive()
        if source.source_type == "bible" and scope.get("selection_id"):
            sel = db.query(QuestBibleBookSelection).filter(QuestBibleBookSelection.id == scope.get("selection_id")).first()
            if sel:
                sel.state = "indexed"
                sel.completed_chapters = sel.total_chapters
                sel.indexed_at = job.finished_at
                sel.last_error = None
            enqueue_next_bible_book_job_if_idle(db, source)
        db.commit()
        _event(
            db,
            quest_id=source.session_id,
            source=source,
            job=job,
            status="ready",
            message=f'Argo indexed {job.chunks_created} chunks from {job.artifacts_created} artifact{"s" if job.artifacts_created != 1 else ""}.',
            counts={"artifacts": job.artifacts_created, "chunks": job.chunks_created},
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        code, message, permanent = _safe_error(exc)
        job = db.query(QuestIndexJob).filter(QuestIndexJob.id == job_id).first()
        source = db.query(QuestSource).filter(QuestSource.id == job.source_id).first() if job else None
        if job:
            job.error_code = code
            job.safe_error_message = message
            if permanent or job.attempt_count >= 3:
                job.status = "failed"
                job.finished_at = utcnow_naive()
                if source:
                    if source.source_type == "bible":
                        completed = db.query(QuestBibleBookSelection).filter(
                            QuestBibleBookSelection.source_id == source.id,
                            QuestBibleBookSelection.state == "indexed",
                            QuestBibleBookSelection.active == True,  # noqa: E712
                        ).count()
                        source.index_state = "partial" if completed else "failed"
                    else:
                        source.index_state = "failed"
            else:
                job.status = "queued"
                delay = min(60 * (2 ** max(job.attempt_count - 1, 0)), 900)
                job.next_retry_at = utcnow_naive() + timedelta(seconds=delay)
                if source:
                    source.index_state = "queued"
            if source:
                scope = json.loads(job.scope_json or "{}") if getattr(job, "scope_json", None) else {}
                if source.source_type == "bible" and scope.get("selection_id"):
                    sel = db.query(QuestBibleBookSelection).filter(QuestBibleBookSelection.id == scope.get("selection_id")).first()
                    if sel:
                        sel.state = "failed" if job.status == "failed" else "queued"
                        sel.last_error = message
                    if job.status == "failed":
                        enqueue_next_bible_book_job_if_idle(db, source)
                _event(db, quest_id=source.session_id, source=source, job=job, status=job.status, message=f'Argo could not index "{source.display_name}": {message}', error_code=code)
        db.commit()
        logger.info("Quest index job %s failed with %s", job_id, code)
    finally:
        db.close()


def _claim_next_job() -> str | None:
    db = SessionLocal()
    try:
        now = utcnow_naive()
        stale_before = now - timedelta(minutes=30)
        db.query(QuestIndexJob).filter(
            QuestIndexJob.status == "running",
            QuestIndexJob.started_at < stale_before,
        ).update({"status": "queued", "next_retry_at": now}, synchronize_session=False)
        bible_stale_before = now - timedelta(minutes=5)
        db.query(QuestIndexJob).filter(
            QuestIndexJob.status == "running",
            QuestIndexJob.scope_json.like('%"kind": "bible_book_import"%'),
            QuestIndexJob.progress_completed == 0,
            QuestIndexJob.started_at < bible_stale_before,
        ).update({"status": "queued", "next_retry_at": now}, synchronize_session=False)
        bible_running = db.query(QuestIndexJob).filter(
            QuestIndexJob.status == "running",
            QuestIndexJob.scope_json.like('%"kind": "bible_book_import"%'),
        ).count() > 0
        candidates = (
            db.query(QuestIndexJob)
            .filter(QuestIndexJob.status == "queued")
            .filter((QuestIndexJob.next_retry_at == None) | (QuestIndexJob.next_retry_at <= now))  # noqa: E711
            .order_by(QuestIndexJob.priority.asc(), QuestIndexJob.requested_at.asc())
            .limit(20)
            .all()
        )
        job = None
        for candidate in candidates:
            is_bible = '"kind": "bible_book_import"' in (candidate.scope_json or "")
            if bible_running and is_bible:
                continue
            job = candidate
            break
        if not job:
            db.commit()
            return None
        job.status = "running"
        job.started_at = now
        db.commit()
        return job.id
    finally:
        db.close()


async def _worker_loop(concurrency: int = 2) -> None:
    global _worker_stop
    sem = asyncio.Semaphore(max(1, concurrency))
    active: set[asyncio.Task] = set()
    _worker_stop = asyncio.Event()
    while not _worker_stop.is_set():
        job_id = _claim_next_job()
        if not job_id:
            await asyncio.sleep(2.0)
            continue
        await sem.acquire()
        task = asyncio.create_task(process_index_job(job_id))
        active.add(task)
        task.add_done_callback(lambda t: (active.discard(t), sem.release()))
    if active:
        await asyncio.gather(*active, return_exceptions=True)


def start_quest_index_worker(app=None, *, concurrency: int = 2) -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _worker_task = loop.create_task(_worker_loop(concurrency=concurrency))
    if app is not None:
        try:
            app.state.quest_index_worker = _worker_task
        except Exception:
            pass


async def stop_quest_index_worker() -> None:
    global _worker_stop, _worker_task
    if _worker_stop:
        _worker_stop.set()
    if _worker_task:
        await asyncio.gather(_worker_task, return_exceptions=True)
    _worker_task = None
