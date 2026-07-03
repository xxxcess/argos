"""Strict quality contract for Venture Artifacts and Artifact-derived Memory."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

_GENERIC = {"", "artifact", "insight", "insights", "key point", "key points", "quest artifact", "quest insight", "summary", "untitled"}
_CATEGORIES = {"finding", "decision", "risk", "open_question", "constraint", "next_step", "contradiction"}
_CONFIDENCE = {"low", "medium", "high"}


def _clean(value: Any, limit: int | None = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit].rstrip() if limit else text


def _normal(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _words(value: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z'-]*", value or "")


def is_semantic_title(value: Any) -> bool:
    title = _clean(value, 140)
    normalized = _normal(title)
    if normalized in _GENERIC or not re.search(r"[A-Za-z]", title):
        return False
    if re.fullmatch(r"\s*\d+[.)-]?\s*", title):
        return False
    if re.match(r"^\s*(?:[CSA]\d+|\d+)\s*[.)-]", title, flags=re.I):
        return False
    if re.search(r"\[(?:C|S|A)\d+\]", title):
        return False
    if re.match(r"^(?:matthew|mark|luke|john|acts|romans|genesis|exodus)\s+\d+[:.]\d+", title, flags=re.I):
        return False
    return len(_words(title)) >= 3 and 14 <= len(title) <= 120


def _citations(values: Any, valid: set[str]) -> list[str]:
    out: list[str] = []
    for value in values or []:
        citation = str(value).strip()
        if citation not in valid:
            raise ValueError("unsupported_citation")
        if citation not in out:
            out.append(citation)
    if not out:
        raise ValueError("missing_citation")
    return out


def _is_numbered_passage(text: str) -> bool:
    return len(re.findall(r"(?:^|\s)\d+\.\s+[A-Z]", text or "")) >= 2


def _is_evidence_echo(text: str, citations: list[str], labels: dict[str, dict[str, Any]]) -> bool:
    statement = _clean(text, 800)
    if len(statement) > 360 or _is_numbered_passage(statement):
        return True
    if statement.count('"') >= 2 or statement.count('“') >= 2:
        return True
    normalized = _normal(statement)
    if len(normalized) < 24:
        return True
    for citation in citations:
        source = _normal(_clean(labels.get(citation, {}).get("text"), 1800))
        if len(source) < 50:
            continue
        if len(normalized) >= 60 and normalized in source:
            return True
        compare = source[:max(240, len(normalized) * 2)]
        if len(normalized) >= 80 and SequenceMatcher(None, normalized, compare).ratio() >= 0.82:
            return True
    return False


def is_revelation(value: Any, citations: list[str] | None = None, labels: dict[str, dict[str, Any]] | None = None) -> bool:
    text = _clean(value, 420)
    if len(text) < 24 or len(_words(text)) < 6 or _is_numbered_passage(text):
        return False
    return not labels or not _is_evidence_echo(text, citations or [], labels)


def _speaker(citations: list[str], labels: dict[str, dict[str, Any]]) -> str:
    for citation in citations:
        value = _clean(labels.get(citation, {}).get("speaker"), 120)
        if value:
            return value
    return "Participant"


def _category(title: str, revelation: str, value: Any) -> str:
    declared = _clean(value).lower()
    if declared in _CATEGORIES:
        return declared
    lower = f"{title} {revelation}".lower()
    if "?" in revelation or "question" in lower:
        return "open_question"
    if "risk" in lower:
        return "risk"
    if "constraint" in lower or "must" in lower:
        return "constraint"
    if "decision" in lower or "decided" in lower:
        return "decision"
    if "should" in lower or "next step" in lower:
        return "next_step"
    if "conflict" in lower or "contradict" in lower:
        return "contradiction"
    return "finding"


def validate_synthesis_json(data: dict[str, Any], labels: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("invalid_json")
    if data.get("should_create") is False:
        return {"should_create": False, "reason": _clean(data.get("reason")) or "no_durable_insight"}

    valid = {label for label in labels if label.startswith(("C", "S"))}
    if not valid:
        return {"should_create": False, "reason": "no_grounded_material"}

    observations = []
    for raw in (data.get("conversation_observations") or data.get("observations") or [])[:3]:
        if not isinstance(raw, dict):
            continue
        cites = _citations(raw.get("citations"), valid)
        conversation = [citation for citation in cites if citation.startswith("C")]
        if not conversation:
            raise ValueError("conversation_observation_requires_conversation_citation")
        if any(labels[citation].get("is_conversation_path") is False for citation in conversation):
            raise ValueError("observation_requires_chronological_conversation_citation")
        text = _clean(raw.get("text"), 800)
        if not text:
            continue
        kind = _clean(raw.get("kind") or "observation").lower()
        if kind not in {"request", "observation", "decision", "constraint", "question", "inference", "risk"}:
            kind = "observation"
        observations.append({"speaker": _clean(raw.get("speaker") or _speaker(cites, labels), 120), "kind": kind, "text": text, "citations": cites})
    if not observations:
        return {"should_create": False, "reason": "insufficient_grounded_conversation"}

    questions = []
    for raw in (data.get("questions_raised") or [])[:6]:
        if not isinstance(raw, dict):
            continue
        cites = _citations(raw.get("citations"), valid)
        if not any(citation.startswith("C") for citation in cites):
            raise ValueError("question_requires_conversation_citation")
        text = _clean(raw.get("text"), 500)
        if text:
            questions.append({"text": text, "citations": cites})

    points = []
    seen = set()
    for raw in (data.get("key_points") or [])[:5]:
        if not isinstance(raw, dict):
            continue
        cites = _citations(raw.get("citations"), valid)
        title = _clean(raw.get("title"), 120)
        revelation = _clean(raw.get("revelation") or raw.get("memory") or raw.get("content"), 360)
        if not is_semantic_title(title) or not is_revelation(revelation, cites, labels):
            continue
        signature = _normal(f"{title} {revelation}")
        if signature in seen:
            continue
        seen.add(signature)
        confidence = _clean(raw.get("confidence") or "medium").lower()
        points.append({
            "title": title,
            "revelation": revelation,
            "content": revelation,
            "category": _category(title, revelation, raw.get("category")),
            "confidence": confidence if confidence in _CONFIDENCE else "medium",
            "citations": cites,
        })
    if len(points) < 3:
        return {"should_create": False, "reason": "insufficient_synthesized_key_points"}

    title = _clean(data.get("title"), 140)
    if not is_semantic_title(title):
        title = points[0]["title"]
    return {
        "should_create": True,
        "title": title,
        "claim_key": re.sub(r"[^a-z0-9]+", "-", _clean(data.get("claim_key") or title).lower()).strip("-")[:120],
        "conversation_observations": observations,
        "questions_raised": questions,
        "key_points": points,
        "key_finding": _clean(data.get("key_finding") or points[0]["revelation"], 1200),
        "interpretation": _clean(data.get("interpretation"), 1600),
        "recommended_next_bearing": [_clean(item, 240) for item in (data.get("recommended_next_bearing") or [])[:6] if _clean(item)],
    }


def _all_citations(synthesis: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for section in ("conversation_observations", "questions_raised", "key_points"):
        for item in synthesis.get(section) or []:
            for citation in item.get("citations") or []:
                if citation not in out:
                    out.append(citation)
    return out


def _source_label(label: str, item: dict[str, Any]) -> str:
    if label.startswith("C"):
        return _clean(item.get("speaker") or "Quest discussion", 180)
    source = _clean(item.get("source_name") or "Quest source", 180)
    locator = _clean(item.get("locator"), 180)
    return f"{source} — {locator}" if locator else source


def render_artifact_markdown(synthesis: dict[str, Any], labels: dict[str, dict[str, Any]]) -> str:
    cited = [label for label in _all_citations(synthesis) if label in labels]
    captured = [labels[label].get("captured_at") for label in cited if labels[label].get("captured_at")]
    window = f"{min(captured)}–{max(captured)}" if captured else "unspecified"
    lines = [
        f"# {synthesis['title']}", "",
        f"**Quest:** {synthesis.get('quest_title') or 'Quest'}  ",
        f"**Current Bearing:** {synthesis.get('current_bearing') or 'See Voyage Log'}  ",
        "**Artifact status:** draft  ", f"**Evidence window:** {window}", "", "## Conversation path",
    ]
    for number, observation in enumerate(synthesis.get("conversation_observations") or [], 1):
        cites = " ".join(f"[{citation}]" for citation in observation.get("citations") or [] if citation in labels)
        lines.extend(["", f"{number}. **{observation['speaker']} — {observation['kind'].title()}**: {observation['text']} {cites}".rstrip()])
    if synthesis.get("questions_raised"):
        lines.extend(["", "## Questions raised"])
        for question in synthesis["questions_raised"]:
            cites = " ".join(f"[{citation}]" for citation in question.get("citations") or [] if citation in labels)
            lines.append(f"- {question['text']} {cites}".rstrip())
    lines.extend(["", "## Key points"])
    for number, point in enumerate(synthesis.get("key_points") or [], 1):
        cites = " ".join(f"[{citation}]" for citation in point.get("citations") or [] if citation in labels)
        lines.extend(["", f"{number}. **{point['title']}**", f"**Revelation:** {point['revelation']} {cites}".rstrip()])
    if synthesis.get("interpretation"):
        lines.extend(["", "## Why this matters", synthesis["interpretation"]])
    if synthesis.get("recommended_next_bearing"):
        lines.extend(["", "## Recommended next bearing"])
        lines.extend(f"{number}. {step}" for number, step in enumerate(synthesis["recommended_next_bearing"], 1))
    lines.extend(["", "## Evidence index", "", "| ID | Source | Locator | Supports |", "|---|---|---|---|"])
    for label in cited:
        item = labels[label]
        supports = []
        for point in synthesis.get("key_points") or []:
            if label in point.get("citations") or []:
                supports.append(point["revelation"][:100])
        source = _source_label(label, item).replace("|", "\\|")
        locator = _clean(item.get("locator")).replace("|", "\\|")
        support = "; ".join(supports).replace("|", "\\|")
        lines.append(f"| {label} | {source} | {locator} | {support} |")
    return "\n".join(lines).rstrip() + "\n"


def _evidence_index(content: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    in_index = False
    for source_line in content.splitlines():
        line = source_line.strip()
        if line.lower() == "## evidence index":
            in_index = True
            continue
        if in_index and line.startswith("## "):
            break
        if not in_index or not line.startswith("|") or line.startswith("|---") or " ID " in line:
            continue
        cells = [cell.strip().replace("\\|", "|") for cell in line.strip("|").split("|")]
        if len(cells) >= 4 and re.fullmatch(r"[CSA]\d+", cells[0] or ""):
            result[cells[0]] = {"label": cells[1], "locator": cells[2], "supports": cells[3]}
    return result


def extract_artifact_key_point_candidates(proposal) -> list[dict[str, Any]]:
    document = getattr(proposal, "document", None)
    content = _clean(getattr(document, "current_content", None))
    if not content:
        return []
    evidence = _evidence_index(content)
    active = False
    current: dict[str, Any] | None = None
    blocks: list[dict[str, Any]] = []
    for source_line in content.splitlines():
        line = source_line.strip()
        if line.lower() == "## key points":
            active = True
            continue
        if active and line.startswith("## "):
            break
        if not active or not line:
            continue
        match = re.match(r"^\d+\.\s+\*\*(.+?)\*\*\s*$", line)
        if match:
            if current:
                blocks.append(current)
            current = {"title": _clean(match.group(1), 120), "revelation": ""}
            continue
        revelation = re.match(r"^\*\*Revelation:\*\*\s*(.+)$", line, flags=re.I)
        if current and revelation:
            current["revelation"] = _clean(revelation.group(1), 420)
    if current:
        blocks.append(current)

    candidates = []
    for block in blocks[:5]:
        raw = block["revelation"]
        citations = []
        for citation in re.findall(r"\[(C\d+|S\d+|A\d+)\]", raw):
            if citation not in citations:
                citations.append(citation)
        revelation = re.sub(r"\s*\[(?:C\d+|S\d+|A\d+)\]", "", raw).strip(" -")
        if not is_semantic_title(block["title"]) or not citations or not is_revelation(revelation):
            continue
        candidates.append({
            "title": block["title"],
            "content": revelation,
            "category": _category(block["title"], revelation, ""),
            "confidence": "high" if any(citation.startswith("S") for citation in citations) else "medium",
            "citations": citations,
            "evidence": [{
                "id": citation,
                "label": evidence.get(citation, {}).get("label") or citation,
                "locator": evidence.get(citation, {}).get("locator") or "",
                "supports": evidence.get(citation, {}).get("supports") or revelation,
            } for citation in citations],
        })
    return candidates


def is_displayable_memory(memory) -> bool:
    if not getattr(memory, "artifact_id", None) or getattr(memory, "created_by", None) != "argo":
        return True
    return is_semantic_title(getattr(memory, "title", "")) and is_revelation(getattr(memory, "content", ""))


def artifact_display_title(document, proposal, loads) -> str:
    for value in (getattr(proposal, "title", None), getattr(document, "title", None)):
        if is_semantic_title(value):
            return _clean(value, 140)
    synthesis = loads(getattr(proposal, "synthesis_json", None), {}) if proposal else {}
    if isinstance(synthesis, dict):
        for point in synthesis.get("key_points") or []:
            value = point.get("title") if isinstance(point, dict) else None
            if is_semantic_title(value):
                return _clean(value, 140)
    return "Artifact needs a new summary"


def install_artifact_quality_contract() -> None:
    """Install once after ``src.venture_synthesis`` has completed import."""
    import src.venture_synthesis as synthesis
    if getattr(synthesis, "_artifact_quality_contract_installed", False):
        return

    synthesis.SYNTHESIS_SYSTEM_PROMPT = """\
