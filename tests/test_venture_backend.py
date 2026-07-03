import tempfile
import asyncio

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.models import ChatMessage as RuntimeChatMessage
from core.database import (
    Base,
    ChatMessage,
    Document,
    EmailAccount,
    GalleryImage,
    QuestArtifactProposal,
    QuestMember,
    QuestMemoryEntry,
    QuestSourceCheckpoint,
    QuestSynthesisJob,
    QuestSourceVersion,
    Session as DbSession,
    SessionLocal as RealSessionLocal,
    UserNotification,
    _migrate_backfill_quest_captain_members,
)
from core.session_manager import SessionManager
from tests.helpers.sqlite_db import make_temp_sqlite


class _Auth:
    users = {
        "ada": {"is_admin": True},
        "turing": {"is_admin": True},
        "mara": {"is_admin": False},
        "niko": {"is_admin": False},
    }

    def is_admin(self, username):
        return bool(self.users.get(username, {}).get("is_admin"))


def _client(monkeypatch, username="ada", venture=True):
    if venture:
        monkeypatch.setenv("ARGOS_RUNTIME_ID", "argos-venture")
    else:
        monkeypatch.setenv("ARGOS_RUNTIME_ID", "nightly")
    SessionLocal, engine, tmp = make_temp_sqlite(Base.metadata)

    import core.database as cdb
    import core.session_manager as csm
    import routes.venture_routes as vr
    import src.venture_auth as va
    import src.venture_synthesis as vs

    monkeypatch.setattr(cdb, "SessionLocal", SessionLocal)
    monkeypatch.setattr(csm, "SessionLocal", SessionLocal)
    monkeypatch.setattr(vr, "SessionLocal", SessionLocal)
    monkeypatch.setattr(va, "SessionLocal", SessionLocal)
    monkeypatch.setattr(vs, "SessionLocal", SessionLocal)

    sm = SessionManager(tempfile.NamedTemporaryFile(delete=False).name)
    app = FastAPI()
    app.state.auth_manager = _Auth()
    app.state.test_user = username

    @app.middleware("http")
    async def _user(request: Request, call_next):
        request.state.current_user = request.app.state.test_user
        return await call_next(request)

    from routes.venture_routes import setup_venture_routes

    app.include_router(setup_venture_routes(sm))
    return TestClient(app), SessionLocal, app, sm, engine, tmp


def _quest_payload(**overrides):
    payload = {
        "title": "Atlas Review",
        "exploration_goal": "Find recurring churn signals",
        "initial_question": "Which accounts show repeated risk?",
        "source": {
            "source_type": "website",
            "display_name": "Captured churn notes",
            "configuration": {"url": "https://example.test/churn", "summary": "Scoped notes"},
        },
    }
    payload.update(overrides)
    return payload


def test_venture_routes_are_runtime_gated(monkeypatch):
    client, *_ = _client(monkeypatch, venture=False)
    assert client.get("/api/venture/capabilities").status_code == 404


def test_shipmate_capabilities_expose_only_search_chats_and_quests(monkeypatch):
    client, *_ = _client(monkeypatch, username="mara")
    caps = client.get("/api/venture/capabilities").json()
    assert caps["role"] == "shipmate"
    assert caps["visible_navigation"] == ["Search", "Chats", "Quests"]
    assert caps["visible_feature_categories"] == ["quests", "chat"]
    assert caps["can_use_tools"] is False
    assert caps["can_switch_model"] is False
    assert caps["can_attach_files"] is False


def test_quest_creation_requires_primary_source_and_backfills_captain(monkeypatch):
    client, SessionLocal, *_ = _client(monkeypatch)
    missing_source = _quest_payload()
    missing_source.pop("source")
    assert client.post("/api/quests", json=missing_source).status_code == 422

    created = client.post("/api/quests", json=_quest_payload()).json()
    quest_id = created["quest"]["id"]
    db = SessionLocal()
    try:
        members = db.query(QuestMember).filter(QuestMember.session_id == quest_id).all()
        assert [(m.username, m.role) for m in members] == [("ada", "captain")]
    finally:
        db.close()


