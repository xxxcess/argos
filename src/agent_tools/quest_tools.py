"""Agent handler for scoped Venture Quest session actions."""

from __future__ import annotations

import asyncio
import json
from typing import Any


async def _recover_then_nudge_synthesis(job_id: str) -> None:
    """Reclaim stale work, then attempt this job through its atomic claim guard."""
    from src.venture_synthesis import process_synthesis_job
    from src.venture_synthesis_reliability import recover_stale_synthesis_jobs

    await asyncio.to_thread(recover_stale_synthesis_jobs)
    await process_synthesis_job(job_id)


def _nudge_synthesis(job_id: str | None) -> None:
    """Attempt a committed Quest job now without bypassing its queue lease."""
    if job_id:
        asyncio.create_task(_recover_then_nudge_synthesis(job_id))


class ManageQuestSessionTool:
    async def execute(self, content: str, ctx: dict[str, Any]) -> dict:
        context = ctx if isinstance(ctx, dict) else {}
        try:
            args = json.loads(content or "{}")
        except (TypeError, ValueError):
            args = None
        if not isinstance(args, dict):
            return {"error": "Invalid JSON arguments", "exit_code": 1}

        action = str(args.get("action") or "").strip().lower()
        quest_id = str(args.get("quest_id") or context.get("session_id") or "").strip()
        if action in {"status", "synthesize_artifact", "synthesize_memory"}:
            from core.database import SessionLocal
            from routes import venture_routes_legacy as legacy
            from src.quest_session_management import execute_manage_quest_session
            from src.venture_auth import get_quest_role

            if not quest_id:
                return {"error": "Quest session management requires an active Quest session", "exit_code": 1}
            if get_quest_role(context.get("owner"), quest_id) != "captain":
                return {"error": "Quest synthesis actions require Captain membership.", "exit_code": 1}
            db = SessionLocal()
            try:
                result = await asyncio.to_thread(
                    execute_manage_quest_session,
                    db,
                    legacy,
                    quest_id=quest_id,
                    captain=str(context.get("owner") or ""),
                    action=action,
                    artifact_proposal_id=(args.get("artifact_proposal_id") or args.get("proposal_id")),
                )
                db.commit()
                # A duplicate request may return the existing queued job. Nudge
                # it too: the atomic claim guard prevents duplicate execution.
                _nudge_synthesis(result.get("job_id"))
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

        if action in {"request_bible_passage", "remember_bible_passage"} and not (args.get("reference") or args.get("query")):
            from src.quest_bible_context import recent_bible_reference
            reference = recent_bible_reference(quest_id)
            if reference:
                args["reference"] = reference
                content = json.dumps(args)

        from src.quest_bible_workflow import execute_quest_bible_action
        result = await asyncio.to_thread(
            execute_quest_bible_action,
            content,
            owner=context.get("owner"),
            session_id=context.get("session_id"),
        )
        synthesis = result.get("synthesis_job") if isinstance(result.get("synthesis_job"), dict) else {}
        _nudge_synthesis(synthesis.get("id"))
        return result
