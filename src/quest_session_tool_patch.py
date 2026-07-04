"""Small, startup-safe dispatcher extension for Venture Quest actions.

The legacy ``manage_quest`` implementation remains available. This extension
only adds a deterministic ``manage_quest_session`` tool and routes Bible
workflow aliases through it. It intentionally does not patch nonexistent
attributes on ``src.tool_execution``.
"""

from __future__ import annotations

import json


def _normalize_action(value) -> str:
    action = str(value or "").strip().lower()
    return {
        "lookup_bible": "retrieve_bible_passage",
        "retrieve_bible": "retrieve_bible_passage",
        "request_passage": "request_bible_passage",
        "remember_passage": "remember_bible_passage",
        "synthesize_artifact": "remember_bible_passage",
    }.get(action, action)


def _quest_workflow_request(content: str) -> str | None:
    try:
        args = json.loads(content or "{}")
    except (TypeError, ValueError):
        return None
    if not isinstance(args, dict):
        return None
    action = _normalize_action(args.get("action"))
    if action not in {
        "list_sources",
        "inspect_source",
        "search_bible",
        "retrieve_bible_passage",
        "request_bible_passage",
        "remember_bible_passage",
    }:
        return None
    args["action"] = action
    return json.dumps(args)


def install_quest_session_tool() -> None:
    """Install once after agent_tools has loaded its schemas and dispatcher."""
    import src.tool_execution as execution
    import src.tool_implementations as implementations

    if getattr(execution, "_venture_quest_session_tool_installed", False):
        return

    original_execute = execution._execute_tool_block_impl
    original_manage_quest = implementations.do_manage_quest

    async def execute_impl(block, session_id=None, disabled_tools=None, owner=None, progress_cb=None, tool_policy=None):
        request = None
        if block.tool_type == "manage_quest_session":
            request = block.content
        elif block.tool_type == "manage_quest":
            request = _quest_workflow_request(block.content)
        if request is not None:
            if disabled_tools and block.tool_type in disabled_tools:
                return f"{block.tool_type}: BLOCKED", {"error": f"Tool '{block.tool_type}' is disabled by user.", "exit_code": 1}
            if tool_policy and tool_policy.blocks(block.tool_type):
                return f"{block.tool_type}: BLOCKED", {"error": f"Execution of tool '{block.tool_type}' is forbidden by the active guide-only policy.", "exit_code": 1}
            from src.agent_tools.quest_tools import ManageQuestSessionTool
            result = await ManageQuestSessionTool().execute(request, {"owner": owner, "session_id": session_id})
            return block.tool_type, result
        return await original_execute(
            block,
            session_id=session_id,
            disabled_tools=disabled_tools,
            owner=owner,
            progress_cb=progress_cb,
            tool_policy=tool_policy,
        )

    async def managed_quest(content: str, owner=None, session_id=None):
        request = _quest_workflow_request(content)
        if request is None:
            return await original_manage_quest(content, owner=owner, session_id=session_id)
        from src.agent_tools.quest_tools import ManageQuestSessionTool
        return await ManageQuestSessionTool().execute(request, {"owner": owner, "session_id": session_id})

    execution._execute_tool_block_impl = execute_impl
    implementations.do_manage_quest = managed_quest
    execution._venture_quest_session_tool_installed = True
