"""Captain-scoped Quest management actions.

The HTTP facade and agent tool both call this module so Artifact/Memory synthesis
uses the same authorization-independent queueing and idempotency rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# The original synthesis module intentionally imported a narrow set of database
# models. The resource governor uses SQL-side previews, so make those two mapped
# classes available on the module before the governor installs its overrides.
import src.venture_synthesis as _synthesis
from core.database import QuestSourceArtifact, QuestSourceVersion

_synthesis.QuestSourceArtifact = QuestSourceArtifact
_synthesis.QuestSourceVersion = QuestSourceVersion

from src.venture_synthesis_governor import enqueue_artifact_memory_synthesis


QUEST_MANAGEMENT_TOOL = {
    "name": "manage_quest_session",
    "description": (
        "Manage the active Venture Quest. Captain-only actions include retrieving "
        "compact status, requesting a cited Artifact draft, and extracting Voyage "
        "Memory from a published Artifact. Memory synthesis never creates memories "
        "from raw conversation or source text."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "quest_id": {"type": "string", "description": "Active Venture Quest session id."},
            "action": {
                "type": "string",
                "enum": ["status", "synthesize_artifact", "synthesize_memory"],
                "description": "The requested Quest management action.",
            },
            "artifact_proposal_id": {
                "type": "string",
                "description": "Published Artifact proposal id. Required only when selecting a specific Artifact for memory synthesis.",
            },
        },
        "required": ["quest_id", "action"],
    },
}


@dataclass(frozen=True)
class QuestManagementResult:
    action: str
    queued: bool
    job_id: str | None
    proposal_id: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "queued": self.queued,
            "job_id": self.job_id,
            "proposal_id": self.proposal_id,
            "message": self.message,
        }


def _get_published_proposal(db, legacy, quest_id: str, proposal_id: str | None):
    query = db.query(legacy.QuestArtifactProposal).filter(
        legacy.QuestArtifactProposal.session_id == quest_id,
        legacy.QuestArtifactProposal.status == "published",
    )
    if proposal_id:
        return query.filter(legacy.QuestArtifactProposal.id == proposal_id).first()
    return query.order_by(legacy.QuestArtifactProposal.published_at.desc(), legacy.QuestArtifactProposal.updated_at.desc()).first()


def request_artifact_synthesis(db, legacy, *, quest_id: str, captain: str) -> QuestManagementResult:
    """Request one deduplicated Captain-reviewable Artifact synthesis job."""
    job = _synthesis.enqueue_synthesis_job(
        db,
        quest_id=quest_id,
        trigger="captain_requested",
        created_by=captain,
    )
    is_new = job.status == "queued" and not job.started_at and not job.artifact_proposal_id
    return QuestManagementResult(
        action="synthesize_artifact",
        queued=is_new,
        job_id=job.id,
        proposal_id=None,
        message="Artifact synthesis queued." if is_new else "An Artifact synthesis job is already active for this Quest.",
    )


def request_memory_synthesis(db, legacy, *, quest_id: str, captain: str, proposal_id: str | None = None) -> QuestManagementResult:
    """Queue memory extraction from a published Artifact only."""
    proposal = _get_published_proposal(db, legacy, quest_id, proposal_id)
    if proposal is None:
        return QuestManagementResult(
            action="synthesize_memory",
            queued=False,
            job_id=None,
            proposal_id=None,
            message="Publish an Artifact before requesting Voyage Memory synthesis.",
        )
    job, created = enqueue_artifact_memory_synthesis(
        db,
        quest_id=quest_id,
        proposal=proposal,
        created_by=captain,
        event_id=f"manual-memory-synthesis:{proposal.id}:{proposal.revision_number}",
    )
    return QuestManagementResult(
        action="synthesize_memory",
        queued=created,
        job_id=job.id,
        proposal_id=proposal.id,
        message="Voyage Memory synthesis queued from the published Artifact." if created else "Voyage Memory synthesis is already queued or complete for this Artifact revision.",
    )


def execute_manage_quest_session(db, legacy, *, quest_id: str, captain: str, action: str, artifact_proposal_id: str | None = None) -> dict[str, Any]:
    """Agent-callable implementation behind the manage_quest_session tool."""
    if action == "synthesize_artifact":
        return request_artifact_synthesis(db, legacy, quest_id=quest_id, captain=captain).to_dict()
    if action == "synthesize_memory":
        return request_memory_synthesis(
            db,
            legacy,
            quest_id=quest_id,
            captain=captain,
            proposal_id=artifact_proposal_id,
        ).to_dict()
    if action == "status":
        active = db.query(legacy.QuestSynthesisJob).filter(
            legacy.QuestSynthesisJob.quest_id == quest_id,
            legacy.QuestSynthesisJob.status.in_(("queued", "running")),
        ).order_by(legacy.QuestSynthesisJob.requested_at.asc()).all()
        return {
            "action": "status",
            "queued": False,
            "job_id": None,
            "proposal_id": None,
            "message": "Quest synthesis status retrieved.",
            "active_jobs": [legacy._synthesis_job_to_safe_dict(job) for job in active],
        }
    raise ValueError("unsupported_quest_management_action")
