"""Durable task-event outbox and user-notification projection.

This module is intentionally service-layer code: task workers emit events at
the state-transition boundary, then the projector converts those events into
user-scoped records. The browser only reads persisted notification rows.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta
from typing import Any, Iterable

from core.database import utcnow_naive

logger = logging.getLogger(__name__)

TASK_CREATED = "TASK_CREATED"
TASK_STARTED = "TASK_STARTED"
TASK_PROGRESS_REPORTED = "TASK_PROGRESS_REPORTED"
TASK_WAITING_FOR_USER = "TASK_WAITING_FOR_USER"
TASK_RESUMED = "TASK_RESUMED"
TASK_SUCCEEDED = "TASK_SUCCEEDED"
TASK_FAILED = "TASK_FAILED"
TASK_CANCELLED = "TASK_CANCELLED"
TASK_RETRY_SCHEDULED = "TASK_RETRY_SCHEDULED"
TASK_RETRYING = "TASK_RETRYING"
INTERACTION_REQUESTED = "INTERACTION_REQUESTED"
INTERACTION_RESOLVED = "INTERACTION_RESOLVED"
INTERACTION_EXPIRED = "INTERACTION_EXPIRED"

MAX_SAFE_MESSAGE = 600
MAX_SAFE_TITLE = 120


def _safe_text(value: Any, limit: int = MAX_SAFE_MESSAGE) -> str:
    text = str(value or "").replace("\x00", "").strip()
    # Keep notifications concise and prevent obvious secret-ish blobs from being
    # mirrored into the inbox. Full run output stays in the authorized task view.
    lowered = text.lower()
    if any(marker in lowered for marker in ("authorization:", "bearer ", "api_key", "api key", "secret=", "password=")):
        text = "Details are available in the task activity view."
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _json_loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _task_display_name(task: Any) -> str:
    return _safe_text(getattr(task, "name", None) or "Task", MAX_SAFE_TITLE)


def _run_state(run: Any | None) -> str:
    return _safe_text(getattr(run, "status", None) or "", 40)


def _task_type(task: Any) -> str:
    return _safe_text(getattr(task, "task_type", None) or "llm", 40)


def _notification_enabled(task: Any) -> bool:
    try:
        return bool(getattr(task, "notifications_enabled", True))
    except Exception:
        return True


def _recipient_for_task(task: Any) -> str:
    return getattr(task, "owner", None) or ""


def _event_payload(task: Any, run: Any | None, event_type: str, safe_message: str | None, extra: dict[str, Any] | None) -> dict[str, Any]:
    output_target = getattr(task, "output_target", None) or "session"
    return {
        "task_id": getattr(task, "id", None),
        "run_id": getattr(run, "id", None) if run is not None else None,
        "task_name": _task_display_name(task),
        "task_type": _task_type(task),
        "task_action": _safe_text(getattr(task, "action", None) or "", 80),
        "task_owner": getattr(task, "owner", None),
        "output_target": output_target,
        "notifications_enabled": _notification_enabled(task),
        "run_status": _run_state(run),
        "safe_message": _safe_text(safe_message or getattr(run, "error", None) or getattr(run, "result", None) or ""),
        "event_type": event_type,
        **(extra or {}),
    }


def append_task_event(
    db: Any,
    task: Any,
    *,
    event_type: str,
    run: Any | None = None,
    safe_message: str | None = None,
    actor_type: str = "system",
    actor_id: str | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
    extra: dict[str, Any] | None = None,
) -> Any | None:
    """Append TaskEvent + OutboxMessage inside the caller's transaction."""
    task_id_for_log = getattr(task, "id", None) if task is not None else None
    if not task or not task_id_for_log:
        return None
    try:
        try:
            from sqlalchemy import inspect as _sa_inspect
            bind = db.get_bind()
            inspector = _sa_inspect(bind)
            if not (inspector.has_table("task_events") and inspector.has_table("outbox_messages")):
                return None
        except Exception:
            return None

        from core.database import TaskEvent, OutboxMessage

        current_version = int(getattr(task, "state_version", 0) or 0) + 1
        task.state_version = current_version
        payload = _event_payload(task, run, event_type, safe_message, extra)
        event = TaskEvent(
            id=str(uuid.uuid4()),
            schema_version=1,
            task_id=task.id,
            run_id=getattr(run, "id", None) if run is not None else None,
            owner=getattr(task, "owner", None),
            event_type=event_type,
            state=getattr(task, "status", None) or _run_state(run) or None,
            state_version=current_version,
            occurred_at=utcnow_naive(),
            actor_type=actor_type,
            actor_id=actor_id,
            correlation_id=correlation_id or getattr(run, "id", None) or task.id,
            causation_id=causation_id,
            display_payload=_json_dumps(payload),
            notification_key=f"task:{task.id}:{getattr(run, 'id', '')}:{event_type}:{current_version}",
        )
        db.add(event)
        db.flush()
        db.add(
            OutboxMessage(
                id=str(uuid.uuid4()),
                aggregate_type="task",
                aggregate_id=task.id,
                event_id=event.id,
                payload=event.display_payload or "{}",
                dispatch_state="pending",
                next_retry_at=utcnow_naive(),
            )
        )
        return event
    except Exception:
        logger.exception("Failed to append task event %s for task %s", event_type, task_id_for_log)
        return None


