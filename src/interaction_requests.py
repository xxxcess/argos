"""Durable user interaction requests for agent questions and task pauses."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from core.database import InteractionRequest, SessionLocal, UserNotification, utcnow_naive


def _safe_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _safe_payload(payload: dict[str, Any]) -> dict[str, Any]:
    question = _safe_text(payload.get("question"), 500)
    options = []
    for opt in payload.get("options") or []:
        if isinstance(opt, dict):
            options.append({
                "label": _safe_text(opt.get("label"), 100),
                "description": _safe_text(opt.get("description"), 220),
            })
        else:
            options.append({"label": _safe_text(opt, 100), "description": ""})
    return {
        "kind": payload.get("kind") or "choice",
        "question": question,
        "options": options[:8],
        "multi": bool(payload.get("multi")),
        "sensitive": bool(payload.get("sensitive")),
    }


def _interaction_key(*, owner: str, session_id: str, payload: dict[str, Any]) -> str:
    body = json.dumps(_safe_payload(payload), sort_keys=True, ensure_ascii=False)
    digest = hashlib.sha256(f"{owner}|{session_id}|{body}".encode("utf-8")).hexdigest()[:24]
    return f"ask-user:{owner or 'local'}:{session_id}:{digest}"


def create_session_interaction_request(*, owner: str | None, session_id: str | None, payload: dict[str, Any]) -> str | None:
    if not session_id or not payload.get("question"):
        return None
    user_id = owner or ""
    safe = _safe_payload(payload)
    if not safe["options"] or len(safe["options"]) < 2:
        return None
    key = _interaction_key(owner=user_id, session_id=session_id, payload=safe)
    db = SessionLocal()
    try:
        row = db.query(InteractionRequest).filter(InteractionRequest.idempotency_key == key).first()
        now = utcnow_naive()
        if row is not None and row.status in {"resolved", "cancelled", "expired"}:
            key = f"{key}:{uuid.uuid4().hex[:8]}"
            row = None
        if row is None:
            row = InteractionRequest(
                id=str(uuid.uuid4()),
                task_id=None,
                session_id=session_id,
                target_user=user_id,
                request_type=safe["kind"] or "choice",
                status="pending",
                idempotency_key=key,
                presentation_payload=json.dumps(safe, ensure_ascii=False),
                sensitive=bool(safe.get("sensitive")),
                created_at=now,
                updated_at=now,
            )
            db.add(row)
        note_key = f"inbox:interaction:{user_id}:{row.id}"
        note = db.query(UserNotification).filter(UserNotification.deterministic_key == note_key).first()
        if note is None:
            note = UserNotification(
                id=str(uuid.uuid4()),
                deterministic_key=note_key,
                user_id=user_id,
                owner=user_id or None,
                category="inbox",
                created_at=now,
            )
            db.add(note)
        note.interaction_id = row.id
        note.category = "inbox"
        note.severity = "warning"
        note.state = "action_required"
        note.title = "Agent needs your response"
        note.message = _safe_text(safe["question"], 500)
        note.action_label = "Answer"
        note.action_url = f"#{session_id}"
        note.resource_type = "session"
        note.resource_id = session_id
        note.archived_at = None
        note.updated_at = now
        db.commit()
        return row.id
    finally:
        db.close()


def resolve_interaction_request(*, owner: str | None, interaction_id: str, response_summary: str = "") -> bool:
    user_id = owner or ""
    db = SessionLocal()
    try:
        row = db.query(InteractionRequest).filter(
            InteractionRequest.id == interaction_id,
            InteractionRequest.target_user == user_id,
        ).first()
        if not row:
            return False
        if row.status != "pending":
            return True
        now = utcnow_naive()
        row.status = "resolved"
        row.resolved_at = now
        row.response_summary = _safe_text(response_summary, 500)
        row.updated_at = now
        notes = db.query(UserNotification).filter(
            UserNotification.interaction_id == interaction_id,
            UserNotification.user_id == user_id,
            UserNotification.category == "inbox",
            UserNotification.state == "action_required",
        ).all()
        for note in notes:
            note.state = "resolved"
            note.read_at = note.read_at or now
            note.archived_at = now
            note.updated_at = now
        db.commit()
        return True
    finally:
        db.close()
