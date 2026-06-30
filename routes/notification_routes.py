"""Persisted user notification APIs."""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from core.database import SessionLocal, UserNotification, utcnow_naive
from src.auth_helpers import get_current_user
from src.task_notifications import dispatch_pending_outbox_once


class NotificationMarkRead(BaseModel):
    read: bool = True


def setup_notification_routes() -> APIRouter:
    router = APIRouter(prefix="/api/notifications", tags=["notifications"])

    def _user(request: Request) -> str:
        return get_current_user(request) or ""

    def _encode_cursor(dt: datetime, row_id: str) -> str:
        raw = f"{dt.isoformat()}|{row_id}".encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    def _decode_cursor(cursor: str | None) -> tuple[datetime, str] | None:
        if not cursor:
            return None
        try:
            padded = cursor + ("=" * (-len(cursor) % 4))
            raw = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
            ts, row_id = raw.split("|", 1)
            return datetime.fromisoformat(ts), row_id
        except Exception:
            return None

    def _notification_to_dict(row: UserNotification) -> dict:
        return {
            "id": row.id,
            "category": row.category,
            "severity": row.severity,
            "state": row.state,
            "title": row.title,
            "message": row.message,
            "task_id": row.task_id,
            "event_id": row.event_id,
            "interaction_id": row.interaction_id,
            "action_label": row.action_label,
            "action_url": row.action_url,
            "resource_type": row.resource_type,
            "resource_id": row.resource_id,
            "read": row.read_at is not None,
            "archived": row.archived_at is not None,
            "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
            "updated_at": row.updated_at.isoformat() + "Z" if row.updated_at else None,
        }

    def _owned_query(db, user: str):
        return db.query(UserNotification).filter(UserNotification.user_id == user)

    @router.get("")
    async def list_notifications(
        request: Request,
        view: Optional[str] = None,
        cursor: Optional[str] = None,
        limit: int = 30,
        include_archived: bool = False,
    ):
        dispatch_pending_outbox_once(limit=50)
        user = _user(request)
        limit = max(1, min(limit, 100))
        db = SessionLocal()
        try:
            q = _owned_query(db, user)
            if view in {"activity", "progress", "inbox"}:
                q = q.filter(UserNotification.category == view)
            if not include_archived:
                q = q.filter(UserNotification.archived_at.is_(None))
            if view == "progress":
                q = q.filter(UserNotification.state.in_(("active", "waiting", "retrying")))
            decoded = _decode_cursor(cursor)
            if decoded:
                ts, row_id = decoded
                q = q.filter(
                    (UserNotification.updated_at < ts)
                    | ((UserNotification.updated_at == ts) & (UserNotification.id < row_id))
                )
            rows = q.order_by(UserNotification.updated_at.desc(), UserNotification.id.desc()).limit(limit + 1).all()
            next_cursor = None
            if len(rows) > limit:
                last = rows[limit - 1]
                next_cursor = _encode_cursor(last.updated_at, last.id)
                rows = rows[:limit]
            return {"notifications": [_notification_to_dict(row) for row in rows], "next_cursor": next_cursor}
        finally:
            db.close()

    @router.get("/unread-count")
    async def unread_count(request: Request):
        dispatch_pending_outbox_once(limit=50)
        user = _user(request)
        db = SessionLocal()
        try:
            actionable = _owned_query(db, user).filter(
                UserNotification.archived_at.is_(None),
                UserNotification.category == "inbox",
                UserNotification.state == "action_required",
            ).count()
            unread = _owned_query(db, user).filter(
                UserNotification.archived_at.is_(None),
                UserNotification.category == "inbox",
                UserNotification.read_at.is_(None),
                UserNotification.state != "resolved",
            ).count()
            return {"unread": int(unread or 0), "actionable": int(actionable or 0)}
        finally:
            db.close()

    @router.get("/{notification_id}")
    async def get_notification(request: Request, notification_id: str):
        user = _user(request)
        db = SessionLocal()
        try:
            row = _owned_query(db, user).filter(UserNotification.id == notification_id).first()
            if not row:
                raise HTTPException(404, "Notification not found")
            return _notification_to_dict(row)
        finally:
            db.close()

    @router.post("/{notification_id}/read")
    async def mark_notification_read(request: Request, notification_id: str, body: NotificationMarkRead | None = None):
        user = _user(request)
        db = SessionLocal()
        try:
            row = _owned_query(db, user).filter(UserNotification.id == notification_id).first()
            if not row:
                raise HTTPException(404, "Notification not found")
            read = True if body is None else bool(body.read)
            row.read_at = utcnow_naive() if read else None
            row.updated_at = utcnow_naive()
            db.commit()
            return {"ok": True, "notification": _notification_to_dict(row)}
        finally:
            db.close()

    @router.post("/{notification_id}/archive")
    async def archive_notification(request: Request, notification_id: str):
        user = _user(request)
        db = SessionLocal()
        try:
            row = _owned_query(db, user).filter(UserNotification.id == notification_id).first()
            if not row:
                raise HTTPException(404, "Notification not found")
            row.archived_at = utcnow_naive()
            row.updated_at = utcnow_naive()
            db.commit()
            return {"ok": True, "notification": _notification_to_dict(row)}
        finally:
            db.close()

    @router.post("/mark-read")
    async def mark_eligible_read(request: Request, view: Optional[str] = None):
        user = _user(request)
        db = SessionLocal()
        try:
            q = _owned_query(db, user).filter(UserNotification.archived_at.is_(None))
            if view in {"activity", "progress", "inbox"}:
                q = q.filter(UserNotification.category == view)
            now = utcnow_naive()
            count = 0
            for row in q.all():
                if row.read_at is None:
                    row.read_at = now
                    row.updated_at = now
                    count += 1
            db.commit()
            return {"ok": True, "updated": count}
        finally:
            db.close()

    return router

