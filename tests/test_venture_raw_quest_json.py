import json

from src.quest_session_tool_patch import _raw_quest_json_block


def _block(tool_type, content):
    return {"tool_type": tool_type, "content": content}


def test_recovers_full_response_bible_passage_action():
    block = _raw_quest_json_block(
        '{"op":"retrieve_bible_passage","passage":"Matthew 1:18-25","translation":"WEB"}',
        _block,
    )
    assert block["tool_type"] == "manage_quest_session"
    assert json.loads(block["content"]) == {
        "action": "retrieve_bible_passage",
        "reference": "Matthew 1:18-25",
    }


def test_public_agent_parser_recovers_the_same_action():
    from src.agent_tools import parse_tool_blocks

    blocks = parse_tool_blocks(
        '{"op":"retrieve_bible_passage","passage":"Matthew 1:18-25","translation":"WEB"}',
        skip_fenced=True,
    )
    assert len(blocks) == 1
    assert blocks[0].tool_type == "manage_quest_session"
    assert json.loads(blocks[0].content)["reference"] == "Matthew 1:18-25"


def test_rejects_json_embedded_in_ordinary_prose():
    assert _raw_quest_json_block(
        'I will use this next: {"op":"retrieve_bible_passage","passage":"Matthew 1:18-25"}',
        _block,
    ) is None


def test_rejects_unrelated_json_even_when_it_has_an_action_key():
    assert _raw_quest_json_block('{"action":"delete_all","target":"everything"}', _block) is None
