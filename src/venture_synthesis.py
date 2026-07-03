"""Asynchronous Argos Venture Quest synthesis jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from core.database import (
    ChatMessage,
    Document,
    QuestArtifactProposal,
    QuestBearing,
    QuestEvidenceChunk,
    QuestMemoryEntry,
    QuestMemoryState,
    QuestRetrievalRun,
    QuestSource,
    QuestSynthesisJob,
    Session as DbSession,
    SessionLocal,
    UserNotification,
    utcnow_naive,
)

logger = logging.getLogger(__name__)

JOB_STATUSES = {"queued", "running", "created", "updated", "completed", "no_insight", "failed", "cancelled"}
JOB_TRIGGERS = {"grounded_response", "captain_requested", "artifact_revision"}
TERMINAL_STATUSES = {"created", "updated", "no_insight", "failed", "cancelled"}
MAX_OBSERVATIONS = 8
MAX_OPEN_QUESTIONS = 6
MAX_NEXT_BEARINGS = 6
MAX_MEMORY_CANDIDATES = 3
_worker_task: asyncio.Task | None = None
_worker_stop: asyncio.Event | None = None


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


def _loads(value: str | None, fallback):
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, sort_keys=True)


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


def synthesis_job_to_dict(job: QuestSynthesisJob) -> dict:
    return {
        "id": job.id,
        "quest_id": job.quest_id,
        "trigger": job.trigger,
        "status": job.status,
        "created_by": job.created_by,
        "triggering_user_message_id": job.triggering_user_message_id,
        "triggering_assistant_message_id": job.triggering_assistant_message_id,
        "retrieval_run_id": job.retrieval_run_id,
        "artifact_proposal_id": job.artifact_proposal_id,
        "attempt_count": job.attempt_count,
        "requested_at": job.requested_at.isoformat() + "Z" if job.requested_at else None,
        "started_at": job.started_at.isoformat() + "Z" if job.started_at else None,
        "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
        "next_retry_at": job.next_retry_at.isoformat() + "Z" if job.next_retry_at else None,
        "safe_error_code": job.safe_error_code,
        "safe_error_message": job.safe_error_message,
        "result": _loads(job.result_json, {}),
    }


def enqueue_synthesis_job(
    db,
    *,
    quest_id: str,
    trigger: str,
    created_by: str | None,
    triggering_user_message_id: str | None = None,
    triggering_assistant_message_id: str | None = None,
    retrieval_run_id: str | None = None,
    priority_time=None,
) -> QuestSynthesisJob:
    trigger = trigger if trigger in JOB_TRIGGERS else "grounded_response"
    job = QuestSynthesisJob(
        id=uuid.uuid4().hex,
        quest_id=quest_id,
        trigger=trigger,
        status="queued",
        created_by=created_by,
        triggering_user_message_id=triggering_user_message_id,
        triggering_assistant_message_id=triggering_assistant_message_id,
        retrieval_run_id=retrieval_run_id,
        requested_at=priority_time or utcnow_naive(),
    )
    db.add(job)
    return job


def _normalize_claim_key(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return value[:120] or hashlib.sha256((value or "quest-insight").encode()).hexdigest()[:24]


def _chunk_text(chunk: QuestEvidenceChunk) -> str:
    art = chunk.artifact
    text = (getattr(art, "normalized_text_path_or_text", None) or "").strip()
    if text:
        return text[:1600]
    return ""


def build_evidence_pack(db, job: QuestSynthesisJob) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    quest = db.query(DbSession).filter(DbSession.id == job.quest_id).first()
    bearing = db.query(QuestBearing).filter(QuestBearing.session_id == job.quest_id).first()
    if not quest or not bearing:
        raise ValueError("quest_not_found")
    run = db.query(QuestRetrievalRun).filter(QuestRetrievalRun.id == job.retrieval_run_id, QuestRetrievalRun.quest_id == job.quest_id).first() if job.retrieval_run_id else None
    chunk_ids = _loads(run.chunk_ids_json, []) if run else []
    chunks = []
    if chunk_ids:
        rows = db.query(QuestEvidenceChunk).filter(
            QuestEvidenceChunk.quest_id == job.quest_id,
            QuestEvidenceChunk.id.in_(chunk_ids),
            QuestEvidenceChunk.is_current == True,  # noqa: E712
        ).all()
        by_id = {r.id: r for r in rows}
        chunks = [by_id[cid] for cid in chunk_ids if cid in by_id]
    if not chunks and job.trigger == "captain_requested":
        chunks = db.query(QuestEvidenceChunk).filter(
            QuestEvidenceChunk.quest_id == job.quest_id,
            QuestEvidenceChunk.is_current == True,  # noqa: E712
        ).order_by(QuestEvidenceChunk.created_at.desc()).limit(6).all()

    labels: dict[str, dict[str, Any]] = {}
    source_items = []
    for idx, chunk in enumerate(chunks[:6], 1):
        source = db.query(QuestSource).filter(QuestSource.id == chunk.source_id, QuestSource.session_id == job.quest_id).first()
        if not source:
            continue
        label = f"S{idx}"
        text = _chunk_text(chunk)
        if not text:
            continue
        item = {
            "label": label,
            "source_name": source.display_name,
            "source_type": source.source_type,
            "locator": chunk.locator,
            "text": text,
            "chunk_id": chunk.id,
            "source_version_id": chunk.source_version_id,
            "captured_at": chunk.version.captured_at.isoformat() + "Z" if getattr(chunk, "version", None) and chunk.version.captured_at else None,
        }
        labels[label] = item
        source_items.append(item)

    artifacts = []
    for idx, prop in enumerate(
        db.query(QuestArtifactProposal)
        .filter(QuestArtifactProposal.session_id == job.quest_id, QuestArtifactProposal.status == "published")
        .order_by(QuestArtifactProposal.published_at.desc())
        .limit(2)
        .all(),
        1,
    ):
        label = f"A{idx}"
        labels[label] = {"label": label, "title": prop.title, "summary": prop.summary}
        artifacts.append(labels[label])

    memories = []
    for idx, mem in enumerate(
        db.query(QuestMemoryEntry)
        .filter(
            QuestMemoryEntry.session_id == job.quest_id,
            QuestMemoryEntry.state == "confirmed",
        )
        .order_by(QuestMemoryEntry.updated_at.desc())
        .limit(2)
        .all(),
        1,
    ):
        label = f"M{idx}"
        labels[label] = {"label": label, "title": mem.title, "content": mem.content, "category": mem.category}
        memories.append(labels[label])

    user_msg = db.query(ChatMessage).filter(ChatMessage.id == job.triggering_user_message_id, ChatMessage.session_id == job.quest_id).first() if job.triggering_user_message_id else None
    assistant_msg = db.query(ChatMessage).filter(ChatMessage.id == job.triggering_assistant_message_id, ChatMessage.session_id == job.quest_id).first() if job.triggering_assistant_message_id else None
    discussion_items = []
    if job.trigger == "captain_requested" and user_msg and (user_msg.content or "").strip():
        labels["D1"] = {
            "label": "D1",
            "kind": "captain_message",
            "speaker": "Captain",
            "text": user_msg.content[:1600],
            "locator": "Captain discussion - triggering request",
        }
        discussion_items.append(labels["D1"])
    if job.trigger == "captain_requested" and assistant_msg and (assistant_msg.content or "").strip():
        labels["D2"] = {
            "label": "D2",
            "kind": "assistant_response",
            "speaker": "Argo",
            "text": assistant_msg.content[:1600],
            "locator": "Quest discussion - recent assistant response",
        }
        discussion_items.append(labels["D2"])
    pack = {
        "quest_title": quest.name,
        "current_bearing": {
            "title": bearing.title,
            "exploration_goal": bearing.exploration_goal,
            "current_summary": bearing.current_summary,
            "next_bearing": bearing.next_bearing,
        },
        "triggering_user_message": (user_msg.content if user_msg else "")[:4000],
        "grounded_assistant_response": (assistant_msg.content if assistant_msg else "")[:6000],
        "discussion_items": discussion_items,
        "source_chunks": [{k: v for k, v in item.items() if k not in {"chunk_id", "source_version_id"}} for item in source_items],
        "published_artifacts": artifacts,
        "confirmed_voyage_memory": memories,
    }
    return pack, labels


SYNTHESIS_SYSTEM_PROMPT = """\
Create a Captain-reviewable Quest Artifact from supplied evidence.
Return should_create=false when no durable insight exists.
Use only supplied material for factual claims.
Every factual observation must cite supplied labels like S1.
Distinguish observation, inference, decision, recommendation, and uncertainty.
Do not overstate evidence.
Do not output generic evidence-count language.
Do not expose internal IDs or indexing telemetry.
Return strict JSON only.
"""


async def _call_synthesis_model(pack: dict[str, Any], labels: dict[str, Any]) -> dict[str, Any]:
    """Dedicated no-tools synthesis call, with deterministic local fallback."""

    if not pack.get("source_chunks") and not pack.get("discussion_items"):
        return {"should_create": False}
    try:
        from src.task_endpoint import resolve_task_endpoint
        from src.llm_core import llm_call_async

        endpoint, model, headers = resolve_task_endpoint("", "", {}, owner=None)
        prompt = json.dumps({"evidence_pack": pack}, ensure_ascii=False)
        raw = await llm_call_async(
            endpoint,
            model,
            [{"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
            headers=headers,
            temperature=0.1,
            max_tokens=1800,
            tools=None,
        )
        return json.loads(raw)
    except Exception:
        first = (pack.get("source_chunks") or pack.get("discussion_items") or [])[0]
        is_discussion = str(first.get("label", "")).startswith("D")
        title = (
            "Captain note from Quest discussion"
            if is_discussion
            else f"Insight from {first['source_name']}"
        )
        text_hash = hashlib.sha256((first.get("text") or "").encode()).hexdigest()[:12]
        key_basis = f"{pack.get('quest_title')} {first.get('source_name') or first.get('speaker')} {first.get('locator')} {text_hash}"
        key = _normalize_claim_key(key_basis)
        return {
            "should_create": True,
            "title": title,
            "claim_key": key,
            "key_finding": (
                "The Captain asked Argo to preserve this Quest discussion point for review."
                if is_discussion
                else f"{first['source_name']} contains evidence relevant to the current bearing."
            ),
            "observations": [
                {
                    "kind": "decision" if is_discussion else "observation",
                    "text": first["text"][:280],
                    "citations": [first["label"]],
                }
            ],
            "interpretation": (
                "Captain discussion can record goals, decisions, constraints, and next bearings; it is not datasource proof."
                if is_discussion
                else "Inference: this evidence may help refine the Quest bearing, but Captain review is required."
            ),
            "open_questions": ["Should this be published as a reusable Quest Artifact or kept as a private draft?"] if is_discussion else ["Which decision or next bearing should this evidence support?"],
            "recommended_next_bearing": ["Review and edit the draft before publishing it to Voyage Memory."] if is_discussion else ["Review the cited source chunk and decide whether to publish or revise this draft."],
            "confidence": "low" if is_discussion else "medium",
            "memory_candidates": [],
        }


def validate_synthesis_json(data: dict[str, Any], labels: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("invalid_json")
    if data.get("should_create") is False:
        return {"should_create": False}
    valid = set(labels.keys())
    out = {
        "should_create": True,
        "title": str(data.get("title") or "Quest Insight")[:140],
        "claim_key": _normalize_claim_key(str(data.get("claim_key") or data.get("title") or "")),
        "key_finding": str(data.get("key_finding") or "")[:1200],
        "observations": [],
        "interpretation": str(data.get("interpretation") or "")[:1600],
        "open_questions": [str(x)[:240] for x in (data.get("open_questions") or [])[:MAX_OPEN_QUESTIONS] if str(x).strip()],
        "recommended_next_bearing": [str(x)[:240] for x in (data.get("recommended_next_bearing") or [])[:MAX_NEXT_BEARINGS] if str(x).strip()],
        "confidence": str(data.get("confidence") or "low") if str(data.get("confidence") or "low") in {"low", "medium", "high"} else "low",
        "memory_candidates": [],
    }
    for obs in (data.get("observations") or [])[:MAX_OBSERVATIONS]:
        if not isinstance(obs, dict):
            continue
        citations = []
        for c in obs.get("citations") or []:
            c = str(c)
            if c not in valid:
                raise ValueError("unsupported_citation")
            if c not in citations:
                citations.append(c)
        if not citations:
            raise ValueError("missing_citation")
        kind = str(obs.get("kind") or "observation")
        if kind not in {"observation", "inference", "decision"}:
            kind = "observation"
        out["observations"].append({"kind": kind, "text": str(obs.get("text") or "")[:800], "citations": citations})
    if not out["observations"] or not out["key_finding"]:
        return {"should_create": False}
    for mem in (data.get("memory_candidates") or [])[:MAX_MEMORY_CANDIDATES]:
        if not isinstance(mem, dict):
            continue
        citations = []
        for c in mem.get("citations") or []:
            c = str(c)
            if c not in valid:
                raise ValueError("unsupported_citation")
            if c not in citations:
                citations.append(c)
        category = str(mem.get("category") or "finding")
        if category not in {"finding", "decision", "risk", "open_question", "entity"}:
            category = "finding"
        confidence = str(mem.get("confidence") or "low")
        if confidence not in {"low", "medium", "high"}:
            confidence = "low"
        out["memory_candidates"].append({
            "title": str(mem.get("title") or "")[:120],
            "content": str(mem.get("content") or "")[:500],
            "category": category,
            "confidence": confidence,
            "citations": citations,
        })
    return out


def _first_sentence(text: str, limit: int = 240) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return ""
    match = re.search(r"(.{20,}?[.!?])\s", clean)
    return (match.group(1) if match else clean[:limit]).strip()[:limit]


def _specific_source_label(label: str, item: dict[str, Any]) -> str:
    if label.startswith("S"):
        parts = [item.get("source_name") or "Quest source", item.get("locator")]
        return " - ".join(str(p) for p in parts if p)
    if label.startswith("D"):
        return f"{item.get('speaker', 'Quest discussion')} - {item.get('locator', 'Voyage Log')}"
    return label


def render_artifact_markdown(synth: dict[str, Any], labels: dict[str, Any]) -> str:
    cited_labels = []
    for obs in synth.get("observations", []):
        for citation in obs.get("citations", []):
            if citation in labels and citation not in cited_labels:
                cited_labels.append(citation)
    evidence_times = [
        item.get("captured_at")
        for label, item in labels.items()
        if label in cited_labels and isinstance(item, dict) and item.get("captured_at")
    ]
    evidence_window = "unspecified"
    if evidence_times:
        evidence_window = f"{min(evidence_times)}-{max(evidence_times)}"
    lines = [
        f"# Milestone Report - {synth['title']}",
        "",
        f"**Quest:** {synth.get('quest_title') or 'Quest'}  ",
        f"**Current Bearing:** {synth.get('current_bearing') or 'See Voyage Log'}  ",
        "**Report status:** draft  ",
        f"**Evidence window:** {evidence_window}",
        "",
        "## What changed",
        synth["key_finding"],
        "",
        "## Evidence supporting this milestone",
    ]
    for idx, obs in enumerate(synth.get("observations", []), 1):
        citations = [c for c in obs.get("citations", []) if c in labels]
        if not citations:
            continue
        claim_title = obs.get("kind", "claim").replace("_", " ").title()
        lines.extend(["", f"### {claim_title} {idx}", f"{obs['text']} [{citations[0]}]"])
        for citation in citations:
            item = labels[citation]
            quote = _first_sentence(item.get("text") or obs["text"], 220)
            if quote:
                lines.extend(["", f"> \"{quote}\"", f"- **[{citation}] {_specific_source_label(citation, item)}**"])
            meta = [
                item.get("source_type") or item.get("kind") or "voyage",
                item.get("locator"),
                f"Captured {item.get('captured_at')}" if item.get("captured_at") else None,
            ]
            meta_line = " · ".join(str(x) for x in meta if x)
            if meta_line:
                lines.append(meta_line)
    if synth.get("interpretation"):
        lines.extend(["", "## Why this matters", synth["interpretation"]])
    questions = [q for q in synth.get("open_questions", []) if str(q).strip()]
    if questions:
        lines.extend(["", "## Tension or contradiction to resolve", "\n".join(f"- {q}" for q in questions)])
    lines.extend(["", "## Confidence and limits", f"Confidence: {synth.get('confidence', 'low')}. Claims are limited to the evidence cited above."])
    next_steps = [s for s in synth.get("recommended_next_bearing", []) if str(s).strip()]
    if next_steps:
        lines.extend(["", "## Recommended next bearing", "\n".join(f"{i}. {step}" for i, step in enumerate(next_steps, 1))])
    index_rows = []
    for citation in cited_labels:
        item = labels[citation]
        supports = []
        for obs in synth.get("observations", []):
            if citation in obs.get("citations", []):
                supports.append(obs.get("text", "")[:80])
        index_rows.append(f"| {citation} | {_specific_source_label(citation, item)} | {item.get('locator', '')} | {'; '.join(supports)} |")
    if index_rows:
        lines.extend(["", "## Evidence index", "", "| ID | Specific source | Locator | Supports |", "|---|---|---|---|", *index_rows])
    return "\n".join(lines).rstrip() + "\n"


def _fingerprint(synth: dict[str, Any], chunk_ids: list[str]) -> str:
    return hashlib.sha256(_dumps({"claim_key": synth["claim_key"], "chunks": sorted(chunk_ids)}).encode()).hexdigest()


def persist_synthesis_result(db, job: QuestSynthesisJob, synth: dict[str, Any], labels: dict[str, Any]) -> SynthesisResult:
    if not synth.get("should_create"):
        job.status = "no_insight"
        job.finished_at = utcnow_naive()
        job.result_json = _dumps({"should_create": False})
        return SynthesisResult(False, False, None, 0, "no_insight")
    quest = db.query(DbSession).filter(DbSession.id == job.quest_id).first()
    if not quest:
        raise ValueError("quest_not_found")
    bearing = db.query(QuestBearing).filter(QuestBearing.session_id == job.quest_id).first()
    if bearing:
        synth = {
            **synth,
            "quest_title": quest.name,
            "current_bearing": bearing.current_summary or bearing.next_bearing or bearing.exploration_goal or bearing.title,
        }
    else:
        synth = {**synth, "quest_title": quest.name}
    chunk_ids = [item["chunk_id"] for item in labels.values() if isinstance(item, dict) and item.get("chunk_id")]
    fp = _fingerprint(synth, chunk_ids)
    existing = db.query(QuestArtifactProposal).filter(
        QuestArtifactProposal.session_id == job.quest_id,
        QuestArtifactProposal.claim_key == synth["claim_key"],
        QuestArtifactProposal.artifact_type == "insight",
        QuestArtifactProposal.visibility == "captain_private",
        QuestArtifactProposal.status == "pending_review",
    ).first()
    same_evidence = db.query(QuestArtifactProposal).filter(
        QuestArtifactProposal.session_id == job.quest_id,
        QuestArtifactProposal.claim_key == synth["claim_key"],
        QuestArtifactProposal.artifact_type == "insight",
        QuestArtifactProposal.visibility == "captain_private",
        QuestArtifactProposal.evidence_fingerprint == fp,
    ).first()
    if same_evidence and same_evidence.status != "pending_review":
        job.status = "no_insight"
        job.finished_at = utcnow_naive()
        job.result_json = _dumps({"should_create": False, "reason": "duplicate_claim_without_new_evidence"})
        return SynthesisResult(False, False, None, len(chunk_ids), "duplicate_claim_without_new_evidence")
    content = render_artifact_markdown(synth, labels)
    source_versions = sorted({item["source_version_id"] for item in labels.values() if isinstance(item, dict) and item.get("source_version_id")})
    if existing:
        existing.title = synth["title"]
        existing.summary = synth["key_finding"]
        existing.evidence_refs_json = _dumps([label for label in labels if label.startswith(("S", "D"))])
        existing.evidence_fingerprint = fp
        existing.source_version_refs_json = _dumps(source_versions)
        existing.evidence_chunk_refs_json = _dumps(chunk_ids)
        existing.retrieval_run_ids_json = _dumps([job.retrieval_run_id] if job.retrieval_run_id else [])
        existing.synthesis_json = _dumps(synth)
        existing.updated_at = utcnow_naive()
        if existing.document:
            existing.document.title = synth["title"]
            existing.document.current_content = content
            existing.document.updated_at = utcnow_naive()
        job.artifact_proposal_id = existing.id
        job.status = "updated"
        job.finished_at = utcnow_naive()
        job.result_json = _dumps(synth)
        return SynthesisResult(False, True, existing.id, len(chunk_ids), "updated_existing_draft")

    doc = Document(id=uuid.uuid4().hex, session_id=None, title=synth["title"], language="markdown", current_content=content, owner=quest.owner, is_active=False)
    db.add(doc)
    prop = QuestArtifactProposal(
        id=uuid.uuid4().hex,
        session_id=job.quest_id,
        document_id=doc.id,
        captain_username=quest.owner,
        artifact_type="insight",
        title=synth["title"],
        summary=synth["key_finding"],
        evidence_refs_json=_dumps([label for label in labels if label.startswith(("S", "D"))]),
        evidence_fingerprint=fp,
        source_version_refs_json=_dumps(source_versions),
        claim_key=synth["claim_key"],
        evidence_chunk_refs_json=_dumps(chunk_ids),
        retrieval_run_ids_json=_dumps([job.retrieval_run_id] if job.retrieval_run_id else []),
        synthesis_json=_dumps(synth),
        revision_number=1,
        visibility="captain_private",
    )
    db.add(prop)
    db.flush()
    for mem in synth.get("memory_candidates", [])[:MAX_MEMORY_CANDIDATES]:
        if not mem.get("title") or not mem.get("content"):
            continue
        db.add(QuestMemoryEntry(
            id=uuid.uuid4().hex,
            session_id=job.quest_id,
            category=mem["category"],
            visibility="captain_private",
            state="provisional",
            title=mem["title"],
            content=mem["content"],
            confidence=mem["confidence"],
            provenance_json=_dumps({"artifact_proposal_id": prop.id, "citations": mem.get("citations", [])}),
            source_version_refs_json=_dumps(source_versions),
            evidence_chunk_refs_json=_dumps(chunk_ids),
            artifact_id=prop.id,
            artifact_revision_number=1,
            claim_key=synth["claim_key"],
            created_by="argo",
        ))
    _notify(
        db,
        quest.owner,
        f"artifact-proposal:{prop.id}",
        category="inbox",
        state="action_required",
        title="New Artifact Draft ready",
        message=f"Review '{prop.title}' before publishing it to the Quest.",
        resource_type="quest_artifact_proposal",
        resource_id=prop.id,
        actions=[
            {"id": "review", "label": "Review Draft", "style": "secondary"},
            {"id": "publish", "label": "Publish to Quest", "style": "primary"},
            {"id": "decline", "label": "Decline", "style": "secondary"},
        ],
    )
    job.artifact_proposal_id = prop.id
    job.status = "created"
    job.finished_at = utcnow_naive()
    job.result_json = _dumps(synth)
    return SynthesisResult(True, False, prop.id, len(chunk_ids), "draft_created")


async def process_synthesis_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.query(QuestSynthesisJob).filter(QuestSynthesisJob.id == job_id).first()
        if not job or job.status not in {"queued", "running"}:
            return
        job.status = "running"
        job.started_at = job.started_at or utcnow_naive()
        job.attempt_count = (job.attempt_count or 0) + 1
        db.commit()
        if job.trigger == "artifact_revision":
            result = _process_artifact_memory_synthesis(db, job)
            db.commit()
            logger.debug("[venture-memory-synthesis] %s", result)
            return
        pack, labels = build_evidence_pack(db, job)
        source_count = len([k for k in labels if k.startswith("S")])
        discussion_count = len([k for k in labels if k.startswith("D")])
        if source_count < 1 and (job.trigger != "captain_requested" or discussion_count < 1):
            job.status = "no_insight"
            job.safe_error_code = None
            job.safe_error_message = None
            job.result_json = _dumps({"should_create": False, "reason": "no_source_evidence"})
            job.finished_at = utcnow_naive()
            db.commit()
            return
        raw = await _call_synthesis_model(pack, labels)
        synth = validate_synthesis_json(raw, labels)
        result = persist_synthesis_result(db, job, synth, labels)
        db.commit()
        logger.debug("[venture-synthesis] %s", result.to_dict())
    except Exception as exc:
        db.rollback()
        job = db.query(QuestSynthesisJob).filter(QuestSynthesisJob.id == job_id).first()
        if job:
            code = re.sub(r"[^a-zA-Z0-9_ -]", "_", (str(exc) or type(exc).__name__).split(":", 1)[0])[:80].lower()
            transient = code not in {"unsupported_citation", "missing_citation", "invalid_json", "quest_not_found"}
            if transient and job.attempt_count < 3:
                job.status = "queued"
                job.next_retry_at = utcnow_naive() + timedelta(seconds=min(60 * (2 ** max(job.attempt_count - 1, 0)), 900))
            else:
                job.status = "failed"
                job.finished_at = utcnow_naive()
            job.safe_error_code = code
            job.safe_error_message = "Quest synthesis failed safely."
            db.commit()
        logger.info("Quest synthesis job %s failed: %s", job_id, exc)
    finally:
        db.close()


def _process_artifact_memory_synthesis(db, job: QuestSynthesisJob) -> dict[str, Any]:
    prop = db.query(QuestArtifactProposal).filter(
        QuestArtifactProposal.id == job.artifact_proposal_id,
        QuestArtifactProposal.session_id == job.quest_id,
    ).first()
    if not prop or prop.status != "published":
        job.status = "failed"
        job.finished_at = utcnow_naive()
        job.safe_error_code = "artifact_not_published"
        job.safe_error_message = "Memory synthesis could not find the published Artifact."
        return {"changed_memory_ids": [], "reason": "artifact_not_published"}
    synth = _loads(prop.synthesis_json, {})
    candidates = _extract_artifact_key_point_candidates(prop)
    if not candidates and isinstance(synth, dict):
        candidates = _valid_artifact_memory_candidates_from_synthesis(synth, prop)
    changed = []
    source_versions = _loads(prop.source_version_refs_json, [])
    evidence_chunks = _loads(prop.evidence_chunk_refs_json, [])
    for candidate in (candidates or [])[:MAX_MEMORY_CANDIDATES]:
        if not isinstance(candidate, dict):
            continue
        title = str(candidate.get("title") or prop.title).strip()[:120]
        content = str(candidate.get("content") or "").strip()[:500]
        if not title or not content:
            continue
        category = str(candidate.get("category") or "finding")
        if category not in {"finding", "decision", "risk", "open_question", "constraint", "next_step", "contradiction"}:
            category = "finding"
        existing = db.query(QuestMemoryEntry).filter(
            QuestMemoryEntry.session_id == job.quest_id,
            QuestMemoryEntry.artifact_id == prop.id,
            QuestMemoryEntry.title == title,
            QuestMemoryEntry.content == content,
        ).first()
        if existing:
            continue
        mem = QuestMemoryEntry(
            id=uuid.uuid4().hex,
            session_id=job.quest_id,
            category=category,
            visibility="quest_shared",
            state="confirmed",
            title=title,
            content=content,
            confidence=candidate.get("confidence") if candidate.get("confidence") in {"low", "medium", "high"} else "medium",
            provenance_json=_dumps({
                "artifact_proposal_id": prop.id,
                "document_id": prop.document_id,
                "citations": candidate.get("citations") or [],
                "evidence": candidate.get("evidence") or [],
                "trigger_job_id": job.id,
            }),
            source_version_refs_json=_dumps(source_versions),
            evidence_chunk_refs_json=_dumps(evidence_chunks),
            artifact_id=prop.id,
            artifact_revision_number=prop.revision_number,
            claim_key=prop.claim_key,
            created_by="argo",
        )
        db.add(mem)
        changed.append(mem.id)
    job.status = "completed"
    job.finished_at = utcnow_naive()
    summary = (
        f"{len(changed)} durable memory entr{'y' if len(changed) == 1 else 'ies'} created from the published Artifact."
        if changed else "No new durable memory; published Artifact confirmed existing Quest context."
    )
    job.result_json = _dumps({"changed_memory_ids": changed, "safe_summary": summary, "reason": "completed" if changed else "no_op"})
    doc_id = prop.document_id
    db.add(ChatMessage(
        id=uuid.uuid4().hex,
        session_id=job.quest_id,
        role="system",
        content=summary,
        meta_data=_dumps({
            "event_type": "memory_synthesis",
            "presentation": "background_task",
            "event_id": f"memory-synthesis:artifact:{doc_id}",
            "task_id": job.id,
            "status": "completed",
            "title": "Memory synthesis",
            "subject": prop.title,
            "completed_at": job.finished_at.isoformat() + "Z" if job.finished_at else None,
            "result": {"changed_memory_count": len(changed), "summary": summary},
            "artifact_id": doc_id,
            "quest_id": job.quest_id,
        }),
    ))
    return {"changed_memory_ids": changed, "safe_summary": summary}


def _extract_artifact_key_point_candidates(prop: QuestArtifactProposal) -> list[dict[str, Any]]:
    doc = getattr(prop, "document", None)
    content = (getattr(doc, "current_content", None) or "").strip()
    if not content:
        return []
    evidence_labels: dict[str, dict[str, str]] = {}
    in_index = False
    for raw in content.splitlines():
        line = raw.strip()
        if line.lower().startswith("## evidence index"):
            in_index = True
            continue
        if in_index and line.startswith("## "):
            in_index = False
        if not in_index or not line.startswith("|") or line.startswith("|---") or " ID " in line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 4:
            evidence_labels[cells[0]] = {
                "label": cells[1],
                "locator": cells[2],
                "supports": cells[3],
            }

    candidates = []
    active_section = ""
    top_section = ""
    section_claims: list[tuple[str, str]] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            active_section = line.lstrip("#").strip().lower()
            if level <= 2:
                top_section = active_section
            continue
        if line.startswith(">") or line.startswith("|") or line.startswith("- **["):
            continue
        citations = re.findall(r"\[(E\d+|S\d+|D\d+)\]", line)
        claim = re.sub(r"\s*\[(?:E\d+|S\d+|D\d+)\]", "", line).strip(" -*")
        if not _is_recall_worthy_artifact_claim(claim):
            continue
        if not citations:
            if top_section in {"what changed", "short version", "summary", "decision recorded"}:
                section_claims.append((active_section, claim))
            continue
        if top_section and top_section not in {"what changed", "evidence supporting this milestone", "decision recorded"}:
            continue
        category = "finding"
        if "decision" in active_section or claim.lower().startswith(("decided", "decision")):
            category = "decision"
        elif "risk" in active_section or "risk" in claim.lower():
            category = "risk"
        elif "question" in active_section or claim.endswith("?"):
            category = "open_question"
        elif "recommended next bearing" in active_section or "next bearing" in active_section:
            category = "next_step"
        evidence = []
        for citation in citations:
            info = evidence_labels.get(citation, {})
            evidence.append({
                "id": citation,
                "label": info.get("label") or citation,
                "locator": info.get("locator") or "",
                "supports": info.get("supports") or claim,
            })
        title = claim[:96].rstrip(".")
        candidates.append({
            "title": title,
            "content": claim,
            "category": category,
            "confidence": "high" if evidence else "medium",
            "citations": citations,
            "evidence": evidence,
        })
        if len(candidates) >= MAX_MEMORY_CANDIDATES:
            break
    if len(candidates) < MAX_MEMORY_CANDIDATES:
        existing_claims = {re.sub(r"\W+", " ", str(item.get("content") or "")).strip().lower() for item in candidates}
        for section, claim in section_claims:
            normalized_claim = re.sub(r"\W+", " ", claim).strip().lower()
            if normalized_claim in existing_claims:
                continue
            evidence_id = f"A{len(candidates) + 1}"
            category = "next_step" if "recommended next bearing" in section else ("decision" if "decision" in section else "finding")
            candidates.append({
                "title": claim[:96].rstrip("."),
                "content": claim,
                "category": category,
                "confidence": "medium",
                "citations": [evidence_id],
                "evidence": [{
                    "id": evidence_id,
                    "label": f"{prop.title} - {section.title()}",
                    "locator": section.title(),
                    "supports": claim,
                }],
            })
            if len(candidates) >= MAX_MEMORY_CANDIDATES:
                break
    return candidates


def _valid_artifact_memory_candidates_from_synthesis(synth: dict[str, Any], prop: QuestArtifactProposal) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for raw in (synth.get("memory_candidates") or [])[:MAX_MEMORY_CANDIDATES]:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or "").strip()
        if not _is_recall_worthy_artifact_claim(content):
            continue
        citations = [str(c) for c in (raw.get("citations") or []) if str(c).strip()]
        evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
        if not citations and not evidence:
            continue
        category = str(raw.get("category") or "finding")
        if category not in {"finding", "decision", "risk", "open_question", "constraint", "next_step", "contradiction"}:
            category = "finding"
        candidates.append({
            "title": str(raw.get("title") or content)[:96].rstrip("."),
            "content": content[:500],
            "category": category,
            "confidence": raw.get("confidence") if raw.get("confidence") in {"low", "medium", "high"} else "medium",
            "citations": citations,
            "evidence": evidence or [{
                "id": citation,
                "label": f"{prop.title} - cited Artifact evidence",
                "locator": "",
                "supports": content,
            } for citation in citations],
        })
    return candidates


def _is_recall_worthy_artifact_claim(claim: str) -> bool:
    text = re.sub(r"\s+", " ", claim or "").strip()
    lower = text.lower()
    boilerplate = (
        "this evidence may help refine the quest bearing",
        "missing quotes, locators, timestamps, and urls",
        "review the cited evidence",
        "pending evidence review",
        "pending captain review",
        "intentionally not fabricated",
    )
    if any(marker in lower for marker in boilerplate):
        return False
    if re.search(r"\bet\s+al\.?", lower) or re.search(r"\b(?:doi|isbn|arxiv)\b", lower):
        return False
    if re.fullmatch(r"[\(\)\[\]\w\s,.;:&-]*\b(?:19|20)\d{2}\b[\(\)\[\]\w\s,.;:&-]*", text) and not re.search(r"\b(is|are|was|were|shows|indicates|increased|decreased|risk|decision|must|should|needs?)\b", lower):
        return False
    if len(text) < 32:
        return False
    words = re.findall(r"[A-Za-z][A-Za-z0-9'-]*", text)
    if len(words) < 6 and not any(term in lower for term in ("risk", "increased", "decreased", "decision", "must", "should", "needs")):
        return False
    citation_noise = re.sub(r"[\s,;().&-]+", "", text)
    if citation_noise and sum(ch.isdigit() for ch in citation_noise) >= max(2, len(citation_noise) // 3):
        return False
    useful_terms = {
        "is", "are", "was", "were", "will", "should", "must", "needs", "need",
        "shows", "show", "found", "finds", "identifies", "identified", "indicates", "suggests", "increased", "decreased",
        "risk", "decision", "constraint", "priority", "because", "therefore", "works",
        "changes", "supports", "recommends",
    }
    return any(term in lower.split() for term in useful_terms) or any(term in lower for term in ("risk", "decision", "because", "therefore", "increased", "decreased", "identifies"))


def _claim_next_job() -> str | None:
    db = SessionLocal()
    try:
        now = utcnow_naive()
        stale_before = now - timedelta(minutes=30)
        db.query(QuestSynthesisJob).filter(
            QuestSynthesisJob.status == "running",
            QuestSynthesisJob.started_at < stale_before,
        ).update({"status": "queued", "next_retry_at": now}, synchronize_session=False)
        job = (
            db.query(QuestSynthesisJob)
            .filter(QuestSynthesisJob.status == "queued")
            .filter((QuestSynthesisJob.next_retry_at == None) | (QuestSynthesisJob.next_retry_at <= now))  # noqa: E711
            .order_by(QuestSynthesisJob.requested_at.asc())
            .first()
        )
        if not job:
            db.commit()
            return None
        job.status = "running"
        job.started_at = now
        db.commit()
        return job.id
    finally:
        db.close()


async def _worker_loop(concurrency: int = 1) -> None:
    global _worker_stop
    sem = asyncio.Semaphore(max(1, concurrency))
    active: set[asyncio.Task] = set()
    _worker_stop = asyncio.Event()
    while not _worker_stop.is_set():
        job_id = _claim_next_job()
        if not job_id:
            await asyncio.sleep(2.0)
            continue
        await sem.acquire()
        task = asyncio.create_task(process_synthesis_job(job_id))
        active.add(task)
        task.add_done_callback(lambda t: (active.discard(t), sem.release()))
    if active:
        await asyncio.gather(*active, return_exceptions=True)


def start_quest_synthesis_worker(app=None, *, concurrency: int = 1) -> None:
    global _worker_task
    if _worker_task and not _worker_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    _worker_task = loop.create_task(_worker_loop(concurrency=concurrency))
    if app is not None:
        try:
            app.state.quest_synthesis_worker = _worker_task
        except Exception:
            pass


async def stop_quest_synthesis_worker() -> None:
    global _worker_stop, _worker_task
    if _worker_stop:
        _worker_stop.set()
    if _worker_task:
        await asyncio.gather(_worker_task, return_exceptions=True)
    _worker_task = None
    _worker_stop = None


def run_argo_synthesis(db, session_id: str, captain_username: str) -> SynthesisResult:
    """Compatibility wrapper: enqueue, never synthesize synchronously."""

    user_msg = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id, ChatMessage.role == "user")
        .order_by(ChatMessage.timestamp.desc())
        .first()
    )
    assistant_msg = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id, ChatMessage.role == "assistant")
        .order_by(ChatMessage.timestamp.desc())
        .first()
    )
    job = enqueue_synthesis_job(
        db,
        quest_id=session_id,
        trigger="captain_requested",
        created_by=captain_username,
        triggering_user_message_id=user_msg.id if user_msg else None,
        triggering_assistant_message_id=assistant_msg.id if assistant_msg else None,
    )
    return SynthesisResult(False, False, job.id, 0, "queued")


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