def test_regular_venture_chat_is_not_listed_or_read_as_quest(monkeypatch):
    client, _SessionLocal, _app, sm, *_ = _client(monkeypatch)
    sm.create_session(
        session_id="regular-chat",
        name="Regular Chat",
        endpoint_url="http://localhost:11434/v1",
        model="gpt-test",
        owner="ada",
    )

    listed = client.get("/api/quests").json()["quests"]
    assert all(row["id"] != "regular-chat" for row in listed)
    assert client.get("/api/quests/regular-chat").status_code == 404


def test_regular_venture_chat_history_remains_owner_scoped(monkeypatch):
    client, _SessionLocal, app, sm, *_ = _client(monkeypatch)
    import routes.session_routes as sr

    monkeypatch.setattr(sr, "SessionLocal", _SessionLocal)
    app.include_router(sr.setup_session_routes(sm, {}))
    session = sm.create_session(
        session_id="regular-chat",
        name="Regular Chat",
        endpoint_url="http://localhost:11434/v1",
        model="gpt-test",
        owner="ada",
    )
    session.add_message(RuntimeChatMessage("user", "Owner-only regular chat message"))

    assert client.get("/api/history/regular-chat").status_code == 200
    app.state.test_user = "mara"
    assert client.get("/api/history/regular-chat").status_code == 404


def test_pending_invitation_grants_no_access_and_accept_is_idempotent(monkeypatch):
    client, SessionLocal, app, *_ = _client(monkeypatch)
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]

    invite = client.post(
        f"/api/quests/{quest_id}/invitations",
        json={"invitee_username": "mara"},
    )
    assert invite.status_code == 202
    invitation_id = invite.json()["invitation_id"]

    app.state.test_user = "mara"
    assert client.get(f"/api/quests/{quest_id}").status_code == 404
    assert client.get(f"/api/quests/{quest_id}/memory").status_code == 404

    assert client.post(f"/api/quest-invitations/{invitation_id}/accept").status_code == 200
    assert client.post(f"/api/quest-invitations/{invitation_id}/accept").status_code == 409
    assert client.get(f"/api/quests/{quest_id}").status_code == 200

    db = SessionLocal()
    try:
        assert db.query(QuestMember).filter(
            QuestMember.session_id == quest_id,
            QuestMember.username == "mara",
            QuestMember.role == "shipmate",
        ).count() == 1
        assert db.query(UserNotification).filter(
            UserNotification.user_id == "mara",
            UserNotification.resource_type == "quest_invitation",
        ).count() == 1
    finally:
        db.close()


def test_accepted_shipmate_can_load_shared_quest_history(monkeypatch):
    client, _SessionLocal, app, sm, *_ = _client(monkeypatch)
    import routes.session_routes as sr

    monkeypatch.setattr(sr, "SessionLocal", _SessionLocal)
    app.include_router(sr.setup_session_routes(sm, {}))
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    invitation_id = client.post(
        f"/api/quests/{quest_id}/invitations",
        json={"invitee_username": "mara"},
    ).json()["invitation_id"]
    sm.add_message(quest_id, RuntimeChatMessage("user", "Captain note for the shared Voyage Log"))
    sm.add_message(quest_id, RuntimeChatMessage("assistant", "Argo response for the whole Quest"))

    app.state.test_user = "mara"
    client.post(f"/api/quest-invitations/{invitation_id}/accept")
    history = client.get(f"/api/history/{quest_id}")

    assert history.status_code == 200
    contents = [row["content"] for row in history.json()["history"]]
    assert "Captain note for the shared Voyage Log" in contents
    assert "Argo response for the whole Quest" in contents