def _recipients_for_payload(payload: dict[str, Any]) -> list[str]:
    owner = payload.get("task_owner")
    return [str(owner or "")]


def _upsert_notification(
    db: Any,
    *,
    key: str,
    user_id: str,
    category: str,
    severity: str,
    state: str,
    title: str,
    message: str,
    task_id: str | None,
    event_id: str | None,
    interaction_id: str | None = None,
    action_label: str | None = None,
    action_url: str | None = None,
    resource_type: str | None = "task",
    resource_id: str | None = None,
    archived: bool = False,
) -> Any:
    from core.database import UserNotification

    now = utcnow_naive()
    row = db.query(UserNotification).filter(UserNotification.deterministic_key == key).first()
    if row is None:
        for pending in getattr(db, "new", ()):
            if (
                isinstance(pending, UserNotification)
                and getattr(pending, "deterministic_key", None) == key
            ):
                row = pending
                break
    if row is None:
        row = UserNotification(
            id=str(uuid.uuid4()),
            deterministic_key=key,
            user_id=user_id,
            owner=user_id or None,
            category=category,
            created_at=now,
        )
        db.add(row)
    row.user_id = user_id
    row.owner = user_id or None
    row.task_id = task_id
    row.event_id = event_id
    row.interaction_id = interaction_id
    row.category = category
    row.severity = severity
    row.state = state
    row.title = _safe_text(title, MAX_SAFE_TITLE)
    row.message = _safe_text(message, MAX_SAFE_MESSAGE)
    row.action_label = action_label
    row.action_url = action_url
    row.resource_type = resource_type
    row.resource_id = resource_id or task_id
    row.archived_at = now if archived else None
    row.updated_at = now
    return row


def _resolve_inbox_for_task(db: Any, *, task_id: str, user_id: str, state: str) -> None:
    from core.database import UserNotification

    now = utcnow_naive()
    q = db.query(UserNotification).filter(
        UserNotification.user_id == user_id,
        UserNotification.task_id == task_id,
        UserNotification.category == "inbox",
        UserNotification.state == state,
        UserNotification.archived_at.is_(None),
    )
    for row in q.all():
        row.state = "resolved"
        row.archived_at = now
        row.updated_at = now


