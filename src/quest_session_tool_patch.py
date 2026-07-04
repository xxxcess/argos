"""Runtime extensions for Captain-scoped Venture Quest management tools.

``manage_quest`` retains its legacy source/artifact actions and gains explicit
synthesis actions. ``manage_quest_session`` is a compact dedicated tool for
Quest status plus deliberate Artifact and Memory synthesis.
"""

from __future__ import annotations

import json
from typing import Any


def _tool_args(content: Any) -> dict[str, Any]:
    from src.tool_implementations import _parse_tool_args

    return _parse_tool_args(content)


def _normalize_action(value: Any) -> str:
    action = str(value or "").strip().lower()
    return {
        "request_synthesis": "synthesize_artifact",
        "synthesis_artifact": "synthesize_artifact",
        "synthesis_memory": "synthesize_memory",
    }.get(action, action)


async def _run_quest_session_action(content: str, *, owner: str | None, session_id: str | None) -> dict:
    try:
        args = _tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = _normalize_action(args.get("action"))
    if action not in {"status", "synthesize_artifact", "synthesize_memory"}:
        return {"error": "manage_quest_session supports status, synthesize_artifact, or synthesize_memory.", "exit_code": 1}
    quest_id = str(args.get("quest_id") or session_id or "").strip()
    if not quest_id:
        return {"error": "manage_quest_session requires an active Quest session", "exit_code": 1}

    from core.database import SessionLocal
    from routes import venture_routes_legacy as legacy
    from src.quest_session_management import execute_manage_quest_session
    from src.venture_auth import get_quest_role

    if get_quest_role(owner, quest_id) != "captain":
        return {"error": "Quest session management requires Captain membership in the active Quest.", "exit_code": 1}

    db = SessionLocal()
    try:
        result = execute_manage_quest_session(
            db,
            legacy,
            quest_id=quest_id,
            captain=str(owner or ""),
            action=action,
            artifact_proposal_id=(args.get("artifact_proposal_id") or args.get("proposal_id") or args.get("artifact_id")),
        )
        db.commit()
        return {
            "output": result.get("message") or "Quest session action accepted.",
            "quest_management": result,
            "exit_code": 0,
        }
    except Exception as exc:
        db.rollback()
        return {"error": f"Quest session action failed safely: {exc}", "exit_code": 1}
    finally:
        db.close()


def _synthesis_request(content: str) -> str | None:
    try:
        args = _tool_args(content)
    except ValueError:
        return None
    action = _normalize_action(args.get("action"))
    if action not in {"synthesize_artifact", "synthesize_memory"}:
        return None
    args["action"] = action
    return json.dumps(args)


async def do_manage_quest(content: str, owner: str | None = None, session_id: str | None = None) -> dict:
    """Compatibility wrapper for direct callers outside the normal dispatcher."""
    request = _synthesis_request(content)
    if request is None:
        return await _ORIGINAL_DO_MANAGE_QUEST(content, owner=owner, session_id=session_id)
    return await _run_quest_session_action(request, owner=owner, session_id=session_id)


def _replace_manage_quest_schema() -> None:
    import src.tool_schemas as schemas

    for entry in schemas.FUNCTION_TOOL_SCHEMAS:
        function = entry.get("function", {})
        if function.get("name") != "manage_quest":
            continue
        function["description"] = (
            "Captain-only scoped Argos Venture Quest management for the active Quest. "
            "Use synthesize_artifact to queue one cited Artifact draft. Use "
            "synthesize_memory only after an Artifact is published; it extracts "
            "Voyage Memory from Artifact revelations, never raw evidence."
        )
        properties = function.setdefault("parameters", {}).setdefault("properties", {})
        properties["action"] = {
            "type": "string",
            "enum": [
                "status", "list_sources", "inspect_source", "refresh_source",
                "reindex_source", "list_artifacts", "open_artifact", "list_memory",
                "synthesize_artifact", "synthesize_memory",
            ],
            "description": "Quest action. Synthesis actions are Captain-only and idempotently queued.",
        }
        properties["artifact_proposal_id"] = {
            "type": "string",
            "description": "Published Artifact proposal id for synthesize_memory. Omit to use the latest published Artifact.",
        }
        break