def test_invited_shipmates_show_roster_status_and_captain_progress_resolves(monkeypatch):
    client, SessionLocal, app, *_ = _client(monkeypatch)
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    invitation_id = client.post(
        f"/api/quests/{quest_id}/invitations",
        json={"invitee_username": "mara"},
    ).json()["invitation_id"]

    roster = client.get(f"/api/quests/{quest_id}/roster").json()["members"]
    mara = next(member for member in roster if member["username"] == "mara")
    assert mara["role"] == "shipmate"
    assert mara["invitation_status"] == "pending"

    db = SessionLocal()
    try:
        pending = db.query(UserNotification).filter(
            UserNotification.user_id == "ada",
            UserNotification.deterministic_key == f"quest-invite-progress:{invitation_id}",
        ).one()
        assert pending.category == "progress"
        assert pending.state == "waiting"
    finally:
        db.close()

    app.state.test_user = "mara"
    assert client.post(f"/api/quest-invitations/{invitation_id}/decline").status_code == 200
    app.state.test_user = "ada"

    roster = client.get(f"/api/quests/{quest_id}/roster").json()["members"]
    mara = next(member for member in roster if member["username"] == "mara")
    assert mara["invitation_status"] == "declined"

    db = SessionLocal()
    try:
        resolved = db.query(UserNotification).filter(
            UserNotification.user_id == "ada",
            UserNotification.deterministic_key == f"quest-invite-progress:{invitation_id}",
        ).one()
        assert resolved.category == "inbox"
        assert resolved.state == "resolved"
    finally:
        db.close()


def test_source_visibility_modes_are_enforced(monkeypatch):
    client, _SessionLocal, app, *_ = _client(monkeypatch)
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    invitation_id = client.post(f"/api/quests/{quest_id}/invitations", json={"invitee_username": "mara"}).json()["invitation_id"]
    app.state.test_user = "mara"
    client.post(f"/api/quest-invitations/{invitation_id}/accept")
    app.state.test_user = "ada"

    captain_only = client.post(
        f"/api/quests/{quest_id}/sources",
        json={
            "source_type": "website",
            "display_name": "Private notes",
            "access_mode": "captain_only",
            "configuration": {"url": "https://example.test/private", "summary": "private"},
        },
    ).json()["source"]["id"]
    summary = client.post(
        f"/api/quests/{quest_id}/sources",
        json={
            "source_type": "website",
            "display_name": "Summary notes",
            "access_mode": "shared_summaries",
            "configuration": {"url": "https://example.test/raw", "summary": "safe summary"},
        },
    ).json()["source"]["id"]

    app.state.test_user = "mara"
    assert client.get(f"/api/quests/{quest_id}/sources/{captain_only}").status_code == 404
    visible = client.get(f"/api/quests/{quest_id}/sources/{summary}").json()["source"]
    assert visible["configuration"] == {"summary": "safe summary"}


def test_quest_capabilities_force_modes_by_role(monkeypatch):
    client, _SessionLocal, app, *_ = _client(monkeypatch)
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    invitation_id = client.post(f"/api/quests/{quest_id}/invitations", json={"invitee_username": "mara"}).json()["invitation_id"]
    captain_caps = client.get(f"/api/quests/{quest_id}/capabilities").json()
    assert captain_caps["role"] == "captain"
    assert captain_caps["interaction_mode"] == "agent"
    assert captain_caps["tools_allowed"] is True
    assert set(captain_caps["voice"]) == {"tts_enabled", "tts_ready", "stt_enabled", "stt_ready", "show_voice_mode_control"}

    app.state.test_user = "mara"
    client.post(f"/api/quest-invitations/{invitation_id}/accept")
    shipmate_caps = client.get(f"/api/quests/{quest_id}/capabilities").json()
    assert shipmate_caps["role"] == "shipmate"
    assert shipmate_caps["interaction_mode"] == "chat"
    assert shipmate_caps["tools_allowed"] is False


def test_synthesis_jobs_status_visible_to_captain_only(monkeypatch):
    client, SessionLocal, app, *_ = _client(monkeypatch)
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    invitation_id = client.post(f"/api/quests/{quest_id}/invitations", json={"invitee_username": "mara"}).json()["invitation_id"]
    with SessionLocal() as db:
        db.add(QuestSynthesisJob(
            id="synth-job-1",
            quest_id=quest_id,
            trigger="captain_requested",
            status="no_insight",
            created_by="ada",
            result_json='{"should_create": false, "reason": "no_source_evidence"}',
        ))
        db.commit()

    data = client.get(f"/api/quests/{quest_id}/argo-synthesis/jobs").json()
    assert data["jobs"][0]["status"] == "no_insight"
    assert data["jobs"][0]["reason"] == "no_source_evidence"
    assert "triggering_user_message_id" not in data["jobs"][0]
    assert "retrieval_run_id" not in data["jobs"][0]

    app.state.test_user = "mara"
    client.post(f"/api/quest-invitations/{invitation_id}/accept")
    assert client.get(f"/api/quests/{quest_id}/argo-synthesis/jobs").status_code == 403


