"""Small, startup-safe dispatcher extension for Venture Quest actions.

The legacy ``manage_quest`` implementation remains available. This extension
adds a deterministic ``manage_quest_session`` tool and routes Bible workflow
aliases through it. It never assumes ``src.tool_execution`` exports a
``do_manage_quest`` attribute.
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


def _register_schema_and_tag() -> None:
    """Register after agent_tools has completed its normal import sequence."""
    import src.agent_tools as agent_tools
    import src.tool_schemas as schemas
    from src.quest_session_tool_schema import QUEST_SESSION_TOOL_SCHEMA

    agent_tools.TOOL_TAGS.add("manage_quest_session")
    if not any(item.get("function", {}).get("name") == "manage_quest_session" for item in schemas.FUNCTION_TOOL_SCHEMAS):
        schemas.FUNCTION_TOOL_SCHEMAS.append({"type": "function", "function": QUEST_SESSION_TOOL_SCHEMA})


def install_quest_session_tool() -> None:
    """Install once after agent_tools has loaded schemas and the dispatcher."""
    import src.tool_execution as execution
    import src.tool_implementations as implementations
    from src.quest_exact_bible_retrieval_patch import install_exact_bible_retrieval

    if getattr(execution, "_venture_quest_session_tool_installed", False):
        return

    _register_schema_and_tag()
    install_exact_bible_retrieval()
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


# Compatibility name retained for existing agent_tools imports. The previous
# version attempted to access execution.do_manage_quest and caused startup to
# fail on Python 3.12.
def install_manage_quest_synthesis_actions() -> None:
    install_quest_session_tool()
