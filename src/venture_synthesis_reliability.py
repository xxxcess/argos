"""Reliability guard for Captain-requested Artifact synthesis."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

SYNTHESIS_MODEL_TIMEOUT_SECONDS = 45
STALE_SYNTHESIS_SECONDS = 120


def recover_stale_synthesis_jobs() -> int:
    """Return abandoned running synthesis jobs to the durable queue.

    A bounded model call means a normal job completes quickly. A job still
    running after this grace period is almost certainly from an older process or
    an interrupted pre-guard call, so it is safe to reclaim.
    """
    from core.database import QuestSynthesisJob, SessionLocal, utcnow_naive

    db = SessionLocal()
    try:
        cutoff = utcnow_naive() - timedelta(seconds=STALE_SYNTHESIS_SECONDS)
        count = db.query(QuestSynthesisJob).filter(
            QuestSynthesisJob.status == "running",
            QuestSynthesisJob.started_at.isnot(None),
            QuestSynthesisJob.started_at < cutoff,
        ).update(
            {
                "status": "queued",
                "next_retry_at": utcnow_naive(),
                "safe_error_code": None,
                "safe_error_message": None,
            },
            synchronize_session=False,
        )
        if count:
            db.commit()
        return int(count or 0)
    except Exception:
        db.rollback()
        return 0
    finally:
        db.close()


def install_synthesis_reliability_guard() -> None:
    """Install once without changing the synthesis module's public API."""
    import src.venture_synthesis as synthesis

    if getattr(synthesis, "_reliability_guard_installed", False):
        return
    original_call = synthesis._call_synthesis_model

    async def bounded_call(pack, labels):
        try:
            return await asyncio.wait_for(
                original_call(pack, labels),
                timeout=SYNTHESIS_MODEL_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "Quest synthesis model exceeded %ss; creating an evidence-only draft instead.",
                SYNTHESIS_MODEL_TIMEOUT_SECONDS,
            )
            return synthesis._fallback_synthesis(pack)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Quest synthesis call failed outside the model wrapper; using fallback")
            return synthesis._fallback_synthesis(pack)

    synthesis._call_synthesis_model = bounded_call
    synthesis._reliability_guard_installed = True