def test_captain_requested_discussion_synthesis_creates_review_draft(monkeypatch):
    client, SessionLocal, _app, *_ = _client(monkeypatch)
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    with SessionLocal() as db:
        db.add(ChatMessage(id="captain-msg-1", session_id=quest_id, role="user", content="Remember this Quest decision: focus the Google AI models exploration on enterprise Gemini release risk."))
        db.commit()

    client.post(f"/api/quests/{quest_id}/argo-synthesis/run")
    job_id = client.get(f"/api/quests/{quest_id}/argo-synthesis/jobs").json()["jobs"][0]["id"]

    from src.venture_synthesis import process_synthesis_job
    asyncio.run(process_synthesis_job(job_id))

    jobs = client.get(f"/api/quests/{quest_id}/argo-synthesis/jobs").json()["jobs"]
    assert jobs[0]["status"] == "created"
    proposal_id = jobs[0]["artifact_proposal_id"]
    proposal = client.get(f"/api/quests/{quest_id}/artifact-proposals/{proposal_id}").json()["proposal"]
    assert proposal["status"] == "pending_review"
    assert proposal["visibility"] == "captain_private"
    assert "Milestone Report" in proposal["document"]["content"]
    assert "Evidence supporting this milestone" in proposal["document"]["content"]
    assert "Captain discussion" in proposal["document"]["content"]
    assert "[D1]" in proposal["document"]["content"]


def test_quest_human_message_persists_author_role_at_send(monkeypatch):
    client, SessionLocal, _app, sm, *_ = _client(monkeypatch)
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    sess = sm.get_session(quest_id)

    from routes.chat_helpers import PreprocessedMessage, add_user_message

    class _ChatHandler:
        def update_session_name_if_needed(self, *_args, **_kwargs):
            return None

    add_user_message(
        sess,
        _ChatHandler(),
        PreprocessedMessage(
            enhanced_message="Captain decision",
            user_content="Captain decision",
            text_for_context="Captain decision",
            youtube_transcripts=[],
            attachment_meta=[],
        ),
        user="ada",
        session_id=quest_id,
    )

    meta = sess.history[-1].metadata
    assert meta["author_type"] == "human"
    assert meta["author_username"] == "ada"
    assert meta["author_role_at_send"] == "captain"
    assert meta["venture_quest"] is True


def test_shipmate_quest_message_records_without_model_endpoint(monkeypatch):
    client, SessionLocal, app, sm, *_ = _client(monkeypatch)
    import routes.chat_routes as cr
    monkeypatch.setattr(cr, "SessionLocal", SessionLocal)
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    invite = client.post(f"/api/quests/{quest_id}/invitations", json={"invitee_username": "mara"}).json()["invitation_id"]
    app.state.test_user = "mara"
    client.post(f"/api/quest-invitations/{invite}/accept")
    result = cr._record_shipmate_quest_turn(
        type("Req", (), {"state": type("State", (), {"current_user": "mara"})(), "app": app})(),
        sm,
        quest_id,
        "Can Argo clarify the risk evidence?",
    )
    assert result["status"] == "recorded"
    assert result["classification"] == "question"
    db = SessionLocal()
    try:
        msg = db.query(ChatMessage).filter(ChatMessage.session_id == quest_id, ChatMessage.role == "user").order_by(ChatMessage.timestamp.desc()).first()
        assert "author_role_at_send" in msg.meta_data
        notice = db.query(UserNotification).filter(UserNotification.user_id == "ada", UserNotification.resource_type == "quest_shipmate_contribution").one()
        assert "Answer with Argo" in notice.actions_json
    finally:
        db.close()


def test_venture_tool_policy_allowlist_blocks_generic_and_mcp_tools():
    from src.tool_policy import VENTURE_QUEST_ALLOWED_TOOLS, build_effective_tool_policy

    policy = build_effective_tool_policy(allowlist=VENTURE_QUEST_ALLOWED_TOOLS)
    for allowed in VENTURE_QUEST_ALLOWED_TOOLS:
        assert not policy.blocks(allowed)
    assert VENTURE_QUEST_ALLOWED_TOOLS == frozenset({"web_search", "web_fetch", "manage_quest"})
    for denied in [
        "trigger_research",
        "manage_research",
        "bash",
        "python",
        "read_file",
        "write_file",
        "app_api",
        "manage_settings",
        "generate_image",
        "mcp__x__y",
    ]:
        assert policy.blocks(denied)
    assert policy.disable_mcp is True


