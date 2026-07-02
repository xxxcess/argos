import re
import json
import asyncio

from src.quest_vector_store import collection_name_for_quest, deterministic_chunk_doc_id
from src.quest_retrieval import visibility_lanes_for_role


def test_collection_name_is_opaque_and_stable():
    quest_id = "quest-visible-id-123"
    first = collection_name_for_quest(quest_id, "shared")
    second = collection_name_for_quest(quest_id, "shared")
    other = collection_name_for_quest("another-quest", "shared")

    assert first == second
    assert first != other
    assert quest_id not in first
    assert re.fullmatch(r"venture_q_[a-f0-9]{24}_shared", first)


def test_deterministic_chunk_doc_id_is_retry_stable_and_opaque():
    a = deterministic_chunk_doc_id("quest-one", "src", "ver", "art", 3)
    b = deterministic_chunk_doc_id("quest-one", "src", "ver", "art", 3)
    c = deterministic_chunk_doc_id("quest-two", "src", "ver", "art", 3)

    assert a == b
    assert a != c
    assert "quest-one" not in a
    assert a.endswith(":chunk:3")


def test_visibility_lane_resolution():
    assert visibility_lanes_for_role("captain") == ["captain", "shared", "summaries"]
    assert visibility_lanes_for_role("shipmate") == ["shared", "summaries"]
    assert visibility_lanes_for_role(None) == []


def test_retrieval_search_is_called_only_with_active_quest_and_lanes(monkeypatch):
    calls = []

    def fake_search(quest_id, query, visibility_lanes, **kwargs):
        calls.append((quest_id, query, visibility_lanes, kwargs))
        return []

    monkeypatch.setattr("src.quest_retrieval.search_quest_evidence", fake_search)
    monkeypatch.setattr("src.quest_retrieval.get_quest_role", lambda requester, quest_id: "shipmate")

    class Query:
        def __init__(self, row):
            self.row = row
        def filter(self, *args, **kwargs):
            return self
        def first(self):
            return self.row

    class Db:
        def query(self, model):
            return Query(object())
        def close(self):
            pass

    monkeypatch.setattr("src.quest_retrieval.SessionLocal", lambda: Db())
    from src.quest_retrieval import retrieve_quest_evidence

    retrieve_quest_evidence(quest_id="q-active", requester="shipmate", query="budget")

    assert calls == [("q-active", "budget", ["shared", "summaries"], {"limit": 6})]


def test_youtube_adapter_uses_canonical_youtube_handler(monkeypatch):
    from core.database import QuestSource
    from src.quest_source_adapters import YoutubeQuestSourceAdapter

    source = QuestSource(
        id="src-youtube",
        session_id="quest-youtube",
        captain_username="ada",
        source_type="youtube",
        source_mode="static",
        display_name="Video",
        access_mode="shared_read",
        configuration_json=json.dumps({"video_urls": ["https://youtu.be/dQw4w9WgXcQ"]}),
    )

    refs = asyncio.run(YoutubeQuestSourceAdapter().discover(source))

    assert len(refs) == 1
    assert refs[0].raw["video_id"] == "dQw4w9WgXcQ"
