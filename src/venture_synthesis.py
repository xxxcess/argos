"""Functional Argos Venture synthesis pipeline.

This is deliberately conservative: it creates a draft only when there is
multiple pieces of scoped Quest evidence, deduplicates by fingerprint, and
stores all generated output as Captain-private Markdown until publication.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

from core.database import (
    ChatMessage,
    Document,
    QuestArtifactProposal,
    QuestBearing,
    QuestMemoryEntry,
    QuestMemoryState,
    QuestSource,
    QuestSourceVersion,
    Session as DbSession,
    UserNotification,
    utcnow_naive,
)


def _loads(value, fallback):
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _dumps(value) -> str:
    return json.dumps(value, sort_keys=True)


def _notify(db, user: str, key: str, **fields):
    row = db.query(UserNotification).filter(UserNotification.deterministic_key == key).first()
    if row is None:
        row = UserNotification(
            id=uuid.uuid4().hex,
            user_id=user,
            owner=user,
            deterministic_key=key,
            category=fields.get("category", "inbox"),
            severity=fields.get("severity", "info"),
            state=fields.get("state", "unread"),
            title=fields.get("title", ""),
            message=fields.get("message", ""),
            resource_type=fields.get("resource_type"),
            resource_id=fields.get("resource_id"),
            actions_json=_dumps(fields.get("actions", [])),
        )
        db.add(row)
    else:
        row.category = fields.get("category", row.category)
        row.severity = fields.get("severity", row.severity)
        row.state = fields.get("state", row.state)
        row.title = fields.get("title", row.title)
        row.message = fields.get("message", row.message)
        row.resource_type = fields.get("resource_type", row.resource_type)
        row.resource_id = fields.get("resource_id", row.resource_id)
        row.actions_json = _dumps(fields.get("actions", _loads(row.actions_json, [])))
        row.archived_at = None
        row.updated_at = utcnow_naive()
    return row


@dataclass
class SynthesisResult:
    created: bool
    updated: bool
    proposal_id: str | None
    evidence_count: int
    reason: str

    def to_dict(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "proposal_id": self.proposal_id,
            "evidence_count": self.evidence_count,
            "reason": self.reason,
        }


def _markdown(title: str, summary: str, evidence_refs: list[str], bearing: QuestBearing | None) -> str:
    evidence = "\n".join(f"- {ref}" for ref in evidence_refs)
    goal = bearing.exploration_goal if bearing else "the Quest's Current Bearing"
    return f"""# {title}

## What changed or was discovered
{summary}

## Why it matters
This may affect {goal}.

## Evidence
{evidence}

## Confidence and uncertainty
Confidence: medium. The draft is based on scoped Quest evidence, but Captain review is required before treating it as shared knowledge. Re-check source records before relying on the conclusion.