def test_artifact_draft_private_until_publish_without_generic_shared_memory(monkeypatch):
    client, SessionLocal, app, sm, *_ = _client(monkeypatch)
    from routes.document_routes import setup_document_routes
    import routes.document_routes as dr
    import routes.document_helpers as dh

    monkeypatch.setattr(dr, "SessionLocal", SessionLocal)
    monkeypatch.setattr(dh, "SessionLocal", SessionLocal, raising=False)
    app.include_router(setup_document_routes(sm))

    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    invitation_id = client.post(f"/api/quests/{quest_id}/invitations", json={"invitee_username": "mara"}).json()["invitation_id"]
    app.state.test_user = "mara"
    client.post(f"/api/quest-invitations/{invitation_id}/accept")
    app.state.test_user = "ada"

    proposal = client.post(
        f"/api/quests/{quest_id}/artifact-proposals",
        json={
            "title": "Recurring Churn Signals",
            "summary": "Several accounts repeated the same risk language.",
            "evidence_refs": ["source:v1:row7"],
        },
    ).json()["proposal"]
    proposal_id = proposal["id"]
    doc_id = proposal["document_id"]

    app.state.test_user = "mara"
    assert client.get(f"/api/quests/{quest_id}/artifact-proposals/{proposal_id}").status_code in (403, 404)
    assert client.get(f"/api/quests/{quest_id}/artifacts").json()["documents"] == []

    app.state.test_user = "ada"
    assert client.post(f"/api/quests/{quest_id}/artifact-proposals/{proposal_id}/publish").status_code == 200
    assert client.post(f"/api/quests/{quest_id}/artifact-proposals/{proposal_id}/publish").json()["already_published"] is True

    db = SessionLocal()
    try:
        original_doc = db.query(Document).filter(Document.id == doc_id).one()
        assert original_doc.session_id == quest_id
        assert original_doc.owner == "ada"
        shipmate_copy = db.query(Document).filter(
            Document.session_id == quest_id,
            Document.owner == "mara",
            Document.is_active == True,
        ).one()
        assert shipmate_copy.id != doc_id
        assert shipmate_copy.title == original_doc.title
        assert shipmate_copy.current_content == original_doc.current_content
        assert db.query(QuestArtifactProposal).filter(QuestArtifactProposal.id == proposal_id).one().status == "published"
        assert db.query(QuestMemoryEntry).filter(
            QuestMemoryEntry.session_id == quest_id,
            QuestMemoryEntry.category == "artifact_reference",
        ).count() == 0
        jobs = db.query(QuestSynthesisJob).filter(
            QuestSynthesisJob.quest_id == quest_id,
            QuestSynthesisJob.trigger == "artifact_revision",
            QuestSynthesisJob.artifact_proposal_id == proposal_id,
        ).all()
        assert len(jobs) == 1
        activity = db.query(ChatMessage).filter(ChatMessage.session_id == quest_id, ChatMessage.role == "system").order_by(ChatMessage.timestamp.desc()).first()
        assert "background_task" in (activity.meta_data or "")
    finally:
        db.close()

    app.state.test_user = "mara"
    docs = client.get(f"/api/quests/{quest_id}/artifacts").json()["documents"]
    assert len(docs) == 1
    assert docs[0]["id"] != doc_id
    copy_id = docs[0]["id"]
    assert client.get(f"/api/document/{copy_id}").status_code == 200
    assert client.get(f"/api/document/{doc_id}").status_code == 200
    library_docs = client.get("/api/documents/library").json()["documents"]
    assert copy_id in {d["id"] for d in library_docs}


