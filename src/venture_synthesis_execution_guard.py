"""Last-mile execution guard for Venture synthesis jobs.

All callers may enqueue work. The queue worker privately executes only jobs it
atomically claimed; stale direct/background callers must independently win the
queued-to-running database transition. This prevents duplicate LLM contexts in
one process as well as across processes.
"""

from __future__ import annotations

import asyncio


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

    async def guarded_process(job_id: str) -> None:
        # Direct callers (including legacy BackgroundTasks) cannot execute a job
        # that is already running. The worker below is the only path that may
        # execute a status=running job without claiming it again.
        if not _claim_specific_job(synthesis, job_id):
            return
        await original_process(job_id)

    async def guarded_worker_loop(concurrency: int = 1) -> None:
        semaphore = asyncio.Semaphore(max(1, concurrency))
        active: set[asyncio.Task] = set()
        synthesis._worker_stop = asyncio.Event()

        async def run_worker_claim(job_id: str) -> None:
            try:
                # ``original_claim_next`` already performed the atomic lease.
                await original_process(job_id)
            finally:
                semaphore.release()

        while not synthesis._worker_stop.is_set():
            job_id = original_claim_next()
            if not job_id:
                await asyncio.sleep(2.0)
                continue
            await semaphore.acquire()
            task = asyncio.create_task(run_worker_claim(job_id))
            active.add(task)
            task.add_done_callback(active.discard)
        if active:
            await asyncio.gather(*active, return_exceptions=True)

    synthesis.process_synthesis_job = guarded_process
    synthesis._worker_loop = guarded_worker_loop
    synthesis._execution_guard_installed = True