## Recommended next bearing
Validate the pattern against the cited source versions and decide whether this should become a shared Quest Artifact.
"""


def _active_memory_state_filter(q):
    return q.filter(QuestMemoryEntry.state.in_(("provisional", "confirmed")))


def run_argo_synthesis(db, session_id: str, captain_username: str) -> SynthesisResult:
    quest = db.query(DbSession).filter(DbSession.id == session_id).first()
    if not quest:
        return SynthesisResult(False, False, None, 0, "quest_not_found")
    state = db.query(QuestMemoryState).filter(QuestMemoryState.session_id == session_id).first()
    if state and _loads(state.current_bearing_json, {}).get("synthesis_paused"):
        return SynthesisResult(False, False, None, 0, "synthesis_paused")

    bearing = db.query(QuestBearing).filter(QuestBearing.session_id == session_id).first()
    source_versions = (
        db.query(QuestSourceVersion, QuestSource)
        .join(QuestSource, QuestSourceVersion.quest_source_id == QuestSource.id)
        .filter(QuestSource.session_id == session_id)
        .order_by(QuestSourceVersion.created_at.desc())
        .limit(8)
        .all()
    )
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id, ChatMessage.role.in_(("user", "assistant")))
        .order_by(ChatMessage.timestamp.desc())
        .limit(8)
        .all()
    )
    published = db.query(Document).filter(Document.session_id == session_id, Document.archived == False).limit(8).all()
    pending = db.query(QuestArtifactProposal).filter(
        QuestArtifactProposal.session_id == session_id,
        QuestArtifactProposal.status == "pending_review",
    ).all()

    evidence_refs: list[str] = []
    for version, source in source_versions:
        evidence_refs.append(f"source:{source.display_name}:{version.version_label}:{version.id}")
    for msg in messages[:4]:
        evidence_refs.append(f"voyage_log:{msg.role}:{msg.id}")

    if len(evidence_refs) < 2:
        return SynthesisResult(False, False, None, len(evidence_refs), "minimum_evidence_not_met")

    fingerprint_payload = {
        "quest_id": session_id,
        "bearing": bearing.exploration_goal if bearing else quest.name,
        "evidence_refs": sorted(evidence_refs),
    }
    fingerprint = hashlib.sha256(_dumps(fingerprint_payload).encode("utf-8")).hexdigest()

    existing = db.query(QuestArtifactProposal).filter(
        QuestArtifactProposal.session_id == session_id,
        QuestArtifactProposal.evidence_fingerprint == fingerprint,
        QuestArtifactProposal.status == "pending_review",
    ).first()

    title = f"Emerging pattern for {quest.name or 'Quest'}"
    summary = (
        f"Argo found {len(evidence_refs)} related evidence references across the Voyage Log "
        f"and Quest Sources. {len(published)} published Artifact(s) and {len(pending)} pending draft(s) were considered for novelty."
    )
    source_refs = [version.id for version, _source in source_versions]
    content = _markdown(title, summary, evidence_refs, bearing)

    if existing:
        existing.summary = summary
        existing.evidence_refs_json = _dumps(evidence_refs)
        existing.source_version_refs_json = _dumps(source_refs)
        existing.updated_at = utcnow_naive()
        if existing.document:
            existing.document.current_content = content
            existing.document.updated_at = utcnow_naive()
        db.add(ChatMessage(
            id=uuid.uuid4().hex,
            session_id=session_id,
            role="system",
            content="Argo Exploration Synthesis updated an Artifact Draft.",
            meta_data=_dumps({"event_type": "artifact_draft_updated", "proposal_id": existing.id, "actor": "argo"}),
        ))
        return SynthesisResult(False, True, existing.id, len(evidence_refs), "updated_existing_draft")

    doc = Document(
        id=uuid.uuid4().hex,
        session_id=None,
        title=title,
        language="markdown",
        current_content=content,
        owner=captain_username,
        is_active=False,
    )
    db.add(doc)
    proposal = QuestArtifactProposal(
        id=uuid.uuid4().hex,
        session_id=session_id,
        document_id=doc.id,
        captain_username=captain_username,
        artifact_type="insight",
        title=title,
        summary=summary,
        evidence_refs_json=_dumps(evidence_refs),
        evidence_fingerprint=fingerprint,
        source_version_refs_json=_dumps(source_refs),
    )
    db.add(proposal)
    db.add(QuestMemoryEntry(
        id=uuid.uuid4().hex,
        session_id=session_id,
        category="finding",
        visibility="captain_private",
        state="provisional",
        title=title,
        content=summary,
        confidence="medium",
        provenance_json=_dumps({"proposal_id": proposal.id, "synthesis": "argo_exploration_synthesis"}),
        source_version_refs_json=_dumps(source_refs),
        origin_event_ids_json=_dumps([m.id for m in messages[:4]]),
        created_by="argo",
    ))
    db.add(ChatMessage(
        id=uuid.uuid4().hex,
        session_id=session_id,
        role="system",
        content="Argo Exploration Synthesis created an Artifact Draft for Captain review.",
        meta_data=_dumps({"event_type": "artifact_draft_created", "proposal_id": proposal.id, "actor": "argo"}),
    ))
    _notify(
        db,
        captain_username,
        f"artifact-proposal:{proposal.id}",
        category="inbox",
        state="action_required",
        title="New Artifact Draft ready",
        message="Argo identified a possible pattern from newly scoped Quest evidence.",
        resource_type="quest_artifact_proposal",
        resource_id=proposal.id,
        actions=[
            {"id": "review", "label": "Review Draft", "style": "secondary"},
            {"id": "publish", "label": "Publish to Quest", "style": "primary"},
            {"id": "decline", "label": "Decline", "style": "secondary"},
        ],
    )
    return SynthesisResult(True, False, proposal.id, len(evidence_refs), "draft_created")


def set_synthesis_paused(db, session_id: str, paused: bool) -> dict:
    state = db.query(QuestMemoryState).filter(QuestMemoryState.session_id == session_id).first()
    if state is None:
        state = QuestMemoryState(session_id=session_id)
        db.add(state)
    payload = _loads(state.current_bearing_json, {})
    payload["synthesis_paused"] = bool(paused)
    state.current_bearing_json = _dumps(payload)
    state.updated_at = utcnow_naive()
    return payload