def test_artifact_memory_synthesis_extracts_cited_key_points(monkeypatch):
    client, SessionLocal, _app, *_ = _client(monkeypatch)
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    proposal = client.post(
        f"/api/quests/{quest_id}/artifact-proposals",
        json={"title": "Regional Risk", "summary": "Northeast rollout risk increased.", "evidence_refs": ["FY2026 Market Outlook.pdf - p. 18"]},
    ).json()["proposal"]
    client.post(f"/api/quests/{quest_id}/artifact-proposals/{proposal['id']}/publish")
    db = SessionLocal()
    try:
        job = db.query(QuestSynthesisJob).filter(QuestSynthesisJob.artifact_proposal_id == proposal["id"]).one()
        job_id = job.id
        doc_id = db.query(QuestArtifactProposal).filter(QuestArtifactProposal.id == proposal["id"]).one().document_id
    finally:
        db.close()

    from src.venture_synthesis import process_synthesis_job
    asyncio.run(process_synthesis_job(job_id))

    db = SessionLocal()
    try:
        assert db.query(Document).filter(Document.id == doc_id).one().is_active is True
        job = db.query(QuestSynthesisJob).filter(QuestSynthesisJob.id == job_id).one()
        assert job.status == "completed"
        memory = db.query(QuestMemoryEntry).filter(QuestMemoryEntry.session_id == quest_id, QuestMemoryEntry.artifact_id == proposal["id"]).all()
        assert len(memory) == 1
        assert memory[0].state == "confirmed"
        assert memory[0].visibility == "quest_shared"
        assert "Northeast rollout risk increased" in memory[0].content
        assert "E1" in memory[0].provenance_json
    finally:
        db.close()
    api_memory = client.get(f"/api/quests/{quest_id}/memory").json()["memory"]
    assert len(api_memory) == 1
    assert api_memory[0]["evidence"][0]["id"] == "E1"
    assert "FY2026 Market Outlook.pdf" in api_memory[0]["evidence"][0]["label"]


def test_artifact_memory_synthesis_noop_preserves_publication_without_cited_claims(monkeypatch):
    client, SessionLocal, _app, *_ = _client(monkeypatch)
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    proposal = client.post(
        f"/api/quests/{quest_id}/artifact-proposals",
        json={"title": "No New Memory", "summary": "Reference only.", "evidence_refs": []},
    ).json()["proposal"]
    client.post(f"/api/quests/{quest_id}/artifact-proposals/{proposal['id']}/publish")
    db = SessionLocal()
    try:
        job = db.query(QuestSynthesisJob).filter(QuestSynthesisJob.artifact_proposal_id == proposal["id"]).one()
        job_id = job.id
        doc_id = db.query(QuestArtifactProposal).filter(QuestArtifactProposal.id == proposal["id"]).one().document_id
    finally:
        db.close()

    from src.venture_synthesis import process_synthesis_job
    asyncio.run(process_synthesis_job(job_id))

    db = SessionLocal()
    try:
        assert db.query(Document).filter(Document.id == doc_id).one().is_active is True
        job = db.query(QuestSynthesisJob).filter(QuestSynthesisJob.id == job_id).one()
        assert job.status == "completed"
        assert "No new durable memory" in job.result_json
    finally:
        db.close()


def test_quest_memory_isolation_and_visibility(monkeypatch):
    client, _SessionLocal, app, *_ = _client(monkeypatch)
    q1 = client.post("/api/quests", json=_quest_payload(title="Quest One")).json()["quest"]["id"]
    q2 = client.post("/api/quests", json=_quest_payload(title="Quest Two")).json()["quest"]["id"]
    invite = client.post(f"/api/quests/{q1}/invitations", json={"invitee_username": "mara"}).json()["invitation_id"]
    app.state.test_user = "mara"
    client.post(f"/api/quest-invitations/{invite}/accept")
    app.state.test_user = "ada"

    client.post(f"/api/quests/{q1}/memory", json={"category": "finding", "visibility": "quest_shared", "title": "Q1 shared"})
    private = client.post(f"/api/quests/{q1}/memory", json={"category": "risk", "visibility": "captain_private", "title": "Q1 private"}).json()["memory"]["id"]
    client.post(f"/api/quests/{q2}/memory", json={"category": "finding", "visibility": "quest_shared", "title": "Q2 shared"})

    q1_mem = client.get(f"/api/quests/{q1}/memory").json()["memory"]
    assert {m["title"] for m in q1_mem} == {"Q1 shared", "Q1 private"}
    assert all(m["session_id"] == q1 for m in q1_mem)

    app.state.test_user = "mara"
    q1_shipmate = client.get(f"/api/quests/{q1}/memory").json()["memory"]
    assert [m["title"] for m in q1_shipmate] == ["Q1 shared"]
    assert client.get(f"/api/quests/{q1}/memory/{private}").status_code == 404
    assert client.get(f"/api/quests/{q2}/memory").status_code == 404