def project_task_event(db: Any, outbox: Any) -> int:
    """Project one outbox task event into idempotent user notifications."""
    from core.database import TaskEvent

    event = getattr(outbox, "event", None)
    if event is None and getattr(outbox, "event_id", None):
        event = db.query(TaskEvent).filter(TaskEvent.id == outbox.event_id).first()
    if event is None:
        return 0
    payload = _json_loads(getattr(outbox, "payload", None) or getattr(event, "display_payload", None))
    event_type = getattr(event, "event_type", None) or payload.get("event_type") or ""
    task_id = payload.get("task_id") or getattr(event, "task_id", None)
    run_id = payload.get("run_id") or getattr(event, "run_id", None)
    task_name = payload.get("task_name") or "Task"
    msg = payload.get("safe_message") or ""
    recipients = _recipients_for_payload(payload)
    wrote = 0

    for user_id in recipients:
        activity_key = f"activity:{user_id}:{event.id}"
        _upsert_notification(
            db,
            key=activity_key,
            user_id=user_id,
            category="activity",
            severity="error" if event_type == TASK_FAILED else "info",
            state="seen",
            title=_activity_title(event_type, task_name),
            message=_activity_message(event_type, msg),
            task_id=task_id,
            event_id=event.id,
            action_label="Open task",
            action_url=f"/tasks#{task_id}" if task_id else "/tasks",
            resource_id=task_id,
        )
        wrote += 1

        progress_key = f"progress:{user_id}:{task_id}"
        if event_type in {TASK_CREATED, TASK_STARTED, TASK_PROGRESS_REPORTED, TASK_RETRY_SCHEDULED, TASK_RETRYING}:
            _upsert_notification(
                db,
                key=progress_key,
                user_id=user_id,
                category="progress",
                severity="info",
                state="active" if event_type != TASK_RETRY_SCHEDULED else "retrying",
                title=task_name,
                message=msg or _progress_message(event_type),
                task_id=task_id,
                event_id=event.id,
                action_label="Open task",
                action_url=f"/tasks#{task_id}" if task_id else "/tasks",
                resource_id=task_id,
            )
            wrote += 1
        elif event_type in {TASK_WAITING_FOR_USER, INTERACTION_REQUESTED}:
            _upsert_notification(
                db,
                key=progress_key,
                user_id=user_id,
                category="progress",
                severity="warning",
                state="waiting",
                title=task_name,
                message=msg or "Waiting for your response.",
                task_id=task_id,
                event_id=event.id,
                action_label="Respond",
                action_url=f"/tasks#{task_id}" if task_id else "/tasks",
                resource_id=task_id,
            )
            _upsert_notification(
                db,
                key=f"inbox:action:{user_id}:{task_id}:{payload.get('interaction_id') or run_id or event.id}",
                user_id=user_id,
                category="inbox",
                severity="warning",
                state="action_required",
                title=f"{task_name} needs your response",
                message=msg or "A task is waiting for your input.",
                task_id=task_id,
                event_id=event.id,
                interaction_id=payload.get("interaction_id"),
                action_label="Respond",
                action_url=f"/tasks#{task_id}" if task_id else "/tasks",
                resource_id=task_id,
            )
            wrote += 2
        elif event_type in {TASK_RESUMED, INTERACTION_RESOLVED, INTERACTION_EXPIRED}:
            _resolve_inbox_for_task(db, task_id=task_id, user_id=user_id, state="action_required")
            _upsert_notification(
                db,
                key=progress_key,
                user_id=user_id,
                category="progress",
                severity="info",
                state="active" if event_type != INTERACTION_EXPIRED else "cancelled",
                title=task_name,
                message=_progress_message(event_type),
                task_id=task_id,
                event_id=event.id,
                action_label="Open task",
                action_url=f"/tasks#{task_id}" if task_id else "/tasks",
                resource_id=task_id,
                archived=(event_type == INTERACTION_EXPIRED),
            )
            wrote += 1
        elif event_type == TASK_SUCCEEDED:
            _upsert_notification(
                db,
                key=progress_key,
                user_id=user_id,
                category="progress",
                severity="success",
                state="completed",
                title=task_name,
                message=msg or "Task completed.",
                task_id=task_id,
                event_id=event.id,
                action_label="Open task",
                action_url=f"/tasks#{task_id}" if task_id else "/tasks",
                resource_id=task_id,
                archived=True,
            )
            if payload.get("notifications_enabled", True) and payload.get("task_type") in {"llm", "research"}:
                _upsert_notification(
                    db,
                    key=f"inbox:done:{user_id}:{task_id}:{run_id or event.id}",
                    user_id=user_id,
                    category="inbox",
                    severity="success",
                    state="unread",
                    title=f"{task_name} finished",
                    message=msg or "Task completed.",
                    task_id=task_id,
                    event_id=event.id,
                    action_label="Open task",
                    action_url=f"/tasks#{task_id}" if task_id else "/tasks",
                    resource_id=task_id,
                )
                wrote += 1
            wrote += 1
        elif event_type == TASK_FAILED:
            _upsert_notification(
                db,
                key=progress_key,
                user_id=user_id,
                category="progress",
                severity="error",
                state="failed",
                title=task_name,
                message=msg or "Task failed.",
                task_id=task_id,
                event_id=event.id,
                action_label="Open task",
                action_url=f"/tasks#{task_id}" if task_id else "/tasks",
                resource_id=task_id,
                archived=True,
            )
            _upsert_notification(
                db,
                key=f"inbox:failed:{user_id}:{task_id}:{run_id or event.id}",
                user_id=user_id,
                category="inbox",
                severity="error",
                state="unread",
                title=f"{task_name} failed",
                message=msg or "Task failed. Open the task activity for details.",
                task_id=task_id,
                event_id=event.id,
                action_label="Open task",
                action_url=f"/tasks#{task_id}" if task_id else "/tasks",
                resource_id=task_id,
            )
            wrote += 2
        elif event_type == TASK_CANCELLED:
            _upsert_notification(
                db,
                key=progress_key,
                user_id=user_id,
                category="progress",
                severity="warning",
                state="cancelled",
                title=task_name,
                message=msg or "Task was cancelled.",
                task_id=task_id,
                event_id=event.id,
                action_label="Open task",
                action_url=f"/tasks#{task_id}" if task_id else "/tasks",
                resource_id=task_id,
                archived=True,
            )
            wrote += 1

    return wrote


