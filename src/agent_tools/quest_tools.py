"""Agent handler for scoped Venture Quest session actions."""

from __future__ import annotations

import asyncio
from typing import Any


class ManageQuestSessionTool:
    async def execute(self, content: str, ctx: dict[str, Any]) -> dict:
        from src.quest_bible_workflow import execute_quest_bible_action

        context = ctx if isinstance(ctx, dict) else {}
        return await asyncio.to_thread(
            execute_quest_bible_action,
            content,
            owner=context.get("owner"),
            session_id=context.get("session_id"),
        )