def test_backfill_skips_null_owner_sessions(monkeypatch):
    SessionLocal, engine, _tmp = make_temp_sqlite(Base.metadata)
    import core.database as cdb

    monkeypatch.setattr(cdb, "engine", engine)
    db = SessionLocal()
    try:
        db.add(DbSession(id="owned", name="Owned", endpoint_url="", model="", owner="ada"))
        db.add(DbSession(id="legacy", name="Legacy", endpoint_url="", model="", owner=None))
        db.commit()
    finally:
        db.close()

    _migrate_backfill_quest_captain_members()

    db = SessionLocal()
    try:
        assert db.query(QuestMember).filter(QuestMember.session_id == "owned", QuestMember.username == "ada").count() == 1
        assert db.query(QuestMember).filter(QuestMember.session_id == "legacy").count() == 0
    finally:
        db.close()


def test_quest_memory_vector_namespace_requires_matching_session(monkeypatch):
    monkeypatch.setenv("ARGOS_RUNTIME_ID", "argos-venture")
    from src.quest_memory_vector import (
        assert_quest_memory_metadata,
        quest_memory_collection_name,
        quest_memory_metadata,
        quest_memory_path,
    )

    meta = quest_memory_metadata("quest-a")
    assert quest_memory_collection_name("quest-a") == "quest-memory:quest-a"
    assert str(quest_memory_path("quest-a")).endswith("/chroma/quest-memory/quest-a")
    assert_quest_memory_metadata("quest-a", meta)

    try:
        assert_quest_memory_metadata("quest-b", meta)
    except ValueError:
        pass
    else:
        raise AssertionError("cross-Quest memory metadata should be rejected")


def test_shipmate_can_read_published_document_artifact_but_not_edit(monkeypatch):
    client, SessionLocal, app, sm, *_ = _client(monkeypatch)
    from routes.document_routes import setup_document_routes
    import routes.document_routes as dr
    import routes.document_helpers as dh

    monkeypatch.setattr(dr, "SessionLocal", SessionLocal)
    monkeypatch.setattr(dh, "SessionLocal", SessionLocal, raising=False)

    app.include_router(setup_document_routes(sm))
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    invitation_id = client.post(f"/api/quests/{quest_id}/invitations", json={"invitee_username": "mara"}).json()["invitation_id"]
    app.state.test_user = "mara"
    client.post(f"/api/quest-invitations/{invitation_id}/accept")
    app.state.test_user = "ada"

    db = SessionLocal()
    try:
        doc = Document(id="artifact-doc", session_id=quest_id, title="Published Artifact", language="markdown", current_content="# Artifact", owner="ada", is_active=True)
        draft = Document(id="draft-doc", session_id=None, title="Private Draft", language="markdown", current_content="# Draft", owner="ada", is_active=True)
        db.add_all([doc, draft])
        db.commit()
    finally:
        db.close()

    app.state.test_user = "mara"
    assert client.get("/api/document/artifact-doc").status_code == 200
    assert client.put("/api/document/artifact-doc", json={"content": "# edited"}).status_code == 404
    assert client.get("/api/document/draft-doc").status_code == 404
    docs = client.get("/api/documents/library").json()["documents"]
    assert [d["id"] for d in docs] == ["artifact-doc"]


def test_shipmate_can_read_published_gallery_artifact(monkeypatch):
    client, SessionLocal, app, *_ = _client(monkeypatch)
    from routes.gallery_routes import setup_gallery_routes
    import routes.gallery_routes as gr

    monkeypatch.setattr(gr, "SessionLocal", SessionLocal)

    app.include_router(setup_gallery_routes())
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    invitation_id = client.post(f"/api/quests/{quest_id}/invitations", json={"invitee_username": "mara"}).json()["invitation_id"]
    app.state.test_user = "mara"
    client.post(f"/api/quest-invitations/{invitation_id}/accept")
    app.state.test_user = "ada"

    db = SessionLocal()
    try:
        db.add(GalleryImage(id="img-artifact", filename="artifact.png", prompt="Artifact image", owner="ada", session_id=quest_id, is_active=True))
        db.add(GalleryImage(id="img-private", filename="private.png", prompt="Private image", owner="ada", session_id=None, is_active=True))
        db.commit()
    finally:
        db.close()

    app.state.test_user = "mara"
    assert client.get("/api/gallery/img-artifact").status_code == 200
    assert client.get("/api/gallery/img-private").status_code == 404
    items = client.get("/api/gallery/library").json()["items"]
    assert [i["id"] for i in items] == ["img-artifact"]


