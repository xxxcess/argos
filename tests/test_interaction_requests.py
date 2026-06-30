from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.database import Base, InteractionRequest, Session, UserNotification
from tests.helpers.sqlite_db import make_temp_sqlite


def _db(monkeypatch):
    SessionLocal, _engine, _tmp = make_temp_sqlite(Base.metadata)
    import core.database as cdb
    import src.interaction_requests as ir

    monkeypatch.setattr(cdb, "SessionLocal", SessionLocal)
    monkeypatch.setattr(ir, "SessionLocal", SessionLocal)
    return SessionLocal


def test_session_interaction_creates_actionable_inbox_and_resolves(monkeypatch):
    SessionLocal = _db(monkeypatch)
    db = SessionLocal()
    try:
        db.add(Session(id="s1", owner="alice", name="Chat", endpoint_url="http://x", model="m"))
        db.commit()
    finally:
        db.close()

    from src.interaction_requests import create_session_interaction_request

    interaction_id = create_session_interaction_request(
        owner="alice",
        session_id="s1",
        payload={
            "question": "Choose one",
            "options": [{"label": "A"}, {"label": "B"}],
            "multi": False,
        },
    )
    assert interaction_id

    db = SessionLocal()
    try:
        req = db.query(InteractionRequest).filter(InteractionRequest.id == interaction_id).one()
        note = db.query(UserNotification).filter(UserNotification.interaction_id == interaction_id).one()
        assert req.status == "pending"
        assert req.session_id == "s1"
        assert note.category == "inbox"
        assert note.state == "action_required"
        assert note.resource_type == "session"
    finally:
        db.close()

    from routes.interaction_routes import setup_interaction_routes

    app = FastAPI()

    @app.middleware("http")
    async def _user(request: Request, call_next):
        request.state.current_user = "alice"
        return await call_next(request)

    app.include_router(setup_interaction_routes())
    client = TestClient(app)

    res = client.post(f"/api/interactions/{interaction_id}/resolve", json={"response_summary": "A"})
    assert res.status_code == 200

    db = SessionLocal()
    try:
        req = db.query(InteractionRequest).filter(InteractionRequest.id == interaction_id).one()
        note = db.query(UserNotification).filter(UserNotification.interaction_id == interaction_id).one()
        assert req.status == "resolved"
        assert req.response_summary == "A"
        assert note.state == "resolved"
        assert note.archived_at is not None
    finally:
        db.close()