Create a concise Captain-reviewable Quest Artifact from supplied conversation and source evidence.
Return strict JSON only. Return {"should_create": false} unless three distinct, well-supported revelations exist.

The Artifact needs a semantic 3–12 word title; 1–3 chronological conversation observations with C#
citations; cited questions; and 3–5 key points. Each key point needs title, revelation, category,
confidence, and citations. A revelation is a short, original explanation of what the crew should retain:
an implication, decision, relationship, risk, or next move. Never copy source passages, verse text,
conversation transcripts, numbered items, citations, or quotations into a revelation. Memories are
extracted directly from revelations, so every revelation must stand alone without reopening the Artifact.
"""

    def no_raw_evidence_fallback(_pack: dict[str, Any]) -> dict[str, Any]:
        return {"should_create": False, "reason": "synthesis_model_unavailable"}

    original_process = synthesis._process_artifact_memory_synthesis

    def process_artifact_memory_synthesis(db, job):
        proposal = db.query(synthesis.QuestArtifactProposal).filter(
            synthesis.QuestArtifactProposal.id == job.artifact_proposal_id,
            synthesis.QuestArtifactProposal.session_id == job.quest_id,
        ).first()
        if proposal:
            rows = db.query(synthesis.QuestMemoryEntry).filter(
                synthesis.QuestMemoryEntry.session_id == job.quest_id,
                synthesis.QuestMemoryEntry.artifact_id == proposal.id,
                synthesis.QuestMemoryEntry.state != "retired",
            ).all()
            for row in rows:
                if not is_displayable_memory(row):
                    row.state = "retired"
                    row.updated_at = synthesis.utcnow_naive()
        return original_process(db, job)

    synthesis._fallback_synthesis = no_raw_evidence_fallback
    synthesis.validate_synthesis_json = validate_synthesis_json
    synthesis.render_artifact_markdown = render_artifact_markdown
    synthesis._extract_artifact_key_point_candidates = extract_artifact_key_point_candidates
    synthesis._process_artifact_memory_synthesis = process_artifact_memory_synthesis
    synthesis._artifact_quality_contract_installed = True