def test_static_source_refresh_creates_no_artifact_or_synthesis(monkeypatch):
    client, SessionLocal, *_ = _client(monkeypatch)
    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    source_id = client.get(f"/api/quests/{quest_id}/sources").json()["sources"][0]["id"]
    db = SessionLocal()
    try:
        db.add(ChatMessage(id="m1", session_id=quest_id, role="user", content="The same churn risk appears again."))
        db.add(ChatMessage(id="m2", session_id=quest_id, role="assistant", content="Argo notes a possible recurring pattern."))
        db.commit()
    finally:
        db.close()

    result = client.post(f"/api/quests/{quest_id}/sources/{source_id}/refresh").json()
    assert "synthesis" not in result

    db = SessionLocal()
    try:
        assert db.query(QuestArtifactProposal).filter(QuestArtifactProposal.session_id == quest_id).count() == 0
        assert db.query(QuestMemoryEntry).filter(QuestMemoryEntry.session_id == quest_id).count() == 0
        assert db.query(QuestSynthesisJob).filter(QuestSynthesisJob.quest_id == quest_id).count() == 0
    finally:
        db.close()


def test_email_source_poll_uses_checkpoint_and_marks_memory_stale(monkeypatch, tmp_path):
    email_db = tmp_path / "email_cache.db"
    import sqlite3
    conn = sqlite3.connect(email_db)
    conn.execute("CREATE TABLE email_tags (message_id TEXT, owner TEXT, uid TEXT, folder TEXT, subject TEXT, sender TEXT, tags TEXT, spam_verdict INTEGER, created_at TEXT)")
    conn.execute("INSERT INTO email_tags VALUES ('mid1','ada','101','INBOX','Customer churn risk','analyst@example.com','retention',0,'2026-06-30T12:00:00')")
    conn.commit()
    conn.close()

    import src.venture_email as ve
    monkeypatch.setattr(ve, "EMAIL_CACHE_DB", str(email_db))

    client, SessionLocal, *_ = _client(monkeypatch)
    db = SessionLocal()
    try:
        db.add(EmailAccount(id="email-account", owner="ada", name="Work", imap_host="imap.example.test", imap_user="ada@example.test", from_address="ada@example.test"))
        db.commit()
    finally:
        db.close()

    quest_id = client.post("/api/quests", json=_quest_payload()).json()["quest"]["id"]
    email_source = client.post(
        f"/api/quests/{quest_id}/sources",
        json={
            "source_type": "email",
            "display_name": "Retention mailbox scope",
            "configuration": {"account_id": "email-account", "scope": {"mailbox": "INBOX", "subject": "churn"}},
        },
    ).json()["source"]
    db = SessionLocal()
    try:
        db.add(QuestMemoryEntry(id="mem-stale", session_id=quest_id, category="finding", visibility="captain_private", state="confirmed", title="Old finding", content="Old", confidence="medium", created_by="argo"))
        db.commit()
    finally:
        db.close()

    result = client.post(f"/api/quests/{quest_id}/sources/{email_source['id']}/refresh").json()
    assert result["email_poll"]["changed"] is True
    assert result["email_poll"]["matched_records"] == 1

    db = SessionLocal()
    try:
        assert db.query(QuestSourceCheckpoint).filter(QuestSourceCheckpoint.quest_source_id == email_source["id"]).one().cursor
        assert db.query(QuestSourceVersion).filter(QuestSourceVersion.quest_source_id == email_source["id"]).count() == 1
        assert db.query(QuestMemoryEntry).filter(QuestMemoryEntry.id == "mem-stale").one().state == "stale"
    finally:
        db.close()
