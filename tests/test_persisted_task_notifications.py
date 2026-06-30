from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.database import (
    Base,
    OutboxMessage,
    ScheduledTask,
    TaskEvent,
    TaskRun,
    UserNotification,
)
from tests.helpers.sqlite_db import make_temp_sqlite


def _db(monkeypatch):
    SessionLocal, _engine, _tmp = make_temp_sqlite(Base.metadata)
    import core.database as cdb
    import routes.notification_routes as nr

    monkeypatch.setattr(cdb, "SessionLocal", SessionLocal)
    monkeypatch.setattr(nr, "SessionLocal", SessionLocal)
    return SessionLocal


def _seed_task(SessionLocal, owner="alice", task_id="task-1", run_id="run-1"):
    db = SessionLocal()
    try:
        task = ScheduledTask(
            id=task_id,
            owner=owner,
            name="Daily Briefing",
            task_type="llm",
            prompt="summarize",
            status="active",
            notifications_enabled=True,
            state_version=0,
        )
        run = TaskRun(
            id=run_id,
            task_id=task_id,
            started_at=datetime.utcnow(),
            status="running",
            result="Starting",
        )
        db.add(task)
        db.add(run)
        db.commit()
    finally:
        db.close()
    return task_id, run_id


def test_task_event_and_outbox_are_written_together(monkeypatch):
    SessionLocal = _db(monkeypatch)
    task_id, run_id = _seed_task(SessionLocal)

    from src.task_notifications import TASK_STARTED, append_task_event

    db = SessionLocal()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        run = db.query(TaskRun).filter(TaskRun.id == run_id).first()
        event = append_task_event(db, task, event_type=TASK_STARTED, run=run, safe_message="Running")
        db.commit()

        assert event is not None
        assert db.query(TaskEvent).count() == 1
        assert db.query(OutboxMessage).count() == 1
        assert db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first().state_version == 1
    finally:
        db.close()


def test_projection_is_idempotent_and_redacts_sensitive_payload(monkeypatch):
    SessionLocal = _db(monkeypatch)
    task_id, run_id = _seed_task(SessionLocal)

    from src.task_notifications import TASK_PROGRESS_REPORTED, append_task_event, project_task_event

    db = SessionLocal()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        run = db.query(TaskRun).filter(TaskRun.id == run_id).first()
        event = append_task_event(
            db,
            task,
            event_type=TASK_PROGRESS_REPORTED,
            run=run,
            safe_message="Authorization: Bearer secret-token",
        )
        db.commit()
        outbox = db.query(OutboxMessage).filter(OutboxMessage.event_id == event.id).first()
        project_task_event(db, outbox)
        project_task_event(db, outbox)
        db.commit()

        progress = db.query(UserNotification).filter(UserNotification.category == "progress").all()
        assert len(progress) == 1
        assert "Bearer" not in progress[0].message
        assert "task activity" in progress[0].message
    finally:
        db.close()


def test_success_creates_completion_inbox_and_retires_progress(monkeypatch):
    SessionLocal = _db(monkeypatch)
    task_id, run_id = _seed_task(SessionLocal)

    from src.task_notifications import TASK_STARTED, TASK_SUCCEEDED, append_task_event, dispatch_outbox_for_events

    db = SessionLocal()
    try:
        task = db.query(ScheduledTask).filter(ScheduledTask.id == task_id).first()
        run = db.query(TaskRun).filter(TaskRun.id == run_id).first()
        started = append_task_event(db, task, event_type=TASK_STARTED, run=run, safe_message="Running")
        run.status = "success"
        run.result = "Done"
        succeeded = append_task_event(db, task, event_type=TASK_SUCCEEDED, run=run, safe_message="Done")
        event_ids = [started.id, succeeded.id]
        db.commit()
    finally:
        db.close()

    dispatch_outbox_for_events(event_ids)

    db = SessionLocal()
    try:
        progress = db.query(UserNotification).filter(UserNotification.category == "progress").one()
        inbox = db.query(UserNotification).filter(UserNotification.category == "inbox").one()
        assert progress.archived_at is not None
        assert inbox.user_id == "alice"
        assert inbox.state == "unread"
        assert "finished" in inbox.title
    finally:
        db.close()


def test_notification_api_is_owner_scoped_and_counts_actionable(monkeypatch):
    SessionLocal = _db(monkeypatch)
    db = SessionLocal()
    try:
        db.add(UserNotification(
            id="n1",
            user_id="alice",
            owner="alice",
            category="inbox",
            severity="warning",
            state="action_required",
            title="Alice action",
            message="Needs response",
            deterministic_key="alice-action",
        ))
        db.add(UserNotification(
            id="n2",
            user_id="bob",
            owner="bob",
            category="inbox",
            severity="error",
            state="unread",
            title="Bob failure",
            message="Hidden",
            deterministic_key="bob-failure",
        ))
        db.commit()
    finally:
        db.close()

    from routes.notification_routes import setup_notification_routes

    app = FastAPI()

    @app.middleware("http")
    async def _user(request: Request, call_next):
        request.state.current_user = "alice"
        return await call_next(request)

    app.include_router(setup_notification_routes())
    client = TestClient(app)

    listed = client.get("/api/notifications?view=inbox").json()["notifications"]
    count = client.get("/api/notifications/unread-count").json()

    assert [row["id"] for row in listed] == ["n1"]
    assert count == {"unread": 1, "actionable": 1}
