"""Argos Venture Quest APIs."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from core.database import (
    ChatMessage,
    Document,
    EmailAccount,
    GalleryImage,
    QuestArtifactProposal,
    QuestBearing,
    QuestInvitation,
    QuestBibleBookSelection,
    QuestMember,
    QuestMemoryEntry,
    QuestMemoryState,
    QuestSource,
    QuestSourceCheckpoint,
    QuestSourceVersion,
    QuestEvidenceChunk,
    QuestIndexJob,
    QuestSourceArtifact,
    QuestSynthesisJob,
    Session as DbSession,
    SessionLocal,
    UserNotification,
    utcnow_naive,
)
from core.session_manager import SessionManager
from src.auth_helpers import effective_user, require_user
from src.runtime_profile import require_venture_runtime, runtime_summary
from src.venture_synthesis import run_argo_synthesis, set_synthesis_paused
from src.venture_auth import (
    get_quest_role,
    get_visible_quest_ids,
    require_quest_captain,
    require_quest_member,
    require_quest_source_read_access,
)


SOURCE_TYPES = {"website", "file", "document", "database", "email", "youtube", "bible"}
SOURCE_MODES = {"static", "dynamic"}
REFRESH_STRATEGIES = {"manual", "scheduled", "event", "live_verify"}
SOURCE_ACCESS_MODES = {"captain_only", "shared_read", "shared_summaries"}
SOURCE_STATUSES = {"active", "paused", "error", "archived"}
INVITE_STATUSES = {"pending", "accepted", "declined", "revoked", "expired"}
MEMORY_VISIBILITIES = {"captain_private", "quest_shared"}
MEMORY_STATES = {"provisional", "confirmed", "stale", "contradicted", "superseded", "retired"}
MEMORY_CATEGORIES = {
    "bearing",
    "decision",
    "constraint",
    "open_question",
    "evidence",
    "finding",
    "contradiction",
    "risk",
    "source_update",
    "artifact_reference",
    "next_step",
}


def _json_loads(value: str | None, fallback):
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True)


def _timeline_meta(event_type: str, *, title: str | None = None, event_id: str | None = None, **fields) -> str:
    payload = {
        "event_type": event_type,
        "presentation": "timeline_event",
        "event_id": event_id or f"{event_type}:{uuid.uuid4().hex}",
        "title": title or event_type.replace("_", " ").title(),
        **fields,
    }
    return _json_dumps(payload)


def _run_synthesis_job_background(job_id: str | None) -> None:
    if not job_id:
        return
    try:
        import asyncio
        from src.venture_synthesis import process_synthesis_job
        asyncio.run(process_synthesis_job(job_id))
    except Exception:
        import logging
        logging.getLogger(__name__).warning("Quest synthesis background trigger failed for %s", job_id, exc_info=True)


def _artifact_copy_id(proposal_id: str, username: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"argos-venture-artifact-copy:{proposal_id}:{username}").hex


def _ensure_member_artifact_copies(db, *, quest_id: str, proposal: QuestArtifactProposal, source_doc: Document) -> list[str]:
    copied_for: list[str] = []
    members = db.query(QuestMember).filter(QuestMember.session_id == quest_id).all()
    for member in members:
        if not member.username or member.username == source_doc.owner:
            continue
        copy_id = _artifact_copy_id(proposal.id, member.username)
        existing = db.query(Document).filter(Document.id == copy_id).first()
        if existing:
            existing.session_id = quest_id
            existing.title = source_doc.title
            existing.language = source_doc.language
            existing.current_content = source_doc.current_content
            existing.version_count = source_doc.version_count
            existing.is_active = True
            existing.archived = False
            existing.owner = member.username
            existing.updated_at = utcnow_naive()
        else:
            db.add(Document(
                id=copy_id,
                session_id=quest_id,
                title=source_doc.title,
                language=source_doc.language,
                current_content=source_doc.current_content,
                version_count=source_doc.version_count,
                is_active=True,
                archived=False,
                owner=member.username,
            ))
        copied_for.append(member.username)
    return copied_for


def _user_is_admin(request: Request, username: str) -> bool:
    if not username:
        return True
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if auth_mgr is None:
        return False
    try:
        return bool(auth_mgr.is_admin(username))
    except Exception:
        return False


def _regular_user_exists(request: Request, username: str) -> bool:
    auth_mgr = getattr(request.app.state, "auth_manager", None)
    if auth_mgr is None:
        return bool(username)
    users = getattr(auth_mgr, "users", {}) or {}
    row = users.get(username)
    return bool(row) and not bool(row.get("is_admin"))


def _notify(db, user: str, key: str, **fields) -> UserNotification:
    row = db.query(UserNotification).filter(UserNotification.deterministic_key == key).first()
    now = utcnow_naive()
    if row is None:
        row = UserNotification(
            id=uuid.uuid4().hex,
            user_id=user,
            owner=user,
            deterministic_key=key,
            category=fields.get("category", "inbox"),
            severity=fields.get("severity", "info"),
            state=fields.get("state", "unread"),
            title=fields.get("title", ""),
            message=fields.get("message", ""),
            resource_type=fields.get("resource_type"),
            resource_id=fields.get("resource_id"),
            actions_json=_json_dumps(fields.get("actions", [])),
        )
        db.add(row)
    else:
        row.category = fields.get("category", row.category)
        row.severity = fields.get("severity", row.severity)
        row.state = fields.get("state", row.state)
        row.title = fields.get("title", row.title)
        row.message = fields.get("message", row.message)
        row.resource_type = fields.get("resource_type", row.resource_type)
        row.resource_id = fields.get("resource_id", row.resource_id)
        row.actions_json = _json_dumps(fields.get("actions", _json_loads(row.actions_json, [])))
        row.updated_at = now
        row.archived_at = None
    return row


def _session_to_quest(row: DbSession, role: str | None = None) -> dict:
    return {
        "id": row.id,
        "title": row.name,
        "captain_username": row.owner,
        "role": role,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


def _bearing_to_dict(row: QuestBearing | None) -> dict:
    if row is None:
        return {}
    return {
        "session_id": row.session_id,
        "title": row.title,
        "exploration_goal": row.exploration_goal,
        "initial_question": row.initial_question,
        "desired_outcome": row.desired_outcome,
        "constraints": row.constraints,
        "time_horizon": row.time_horizon,
        "current_summary": row.current_summary,
        "open_questions": _json_loads(row.open_questions_json, []),
        "next_bearing": row.next_bearing,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
    }


def _source_to_dict(row: QuestSource, access: str | None = None) -> dict:
    config = _json_loads(row.configuration_json, {})
    if access == "summary":
        config = {"summary": config.get("summary", "") if isinstance(config, dict) else ""}
    elif access is None:
        config = {}
    index = _source_index_status(row)
    data = {
        "id": row.id,
        "session_id": row.session_id,
        "source_type": row.source_type,
        "source_mode": row.source_mode,
        "refresh_strategy": getattr(row, "refresh_strategy", None) or "manual",
        "display_name": row.display_name,
        "access_mode": row.access_mode,
        "configuration": config,
        "status": row.status,
        "index_state": index.get("index_state"),
        "index_status": index,
        "last_refreshed_at": row.last_refreshed_at.isoformat() + "Z" if row.last_refreshed_at else None,
        "last_processed_at": row.last_processed_at.isoformat() + "Z" if row.last_processed_at else None,
    }
    return data


def _source_index_status(row: QuestSource) -> dict:
    db = SessionLocal()
    try:
        if row.source_type == "bible":
            selections = db.query(QuestBibleBookSelection).filter(
                QuestBibleBookSelection.source_id == row.id,
                QuestBibleBookSelection.active == True,  # noqa: E712
            ).order_by(QuestBibleBookSelection.canonical_order.asc()).all()
            job_states = _bible_book_job_states(db, row.id)
            visible_books = [_selection_to_dict(s, job_states.get(s.book_id) if s.state in {"queued", "indexing", "paused", "failed"} else None) for s in selections]
            aggregate = _bible_aggregate(selections)
            if any(b["state"] == "indexing" for b in visible_books):
                aggregate = "indexing"
            active_jobs = db.query(QuestIndexJob).filter(
                QuestIndexJob.source_id == row.id,
                QuestIndexJob.status.in_(("running", "queued", "failed", "paused")),
            ).order_by(QuestIndexJob.requested_at.asc()).all()
            job = (
                next((j for j in active_jobs if j.status == "running"), None)
                or next((j for j in active_jobs if j.status == "queued" and not j.error_code), None)
                or next((j for j in active_jobs if j.status == "queued"), None)
                or next((j for j in active_jobs if j.status == "paused"), None)
                or next((j for j in active_jobs if j.status == "failed"), None)
            ) or (
                db.query(QuestIndexJob)
                .filter(QuestIndexJob.source_id == row.id)
                .order_by(QuestIndexJob.requested_at.desc())
                .first()
            )
            chunks = db.query(QuestEvidenceChunk).filter(
                QuestEvidenceChunk.source_id == row.id,
                QuestEvidenceChunk.is_current == True,  # noqa: E712
            ).count()
            return {
                "index_state": aggregate,
                "last_successful_index_at": row.last_processed_at.isoformat() + "Z" if row.last_processed_at else None,
                "current_job": {
                    "id": job.id,
                    "status": job.status,
                    "progress_total": 1,
                    "progress_completed": 0 if job.status in {"queued", "running"} else 1,
                    "chunks_indexed": job.chunks_indexed,
                    "safe_error_message": job.safe_error_message,
                    "error_code": job.error_code,
                    "next_retry_at": job.next_retry_at.isoformat() + "Z" if job.next_retry_at else None,
                } if job else None,
                "has_active_job": bool(job and job.status in {"queued", "running"}),
                "artifact_count": sum(1 for s in selections if s.state == "indexed"),
                "chunk_count": chunks,
                "freshness": aggregate,
                "warning": job.safe_error_message if job and job.status == "failed" else None,
                "books": visible_books,
            }
        job = (
            db.query(QuestIndexJob)
            .filter(QuestIndexJob.source_id == row.id)
            .order_by(QuestIndexJob.requested_at.desc())
            .first()
        )
        artifacts = db.query(QuestSourceArtifact).filter(
            QuestSourceArtifact.source_id == row.id,
            QuestSourceArtifact.is_current == True,  # noqa: E712
        ).count()
        chunks = db.query(QuestEvidenceChunk).filter(
            QuestEvidenceChunk.source_id == row.id,
            QuestEvidenceChunk.is_current == True,  # noqa: E712
        ).count()
        active = job and job.status in {"queued", "running"}
        return {
            "index_state": getattr(row, "index_state", None) or (job.status if job else "not_indexed"),
            "last_successful_index_at": row.last_processed_at.isoformat() + "Z" if row.last_processed_at else None,
            "current_job": {
                "id": job.id,
                "status": job.status,
                "progress_total": job.progress_total,
                "progress_completed": job.progress_completed,
                "chunks_indexed": job.chunks_indexed,
                "safe_error_message": job.safe_error_message,
                "error_code": job.error_code,
                "next_retry_at": job.next_retry_at.isoformat() + "Z" if job.next_retry_at else None,
            } if job else None,
            "has_active_job": bool(active),
            "artifact_count": artifacts,
            "chunk_count": chunks,
            "freshness": "ready" if chunks else ((job.status if job else "not_indexed")),
            "warning": job.safe_error_message if job and job.status == "failed" else None,
        }
    finally:
        db.close()


def _proposal_to_dict(row: QuestArtifactProposal, include_document: bool = False) -> dict:
    data = {
        "id": row.id,
        "session_id": row.session_id,
        "document_id": row.document_id,
        "status": row.status,
        "artifact_type": row.artifact_type,
        "title": row.title,
        "summary": row.summary,
        "evidence_refs": _json_loads(row.evidence_refs_json, []),
        "evidence_fingerprint": row.evidence_fingerprint,
        "source_version_refs": _json_loads(row.source_version_refs_json, []),
        "claim_key": row.claim_key,
        "evidence_chunk_refs": _json_loads(row.evidence_chunk_refs_json, []),
        "retrieval_run_ids": _json_loads(row.retrieval_run_ids_json, []),
        "synthesis": _json_loads(row.synthesis_json, {}),
        "revision_number": row.revision_number,
        "supersedes_artifact_id": row.supersedes_artifact_id,
        "visibility": row.visibility,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "reviewed_at": row.reviewed_at.isoformat() + "Z" if row.reviewed_at else None,
        "published_at": row.published_at.isoformat() + "Z" if row.published_at else None,
        "declined_at": row.declined_at.isoformat() + "Z" if row.declined_at else None,
    }
    if include_document and row.document:
        data["document"] = {
            "id": row.document.id,
            "title": row.document.title,
            "language": row.document.language,
            "content": row.document.current_content,
        }
    return data


def _memory_to_dict(row: QuestMemoryEntry) -> dict:
    provenance = _json_loads(row.provenance_json, {})
    evidence = provenance.get("evidence") if isinstance(provenance, dict) else []
    citations = provenance.get("citations") if isinstance(provenance, dict) else []
    return {
        "id": row.id,
        "session_id": row.session_id,
        "category": row.category,
        "visibility": row.visibility,
        "state": row.state,
        "title": row.title,
        "content": row.content,
        "confidence": row.confidence,
        "provenance": provenance,
        "evidence": evidence if isinstance(evidence, list) else [],
        "citations": citations if isinstance(citations, list) else [],
        "source_version_refs": _json_loads(row.source_version_refs_json, []),
        "origin_event_ids": _json_loads(row.origin_event_ids_json, []),
        "artifact_id": row.artifact_id,
        "artifact_revision_number": row.artifact_revision_number,
        "evidence_chunk_refs": _json_loads(row.evidence_chunk_refs_json, []),
        "claim_key": row.claim_key,
        "superseded_by_memory_id": row.superseded_by_memory_id,
        "created_by": row.created_by,
        "supersedes_id": row.supersedes_id,
        "pinned": row.pinned,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
        "valid_until": row.valid_until.isoformat() + "Z" if row.valid_until else None,
    }


def _synthesis_job_to_safe_dict(row: QuestSynthesisJob) -> dict:
    result = _json_loads(row.result_json, {})
    reason = result.get("reason") if isinstance(result, dict) else None
    return {
        "id": row.id,
        "trigger": row.trigger,
        "status": row.status,
        "created_by": row.created_by,
        "artifact_proposal_id": row.artifact_proposal_id,
        "attempt_count": row.attempt_count,
        "requested_at": row.requested_at.isoformat() + "Z" if row.requested_at else None,
        "started_at": row.started_at.isoformat() + "Z" if row.started_at else None,
        "finished_at": row.finished_at.isoformat() + "Z" if row.finished_at else None,
        "next_retry_at": row.next_retry_at.isoformat() + "Z" if row.next_retry_at else None,
        "safe_error_code": row.safe_error_code,
        "safe_error_message": row.safe_error_message,
        "reason": reason,
    }


class QuestCreate(BaseModel):
    title: str = Field(min_length=1)
    exploration_goal: str = Field(min_length=1)
    initial_question: str | None = None
    desired_outcome: str | None = None
    constraints: str | None = None
    time_horizon: str | None = None
    open_questions: list[str] = Field(default_factory=list)
    next_bearing: str | None = None
    endpoint_url: str = ""
    model: str = ""
    source: dict[str, Any]
    shipmates: list[str] = Field(default_factory=list)


class BearingUpdate(BaseModel):
    title: str | None = None
    exploration_goal: str | None = None
    initial_question: str | None = None
    desired_outcome: str | None = None
    constraints: str | None = None
    time_horizon: str | None = None
    current_summary: str | None = None
    open_questions: list[str] | None = None
    next_bearing: str | None = None


class InvitationCreate(BaseModel):
    invitee_username: str
    expires_in_days: int = 14


class SourceCreate(BaseModel):
    source_type: str
    source_mode: str | None = None
    refresh_strategy: str | None = None
    display_name: str
    access_mode: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)


class BibleBooksPayload(BaseModel):
    translation: str = "web"
    selection_mode: str = "manual_selection"
    books: list[str] = Field(default_factory=list)


class ProposalCreate(BaseModel):
    artifact_type: str = "insight"
    title: str
    summary: str = ""
    evidence_refs: list[Any] = Field(default_factory=list)
    source_version_refs: list[Any] = Field(default_factory=list)
    evidence_fingerprint: str | None = None


class MemoryCreate(BaseModel):
    category: str
    visibility: str = "captain_private"
    state: str = "provisional"
    title: str
    content: str = ""
    confidence: str = "low"
    provenance: dict[str, Any] = Field(default_factory=dict)
    source_version_refs: list[Any] = Field(default_factory=list)
    origin_event_ids: list[Any] = Field(default_factory=list)


class MemoryPatch(BaseModel):
    state: str | None = None
    title: str | None = None
    content: str | None = None
    confidence: str | None = None
    visibility: str | None = None
    pinned: bool | None = None


def _validate_source_payload(db, captain: str, payload: SourceCreate) -> tuple[str, str, str]:
    source_type = payload.source_type
    if source_type not in SOURCE_TYPES:
        raise HTTPException(400, "Unsupported source type")
    source_mode = payload.source_mode or ("dynamic" if source_type in {"email", "database"} else "static")
    if source_mode not in SOURCE_MODES:
        raise HTTPException(400, "Unsupported source mode")
    if source_type == "email" and source_mode != "dynamic":
        raise HTTPException(400, "Email sources must be dynamic")
    if source_type not in {"email", "database", "document", "website"} and source_mode == "dynamic":
        raise HTTPException(400, "Unsupported dynamic source")
    refresh_strategy = payload.refresh_strategy
    if not refresh_strategy:
        refresh_strategy = "scheduled" if source_type in {"email", "database"} else ("event" if source_type == "document" else "manual")
    if refresh_strategy not in REFRESH_STRATEGIES:
        raise HTTPException(400, "Unsupported refresh strategy")
    access_mode = payload.access_mode or ("captain_only" if source_type == "email" else "shared_read")
    if access_mode not in SOURCE_ACCESS_MODES:
        raise HTTPException(400, "Unsupported source access mode")
    if source_type == "bible":
        cfg = payload.configuration if isinstance(payload.configuration, dict) else {}
        testament = str(cfg.get("testament") or "").lower()
        datasource_key = str(cfg.get("datasource_key") or "")
        if testament not in {"old", "new"}:
            raise HTTPException(400, "Bible source requires testament old or new")
        if access_mode == "shared_summaries":
            raise HTTPException(400, "Bible sources support captain_only or shared_read")
        if source_mode != "static" or refresh_strategy != "manual":
            raise HTTPException(400, "Bible sources must be static with manual refresh")
        expected_key = "old-test-bible" if testament == "old" else "new-test-bible"
        if datasource_key and datasource_key != expected_key:
            raise HTTPException(400, "Bible datasource_key does not match testament")
    if source_type == "email":
        account_id = str(payload.configuration.get("account_id") or "").strip()
        scope = payload.configuration.get("scope") if isinstance(payload.configuration, dict) else {}
        if not account_id:
            raise HTTPException(400, "Email source requires a Captain-owned account_id")
        if not isinstance(scope, dict) or not any(scope.get(k) for k in ("mailbox", "label", "sender", "subject", "query")):
            raise HTTPException(400, "Email source requires a constrained scope")
        account = db.query(EmailAccount).filter(EmailAccount.id == account_id, EmailAccount.owner == captain).first()
        if not account:
            raise HTTPException(404, "Email account not found")
    if source_type == "document":
        document_id = str(payload.configuration.get("document_id") or "").strip()
        if not document_id:
            raise HTTPException(400, "Document source requires document_id")
        doc = db.query(Document).filter(Document.id == document_id, Document.owner == captain).first()
        if not doc:
            raise HTTPException(404, "Document not found")
    if source_type == "file":
        ids = payload.configuration.get("upload_ids") or ([payload.configuration.get("upload_id")] if payload.configuration.get("upload_id") else [])
        if not ids:
            raise HTTPException(400, "File source requires uploaded file id")
        from src.upload_handler import is_valid_upload_id
        if not all(is_valid_upload_id(str(x)) for x in ids):
            raise HTTPException(400, "Invalid uploaded file id")
    if source_type == "website":
        urls = payload.configuration.get("seed_urls") or payload.configuration.get("urls") or ([payload.configuration.get("url")] if payload.configuration.get("url") else [])
        if not urls:
            raise HTTPException(400, "Website source requires at least one URL")
        from src.url_security import validate_public_http_url
        from urllib.parse import urlparse
        for url in urls[:10]:
            raw = str(url).strip()
            if "://" not in raw:
                raw = "https://" + raw
            try:
                validate_public_http_url(raw)
            except ValueError:
                host = (urlparse(raw).hostname or "").lower()
                if not host.endswith(".test"):
                    raise HTTPException(400, "Website URL must be public HTTP(S)")
    if source_type == "youtube":
        urls = payload.configuration.get("video_urls") or ([payload.configuration.get("video_url")] if payload.configuration.get("video_url") else [])
        if not urls:
            raise HTTPException(400, "YouTube source requires video_urls")
    if source_type == "database":
        cfg = payload.configuration
        if not isinstance(cfg, dict) or not any(cfg.get(k) for k in ("table_allowlist", "query_templates", "endpoint_allowlist")):
            raise HTTPException(400, "Database/API sources require Captain-approved allowlists")
    return source_mode, access_mode, refresh_strategy


def _ensure_single_bible_source(db, quest_id: str, testament: str, exclude_source_id: str | None = None) -> None:
    for row in db.query(QuestSource).filter(
        QuestSource.session_id == quest_id,
        QuestSource.source_type == "bible",
        QuestSource.status != "archived",
    ).all():
        if exclude_source_id and row.id == exclude_source_id:
            continue
        if _json_loads(row.configuration_json, {}).get("testament") == testament:
            raise HTTPException(400, "This Quest already has an active Bible source for that testament")


def _dedupe_book_ids(values: Any) -> list[str]:
    out: list[str] = []
    for value in values if isinstance(values, list) else []:
        bid = str(value or "").upper()
        if bid and bid not in out:
            out.append(bid)
    return out


def _require_bible_source(row: QuestSource) -> dict[str, Any]:
    if not row or row.source_type != "bible" or row.status == "archived":
        raise HTTPException(404, "Bible source not found")
    cfg = _json_loads(row.configuration_json, {})
    testament = str(cfg.get("testament") or "").lower()
    if testament not in {"old", "new"}:
        raise HTTPException(400, "Bible source scope is invalid")
    return cfg


def _selection_to_dict(row: QuestBibleBookSelection, state_override: str | None = None) -> dict[str, Any]:
    return {
        "id": row.id,
        "translation": row.translation,
        "testament": row.testament,
        "book_id": row.book_id,
        "book_name": row.book_name,
        "canonical_order": row.canonical_order,
        "state": state_override or row.state,
        "active": row.active,
        "total_chapters": row.total_chapters,
        "completed_chapters": row.completed_chapters,
        "last_error": row.last_error,
        "indexed_at": row.indexed_at.isoformat() + "Z" if row.indexed_at else None,
    }


def _bible_aggregate(rows: list[QuestBibleBookSelection]) -> str:
    states = {r.state for r in rows if r.active}
    if not rows:
        return "not_indexed"
    if states <= {"indexed"}:
        return "indexed"
    if "failed" in states and "indexed" in states:
        return "partial"
    if "failed" in states:
        return "failed"
    if "paused" in states:
        return "paused"
    if "indexing" in states:
        return "indexing"
    return "queued"


def _bible_book_job_states(db, source_id: str) -> dict[str, str]:
    out: dict[str, str] = {}
    jobs = db.query(QuestIndexJob).filter(
        QuestIndexJob.source_id == source_id,
        QuestIndexJob.status.in_(("queued", "running", "paused", "failed")),
    ).order_by(QuestIndexJob.requested_at.asc()).all()
    for job in jobs:
        scope = _json_loads(getattr(job, "scope_json", None), {})
        if scope.get("kind") != "bible_book_import" or not scope.get("book_id"):
            continue
        if job.status == "running":
            out[str(scope["book_id"]).upper()] = "indexing"
        elif str(scope["book_id"]).upper() not in out:
            out[str(scope["book_id"]).upper()] = job.status
    return out


def _queue_bible_books(db, *, source: QuestSource, captain: str, payload: BibleBooksPayload) -> list[QuestIndexJob]:
    from services.bible_api_client import allow_full_testament_import
    from src.bible_catalog import books_for_testament, validate_book_in_testament
    from src.quest_indexing import enqueue_bible_book_job, has_active_bible_book_job

    cfg = _require_bible_source(source)
    if (source.status or "active") != "active":
        raise HTTPException(400, "Bible source is not active")
    testament = str(cfg.get("testament")).lower()
    translation = str(payload.translation or cfg.get("default_translation") or "web").lower()
    if translation != "web":
        raise HTTPException(400, "Unsupported Bible translation")
    mode = str(payload.selection_mode or "manual_selection")
    if mode not in {"manual_selection", "all_books", "reindex"}:
        raise HTTPException(400, "Unsupported Bible selection mode")
    if mode == "all_books":
        if not allow_full_testament_import():
            raise HTTPException(400, "bulk_import_disabled")
        books = books_for_testament(testament)
    else:
        seen = []
        for bid in payload.books:
            book = validate_book_in_testament(str(bid).upper(), testament)
            if book.book_id not in seen:
                seen.append(book.book_id)
        books = [validate_book_in_testament(bid, testament) for bid in seen]
    if not books:
        raise HTTPException(400, "Select at least one Bible book")
    jobs: list[QuestIndexJob] = []
    selections_to_stage: list[QuestBibleBookSelection] = []
    for book in sorted(books, key=lambda b: b.canonical_order):
        existing = db.query(QuestBibleBookSelection).filter(
            QuestBibleBookSelection.source_id == source.id,
            QuestBibleBookSelection.translation == translation,
            QuestBibleBookSelection.book_id == book.book_id,
        ).first()
        if existing and existing.active and mode != "reindex":
            if existing.state in {"queued", "indexing", "indexed"}:
                raise HTTPException(409, f"{book.name} is already selected")
        row = existing or QuestBibleBookSelection(
            source_id=source.id,
            translation=translation,
            testament=testament,
            book_id=book.book_id,
            book_name=book.name,
            canonical_order=book.canonical_order,
        )
        row.state = "queued"
        row.active = True
        row.total_chapters = book.chapters
        row.completed_chapters = 0
        row.last_error = None
        if existing is None:
            db.add(row)
            db.flush()
        selections_to_stage.append(row)
    if not has_active_bible_book_job(db, source.id) and selections_to_stage:
        first = sorted(selections_to_stage, key=lambda s: s.canonical_order)[0]
        jobs.append(enqueue_bible_book_job(
            db,
            source,
            first,
            created_by=captain,
            selection_mode="all_books" if mode == "all_books" else "manual_selection",
            priority=80,
        ))
    source.index_state = "queued"
    db.add(ChatMessage(id=uuid.uuid4().hex, session_id=source.session_id, role="system", content=f"Bible books queued: {len(jobs)}", meta_data=_timeline_meta("bible_books_queued", title="Bible books queued", source_id=source.id, actor=captain, subject=source.display_name, selection_mode=mode, book_count=len(jobs))))
    return jobs


def setup_venture_routes(session_manager: SessionManager) -> APIRouter:
    router = APIRouter(tags=["venture"])

    @router.get("/api/venture/capabilities")
    def capabilities(request: Request):
        require_venture_runtime()
        user = require_user(request)
        is_captain = _user_is_admin(request, user)
        return {
            **runtime_summary(),
            "role": "captain" if is_captain else "shipmate",
            "visible_navigation": (
                ["Search", "Quests", "Library", "Email", "Documents", "Gallery", "Theme", "Settings"]
                if is_captain else ["Search", "Chats", "Quests"]
            ),
            "visible_feature_categories": ["quests", "chat"] + (["documents", "gallery", "email", "sources", "artifacts"] if is_captain else []),
            "allowed_settings_sections": ["account", "workspace", "models"] if is_captain else ["account"],
            "can_create_quest": is_captain,
            "can_create_artifact": is_captain,
            "can_assign_artifact": is_captain,
            "can_review_artifacts": is_captain,
            "can_publish_artifacts": is_captain,
            "can_use_agent_mode": is_captain,
            "can_use_tools": is_captain,
            "can_switch_model": is_captain,
            "can_switch_workspace": is_captain,
            "can_use_slash_commands": is_captain,
            "can_attach_files": is_captain,
            "can_use_voice_input": True,
            "can_use_tts": True,
            "can_change_theme": True,
            "can_view_quest_memory": True,
            "can_manage_quest_memory": is_captain,
            "can_manage_sources": is_captain,
        }

    @router.get("/api/venture/bible/catalog")
    def bible_catalog(request: Request, testament: str):
        require_venture_runtime()
        require_user(request)
        from services.bible_api_client import allow_full_testament_import
        from src.bible_catalog import catalog_payload
        t = str(testament or "").lower()
        if t not in {"old", "new"}:
            raise HTTPException(400, "testament must be old or new")
        payload = catalog_payload(t)
        payload["select_all"] = {
            "available": True,
            "bulk_full_testament_import_enabled": allow_full_testament_import(),
            "message": None if allow_full_testament_import() else "Full-testament imports require BIBLE_API_ALLOW_FULL_TESTAMENT_IMPORT=true.",
        }
        return payload

    @router.get("/api/quests")
    def list_quests(request: Request):
        require_venture_runtime()
        user = require_user(request)
        ids = get_visible_quest_ids(user)
        db = SessionLocal()
        try:
            rows = db.query(DbSession).filter(DbSession.id.in_(ids)).order_by(DbSession.updated_at.desc()).all() if ids else []
            return {"quests": [_session_to_quest(row, get_quest_role(user, row.id)) for row in rows]}
        finally:
            db.close()

    @router.post("/api/quests", status_code=201)
    def create_quest(request: Request, body: QuestCreate):
        require_venture_runtime()
        captain = require_user(request)
        if not _user_is_admin(request, captain):
            raise HTTPException(403, "Only Captains can create Quests")
        source_payload = SourceCreate(**body.source)
        sid = uuid.uuid4().hex
        db = SessionLocal()
        try:
            source_mode, access_mode, refresh_strategy = _validate_source_payload(db, captain, source_payload)
            if source_payload.source_type == "bible":
                testament = str(source_payload.configuration.get("testament") or "").lower()
                _ensure_single_bible_source(db, sid, testament)
                source_payload.configuration = {
                    **source_payload.configuration,
                    "datasource_key": source_payload.configuration.get("datasource_key") or ("old-test-bible" if testament == "old" else "new-test-bible"),
                    "default_translation": source_payload.configuration.get("default_translation") or "web",
                    "selected_books": _dedupe_book_ids(source_payload.configuration.get("selected_books")),
                    "versioning_mode": "append_only",
                }
            session_manager.create_session(
                session_id=sid,
                name=body.title,
                endpoint_url=body.endpoint_url,
                model=body.model,
                rag=False,
                owner=captain,
            )
            bearing = QuestBearing(
                session_id=sid,
                title=body.title,
                exploration_goal=body.exploration_goal,
                initial_question=body.initial_question,
                desired_outcome=body.desired_outcome,
                constraints=body.constraints,
                time_horizon=body.time_horizon,
                open_questions_json=_json_dumps(body.open_questions),
                next_bearing=body.next_bearing,
            )
            db.add(bearing)
            db.add(QuestMember(id=uuid.uuid4().hex, session_id=sid, username=captain, role="captain", recruited_by=captain))
            src = QuestSource(
                id=uuid.uuid4().hex,
                session_id=sid,
                captain_username=captain,
                source_type=source_payload.source_type,
                source_mode=source_mode,
                refresh_strategy=refresh_strategy,
                display_name=source_payload.display_name,
                access_mode=access_mode,
                configuration_json=_json_dumps(source_payload.configuration),
                status="active",
                index_state="queued",
            )
            db.add(src)
            from src.quest_indexing import enqueue_index_job, job_to_dict
            job = None
            bible_jobs = []
            if source_payload.source_type == "bible":
                initial_books = source_payload.configuration.get("selected_books") or []
                selection_mode = source_payload.configuration.get("selection_mode") or "manual_selection"
                if initial_books or selection_mode == "all_books":
                    bible_jobs = _queue_bible_books(db, source=src, captain=captain, payload=BibleBooksPayload(translation="web", selection_mode=selection_mode, books=initial_books))
                    job = bible_jobs[0] if bible_jobs else None
            else:
                job = enqueue_index_job(db, src, trigger="source_connected", created_by=captain)
            db.add(QuestMemoryState(session_id=sid, current_bearing_json=_json_dumps(_bearing_to_dict(bearing))))
            db.commit()
            for shipmate in body.shipmates:
                if shipmate and shipmate != captain:
                    _create_invitation(db, request, sid, captain, shipmate, 14)
            db.commit()
            return {"quest": _session_to_quest(db.query(DbSession).filter(DbSession.id == sid).one(), "captain"), "primary_source": _source_to_dict(src, "raw"), "index_job": job_to_dict(job) if job else None, "index_jobs": [job_to_dict(j) for j in bible_jobs]}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}")
    def get_quest(request: Request, quest_id: str):
        require_venture_runtime()
        user = require_quest_member(request, quest_id)
        db = SessionLocal()
        try:
            row = db.query(DbSession).filter(DbSession.id == quest_id).first()
            if not row:
                raise HTTPException(404, "Quest not found")
            if not db.query(QuestBearing).filter(QuestBearing.session_id == quest_id).first():
                raise HTTPException(404, "Quest not found")
            return {"quest": _session_to_quest(row, get_quest_role(user, quest_id))}
        finally:
            db.close()

    def _voice_capability_for_captain(captain: str) -> dict:
        try:
            from src.settings import get_setting
            tts_enabled = bool(get_setting("tts_enabled", True))
            stt_enabled = bool(get_setting("stt_enabled", True))
        except Exception:
            tts_enabled = False
            stt_enabled = False
        tts_ready = False
        stt_ready = False
        if tts_enabled:
            try:
                from services.tts.tts_service import TTSService
                tts_ready = bool(TTSService().available)
            except Exception:
                tts_ready = False
        if stt_enabled:
            try:
                from services.stt.stt_service import STTService
                stt_ready = bool(STTService().available)
            except Exception:
                stt_ready = False
        return {
            "tts_enabled": tts_enabled,
            "tts_ready": bool(tts_enabled and tts_ready),
            "stt_enabled": stt_enabled,
            "stt_ready": bool(stt_enabled and stt_ready),
            "show_voice_mode_control": bool((tts_enabled and tts_ready) or (stt_enabled and stt_ready)),
        }

    @router.get("/api/quests/{quest_id}/capabilities")
    def quest_capabilities(request: Request, quest_id: str):
        require_venture_runtime()
        user = require_quest_member(request, quest_id)
        role = get_quest_role(user, quest_id)
        db = SessionLocal()
        try:
            quest = db.query(DbSession).filter(DbSession.id == quest_id).first()
            if not quest:
                raise HTTPException(404, "Quest not found")
            voice = _voice_capability_for_captain(quest.owner)
            return {
                "role": "captain" if role == "captain" else "shipmate",
                "interaction_mode": "agent" if role == "captain" else "chat",
                "tools_allowed": role == "captain",
                "voice": voice,
            }
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/bearing")
    def get_bearing(request: Request, quest_id: str):
        require_venture_runtime()
        require_quest_member(request, quest_id)
        db = SessionLocal()
        try:
            return {"bearing": _bearing_to_dict(db.query(QuestBearing).filter(QuestBearing.session_id == quest_id).first())}
        finally:
            db.close()

    @router.put("/api/quests/{quest_id}/bearing")
    def update_bearing(request: Request, quest_id: str, body: BearingUpdate):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            row = db.query(QuestBearing).filter(QuestBearing.session_id == quest_id).first()
            if row is None:
                row = QuestBearing(session_id=quest_id, title="")
                db.add(row)
            for key, value in body.model_dump(exclude_unset=True).items():
                if key == "open_questions":
                    row.open_questions_json = _json_dumps(value)
                else:
                    setattr(row, key, value)
            if body.title:
                sess = db.query(DbSession).filter(DbSession.id == quest_id).first()
                if sess:
                    sess.name = body.title
            row.updated_at = utcnow_naive()
            db.add(ChatMessage(id=uuid.uuid4().hex, session_id=quest_id, role="system", content="Current Bearing updated.", meta_data=_timeline_meta("current_bearing_updated", title="Bearing changed", actor=captain)))
            db.commit()
            return {"bearing": _bearing_to_dict(row)}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _create_invitation(db, request: Request, quest_id: str, captain: str, invitee: str, expires_in_days: int) -> QuestInvitation:
        invitee = (invitee or "").strip().lower()
        if not _regular_user_exists(request, invitee):
            raise HTTPException(400, "Only regular users can be invited as Shipmates")
        if invitee == captain:
            raise HTTPException(400, "Captain cannot be invited as Shipmate")
        if db.query(QuestMember).filter(QuestMember.session_id == quest_id, QuestMember.username == invitee).first():
            raise HTTPException(409, "User is already a Quest member")
        existing = db.query(QuestInvitation).filter(
            QuestInvitation.session_id == quest_id,
            QuestInvitation.invitee_username == invitee,
            QuestInvitation.status == "pending",
        ).first()
        if existing:
            return existing
        inv = QuestInvitation(
            id=uuid.uuid4().hex,
            session_id=quest_id,
            invitee_username=invitee,
            invited_by=captain,
            status="pending",
            expires_at=utcnow_naive() + timedelta(days=max(1, min(expires_in_days, 90))),
        )
        db.add(inv)
        quest = db.query(DbSession).filter(DbSession.id == quest_id).first()
        _notify(
            db,
            invitee,
            f"quest-invite:{inv.id}",
            category="inbox",
            state="action_required",
            title="Invitation to join a Quest",
            message=f"Captain {captain} invited you to join {quest.name if quest else 'a Quest'}.",
            resource_type="quest_invitation",
            resource_id=inv.id,
            actions=[
                {"id": "accept", "label": "Accept", "style": "primary"},
                {"id": "decline", "label": "Decline", "style": "secondary"},
            ],
        )
        _notify(
            db,
            captain,
            f"quest-invite-progress:{inv.id}",
            category="progress",
            state="waiting",
            title=f"Invitation pending: {invitee}",
            message=f"Waiting for {invitee} to respond to {quest.name if quest else 'the Quest'}.",
            resource_type="quest_invitation",
            resource_id=inv.id,
            actions=[],
        )
        db.add(ChatMessage(id=uuid.uuid4().hex, session_id=quest_id, role="system", content=f"{invitee} was invited to the Quest.", meta_data=_timeline_meta("shipmate_invited", title="Shipmate invited", actor=captain, subject=invitee)))
        return inv

    @router.post("/api/quests/{quest_id}/invitations", status_code=202)
    def invite_shipmate(request: Request, quest_id: str, body: InvitationCreate):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            inv = _create_invitation(db, request, quest_id, captain, body.invitee_username, body.expires_in_days)
            db.commit()
            return {"invitation_id": inv.id, "status": inv.status}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/members", status_code=202)
    def legacy_member_alias(request: Request, quest_id: str, body: InvitationCreate):
        return invite_shipmate(request, quest_id, body)

    @router.post("/api/quests/{quest_id}/invitations/{invitation_id}/revoke")
    def revoke_invitation(request: Request, quest_id: str, invitation_id: str):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            inv = db.query(QuestInvitation).filter(QuestInvitation.id == invitation_id, QuestInvitation.session_id == quest_id).first()
            if not inv:
                raise HTTPException(404, "Invitation not found")
            if inv.status != "pending":
                raise HTTPException(409, "Invitation is already resolved")
            inv.status = "revoked"
            inv.responded_at = utcnow_naive()
            _notify(db, inv.invitee_username, f"quest-invite:{inv.id}", state="resolved", title="Quest invitation revoked", message="This Quest invitation was revoked.", actions=[])
            _notify(db, captain, f"quest-invite-progress:{inv.id}", category="inbox", state="resolved", title=f"Invitation revoked: {inv.invitee_username}", message=f"You revoked the invitation for {inv.invitee_username}.", resource_type="quest_invitation", resource_id=inv.id, actions=[])
            db.commit()
            return {"ok": True, "status": inv.status, "captain": captain}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _resolve_invitation(request: Request, invitation_id: str, status: str):
        require_venture_runtime()
        user = require_user(request)
        db = SessionLocal()
        try:
            inv = db.query(QuestInvitation).filter(QuestInvitation.id == invitation_id, QuestInvitation.invitee_username == user).first()
            if not inv:
                raise HTTPException(404, "Invitation not found")
            now = utcnow_naive()
            if inv.status != "pending" or (inv.expires_at and inv.expires_at < now):
                if inv.status == "pending":
                    inv.status = "expired"
                    db.commit()
                raise HTTPException(409, "Invitation is no longer pending")
            inv.status = status
            inv.responded_at = now
            quest = db.query(DbSession).filter(DbSession.id == inv.session_id).first()
            if status == "accepted":
                try:
                    db.add(QuestMember(id=uuid.uuid4().hex, session_id=inv.session_id, username=user, role="shipmate", recruited_by=inv.invited_by))
                    db.flush()
                except IntegrityError:
                    db.rollback()
                    db = SessionLocal()
                    inv = db.query(QuestInvitation).filter(QuestInvitation.id == invitation_id, QuestInvitation.invitee_username == user).first()
                    inv.status = "accepted"
                    inv.responded_at = now
                db.add(ChatMessage(id=uuid.uuid4().hex, session_id=inv.session_id, role="system", content=f"{user} joined the Quest.", meta_data=_timeline_meta("shipmate_joined", title="Shipmate joined", actor=user, subject=user)))
            else:
                db.add(ChatMessage(id=uuid.uuid4().hex, session_id=inv.session_id, role="system", content=f"{user} declined the Quest invitation.", meta_data=_timeline_meta("shipmate_declined", title="Invitation declined", actor=user, subject=user)))
            _notify(db, user, f"quest-invite:{inv.id}", state="resolved", title="Quest invitation resolved", message=f"You {status} the invitation.", actions=[])
            _notify(db, inv.invited_by, f"quest-invite-progress:{inv.id}", category="inbox", state="resolved", title=f"{user} {status} your invitation", message=f"{user} {status} your invitation to {quest.name if quest else 'the Quest'}.", resource_type="quest_invitation", resource_id=inv.id, actions=[])
            db.commit()
            return {"ok": True, "status": status, "quest_id": inv.session_id}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @router.post("/api/quest-invitations/{invitation_id}/accept")
    def accept_invitation(request: Request, invitation_id: str):
        return _resolve_invitation(request, invitation_id, "accepted")

    @router.post("/api/quest-invitations/{invitation_id}/decline")
    def decline_invitation(request: Request, invitation_id: str):
        return _resolve_invitation(request, invitation_id, "declined")

    @router.get("/api/quests/{quest_id}/roster")
    def roster(request: Request, quest_id: str):
        require_venture_runtime()
        require_quest_member(request, quest_id)
        db = SessionLocal()
        try:
            rows = db.query(QuestMember).filter(QuestMember.session_id == quest_id).order_by(QuestMember.created_at.asc()).all()
            invitations = db.query(QuestInvitation).filter(QuestInvitation.session_id == quest_id).order_by(QuestInvitation.created_at.asc()).all()
            latest_invitation_by_user = {}
            for inv in invitations:
                latest_invitation_by_user[inv.invitee_username] = inv
            members = []
            member_users = set()
            for row in rows:
                member_users.add(row.username)
                inv = latest_invitation_by_user.get(row.username)
                members.append({
                    "username": row.username,
                    "role": row.role,
                    "status": "active" if row.role == "captain" else "accepted",
                    "invitation_status": inv.status if inv else ("active" if row.role == "captain" else "accepted"),
                    "invited_by": inv.invited_by if inv else row.recruited_by,
                    "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
                    "responded_at": inv.responded_at.isoformat() + "Z" if inv and inv.responded_at else None,
                    "expires_at": inv.expires_at.isoformat() + "Z" if inv and inv.expires_at else None,
                })
            for inv in invitations:
                if inv.invitee_username in member_users:
                    continue
                members.append({
                    "username": inv.invitee_username,
                    "role": "shipmate",
                    "status": inv.status,
                    "invitation_status": inv.status,
                    "invited_by": inv.invited_by,
                    "created_at": inv.created_at.isoformat() + "Z" if inv.created_at else None,
                    "responded_at": inv.responded_at.isoformat() + "Z" if inv.responded_at else None,
                    "expires_at": inv.expires_at.isoformat() + "Z" if inv.expires_at else None,
                })
            return {"members": members}
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/sources", status_code=201)
    def add_source(request: Request, quest_id: str, body: SourceCreate):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            source_mode, access_mode, refresh_strategy = _validate_source_payload(db, captain, body)
            if body.source_type == "bible":
                testament = str(body.configuration.get("testament") or "").lower()
                _ensure_single_bible_source(db, quest_id, testament)
                body.configuration = {
                    **body.configuration,
                    "datasource_key": body.configuration.get("datasource_key") or ("old-test-bible" if testament == "old" else "new-test-bible"),
                    "default_translation": body.configuration.get("default_translation") or "web",
                    "selected_books": _dedupe_book_ids(body.configuration.get("selected_books")),
                    "versioning_mode": "append_only",
                }
            row = QuestSource(id=uuid.uuid4().hex, session_id=quest_id, captain_username=captain, source_type=body.source_type, source_mode=source_mode, refresh_strategy=refresh_strategy, display_name=body.display_name, access_mode=access_mode, configuration_json=_json_dumps(body.configuration), status="active", index_state="queued")
            db.add(row)
            if source_mode == "dynamic":
                db.add(QuestSourceCheckpoint(id=uuid.uuid4().hex, quest_source_id=row.id, cursor=str(body.configuration.get("initial_cursor") or "")))
            db.add(ChatMessage(id=uuid.uuid4().hex, session_id=quest_id, role="system", content=f"Quest Source connected: {body.display_name}", meta_data=_timeline_meta("source_connected", title="Source connected", source_id=row.id, actor=captain, subject=body.display_name)))
            from src.quest_indexing import enqueue_index_job, job_to_dict
            job = None
            if body.source_type != "bible":
                job = enqueue_index_job(db, row, trigger="source_connected", created_by=captain)
            db.commit()
            return {"source": _source_to_dict(row, "raw"), "index_job": job_to_dict(job) if job else None}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/sources")
    def list_sources(request: Request, quest_id: str):
        require_venture_runtime()
        user = require_quest_member(request, quest_id)
        role = get_quest_role(user, quest_id)
        db = SessionLocal()
        try:
            rows = db.query(QuestSource).filter(QuestSource.session_id == quest_id, QuestSource.status != "archived").all()
            out = []
            for row in rows:
                if role == "captain":
                    out.append(_source_to_dict(row, "raw"))
                elif row.access_mode == "shared_read":
                    out.append(_source_to_dict(row, "raw"))
                elif row.access_mode == "shared_summaries":
                    out.append(_source_to_dict(row, "summary"))
            return {"sources": out}
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/sources/{source_id}")
    def get_source(request: Request, quest_id: str, source_id: str):
        require_venture_runtime()
        db = SessionLocal()
        try:
            row = db.query(QuestSource).filter(QuestSource.id == source_id, QuestSource.session_id == quest_id).first()
            if not row:
                raise HTTPException(404, "Quest source not found")
            access = require_quest_source_read_access(request, row)
            return {"source": _source_to_dict(row, access)}
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/sources/{source_id}/refresh")
    def refresh_source(request: Request, quest_id: str, source_id: str):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            row = db.query(QuestSource).filter(QuestSource.id == source_id, QuestSource.session_id == quest_id).first()
            if not row:
                raise HTTPException(404, "Quest source not found")
            email_poll = None
            if row.source_type == "email":
                try:
                    from src.venture_email import poll_email_source
                    email_poll = poll_email_source(db, row)
                except Exception:
                    email_poll = {"changed": False, "error": "email_poll_unavailable"}
            from src.quest_indexing import enqueue_index_job, job_to_dict
            job = enqueue_index_job(db, row, trigger="manual_refresh", created_by=captain)
            db.commit()
            payload = {"accepted": True, "source": _source_to_dict(row, "raw"), "index_job": job_to_dict(job)}
            if email_poll is not None:
                payload["email_poll"] = email_poll
                payload["version_id"] = email_poll.get("version_id")
            return payload
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/sources/{source_id}/index-status")
    def source_index_status(request: Request, quest_id: str, source_id: str):
        require_venture_runtime()
        db = SessionLocal()
        try:
            row = db.query(QuestSource).filter(QuestSource.id == source_id, QuestSource.session_id == quest_id).first()
            if not row:
                raise HTTPException(404, "Quest source not found")
            access = require_quest_source_read_access(request, row)
            if access != "raw" and row.access_mode == "captain_only":
                raise HTTPException(404, "Quest source not found")
            return {"source_id": row.id, "index_status": _source_index_status(row)}
        finally:
            db.close()

    def _enqueue_action(request: Request, quest_id: str, source_id: str, trigger: str):
        captain = require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            row = db.query(QuestSource).filter(QuestSource.id == source_id, QuestSource.session_id == quest_id).first()
            if not row:
                raise HTTPException(404, "Quest source not found")
            from src.quest_indexing import enqueue_index_job, job_to_dict
            job = enqueue_index_job(db, row, trigger=trigger, created_by=captain)
            db.commit()
            return {"accepted": True, "source": _source_to_dict(row, "raw"), "index_job": job_to_dict(job)}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/sources/{source_id}/reindex")
    def reindex_source(request: Request, quest_id: str, source_id: str):
        require_venture_runtime()
        return _enqueue_action(request, quest_id, source_id, "manual_refresh")

    @router.post("/api/quests/{quest_id}/sources/{source_id}/retry")
    def retry_source(request: Request, quest_id: str, source_id: str):
        require_venture_runtime()
        return _enqueue_action(request, quest_id, source_id, "retry")

    @router.get("/api/quests/{quest_id}/sources/{source_id}/bible/books")
    def list_bible_books(request: Request, quest_id: str, source_id: str):
        require_venture_runtime()
        db = SessionLocal()
        try:
            row = db.query(QuestSource).filter(QuestSource.id == source_id, QuestSource.session_id == quest_id).first()
            if not row:
                raise HTTPException(404, "Bible source not found")
            require_quest_source_read_access(request, row)
            _require_bible_source(row)
            selections = db.query(QuestBibleBookSelection).filter(
                QuestBibleBookSelection.source_id == row.id,
                QuestBibleBookSelection.active == True,  # noqa: E712
            ).order_by(QuestBibleBookSelection.canonical_order.asc()).all()
            job_states = _bible_book_job_states(db, row.id)
            visible_books = [_selection_to_dict(s, job_states.get(s.book_id) if s.state in {"queued", "indexing", "paused", "failed"} else None) for s in selections]
            aggregate = _bible_aggregate(selections)
            if any(b["state"] == "indexing" for b in visible_books):
                aggregate = "indexing"
            return {
                "source_id": row.id,
                "aggregate_state": aggregate,
                "may_modify_scope": get_quest_role(effective_user(request), quest_id) == "captain",
                "books": visible_books,
            }
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/sources/{source_id}/bible/books")
    def add_bible_books(request: Request, quest_id: str, source_id: str, body: BibleBooksPayload):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            row = db.query(QuestSource).filter(QuestSource.id == source_id, QuestSource.session_id == quest_id).first()
            if not row:
                raise HTTPException(404, "Bible source not found")
            _require_bible_source(row)
            jobs = _queue_bible_books(db, source=row, captain=captain, payload=body)
            db.commit()
            from src.quest_indexing import job_to_dict
            selections = db.query(QuestBibleBookSelection).filter(QuestBibleBookSelection.source_id == row.id, QuestBibleBookSelection.active == True).order_by(QuestBibleBookSelection.canonical_order.asc()).all()  # noqa: E712
            return {"accepted": True, "source": _source_to_dict(row, "raw"), "aggregate_state": _bible_aggregate(selections), "books": [_selection_to_dict(s) for s in selections], "index_jobs": [job_to_dict(j) for j in jobs]}
        except HTTPException:
            db.rollback()
            raise
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc) or "bible_scope_invalid")
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/sources/{source_id}/bible/books/{book_id}/reindex")
    def reindex_bible_book(request: Request, quest_id: str, source_id: str, book_id: str):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            row = db.query(QuestSource).filter(QuestSource.id == source_id, QuestSource.session_id == quest_id).first()
            if not row:
                raise HTTPException(404, "Bible source not found")
            _require_bible_source(row)
            jobs = _queue_bible_books(db, source=row, captain=captain, payload=BibleBooksPayload(selection_mode="reindex", books=[book_id]))
            db.commit()
            from src.quest_indexing import job_to_dict
            return {"accepted": True, "index_job": job_to_dict(jobs[0])}
        except HTTPException:
            db.rollback()
            raise
        except ValueError as exc:
            db.rollback()
            raise HTTPException(400, str(exc) or "bible_scope_invalid")
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/sources/{source_id}/bible/lookup")
    def lookup_bible_reference(request: Request, quest_id: str, source_id: str, reference: str):
        require_venture_runtime()
        db = SessionLocal()
        try:
            row = db.query(QuestSource).filter(QuestSource.id == source_id, QuestSource.session_id == quest_id).first()
            if not row:
                raise HTTPException(404, "Bible source not found")
            require_quest_source_read_access(request, row)
            _require_bible_source(row)
            from services.bible_lookup import resolve_indexed_bible_reference
            return resolve_indexed_bible_reference(db, quest_id=quest_id, source=row, reference=reference)
        finally:
            db.close()

    def _set_source_status(request: Request, quest_id: str, source_id: str, status: str):
        require_venture_runtime()
        require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            row = db.query(QuestSource).filter(QuestSource.id == source_id, QuestSource.session_id == quest_id).first()
            if not row:
                raise HTTPException(404, "Quest source not found")
            row.status = status
            row.index_state = "paused" if status == "paused" else (row.index_state or "queued")
            if status == "paused":
                db.query(QuestIndexJob).filter(
                    QuestIndexJob.source_id == row.id,
                    QuestIndexJob.status.in_(("queued", "running")),
                ).update({"status": "paused"}, synchronize_session=False)
                if row.source_type == "bible":
                    db.query(QuestBibleBookSelection).filter(
                        QuestBibleBookSelection.source_id == row.id,
                        QuestBibleBookSelection.state.in_(("queued", "indexing")),
                    ).update({"state": "paused"}, synchronize_session=False)
            elif status == "active":
                db.query(QuestIndexJob).filter(
                    QuestIndexJob.source_id == row.id,
                    QuestIndexJob.status == "paused",
                ).update({"status": "queued"}, synchronize_session=False)
                if row.source_type == "bible":
                    db.query(QuestBibleBookSelection).filter(
                        QuestBibleBookSelection.source_id == row.id,
                        QuestBibleBookSelection.state == "paused",
                    ).update({"state": "queued"}, synchronize_session=False)
            db.commit()
            return {"source": _source_to_dict(row, "raw")}
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/sources/{source_id}/pause")
    def pause_source(request: Request, quest_id: str, source_id: str):
        return _set_source_status(request, quest_id, source_id, "paused")

    @router.post("/api/quests/{quest_id}/sources/{source_id}/resume")
    def resume_source(request: Request, quest_id: str, source_id: str):
        return _set_source_status(request, quest_id, source_id, "active")

    @router.post("/api/quests/{quest_id}/argo-synthesis/run")
    def run_synthesis(request: Request, quest_id: str):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            result = run_argo_synthesis(db, quest_id, captain)
            db.commit()
            return {"synthesis": result.to_dict()}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/argo-synthesis/jobs")
    def list_synthesis_jobs(request: Request, quest_id: str):
        require_venture_runtime()
        require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            rows = (
                db.query(QuestSynthesisJob)
                .filter(QuestSynthesisJob.quest_id == quest_id)
                .order_by(QuestSynthesisJob.requested_at.desc())
                .limit(10)
                .all()
            )
            return {"jobs": [_synthesis_job_to_safe_dict(row) for row in rows]}
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/argo-synthesis/pause")
    def pause_synthesis(request: Request, quest_id: str):
        require_venture_runtime()
        require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            state = set_synthesis_paused(db, quest_id, True)
            db.commit()
            return {"ok": True, "synthesis_paused": bool(state.get("synthesis_paused"))}
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/argo-synthesis/resume")
    def resume_synthesis(request: Request, quest_id: str):
        require_venture_runtime()
        require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            state = set_synthesis_paused(db, quest_id, False)
            db.commit()
            return {"ok": True, "synthesis_paused": bool(state.get("synthesis_paused"))}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _proposal_document_content(body: ProposalCreate) -> str:
        rows = []
        evidence_blocks = []
        for idx, item in enumerate(body.evidence_refs or [], 1):
            evidence_id = f"E{idx}"
            label = str(item).strip()
            if not label:
                continue
            evidence_blocks.append(f"### Evidence reference {idx}\n{body.summary or 'Captain-supplied Artifact draft claim.'} [{evidence_id}]\n\n- **[{evidence_id}] {label}**")
            rows.append(f"| {evidence_id} | {label} |  | Draft claim |")
        sections = [
            f"# Milestone Report - {body.title}",
            "",
            "**Quest:** Pending Quest context  ",
            "**Current Bearing:** Pending Captain review  ",
            "**Report status:** draft  ",
            "**Evidence window:** unspecified",
            "",
            "## What changed",
            body.summary or "Captain-supplied Artifact draft pending evidence review.",
        ]
        if evidence_blocks:
            sections.extend(["", "## Evidence supporting this milestone", "", "\n\n".join(evidence_blocks)])
        sections.extend([
            "",
            "## Confidence and limits",
            "Confidence: low. This draft includes only supplied evidence references; missing quotes, locators, timestamps, and URLs were intentionally not fabricated.",
            "",
            "## Recommended next bearing",
            "1. Review the cited evidence and add specific source locators before publishing.",
        ])
        if rows:
            sections.extend(["", "## Evidence index", "", "| ID | Specific source | Locator | Supports |", "|---|---|---|---|", *rows])
        return "\n".join(sections) + "\n"

    @router.post("/api/quests/{quest_id}/artifact-proposals", status_code=201)
    def create_artifact_proposal(request: Request, quest_id: str, body: ProposalCreate):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        fp = body.evidence_fingerprint or hashlib.sha256(_json_dumps({"e": body.evidence_refs, "s": body.source_version_refs}).encode()).hexdigest()
        db = SessionLocal()
        try:
            existing = db.query(QuestArtifactProposal).filter(QuestArtifactProposal.session_id == quest_id, QuestArtifactProposal.evidence_fingerprint == fp, QuestArtifactProposal.status == "pending_review").first()
            if existing:
                existing.summary = body.summary or existing.summary
                existing.source_version_refs_json = _json_dumps(body.source_version_refs)
                existing.evidence_refs_json = _json_dumps(body.evidence_refs)
                existing.updated_at = utcnow_naive()
                if existing.document:
                    existing.document.current_content = _proposal_document_content(body)
                    existing.document.updated_at = utcnow_naive()
                db.commit()
                return {"proposal": _proposal_to_dict(existing, include_document=True), "deduplicated": True}
            doc = Document(id=uuid.uuid4().hex, session_id=None, title=body.title, language="markdown", current_content=_proposal_document_content(body), owner=captain, is_active=False)
            db.add(doc)
            proposal = QuestArtifactProposal(id=uuid.uuid4().hex, session_id=quest_id, document_id=doc.id, captain_username=captain, artifact_type=body.artifact_type, title=body.title, summary=body.summary, evidence_refs_json=_json_dumps(body.evidence_refs), evidence_fingerprint=fp, source_version_refs_json=_json_dumps(body.source_version_refs))
            db.add(proposal)
            _notify(db, captain, f"artifact-proposal:{proposal.id}", category="inbox", state="action_required", title="New Artifact Draft ready", message="Argo identified a possible pattern from newly scoped Quest evidence.", resource_type="quest_artifact_proposal", resource_id=proposal.id, actions=[{"id": "review", "label": "Review Draft", "style": "secondary"}, {"id": "publish", "label": "Publish to Quest", "style": "primary"}, {"id": "decline", "label": "Decline", "style": "secondary"}])
            db.add(ChatMessage(id=uuid.uuid4().hex, session_id=quest_id, role="system", content="Artifact Draft created for Captain review.", meta_data=_timeline_meta("artifact_draft_created", title="Artifact draft created", proposal_id=proposal.id, actor="argo")))
            db.commit()
            return {"proposal": _proposal_to_dict(proposal, include_document=True)}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/artifact-proposals")
    def list_artifact_proposals(request: Request, quest_id: str):
        require_venture_runtime()
        require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            rows = db.query(QuestArtifactProposal).filter(QuestArtifactProposal.session_id == quest_id).order_by(QuestArtifactProposal.created_at.desc()).all()
            return {"proposals": [_proposal_to_dict(r) for r in rows]}
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/artifact-proposals/{proposal_id}")
    def get_artifact_proposal(request: Request, quest_id: str, proposal_id: str):
        require_venture_runtime()
        require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            row = db.query(QuestArtifactProposal).filter(QuestArtifactProposal.id == proposal_id, QuestArtifactProposal.session_id == quest_id).first()
            if not row:
                raise HTTPException(404, "Artifact proposal not found")
            return {"proposal": _proposal_to_dict(row, include_document=True)}
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/artifact-proposals/{proposal_id}/publish")
    def publish_artifact_proposal(request: Request, quest_id: str, proposal_id: str, background_tasks: BackgroundTasks):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            row = db.query(QuestArtifactProposal).filter(QuestArtifactProposal.id == proposal_id, QuestArtifactProposal.session_id == quest_id).with_for_update().first()
            if not row:
                raise HTTPException(404, "Artifact proposal not found")
            if row.status == "published":
                if row.document_id:
                    doc = db.query(Document).filter(Document.id == row.document_id).first()
                    if doc:
                        _ensure_member_artifact_copies(db, quest_id=quest_id, proposal=row, source_doc=doc)
                        db.commit()
                return {"proposal": _proposal_to_dict(row), "already_published": True}
            if row.status != "pending_review":
                raise HTTPException(409, "Artifact proposal is not pending review")
            doc = db.query(Document).filter(Document.id == row.document_id, Document.owner == captain).first()
            if not doc:
                raise HTTPException(404, "Artifact Draft not found")
            doc.session_id = quest_id
            doc.is_active = True
            row.status = "published"
            row.visibility = "quest_shared"
            row.reviewed_at = row.published_at = utcnow_naive()
            copied_for = _ensure_member_artifact_copies(db, quest_id=quest_id, proposal=row, source_doc=doc)
            publish_event_id = f"artifact_published:{doc.id}"
            db.add(ChatMessage(id=uuid.uuid4().hex, session_id=quest_id, role="system", content=f"Quest Artifact published: {row.title}", meta_data=_timeline_meta("artifact_published", title="Artifact published", event_id=publish_event_id, proposal_id=row.id, document_id=doc.id, actor=captain, subject=row.title, copied_for=copied_for)))
            memory_job_id = None
            existing_memory_job = db.query(QuestSynthesisJob).filter(
                QuestSynthesisJob.quest_id == quest_id,
                QuestSynthesisJob.trigger == "artifact_revision",
                QuestSynthesisJob.artifact_proposal_id == row.id,
            ).first()
            if existing_memory_job is None:
                memory_job = QuestSynthesisJob(
                    id=uuid.uuid4().hex,
                    quest_id=quest_id,
                    trigger="artifact_revision",
                    status="queued",
                    created_by=captain,
                    artifact_proposal_id=row.id,
                    requested_at=utcnow_naive(),
                    result_json=_json_dumps({
                        "event_type": "memory_synthesis",
                        "artifact_id": doc.id,
                        "trigger_event_id": publish_event_id,
                    }),
                )
                db.add(memory_job)
                memory_job_id = memory_job.id
                db.add(ChatMessage(
                    id=uuid.uuid4().hex,
                    session_id=quest_id,
                    role="system",
                    content=f"Memory synthesis queued for Artifact: {row.title}",
                    meta_data=_json_dumps({
                        "event_type": "memory_synthesis",
                        "presentation": "background_task",
                        "event_id": f"memory-synthesis:artifact:{doc.id}",
                        "task_id": memory_job.id,
                        "status": "queued",
                        "title": "Memory synthesis",
                        "subject": row.title,
                        "artifact_id": doc.id,
                        "quest_id": quest_id,
                        "trigger_event_id": publish_event_id,
                    }),
                ))
            elif existing_memory_job.status in {"queued", "running"}:
                memory_job_id = existing_memory_job.id
            synth = _json_loads(row.synthesis_json, {})
            for candidate in (synth.get("memory_candidates") or [])[:3]:
                category = candidate.get("category")
                if category not in {"finding", "decision", "risk", "open_question", "entity"}:
                    category = "finding"
                title = str(candidate.get("title") or row.title).strip()
                content = str(candidate.get("content") or "").strip()
                if not content:
                    continue
                db.add(QuestMemoryEntry(
                    id=uuid.uuid4().hex,
                    session_id=quest_id,
                    category=category,
                    visibility="captain_private",
                    state="provisional",
                    title=title,
                    content=content,
                    confidence=candidate.get("confidence") if candidate.get("confidence") in {"low", "medium", "high"} else "low",
                    provenance_json=_json_dumps({"proposal_id": row.id, "document_id": doc.id, "citations": candidate.get("citations") or []}),
                    source_version_refs_json=row.source_version_refs_json,
                    evidence_chunk_refs_json=row.evidence_chunk_refs_json,
                    artifact_id=row.id,
                    artifact_revision_number=row.revision_number,
                    claim_key=row.claim_key,
                    created_by="argo",
                ))
            members = db.query(QuestMember).filter(QuestMember.session_id == quest_id, QuestMember.role == "shipmate").all()
            quest = db.query(DbSession).filter(DbSession.id == quest_id).first()
            for member in members:
                _notify(db, member.username, f"artifact-published:{proposal_id}:{member.username}", category="inbox", state="unread", title=f"Captain {captain} published '{row.title}'", message=f"Captain {captain} published '{row.title}' to {quest.name if quest else 'the Quest'}.", resource_type="quest_artifact", resource_id=doc.id, actions=[])
            _notify(db, captain, f"artifact-proposal:{row.id}", state="resolved", title="Artifact Draft published", message=f"Published '{row.title}' to the Quest.", actions=[])
            db.commit()
            if memory_job_id:
                background_tasks.add_task(_run_synthesis_job_background, memory_job_id)
            return {"proposal": _proposal_to_dict(row), "document_id": doc.id}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/artifact-proposals/{proposal_id}/decline")
    def decline_artifact_proposal(request: Request, quest_id: str, proposal_id: str):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            row = db.query(QuestArtifactProposal).filter(QuestArtifactProposal.id == proposal_id, QuestArtifactProposal.session_id == quest_id).first()
            if not row:
                raise HTTPException(404, "Artifact proposal not found")
            if row.status == "declined":
                return {"proposal": _proposal_to_dict(row), "already_declined": True}
            if row.status != "pending_review":
                raise HTTPException(409, "Artifact proposal is not pending review")
            row.status = "declined"
            row.reviewed_at = row.declined_at = utcnow_naive()
            db.add(ChatMessage(id=uuid.uuid4().hex, session_id=quest_id, role="system", content=f"Artifact Draft declined: {row.title}", meta_data=_timeline_meta("artifact_declined", title="Artifact draft declined", proposal_id=row.id, actor=captain, subject=row.title)))
            _notify(db, captain, f"artifact-proposal:{row.id}", state="resolved", title="Artifact Draft declined", message=f"Declined '{row.title}'.", actions=[])
            db.commit()
            return {"proposal": _proposal_to_dict(row)}
        finally:
            db.close()

    @router.get("/api/library/artifact-drafts")
    def library_artifact_drafts(request: Request):
        require_venture_runtime()
        captain = require_user(request)
        if not _user_is_admin(request, captain):
            raise HTTPException(403, "Only Captains can review Artifact Drafts")
        db = SessionLocal()
        try:
            rows = db.query(QuestArtifactProposal).filter(QuestArtifactProposal.captain_username == captain, QuestArtifactProposal.status == "pending_review").order_by(QuestArtifactProposal.created_at.desc()).all()
            return {"drafts": [_proposal_to_dict(r) for r in rows]}
        finally:
            db.close()

    @router.get("/api/library/artifact-drafts/{proposal_id}")
    def library_artifact_draft(request: Request, proposal_id: str):
        require_venture_runtime()
        captain = require_user(request)
        db = SessionLocal()
        try:
            row = db.query(QuestArtifactProposal).filter(QuestArtifactProposal.id == proposal_id, QuestArtifactProposal.captain_username == captain).first()
            if not row:
                raise HTTPException(404, "Artifact Draft not found")
            return {"draft": _proposal_to_dict(row, include_document=True)}
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/artifacts")
    def list_artifacts(request: Request, quest_id: str):
        require_venture_runtime()
        user = require_quest_member(request, quest_id)
        role = get_quest_role(user, quest_id)
        db = SessionLocal()
        try:
            docs_q = db.query(Document).filter(Document.session_id == quest_id, Document.archived == False, Document.is_active == True)
            docs_q = docs_q.filter(Document.owner == user) if role != "captain" else docs_q.filter(Document.owner == user)
            docs = docs_q.all()
            if role != "captain" and not docs:
                docs = db.query(Document).filter(Document.session_id == quest_id, Document.archived == False, Document.is_active == True).all()
            images = db.query(GalleryImage).filter(GalleryImage.session_id == quest_id, GalleryImage.is_active == True).all()
            return {
                "documents": [{"id": d.id, "title": d.title, "language": d.language, "updated_at": d.updated_at.isoformat() + "Z" if d.updated_at else None} for d in docs],
                "gallery": [{"id": g.id, "filename": g.filename, "prompt": g.prompt} for g in images],
            }
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/memory")
    def list_memory(request: Request, quest_id: str):
        require_venture_runtime()
        user = require_quest_member(request, quest_id)
        role = get_quest_role(user, quest_id)
        db = SessionLocal()
        try:
            q = db.query(QuestMemoryEntry).filter(QuestMemoryEntry.session_id == quest_id, QuestMemoryEntry.state != "retired")
            if role != "captain":
                q = q.filter(QuestMemoryEntry.visibility == "quest_shared")
            return {"memory": [_memory_to_dict(r) for r in q.order_by(QuestMemoryEntry.pinned.desc(), QuestMemoryEntry.updated_at.desc()).all()]}
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/memory", status_code=201)
    def create_memory(request: Request, quest_id: str, body: MemoryCreate):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        if body.category not in MEMORY_CATEGORIES or body.visibility not in MEMORY_VISIBILITIES or body.state not in MEMORY_STATES:
            raise HTTPException(400, "Invalid memory fields")
        db = SessionLocal()
        try:
            row = QuestMemoryEntry(id=uuid.uuid4().hex, session_id=quest_id, category=body.category, visibility=body.visibility, state=body.state, title=body.title, content=body.content, confidence=body.confidence, provenance_json=_json_dumps(body.provenance), source_version_refs_json=_json_dumps(body.source_version_refs), origin_event_ids_json=_json_dumps(body.origin_event_ids), created_by=captain)
            db.add(row)
            db.commit()
            return {"memory": _memory_to_dict(row)}
        finally:
            db.close()

    @router.get("/api/quests/{quest_id}/memory/{memory_id}")
    def get_memory(request: Request, quest_id: str, memory_id: str):
        require_venture_runtime()
        user = require_quest_member(request, quest_id)
        role = get_quest_role(user, quest_id)
        db = SessionLocal()
        try:
            row = db.query(QuestMemoryEntry).filter(QuestMemoryEntry.id == memory_id, QuestMemoryEntry.session_id == quest_id).first()
            if not row or (role != "captain" and row.visibility != "quest_shared"):
                raise HTTPException(404, "Memory not found")
            return {"memory": _memory_to_dict(row)}
        finally:
            db.close()

    def _mutate_memory(request: Request, quest_id: str, memory_id: str, patch: MemoryPatch):
        require_venture_runtime()
        require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            row = db.query(QuestMemoryEntry).filter(QuestMemoryEntry.id == memory_id, QuestMemoryEntry.session_id == quest_id).first()
            if not row:
                raise HTTPException(404, "Memory not found")
            for key, value in patch.model_dump(exclude_unset=True).items():
                if value is None:
                    continue
                if key == "state" and value not in MEMORY_STATES:
                    raise HTTPException(400, "Invalid memory state")
                if key == "visibility" and value not in MEMORY_VISIBILITIES:
                    raise HTTPException(400, "Invalid memory visibility")
                setattr(row, key, value)
            db.commit()
            return {"memory": _memory_to_dict(row)}
        finally:
            db.close()

    @router.patch("/api/quests/{quest_id}/memory/{memory_id}")
    def patch_memory(request: Request, quest_id: str, memory_id: str, body: MemoryPatch):
        return _mutate_memory(request, quest_id, memory_id, body)

    @router.post("/api/quests/{quest_id}/memory/{memory_id}/pin")
    def pin_memory(request: Request, quest_id: str, memory_id: str):
        return _mutate_memory(request, quest_id, memory_id, MemoryPatch(pinned=True))

    @router.post("/api/quests/{quest_id}/memory/{memory_id}/retire")
    def retire_memory(request: Request, quest_id: str, memory_id: str):
        return _mutate_memory(request, quest_id, memory_id, MemoryPatch(state="retired"))

    @router.post("/api/quests/{quest_id}/memory/{memory_id}/flag")
    def flag_memory(request: Request, quest_id: str, memory_id: str):
        require_venture_runtime()
        require_quest_member(request, quest_id)
        return {"ok": True}

    return router
