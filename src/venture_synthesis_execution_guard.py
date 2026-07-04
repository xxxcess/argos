"""Last-mile execution guard for Venture synthesis jobs.

All callers may enqueue work, but only a caller that atomically transitions a
job from ``queued`` to ``running`` may execute it.  This protects against old
background-task code paths or multiple application processes invoking
``process_synthesis_job`` directly.
"""

from __future__ import annotations

import asyncio


_claimed_job_ids: set[str] = set()
_claim_lock = asyncio.Lock()


def _claim_specific_job(synthesis, job_id: str) -> bool:
    db = synthesis.SessionLocal()
    try:
        now = synthesis.utcnow_naive()
        claimed = db.query(synthesis.QuestSynthesisJob).filter(
            synthesis.QuestSynthesisJob.id == job_id,
            synthesis.QuestSynthesisJob.status == "queued",
        ).update({"status": "running", "started_at": now}, synchronize_session=False)
        db.commit()
        return bool(claimed)
    finally:
        db.close()


def install_synthesis_execution_guard() -> None:
    """Install after the resource governor has replaced ``_claim_next_job``."""
    import src.venture_synthesis as synthesis

    if getattr(synthesis, "_execution_guard_installed", False):
        return
    original_claim_next = synthesis._claim_next_job
    original_process = synthesis.process_synthesis_job

    def claim_next() -> str | None:
        job_id = original_claim_next()
        if job_id:
            _claimed_job_ids.add(job_id)
        return job_id

    async def guarded_process(job_id: str) -> None:
        async with _claim_lock:
            already_claimed = job_id in _claimed_job_ids
            if not already_claimed and not _claim_specific_job(synthesis, job_id):
                # Another worker owns it, it is terminal, or it was cancelled.
                return
            _claimed_job_ids.add(job_id)
        try:
            await original_process(job_id)
        finally:
            _claimed_job_ids.discard(job_id)

    synthesis._claim_next_job = claim_next
    synthesis.process_synthesis_job = guarded_process
    synthesis._execution_guard_installed = True
