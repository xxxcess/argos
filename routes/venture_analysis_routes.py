"""Argos Venture API surface for persisted data-analysis sessions."""
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
from src.venture_data_analysis import (
    MAX_DATASET_BYTES,
    AnalysisError,
    AnalysisNotFound,
    ingest,
    is_analysis_session,
    message,
    payload,
    register_analysis_session,
    session_target,
)


class AnalysisSessionCreate(BaseModel):
    name: str = Field(default="Data Analysis", min_length=1, max_length=120)


class AnalysisPrompt(BaseModel):
    content: str = Field(min_length=1, max_length=4_000)


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, AnalysisNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def setup_venture_analysis_routes(session_manager: SessionManager) -> APIRouter:
    router = APIRouter(prefix="/api/venture/analysis", tags=["venture-analysis"])

    @router.post("/sessions", status_code=201)
    def create_analysis_session(request: Request, body: AnalysisSessionCreate):
        """Create a workspace tab whose dashboard state is stored separately.

        A single system message anchors the ordinary session lifecycle so the
        tab survives SessionManager's legacy non-empty-session startup filter.
        Every actual dashboard interaction is written to venture_analysis_messages.
        """
        require_venture_runtime()
        owner = require_user(request)
        name = body.name.strip() or "Data Analysis"
        session_id = uuid.uuid4().hex
        try:
            session_manager.create_session(
                session_id=session_id,
                name=name,
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
            session_manager.add_message(session_id, ChatMessage(
                role="system",
                content="Data analysis dashboard created. Upload a CSV to begin.",
                metadata={"event_type": "analysis_session_created", "presentation": "timeline_event"},
            ))
            return payload(session_id, owner)
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
            return payload(session_id, owner)
        except AnalysisError as exc:
            raise _error(exc) from exc

    @router.post("/sessions/{session_id}/dataset")
    async def upload_dataset(request: Request, session_id: str, file: UploadFile = File(...)):
        """Stream a CSV into a per-owner storage root, then profile it off-loop."""
        require_venture_runtime()
        owner = require_user(request)
        try:
            destination = session_target(session_id, owner, file.filename)
        except AnalysisError as exc:
            raise _error(exc) from exc

        temporary = destination.with_suffix(destination.suffix + ".uploading")
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
            return await asyncio.to_thread(ingest, session_id, owner, str(destination), destination.name, received)
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

    @router.post("/sessions/{session_id}/messages")
    async def post_dashboard_message(request: Request, session_id: str, body: AnalysisPrompt):
        require_venture_runtime()
        owner = require_user(request)
        try:
            return await asyncio.to_thread(message, session_id, owner, body.content)
        except AnalysisError as exc:
            raise _error(exc) from exc

    @router.get("/sessions/{session_id}/exists")
    def analysis_session_exists(request: Request, session_id: str):
        """Small identity check used by the workspace UI while restoring tabs."""
        require_venture_runtime()
        owner = require_user(request)
        return {"analysis": is_analysis_session(session_id, owner)}

    return router
