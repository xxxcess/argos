"""Runtime extension for the captain-scoped ``manage_quest`` agent tool.

The repository already exposes ``manage_quest`` to the agent.  This patch keeps
its source and artifact actions while adding deliberate Artifact and published
Artifact-to-Memory synthesis actions through the same idempotent queue used by
the Quest panel.
"""

from __future__ import annotations

from typing import Any


def _tool_args(content: Any) -> dict[str, Any]:
    from src.tool_implementations import _parse_tool_args

    return _parse_tool_args(content)


async def do_manage_quest(content: str, owner: str | None = None, session_id: str | None = None) -> dict:
    """Handle synthesis actions, then defer legacy actions to the original tool."""
    try:
        args = _tool_args(content)
    except ValueError:
        return {"error": "Invalid JSON arguments", "exit_code": 1}

    action = str(args.get("action") or "").strip().lower()
    aliases = {
        "request_synthesis": "synthesize_artifact",
        "synthesis_artifact": "synthesize_artifact",
        "synthesis_memory": "synthesize_memory",
    }
    action = aliases.get(action, action)
    if action not in {"synthesize_artifact", "synthesize_memory"}:
        return await _ORIGINAL_DO_MANAGE_QUEST(content, owner=owner, session_id=session_id)

    quest_id = str(args.get("quest_id") or session_id or "").strip()
    if not quest_id:
        return {"error": "manage_quest requires an active Quest session", "exit_code": 1}

    from core.database import SessionLocal
    from src.quest_session_management import execute_manage_quest_session
    from src.venture_auth import get_quest_role
    from routes import venture_routes_legacy as legacy

    if get_quest_role(owner, quest_id) != "captain":
        return {"error": "manage_quest synthesis actions require Captain membership in the active Quest.", "exit_code": 1}

    db = SessionLocal()
    try:
        result = execute_manage_quest_session(
            db,
            legacy,
            quest_id=quest_id,
            captain=str(owner or ""),
            action=action,
            artifact_proposal_id=(
                args.get("artifact_proposal_id")
                or args.get("proposal_id")
                or args.get("artifact_id")
            ),
        )
        db.commit()
        if result.get("queued"):
            message = result.get("message") or "Quest synthesis queued."
            return {"output": message, "quest_management": result, "exit_code": 0}
        if action == "synthesize_memory" and not result.get("proposal_id"):
            return {"output": result.get("message") or "Publish an Artifact before memory synthesis.", "quest_management": result, "exit_code": 0}
        return {"output": result.get("message") or "Quest synthesis was already active or complete.", "quest_management": result, "exit_code": 0}
    except Exception as exc:
        db.rollback()
        return {"error": f"Quest synthesis request failed safely: {exc}", "exit_code": 1}
    finally:
        db.close()


def _replace_schema() -> None:
    import src.tool_schemas as schemas

    for entry in schemas.FUNCTION_TOOL_SCHEMAS:
        function = entry.get("function", {})
        if function.get("name") != "manage_quest":
            continue
        function["description"] = (
            "Captain-only scoped Argos Venture Quest management for the active Quest. "
            "Use synthesize_artifact to queue one cited Artifact draft. Use "
            "synthesize_memory only after an Artifact is published; it extracts "
            "Voyage Memory from the Artifact's revelations, never raw evidence."
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


def install_manage_quest_synthesis_actions() -> None:
    """Patch the tool dispatcher/schema once after the agent facade loads."""
    global _ORIGINAL_DO_MANAGE_QUEST
    import src.tool_execution as execution

    if getattr(execution, "_venture_manage_quest_actions_installed", False):
        return
    _ORIGINAL_DO_MANAGE_QUEST = execution.do_manage_quest
    execution.do_manage_quest = do_manage_quest
    _replace_schema()
    execution._venture_manage_quest_actions_installed = True


_ORIGINAL_DO_MANAGE_QUEST = None
