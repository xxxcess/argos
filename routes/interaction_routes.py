"""Durable interaction request APIs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.auth_helpers import get_current_user
from src.interaction_requests import resolve_interaction_request


class InteractionResolveBody(BaseModel):
    response_summary: str = ""


def setup_interaction_routes() -> APIRouter:
    router = APIRouter(prefix="/api/interactions", tags=["interactions"])

    @router.post("/{interaction_id}/resolve")
    async def resolve_interaction(request: Request, interaction_id: str, body: InteractionResolveBody | None = None):
        owner = get_current_user(request) or ""
        ok = resolve_interaction_request(
            owner=owner,
            interaction_id=interaction_id,
            response_summary=(body.response_summary if body else ""),
        )
        if not ok:
            raise HTTPException(404, "Interaction request not found")
        return {"ok": True}

    return router

