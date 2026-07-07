"""HTTP API for Argos data-analysis sessions."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from src.auth_helpers import require_user
from src.data_analysis_service import (
    MAX_DATASET_BYTES,
    AnalysisSessionNotFound,
    DataAnalysisError,
    build_session_payload,
    create_analysis_session,
    dataset_target,
    delete_analysis_session,
    ingest_dataset,
    list_analysis_sessions,
    post_analysis_message,
)


router = APIRouter(prefix="/analysis", tags=["data-analysis"])


class CreateAnalysisSessionRequest(BaseModel):
    name: str = Field(default="Data analysis", max_length=120)


class AnalysisMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4_000)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AnalysisSessionNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/sessions")
def get_analysis_sessions(request: Request):
    owner = require_user(request)
    return {"sessions": list_analysis_sessions(owner)}


@router.post("/sessions", status_code=201)
def create_session(request: Request, payload: Optional[CreateAnalysisSessionRequest] = None):
    owner = require_user(request)
    session = create_analysis_session(owner, (payload.name if payload else "Data analysis"))
    return build_session_payload(owner, session["id"])


@router.get("/sessions/{session_id}")
def get_analysis_session(request: Request, session_id: str):
    owner = require_user(request)
    try:
        return build_session_payload(owner, session_id)
    except DataAnalysisError as exc:
        raise _http_error(exc) from exc


@router.post("/sessions/{session_id}/dataset")
async def upload_dataset(request: Request, session_id: str, file: UploadFile = File(...)):
    """Persist a CSV incrementally, then profile it off the event loop."""
    owner = require_user(request)
    try:
        destination = dataset_target(owner, session_id, file.filename)
    except DataAnalysisError as exc:
        raise _http_error(exc) from exc

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
                    raise DataAnalysisError("The dataset exceeds the configured 200 MB limit")
                output.write(chunk)
        if received == 0:
            raise DataAnalysisError("The uploaded CSV is empty")
        os.replace(temporary, destination)
        return await asyncio.to_thread(
            ingest_dataset,
            owner,
            session_id,
            str(destination),
            destination.name,
            received,
        )
    except DataAnalysisError as exc:
        for path in (temporary, destination):
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
        raise _http_error(exc) from exc
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
async def send_analysis_message(request: Request, session_id: str, payload: AnalysisMessageRequest):
    owner = require_user(request)
    try:
        return await asyncio.to_thread(post_analysis_message, owner, session_id, payload.content)
    except DataAnalysisError as exc:
        raise _http_error(exc) from exc


@router.delete("/sessions/{session_id}", status_code=204)
def remove_analysis_session(request: Request, session_id: str):
    owner = require_user(request)
    try:
        delete_analysis_session(owner, session_id)
    except DataAnalysisError as exc:
        raise _http_error(exc) from exc
