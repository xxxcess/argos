from src.quest_session_tool_patch import _normalize_action, _quest_workflow_request
from src.quest_session_tool_schema import QUEST_SESSION_TOOL_SCHEMA
from src.quest_scope_guard import block_external_bible_lookup


def test_quest_session_schema_exposes_bible_and_synthesis_actions():
    actions = set(QUEST_SESSION_TOOL_SCHEMA["parameters"]["properties"]["action"]["enum"])
    assert {
        "list_sources",
        "inspect_source",
        "search_bible",
        "retrieve_bible_passage",
        "request_bible_passage",
        "remember_bible_passage",
        "synthesize_artifact",
        "synthesize_memory",
    }.issubset(actions)


def test_legacy_synthesis_alias_routes_to_the_dedicated_quest_tool():
    assert _normalize_action("request_synthesis") == "synthesize_artifact"
    request = _quest_workflow_request('{"action":"request_synthesis","quest_id":"quest-1"}')
    assert '"synthesize_artifact"' in request


def test_scope_guard_is_inert_outside_a_quest():
    assert block_external_bible_lookup(
        session_id=None,
        owner=None,
        request_text="John 1:1 Word of God",
    ) is None
