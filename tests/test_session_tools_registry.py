"""Tests for the session tools' move to the agent_tools registry (#3629):
create_session, list_sessions, send_to_session, manage_session.

The implementations now live in src/agent_tools/session_tools.py (moved out of
src/ai_interaction.py). These assert (1) the handlers are registered in
TOOL_HANDLERS, (2) the moved logic runs and threads owner/session from ctx
(the session manager is fetched via ai_interaction.get_session_manager), and
(3) tool_execution.py dispatches them through the registry rather than the
legacy dispatch_ai_tool elif.
"""
import asyncio
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import src.ai_interaction as ai_interaction
import core.database as core_database
import src.database as database
from core.models import Session
from src.agent_tools import TOOL_HANDLERS
from src.agent_tools import session_tools as st

_SESSION_TOOLS = ("create_session", "list_sessions", "send_to_session", "manage_session")


def test_session_tools_registered():
    for name in _SESSION_TOOLS:
        assert name in TOOL_HANDLERS, f"{name} missing from TOOL_HANDLERS"


def test_list_sessions_handler_threads_ctx(monkeypatch):
    # The handler must thread content + session_id + owner from ctx into the
    # moved list_sessions implementation. Spy at the function boundary so the
    # test does not depend on list_sessions' DB internals.
    seen = {}

    async def spy(content, session_id=None, owner=None):
        seen.update(content=content, session_id=session_id, owner=owner)
        return {"results": "ok"}

    monkeypatch.setattr(st, "list_sessions", spy)
    res = asyncio.run(st.ListSessionsTool().execute("q", {"owner": "alice", "session_id": "s1"}))
    assert res == {"results": "ok"}
    assert seen == {"content": "q", "session_id": "s1", "owner": "alice"}


def test_manage_session_list_delegates_to_list_sessions(monkeypatch):
    # manage_session("list") must delegate to list_sessions; guards against a
    # stale do_list_sessions reference surviving the move (caught live in e2e).
    called = {}

    async def spy(content, session_id=None, owner=None):
        called["owner"] = owner
        return {"results": "ok"}

    monkeypatch.setattr(st, "list_sessions", spy)
    # manage_session imports `Session` from src.database before the list branch;
    # the src.database test double may not expose it, so provide a stand-in.
    monkeypatch.setattr(database, "Session", object, raising=False)
    monkeypatch.setattr(ai_interaction, "_session_manager", object())  # truthy: pass the guard
    res = asyncio.run(st.ManageSessionTool().execute("list", {"owner": "carol"}))
    assert called.get("owner") == "carol"
    assert res == {"results": "ok"}


def test_create_session_reaches_uuid_and_creates(monkeypatch):
    # Regression for the missing `import uuid` (PR review): create_session must
    # get past _resolve_model and mint a session id without NameError.
    monkeypatch.setattr(st, "_resolve_model", lambda spec, owner=None: ("http://x", "model-x", {}))
    created = {}

    class FakeMgr:
        def create_session(self, **kw):
            created.update(kw)

        def get_session(self, sid):
            return None

    monkeypatch.setattr(ai_interaction, "_session_manager", FakeMgr())
    res = asyncio.run(st.CreateSessionTool().execute("My Chat\nmodel-x", {"owner": "alice"}))
    assert res.get("name") == "My Chat" and res.get("model") == "model-x"
    assert isinstance(res.get("session_id"), str) and res["session_id"]
    assert created.get("name") == "My Chat"  # the uuid-minted id reached the manager


