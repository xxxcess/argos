"""Argos Venture Quest APIs."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
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
    QuestMember,
    QuestMemoryEntry,
    QuestMemoryState,
    QuestSource,
    QuestSourceCheckpoint,
    QuestSourceVersion,
    Session as DbSession,
    SessionLocal,
    UserNotification,
    utcnow_naive,
)
from core.session_manager import SessionManager
from src.auth_helpers import effective_user, require_user
from src.runtime_profile import require_venture_runtime, runtime_summary
from src.venture_auth import (
    get_quest_role,
    get_visible_quest_ids,
    require_quest_captain,
    require_quest_member,
    require_quest_source_read_access,
)


SOURCE_TYPES = {"website", "file", "document", "database", "email"}
SOURCE_MODES = {"static", "dynamic"}
SOURCE_ACCESS_MODES = {"captain_only", "shared_read", "shared_summaries"}
SOURCE_STATUSES = {"active", "paused", "error", "archived"}
INVITE_STATUSES = {"pending", "accepted", "declined", "revoked", "expired"}
MEMORY_VISIBILITIES = {"captain_private", "quest_shared"}
MEMORY_STATES = {"provisional", "confirmed", "stale", "superseded", "retired"}
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
    return {
        "id": row.id,
        "session_id": row.session_id,
        "source_type": row.source_type,
        "source_mode": row.source_mode,
        "display_name": row.display_name,
        "access_mode": row.access_mode,
        "configuration": config,
        "status": row.status,
        "last_refreshed_at": row.last_refreshed_at.isoformat() + "Z" if row.last_refreshed_at else None,
        "last_processed_at": row.last_processed_at.isoformat() + "Z" if row.last_processed_at else None,
    }


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
    return {
        "id": row.id,
        "session_id": row.session_id,
        "category": row.category,
        "visibility": row.visibility,
        "state": row.state,
        "title": row.title,
        "content": row.content,
        "confidence": row.confidence,
        "provenance": _json_loads(row.provenance_json, {}),
        "source_version_refs": _json_loads(row.source_version_refs_json, []),
        "origin_event_ids": _json_loads(row.origin_event_ids_json, []),
        "created_by": row.created_by,
        "supersedes_id": row.supersedes_id,
        "pinned": row.pinned,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
        "valid_until": row.valid_until.isoformat() + "Z" if row.valid_until else None,
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
    display_name: str
    access_mode: str | None = None
    configuration: dict[str, Any] = Field(default_factory=dict)


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


def _validate_source_payload(db, captain: str, payload: SourceCreate) -> tuple[str, str]:
    source_type = payload.source_type
    if source_type not in SOURCE_TYPES:
        raise HTTPException(400, "Unsupported source type")
    source_mode = payload.source_mode or ("dynamic" if source_type == "email" else "static")
    if source_mode not in SOURCE_MODES:
        raise HTTPException(400, "Unsupported source mode")
    if source_type == "email" and source_mode != "dynamic":
        raise HTTPException(400, "Email sources must be dynamic")
    if source_type != "email" and source_mode == "dynamic":
        raise HTTPException(400, "Only Email is supported as a dynamic source")
    access_mode = payload.access_mode or ("captain_only" if source_type == "email" else "shared_read")
    if access_mode not in SOURCE_ACCESS_MODES:
        raise HTTPException(400, "Unsupported source access mode")
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
    return source_mode, access_mode


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
                if is_captain else ["Search", "Quests", "Library", "Documents", "Gallery", "Theme", "Settings"]
            ),
            "visible_feature_categories": ["quests", "documents", "gallery"] + (["email", "sources", "artifacts"] if is_captain else []),
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
            source_mode, access_mode = _validate_source_payload(db, captain, source_payload)
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
                display_name=source_payload.display_name,
                access_mode=access_mode,
                configuration_json=_json_dumps(source_payload.configuration),
            )
            db.add(src)
            db.add(QuestMemoryState(session_id=sid, current_bearing_json=_json_dumps(_bearing_to_dict(bearing))))
            db.commit()
            for shipmate in body.shipmates:
                if shipmate and shipmate != captain:
                    _create_invitation(db, request, sid, captain, shipmate, 14)
            db.commit()
            return {"quest": _session_to_quest(db.query(DbSession).filter(DbSession.id == sid).one(), "captain"), "primary_source": _source_to_dict(src, "raw")}
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
            return {"quest": _session_to_quest(row, get_quest_role(user, quest_id))}
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
            db.add(ChatMessage(id=uuid.uuid4().hex, session_id=quest_id, role="system", content="Current Bearing updated.", meta_data=_json_dumps({"event_type": "current_bearing_updated", "actor": captain})))
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
        db.add(ChatMessage(id=uuid.uuid4().hex, session_id=quest_id, role="system", content=f"{invitee} was invited to the Quest.", meta_data=_json_dumps({"event_type": "shipmate_invited", "actor": captain})))
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
                db.add(ChatMessage(id=uuid.uuid4().hex, session_id=inv.session_id, role="system", content=f"{user} joined the Quest.", meta_data=_json_dumps({"event_type": "shipmate_joined", "actor": user})))
            else:
                db.add(ChatMessage(id=uuid.uuid4().hex, session_id=inv.session_id, role="system", content=f"{user} declined the Quest invitation.", meta_data=_json_dumps({"event_type": "shipmate_declined", "actor": user})))
            _notify(db, user, f"quest-invite:{inv.id}", state="resolved", title="Quest invitation resolved", message=f"You {status} the invitation.", actions=[])
            _notify(db, inv.invited_by, f"quest-invite-response:{inv.id}", category="inbox", state="unread", title=f"{user} {status} your invitation", message=f"{user} {status} your invitation to {quest.name if quest else 'the Quest'}.", resource_type="quest_invitation", resource_id=inv.id, actions=[])
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
            return {"members": [{"username": r.username, "role": r.role, "created_at": r.created_at.isoformat() + "Z" if r.created_at else None} for r in rows]}
        finally:
            db.close()

    @router.post("/api/quests/{quest_id}/sources", status_code=201)
    def add_source(request: Request, quest_id: str, body: SourceCreate):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            source_mode, access_mode = _validate_source_payload(db, captain, body)
            row = QuestSource(id=uuid.uuid4().hex, session_id=quest_id, captain_username=captain, source_type=body.source_type, source_mode=source_mode, display_name=body.display_name, access_mode=access_mode, configuration_json=_json_dumps(body.configuration))
            db.add(row)
            if source_mode == "dynamic":
                db.add(QuestSourceCheckpoint(id=uuid.uuid4().hex, quest_source_id=row.id, cursor=str(body.configuration.get("initial_cursor") or "")))
            db.add(ChatMessage(id=uuid.uuid4().hex, session_id=quest_id, role="system", content=f"Quest Source connected: {body.display_name}", meta_data=_json_dumps({"event_type": "source_connected", "source_id": row.id, "actor": captain})))
            db.commit()
            return {"source": _source_to_dict(row, "raw")}
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
            now = utcnow_naive()
            cfg = _json_loads(row.configuration_json, {})
            fingerprint = hashlib.sha256(_json_dumps({"source": row.id, "cfg": cfg, "time": now.isoformat()}).encode("utf-8")).hexdigest()
            version = QuestSourceVersion(id=uuid.uuid4().hex, quest_source_id=row.id, version_label=f"v{len(row.versions) + 1}", source_fingerprint=fingerprint, provenance_json=_json_dumps({"refreshed_by": captain, "source_type": row.source_type}), captured_at=now)
            db.add(version)
            row.last_refreshed_at = now
            if row.source_mode == "dynamic":
                cp = row.checkpoint or QuestSourceCheckpoint(id=uuid.uuid4().hex, quest_source_id=row.id)
                cp.last_polled_at = now
                cp.last_success_at = now
                cp.cursor = hashlib.sha256(f"{row.id}:{now.isoformat()}".encode()).hexdigest()
                db.add(cp)
            db.add(ChatMessage(id=uuid.uuid4().hex, session_id=quest_id, role="system", content=f"Quest Source refreshed: {row.display_name}", meta_data=_json_dumps({"event_type": "source_refreshed", "source_id": row.id, "version_id": version.id, "actor": captain})))
            db.commit()
            return {"source": _source_to_dict(row, "raw"), "version_id": version.id}
        except Exception:
            db.rollback()
            raise
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

    def _proposal_document_content(body: ProposalCreate) -> str:
        evidence = "\n".join(f"- {item}" for item in body.evidence_refs) or "- Evidence reference pending"
        return f"""# {body.title}

