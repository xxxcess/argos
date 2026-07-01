"""Argos Venture authorization helpers.

These helpers are runtime-gated and return non-enumerating 404 for inaccessible
Quest reads. They intentionally use the stored Quest Captain identity
(``sessions.owner``) plus accepted ``QuestMember`` rows only; pending invitations
grant no access.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request

from core.database import (
    Document,
    GalleryImage,
    QuestMember,
    QuestMemoryEntry,
    QuestSource,
    Session as DbSession,
    SessionLocal,
)
from src.auth_helpers import effective_user, require_user
from src.runtime_profile import is_venture_runtime


def _user(request: Request) -> str:
    return require_user(request)


def _session_row(db, session_id: str) -> Optional[DbSession]:
    return db.query(DbSession).filter(DbSession.id == session_id).first()


def _captain_username(db, session_id: str) -> Optional[str]:
    row = _session_row(db, session_id)
    return row.owner if row else None


def is_quest_captain(request: Request, session_id: str) -> bool:
    user = effective_user(request)
    if not user:
        return False
    db = SessionLocal()
    try:
        return _captain_username(db, session_id) == user
    finally:
        db.close()


def get_quest_role(user: str | None, session_id: str) -> Optional[str]:
    if not user:
        return None
    db = SessionLocal()
    try:
        captain = _captain_username(db, session_id)
        if captain == user:
            return "captain"
        member = db.query(QuestMember).filter(
            QuestMember.session_id == session_id,
            QuestMember.username == user,
        ).first()
        return member.role if member else None
    finally:
        db.close()


def is_quest_member(request: Request, session_id: str) -> bool:
    user = effective_user(request)
    return get_quest_role(user, session_id) in {"captain", "shipmate"}


def require_quest_captain(request: Request, session_id: str) -> str:
    user = effective_user(request) or _user(request)
    if get_quest_role(user, session_id) != "captain":
        db = SessionLocal()
        try:
            if _session_row(db, session_id) is None:
                raise HTTPException(404, "Quest not found")
        finally:
            db.close()
        raise HTTPException(403, "Quest Captain required")
    return user


def require_quest_member(request: Request, session_id: str) -> str:
    user = effective_user(request) or _user(request)
    if get_quest_role(user, session_id) not in {"captain", "shipmate"}:
        raise HTTPException(404, "Quest not found")
    return user


def get_visible_quest_ids(user: str | None) -> list[str]:
    if not user:
        return []
    db = SessionLocal()
    try:
        owned = [r[0] for r in db.query(DbSession.id).filter(DbSession.owner == user).all()]
        joined = [
            r[0]
            for r in db.query(QuestMember.session_id)
            .filter(QuestMember.username == user)
            .all()
        ]
        return sorted(set(owned + joined))
    finally:
        db.close()


def require_shipmate_plain_chat(request: Request, session_id: str) -> None:
    if not is_venture_runtime():
        return
    role = get_quest_role(effective_user(request), session_id)
    if role != "shipmate":
        return
    forbidden = {
        "attachments",
        "use_web",
        "use_research",
        "allow_bash",
        "allow_web_search",
        "use_rag",
        "workspace",
        "preset_id",
        "mode",
    }
    raise_detail = "Shipmates can send plain Quest messages only"
    if request.method == "POST":
        # Callers that need form parsing should use the async helper in routes.
        pass
    if any(k in request.query_params for k in forbidden):
        raise HTTPException(403, raise_detail)


def require_quest_source_read_access(request: Request, source: QuestSource) -> str:
    user = require_quest_member(request, source.session_id)
    role = get_quest_role(user, source.session_id)
    if role == "captain":
        return "raw"
    if source.access_mode == "shared_read":
        return "raw"
    if source.access_mode == "shared_summaries":
        return "summary"
    raise HTTPException(404, "Quest source not found")


def require_document_read_access(request: Request, document: Document) -> None:
    user = effective_user(request) or _user(request)
    if not is_venture_runtime() or not document.session_id:
        if user and document.owner not in (user, None):
            raise HTTPException(404, "Document not found")
        return
    require_quest_member(request, document.session_id)


def require_gallery_read_access(request: Request, image: GalleryImage) -> None:
    user = effective_user(request) or _user(request)
    if not is_venture_runtime() or not image.session_id:
        if user and image.owner not in (user, None):
            raise HTTPException(404, "Image not found")
        return
    require_quest_member(request, image.session_id)


def require_quest_artifact_write_access(request: Request, session_id: str) -> str:
    return require_quest_captain(request, session_id)


def require_quest_memory_access(request: Request, session_id: str, visibility: str) -> str:
    user = require_quest_member(request, session_id)
    role = get_quest_role(user, session_id)
    if role == "captain":
        return user
    if visibility == "quest_shared":
        return user
    raise HTTPException(404, "Memory not found")


def assert_memory_row_access(request: Request, row: QuestMemoryEntry) -> str:
    return require_quest_memory_access(request, row.session_id, row.visibility)

