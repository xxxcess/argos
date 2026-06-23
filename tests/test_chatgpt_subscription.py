import json

from src.chatgpt_subscription import (
    RESPONSES_TEXT_TOOL_PROTOCOL,
    build_responses_input,
)
from src.tool_parsing import parse_tool_blocks


def _text(item):
    return item["content"][0]["text"]


def test_responses_input_places_tool_protocol_before_latest_user_message():
    messages = [
        {"role": "assistant", "content": "What should I do?"},
        {"role": "user", "content": "Search for current release notes."},
    ]

    items = build_responses_input(messages)

    assert [item["role"] for item in items] == ["assistant", "developer", "user"]
    assert _text(items[1]) == RESPONSES_TEXT_TOOL_PROTOCOL
    assert _text(items[2]) == "Search for current release notes."


def test_responses_input_keeps_tool_result_rounds_as_user_data():
    messages = [
        {"role": "assistant", "content": "<tool_call>...</tool_call>"},
        {"role": "tool", "tool_call_id": "call_1", "content": "Search returned one result."},
    ]

    items = build_responses_input(messages)

    assert [item["role"] for item in items] == ["assistant", "developer", "user"]
    assert _text(items[2]) == "Search returned one result."


def test_subscription_protocol_xml_is_executable_when_fenced_calls_are_disabled():
    response = """<tool_call>
<invoke name="web_search">
<parameter name="query">latest GPT-5.5 release notes</parameter>
</invoke>
</tool_call>"""

    blocks = parse_tool_blocks(response, skip_fenced=True)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "web_search"
    assert blocks[0].content == "latest GPT-5.5 release notes"


def test_subscription_tool_code_preserves_structured_skill_fields():
    response = """<tool_code>
{tool => 'manage_skills', args => '{"action":"add","name":"listen-to-music","procedure":["Open YouTube","Click the official video"]}'}
</tool_code>"""

    blocks = parse_tool_blocks(response, skip_fenced=True)

    assert len(blocks) == 1
    assert blocks[0].tool_type == "manage_skills"
    assert json.loads(blocks[0].content)["procedure"] == [
        "Open YouTube",
        "Click the official video",
    ]
    assert "manage_skills" in RESPONSES_TEXT_TOOL_PROTOCOL
    assert "<tool_code>" in RESPONSES_TEXT_TOOL_PROTOCOL
