"""OpenAI schema for deterministic Venture Quest management."""

from __future__ import annotations


QUEST_SESSION_TOOL_SCHEMA = {
    "name": "manage_quest_session",
    "description": (
        "Manage the active Venture Quest without leaving Quest scope. Use list_sources or inspect_source "
        "to resolve source ids. Use search_bible for a topic within indexed Quest Bible evidence, "
        "retrieve_bible_passage for an exact reference already indexed, and request_bible_passage to queue "
        "a missing selected Bible book. Use remember_bible_passage to queue a Captain-reviewable Artifact; "
        "Voyage Memory is created only after the Artifact is published. Do not use web search as a substitute "
        "for a scoped Quest Bible source unless the Captain explicitly asks to leave Quest scope."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "list_sources",
                    "inspect_source",
                    "search_bible",
                    "retrieve_bible_passage",
                    "request_bible_passage",
                    "remember_bible_passage",
                ],
                "description": "Quest action. Mutating actions require Captain membership.",
            },
            "quest_id": {
                "type": "string",
                "description": "Quest session id. Omit when the active chat is the Quest.",
            },
            "source_id": {
                "type": "string",
                "description": "Full source id or a unique source-id handle returned by list_sources.",
            },
            "reference": {
                "type": "string",
                "description": "Canonical Bible reference, e.g. John 1:1-14. Required for retrieve/request/remember actions.",
            },
            "query": {
                "type": "string",
                "description": "Topic query for search_bible, e.g. 'what does John say the Word is'.",
            },
        },
        "required": ["action"],
    },
}