def test_manage_session_fork_reaches_uuid(monkeypatch):
    # Regression for the missing `import uuid`: the fork action also mints a new
    # session id and must not NameError. Mocks the DB query layer so the fork
    # branch reaches the uuid call without a real sessions table.
    class FakeDbSession:
        id = "id"
        owner = "owner"

    class FakeQ:
        def filter(self, *a, **k):
            return self

        def first(self):
            return object()

    class FakeDB:
        def query(self, *a, **k):
            return FakeQ()

        def close(self):
            pass

    monkeypatch.setattr(database, "Session", FakeDbSession, raising=False)
    monkeypatch.setattr(database, "SessionLocal", lambda: FakeDB(), raising=False)

    class Src:
        name = "Orig"
        endpoint_url = "http://x"
        model = "m"

        def get_context_messages(self):
            return []

    created = {}

    class FakeMgr:
        def get_session(self, sid):
            return Src() if sid == "abc" else type("S", (), {"add_message": lambda self, m: None})()

        def create_session(self, **kw):
            created.update(kw)

    monkeypatch.setattr(ai_interaction, "_session_manager", FakeMgr())
    res = asyncio.run(st.ManageSessionTool().execute('{"action":"fork","session_id":"abc"}', {"owner": "owner"}))
    assert res.get("action") == "fork"
    assert isinstance(res.get("session_id"), str) and res["session_id"]
    assert created.get("name") == "Fork: Orig"  # uuid-minted new session was created


def test_no_session_manager_is_handled(monkeypatch):
    # With no session manager set, the moved function must fail gracefully
    # (proves the handler reached the impl, not an "unknown tool").
    monkeypatch.setattr(ai_interaction, "_session_manager", None)
    res = asyncio.run(st.ListSessionsTool().execute("", {"owner": "bob"}))
    assert isinstance(res, dict)
    assert "error" in res or "results" in res


def test_list_sessions_queries_only_visible_session_ids(monkeypatch):
    class FakeColumn:
        def __init__(self, name):
            self.name = name

        def in_(self, values):
            return ("in", self.name, tuple(values))

    class FakeDbSession:
        id = FakeColumn("id")
        last_accessed = FakeColumn("last_accessed")
        updated_at = FakeColumn("updated_at")
        created_at = FakeColumn("created_at")

    class FakeQuery:
        def __init__(self):
            self.filter_args = []

        def filter(self, *args):
            self.filter_args.extend(args)
            return self

        def all(self):
            return [
                SimpleNamespace(
                    id="visible",
                    last_accessed=datetime(2026, 7, 3, 12, 0, 0),
                    updated_at=None,
                    created_at=None,
                )
            ]

    class FakeDB:
        def __init__(self):
            self.query_obj = FakeQuery()

        def query(self, *cols):
            seen["query_cols"] = cols
            return self.query_obj

        def close(self):
            seen["closed"] = True

    class FakeMgr:
        def get_sessions_for_user(self, owner):
            seen["owner"] = owner
            return {
                "visible": Session(id="visible", name="Visible", endpoint_url="http://ep", model="m", owner=owner),
            }

    seen = {}
    fake_db = FakeDB()
    monkeypatch.setattr(core_database, "Session", FakeDbSession, raising=False)
    monkeypatch.setattr(core_database, "SessionLocal", lambda: fake_db, raising=False)
    monkeypatch.setattr(ai_interaction, "_session_manager", FakeMgr())

    res = asyncio.run(st.list_sessions("", owner="alice"))

    assert seen["owner"] == "alice"
    assert fake_db.query_obj.filter_args == [("in", "id", ("visible",))]
    assert "Visible" in res["results"]


def test_dispatched_via_registry_not_dispatch_ai_tool():
    source = (Path(__file__).resolve().parent.parent / "src" / "tool_execution.py").read_text(encoding="utf-8")
    assert 'elif tool in ("create_session", "list_sessions", "send_to_session", "manage_session"):' in source

    marker = "from src.ai_interaction import dispatch_ai_tool"
    idx = source.index(marker)
    branch_head = source.rfind("elif tool in (", 0, idx)
    legacy_tuple = source[branch_head:idx]
    for name in _SESSION_TOOLS:
        assert f'"{name}"' not in legacy_tuple, f"{name} still routed via dispatch_ai_tool"