## What changed or was discovered
{body.summary or 'A possible Quest finding was identified from scoped evidence.'}

## Why it matters
This may affect the Quest's Current Bearing and should be reviewed by the Captain.

## Evidence
{evidence}

## Confidence and uncertainty
Confidence: low until the Captain reviews the underlying source evidence. Unsupported conclusions must not be treated as confirmed.

## Recommended next bearing
Review the cited evidence, validate the pattern, and decide whether to publish this as a Quest Artifact.
"""

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
            db.add(ChatMessage(id=uuid.uuid4().hex, session_id=quest_id, role="system", content="Artifact Draft created for Captain review.", meta_data=_json_dumps({"event_type": "artifact_draft_created", "proposal_id": proposal.id, "actor": "argo"})))
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
    def publish_artifact_proposal(request: Request, quest_id: str, proposal_id: str):
        require_venture_runtime()
        captain = require_quest_captain(request, quest_id)
        db = SessionLocal()
        try:
            row = db.query(QuestArtifactProposal).filter(QuestArtifactProposal.id == proposal_id, QuestArtifactProposal.session_id == quest_id).with_for_update().first()
            if not row:
                raise HTTPException(404, "Artifact proposal not found")
            if row.status == "published":
                return {"proposal": _proposal_to_dict(row), "already_published": True}
            if row.status != "pending_review":
                raise HTTPException(409, "Artifact proposal is not pending review")
            doc = db.query(Document).filter(Document.id == row.document_id, Document.owner == captain).first()
            if not doc:
                raise HTTPException(404, "Artifact Draft not found")
            doc.session_id = quest_id
            doc.is_active = True
            row.status = "published"
            row.reviewed_at = row.published_at = utcnow_naive()
            db.add(ChatMessage(id=uuid.uuid4().hex, session_id=quest_id, role="system", content=f"Quest Artifact published: {row.title}", meta_data=_json_dumps({"event_type": "artifact_published", "proposal_id": row.id, "document_id": doc.id, "actor": captain})))
            db.add(QuestMemoryEntry(id=uuid.uuid4().hex, session_id=quest_id, category="artifact_reference", visibility="quest_shared", state="confirmed", title=row.title, content=row.summary, confidence="high", provenance_json=_json_dumps({"proposal_id": row.id, "document_id": doc.id}), source_version_refs_json=row.source_version_refs_json, created_by="argo"))
            members = db.query(QuestMember).filter(QuestMember.session_id == quest_id, QuestMember.role == "shipmate").all()
            quest = db.query(DbSession).filter(DbSession.id == quest_id).first()
            for member in members:
                _notify(db, member.username, f"artifact-published:{proposal_id}:{member.username}", category="inbox", state="unread", title=f"Captain {captain} published '{row.title}'", message=f"Captain {captain} published '{row.title}' to {quest.name if quest else 'the Quest'}.", resource_type="quest_artifact", resource_id=doc.id, actions=[])
            _notify(db, captain, f"artifact-proposal:{row.id}", state="resolved", title="Artifact Draft published", message=f"Published '{row.title}' to the Quest.", actions=[])
            db.commit()
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
            db.add(ChatMessage(id=uuid.uuid4().hex, session_id=quest_id, role="system", content=f"Artifact Draft declined: {row.title}", meta_data=_json_dumps({"event_type": "artifact_declined", "proposal_id": row.id, "actor": captain})))
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
        require_quest_member(request, quest_id)
        db = SessionLocal()
        try:
            docs = db.query(Document).filter(Document.session_id == quest_id, Document.archived == False).all()
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