def _activity_title(event_type: str, task_name: str) -> str:
    labels = {
        TASK_CREATED: "Task queued",
        TASK_STARTED: "Task started",
        TASK_PROGRESS_REPORTED: "Task progress",
        TASK_WAITING_FOR_USER: "Task waiting",
        TASK_RESUMED: "Task resumed",
        TASK_SUCCEEDED: "Task finished",
        TASK_FAILED: "Task failed",
        TASK_CANCELLED: "Task cancelled",
        TASK_RETRY_SCHEDULED: "Task retry scheduled",
        TASK_RETRYING: "Task retrying",
        INTERACTION_REQUESTED: "Input requested",
        INTERACTION_RESOLVED: "Input resolved",
        INTERACTION_EXPIRED: "Input expired",
    }
    return f"{labels.get(event_type, 'Task update')}: {task_name}"


def _activity_message(event_type: str, message: str) -> str:
    return message or _progress_message(event_type)


def _progress_message(event_type: str) -> str:
    return {
        TASK_CREATED: "Queued.",
        TASK_STARTED: "Running.",
        TASK_PROGRESS_REPORTED: "Progress updated.",
        TASK_WAITING_FOR_USER: "Waiting for your response.",
        TASK_RESUMED: "Resumed.",
        TASK_SUCCEEDED: "Completed.",
        TASK_FAILED: "Failed.",
        TASK_CANCELLED: "Cancelled.",
        TASK_RETRY_SCHEDULED: "Retry scheduled.",
        TASK_RETRYING: "Retrying.",
        INTERACTION_REQUESTED: "Waiting for your response.",
        INTERACTION_RESOLVED: "Response received.",
        INTERACTION_EXPIRED: "Request expired.",
    }.get(event_type, "Updated.")


def dispatch_pending_outbox_once(limit: int = 100) -> int:
    """Dispatch pending notification outbox records.

    At-least-once delivery is harmless because UserNotification rows are
    idempotently keyed.
    """
    from core.database import SessionLocal, OutboxMessage

    db = SessionLocal()
    dispatched = 0
    try:
        now = utcnow_naive()
        rows = (
            db.query(OutboxMessage)
            .filter(OutboxMessage.dispatch_state.in_(("pending", "retry")))
            .filter((OutboxMessage.next_retry_at == None) | (OutboxMessage.next_retry_at <= now))  # noqa: E711
            .order_by(OutboxMessage.created_at.asc())
            .limit(max(1, min(limit, 500)))
            .all()
        )
        for row in rows:
            try:
                project_task_event(db, row)
                row.dispatch_state = "dispatched"
                row.dispatched_at = utcnow_naive()
                row.last_error = None
                dispatched += 1
                db.commit()
            except Exception as exc:
                db.rollback()
                retry_db = SessionLocal()
                try:
                    fresh = retry_db.query(OutboxMessage).filter(OutboxMessage.id == row.id).first()
                    if fresh:
                        fresh.retry_count = int(fresh.retry_count or 0) + 1
                        fresh.dispatch_state = "dead_letter" if fresh.retry_count >= 8 else "retry"
                        delay = min(300, 2 ** min(fresh.retry_count, 8))
                        fresh.next_retry_at = utcnow_naive() + timedelta(seconds=delay)
                        fresh.last_error = _safe_text(f"{type(exc).__name__}: {exc}", 500)
                        retry_db.commit()
                finally:
                    retry_db.close()
                logger.warning("Task notification projection failed for outbox %s", row.id, exc_info=exc)
    finally:
        db.close()
    return dispatched


def dispatch_outbox_for_events(event_ids: Iterable[str]) -> int:
    """Best-effort immediate projection for freshly committed events."""
    ids = [event_id for event_id in event_ids if event_id]
    if not ids:
        return 0
    from core.database import SessionLocal, OutboxMessage

    db = SessionLocal()
    count = 0
    try:
        rows = db.query(OutboxMessage).filter(
            OutboxMessage.event_id.in_(ids),
            OutboxMessage.dispatch_state.in_(("pending", "retry")),
        ).all()
        for row in rows:
            try:
                project_task_event(db, row)
                row.dispatch_state = "dispatched"
                row.dispatched_at = utcnow_naive()
                row.last_error = None
                count += 1
                db.commit()
            except Exception:
                db.rollback()
                logger.warning("Immediate task notification projection failed for outbox %s", row.id, exc_info=True)
    finally:
        db.close()
    return count
