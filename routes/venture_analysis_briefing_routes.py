"""Goal-aware replacement routes for the Venture CSV analysis workspace."""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from core.database import Session as DbSession, SessionLocal
from core.models import ChatMessage
from core.session_manager import SessionManager
from src.auth_helpers import require_user
from src.runtime_profile import require_venture_runtime
from src.venture_analysis_payload import analysis_payload, ingest_echarts
from src.venture_data_analysis import (
    MAX_DATASET_BYTES,
    AnalysisError,
    AnalysisNotFound,
    is_analysis_session,
    register_analysis_session,
    session_target,
)
from src.venture_data_briefing import MAX_GOAL_CHARS, save_goal


class BriefingSessionCreate(BaseModel):
    name: str = Field(default="Data Analysis", min_length=1, max_length=120)
    goal: str = Field(default="", max_length=MAX_GOAL_CHARS)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, AnalysisNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def setup_venture_analysis_briefing_routes(session_manager: SessionManager) -> APIRouter:
    """Register before legacy analysis routes so the automatic briefing wins."""
    router = APIRouter(prefix="/api/venture/analysis", tags=["venture-analysis"])

    @router.post("/sessions", status_code=201)
    def create_analysis_session(request: Request, body: BriefingSessionCreate):
        require_venture_runtime()
        owner = require_user(request)
        session_id = uuid.uuid4().hex
        try:
            session_manager.create_session(
                session_id=session_id,
                name=body.name.strip() or "Data Analysis",
                endpoint_url="",
                model="",
                rag=False,
                owner=owner,
            )
            db = SessionLocal()
            try:
                row = db.query(DbSession).filter(DbSession.id == session_id, DbSession.owner == owner).first()
                if not row:
                    raise AnalysisNotFound("Data analysis session could not be created")
                row.mode = "analysis"
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()
            register_analysis_session(session_id, owner)
            save_goal(session_id, owner, body.goal)
            session_manager.add_message(session_id, ChatMessage(
                role="system",
                content="Data analysis briefing created. Profiling begins when the selected CSV is uploaded.",
                metadata={"event_type": "analysis_session_created", "presentation": "timeline_event"},
            ))
            return analysis_payload(session_id, owner)
        except AnalysisError as exc:
            session_manager.delete_session(session_id)
            raise _error(exc) from exc
        except Exception as exc:
            session_manager.delete_session(session_id)
            raise HTTPException(status_code=500, detail="Could not create data analysis session") from exc

    @router.get("/sessions/{session_id}")
    def get_analysis_session(request: Request, session_id: str):
        require_venture_runtime()
        owner = require_user(request)
        try:
            return analysis_payload(session_id, owner)
        except AnalysisError as exc:
            raise _error(exc) from exc

    @router.post("/sessions/{session_id}/dataset")
    async def upload_dataset(request: Request, session_id: str, file: UploadFile = File(...)):
        require_venture_runtime()
        owner = require_user(request)
        try:
            destination = session_target(session_id, owner, file.filename)
        except AnalysisError as exc:
            raise _error(exc) from exc

        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.uploading")
        received = 0
        try:
            with temporary.open("wb") as output:
                while True:
                    chunk = await file.read(1024 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > MAX_DATASET_BYTES:
                        raise AnalysisError("The dataset exceeds the 200 MB upload limit")
                    output.write(chunk)
            if received == 0:
                raise AnalysisError("The uploaded CSV is empty")
            os.replace(temporary, destination)
            return await asyncio.to_thread(ingest_echarts, session_id, owner, str(destination), destination.name, received)
        except AnalysisError as exc:
            for path in (temporary, destination):
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
            raise _error(exc) from exc
        except Exception as exc:
            for path in (temporary, destination):
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    pass
            raise HTTPException(status_code=500, detail="Could not analyze the uploaded CSV") from exc
        finally:
            await file.close()

    @router.post("/sessions/{session_id}/messages", status_code=410)
    def dashboard_commands_disabled(request: Request, session_id: str):
        require_venture_runtime()
        owner = require_user(request)
        if not is_analysis_session(session_id, owner):
            raise HTTPException(status_code=404, detail="Data analysis session not found")
        raise HTTPException(
            status_code=410,
            detail="Dashboard prompts are disabled while the automatic insight briefing is active.",
        )

    return router
