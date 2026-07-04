"""Agent handler for scoped Venture Quest session actions."""

from __future__ import annotations

import asyncio
import json
from typing import Any


class ManageQuestSessionTool:
    async def execute(self, content: str, ctx: dict[str, Any]) -> dict:
        from src.quest_bible_workflow import execute_quest_bible_action

        context = ctx if isinstance(ctx, dict) else {}
        payload = content
        try:
            args = json.loads(content or "{}")
        except (TypeError, ValueError):
            args = None
        if isinstance(args, dict):
            action = str(args.get("action") or "").strip().lower()
            if action in {"request_bible_passage", "remember_bible_passage"} and not (args.get("reference") or args.get("query")):
                from src.quest_bible_context import recent_bible_reference
                reference = recent_bible_reference(str(args.get("quest_id") or context.get("session_id") or ""))
                if reference:
                    args["reference"] = reference
                    payload = json.dumps(args)
        return await asyncio.to_thread(
            execute_quest_bible_action,
            payload,
            owner=context.get("owner"),
            session_id=context.get("session_id"),
        )