def _register_dedicated_schema() -> None:
    import src.agent_tools as agent_tools
    import src.tool_schemas as schemas
    from src.quest_session_management import QUEST_MANAGEMENT_TOOL

    agent_tools.TOOL_TAGS.add("manage_quest_session")
    if not any(entry.get("function", {}).get("name") == "manage_quest_session" for entry in schemas.FUNCTION_TOOL_SCHEMAS):
        schemas.FUNCTION_TOOL_SCHEMAS.append({"type": "function", "function": QUEST_MANAGEMENT_TOOL})


def _install_dispatcher(execution) -> None:
    original_impl = execution._execute_tool_block_impl

    async def execute_impl(block, session_id=None, disabled_tools=None, owner=None, progress_cb=None, tool_policy=None):
        if block.tool_type == "manage_quest_session":
            if disabled_tools and block.tool_type in disabled_tools:
                return "manage_quest_session: BLOCKED", {"error": "Tool 'manage_quest_session' is disabled by user.", "exit_code": 1}
            if tool_policy and tool_policy.blocks(block.tool_type):
                return "manage_quest_session: BLOCKED", {"error": "Execution of tool 'manage_quest_session' is forbidden by the active guide-only policy.", "exit_code": 1}
            return "manage_quest_session", await _run_quest_session_action(
                block.content,
                owner=owner,
                session_id=session_id,
            )
        if block.tool_type == "manage_quest":
            request = _synthesis_request(block.content)
            if request is not None:
                return "manage_quest", await _run_quest_session_action(
                    request,
                    owner=owner,
                    session_id=session_id,
                )
        return await original_impl(
            block,
            session_id=session_id,
            disabled_tools=disabled_tools,
            owner=owner,
            progress_cb=progress_cb,
            tool_policy=tool_policy,
        )

    execution._execute_tool_block_impl = execute_impl


def _improve_tool_discovery() -> None:
    """Ensure RAG selection retains both Quest tools for synthesis language."""
    try:
        from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS, ToolIndex

        description = (
            "Captain-only Venture Quest session management. Inspect compact Quest status, "
            "trigger a cited Artifact synthesis, or trigger Voyage Memory synthesis from a "
            "published Artifact's revelations. Use for Quest, Voyage, Artifact, key point, "
            "memory synthesis, distill insight, and evidence-guided session requests."
        )
        BUILTIN_TOOL_DESCRIPTIONS["manage_quest"] = description
        BUILTIN_TOOL_DESCRIPTIONS["manage_quest_session"] = description
        ToolIndex._KEYWORD_HINTS[frozenset({
            "quest", "voyage", "artifact", "artifact synthesis", "synthesize artifact",
            "memory synthesis", "synthesize memory", "distill insight", "voyage memory",
        })] = {"manage_quest_session"}
    except Exception:
        pass


def install_manage_quest_synthesis_actions() -> None:
    """Patch schemas and dispatch once after the agent facade has loaded."""
    global _ORIGINAL_DO_MANAGE_QUEST
    import src.tool_execution as execution
    from src.tool_implementations import do_manage_quest as original_manage_quest

    if getattr(execution, "_venture_manage_quest_actions_installed", False):
        return
    # ``do_manage_quest`` belongs to tool_implementations and is imported into
    # the executor inside its dispatch function; it is not a module attribute on
    # src.tool_execution. Keep the original for direct callers and intercept
    # synthesis actions at the dispatcher boundary below.
    _ORIGINAL_DO_MANAGE_QUEST = original_manage_quest
    _replace_manage_quest_schema()
    _register_dedicated_schema()
    _install_dispatcher(execution)
    _improve_tool_discovery()
    execution._venture_manage_quest_actions_installed = True


_ORIGINAL_DO_MANAGE_QUEST = None
