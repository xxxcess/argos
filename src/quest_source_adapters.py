"""Datasource adapters for Venture Quest evidence ingestion."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import mimetypes
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

from core.database import Document, DocumentVersion, EmailAccount, QuestBibleBookSelection, QuestSource, SessionLocal, utcnow_naive
from src.constants import UPLOAD_DIR
from src.markitdown_runtime import convert_to_markdown, is_markitdown_format
from src.upload_handler import is_valid_upload_id
from src.url_security import validate_public_http_url

logger = logging.getLogger(__name__)

TEXT_EXTS = {
    ".txt", ".md", ".json", ".csv", ".log", ".py", ".js", ".ts", ".html", ".htm",
    ".css", ".xml", ".yaml", ".yml", ".sql", ".sh", ".nix", ".java", ".go",
    ".rs", ".rb", ".php", ".c", ".cpp", ".h", ".tsx", ".jsx",
}
UNSUPPORTED_PERMANENT = {
    "unsupported_source_type",
    "unsupported_file_type",
    "invalid_url",
    "access_revoked",
    "encrypted_file",
    "unconstrained_email_scope",
    "database_source_unsupported",
}


def content_hash(text: str | bytes) -> str:
    data = text if isinstance(text, bytes) else str(text).encode("utf-8", errors="replace")
    return hashlib.sha256(data).hexdigest()


def safe_excerpt(text: str, limit: int = 280) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    return cleaned[:limit] + ("..." if len(cleaned) > limit else "")


@dataclass
class RecordRef:
    key: str
    locator: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CapturedArtifact:
    artifact_kind: str
    external_locator: str
    title: str
    raw_reference: dict[str, Any]
    captured_at: datetime = field(default_factory=utcnow_naive)
    content: str = ""
    status: str = "captured"


@dataclass
class NormalizedTextUnit:
    content: str
    title: str
    locator: str
    source_id: str
    source_version_id: str
    artifact_id: str
    content_hash: str
    captured_at: datetime
    visibility_lane: str
    extraction_method: str
    extraction_confidence: str = "high"
    artifact_kind: str = "record"
    raw_reference: dict[str, Any] = field(default_factory=dict)
    preserve_boundaries: bool = False


class QuestSourceAdapter:
    async def discover(self, source: QuestSource, checkpoint: Any = None, job: Any = None) -> list[RecordRef]:
        raise NotImplementedError

    async def capture(self, source: QuestSource, record_ref: RecordRef, job: Any = None) -> CapturedArtifact:
        raise NotImplementedError

    async def extract(self, source: QuestSource, artifact: CapturedArtifact, source_version_id: str, artifact_id: str, job: Any = None) -> list[NormalizedTextUnit]:
        raise NotImplementedError

    def visibility_lane(self, source: QuestSource) -> str:
        if source.access_mode == "captain_only":
            return "captain"
        if source.access_mode == "shared_summaries":
            return "summaries"
        return "shared"


def _json_loads(value: str | None, fallback: Any) -> Any:
    try:
        parsed = json.loads(value or "")
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def _resolve_upload(upload_id: str, owner: str | None) -> dict[str, Any]:
    if not is_valid_upload_id(upload_id):
        raise ValueError("invalid_upload_id")
    root = Path(UPLOAD_DIR).resolve()
    index_path = root / "uploads.json"
    row = None
    if index_path.exists():
        try:
            rows = json.loads(index_path.read_text(encoding="utf-8"))
            for item in rows.values() if isinstance(rows, dict) else []:
                if item.get("id") == upload_id:
                    row = item
                    break
        except Exception:
            row = None
    path = Path(row.get("path")) if row and row.get("path") else None
    if path is None:
        for candidate in root.rglob(upload_id):
            path = candidate
            break
    if path is None:
        raise FileNotFoundError("upload_not_found")
    real = path.resolve()
    if root not in real.parents and real != root:
        raise PermissionError("upload_outside_root")
    if row and owner and row.get("owner") not in (owner, None):
        raise PermissionError("access_revoked")
    return {
        "id": upload_id,
        "path": str(real),
        "name": (row or {}).get("name") or real.name,
        "mime": (row or {}).get("mime") or mimetypes.guess_type(real.name)[0] or "application/octet-stream",
        "size": (row or {}).get("size") or real.stat().st_size,
        "hash": (row or {}).get("hash") or content_hash(real.read_bytes()),
        "uploaded_at": (row or {}).get("uploaded_at"),
        "owner": (row or {}).get("owner"),
    }


class FileQuestSourceAdapter(QuestSourceAdapter):
    async def discover(self, source: QuestSource, checkpoint: Any = None, job: Any = None) -> list[RecordRef]:
        cfg = _json_loads(source.configuration_json, {})
        ids = cfg.get("upload_ids") or ([cfg.get("upload_id")] if cfg.get("upload_id") else [])
        refs = []
        for upload_id in ids:
            if upload_id:
                upload_id = str(upload_id)
                try:
                    meta = _resolve_upload(upload_id, source.captain_username)
                    name = meta.get("name") or upload_id
                    mime = meta.get("mime") or mimetypes.guess_type(name)[0] or ""
                    ext = Path(name).suffix.lower()
                    if ext == ".pdf" or mime == "application/pdf":
                        try:
                            from pypdf import PdfReader
                            reader = PdfReader(meta["path"])
                            if getattr(reader, "is_encrypted", False):
                                refs.append(RecordRef(upload_id, name, {"upload_id": upload_id}))
                                continue
                            for page_num in range(len(reader.pages)):
                                refs.append(RecordRef(
                                    f"{upload_id}:page:{page_num + 1}",
                                    f"{name} · page {page_num + 1}",
                                    {"upload_id": upload_id, "page_number": page_num + 1},
                                ))
                            continue
                        except Exception:
                            refs.append(RecordRef(upload_id, name, {"upload_id": upload_id}))
                            continue
                except Exception:
                    refs.append(RecordRef(upload_id, upload_id, {"upload_id": upload_id}))
                    continue
                refs.append(RecordRef(upload_id, str(upload_id), {"upload_id": upload_id}))
        return refs

    async def capture(self, source: QuestSource, record_ref: RecordRef, job: Any = None) -> CapturedArtifact:
        meta = _resolve_upload(record_ref.raw["upload_id"], source.captain_username)
        page_number = record_ref.raw.get("page_number")
        locator = f"{meta['name']} · page {page_number}" if page_number else meta["name"]
        return CapturedArtifact(
            artifact_kind="file",
            external_locator=locator,
            title=meta["name"],
            raw_reference={k: v for k, v in meta.items() if k != "path"} | {"path": meta["path"], "page_number": page_number},
            content="",
        )

    async def extract(self, source: QuestSource, artifact: CapturedArtifact, source_version_id: str, artifact_id: str, job: Any = None) -> list[NormalizedTextUnit]:
        path = str(artifact.raw_reference.get("path") or "")
        name = artifact.title or os.path.basename(path)
        ext = Path(name).suffix.lower()
        mime = artifact.raw_reference.get("mime") or mimetypes.guess_type(name)[0] or ""
        method = "text"
        units: list[tuple[str, str, str, str]] = []
        try:
            if ext in TEXT_EXTS or mime.startswith("text/"):
                raw = Path(path).read_bytes()
                text = None
                for enc in ("utf-8", "utf-8-sig", "latin-1"):
                    try:
                        text = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                text = text if text is not None else raw.decode("utf-8", errors="replace")
                if ext == ".csv":
                    reader = csv.reader(io.StringIO(text))
                    rows = list(reader)
                    headers = rows[0] if rows else []
                    for start in range(1, max(len(rows), 1), 40):
                        body = rows[start:start + 40]
                        csv_text = "\n".join([", ".join(headers)] + [", ".join(r) for r in body]) if headers else "\n".join(", ".join(r) for r in body)
                        units.append((csv_text, name, f"{name} · rows {start + 1}-{start + len(body)}", "csv"))
                else:
                    units.append((text, name, name, "text"))
            elif ext == ".pdf" or mime == "application/pdf":
                method = "pypdf"
                try:
                    from pypdf import PdfReader
                    reader = PdfReader(path)
                    if getattr(reader, "is_encrypted", False):
                        raise ValueError("encrypted_file")
                    only_page = artifact.raw_reference.get("page_number")
                    pages = []
                    if only_page:
                        page_idx = int(only_page) - 1
                        pages = [(page_idx, reader.pages[page_idx])] if 0 <= page_idx < len(reader.pages) else []
                    else:
                        pages = list(enumerate(reader.pages))
                    for idx, page in pages:
                        page_text = (page.extract_text() or "").strip()
                        if page_text:
                            units.append((page_text, name, f"{name} · page {idx + 1}", "pdf_text"))
                except ValueError:
                    raise
                except Exception as exc:
                    raise RuntimeError(f"pdf_extract_failed:{type(exc).__name__}") from exc
            elif is_markitdown_format(path):
                method = "markitdown"
                text = convert_to_markdown(path)
                if text:
                    label = "slides" if ext == ".pptx" else ("workbook" if ext in {".xlsx", ".xls"} else "document")
                    units.append((text, name, f"{name} · {label}", method))
            else:
                raise ValueError("unsupported_file_type")
        except Exception:
            raise
        out = []
        for text, title, locator, extraction_method in units:
            text = (text or "").strip()
            if not text:
                continue
            out.append(NormalizedTextUnit(
                content=text,
                title=title,
                locator=locator,
                source_id=source.id,
                source_version_id=source_version_id,
                artifact_id=artifact_id,
                content_hash=content_hash(text),
                captured_at=artifact.captured_at,
                visibility_lane=self.visibility_lane(source),
                extraction_method=extraction_method or method,
                artifact_kind="file",
                raw_reference={k: v for k, v in artifact.raw_reference.items() if k != "path"},
            ))
        if not out:
            raise ValueError("no_readable_text")
        return out


class DocumentQuestSourceAdapter(QuestSourceAdapter):
    async def discover(self, source: QuestSource, checkpoint: Any = None, job: Any = None) -> list[RecordRef]:
        cfg = _json_loads(source.configuration_json, {})
        doc_id = str(cfg.get("document_id") or "").strip()
        return [RecordRef(doc_id, f"Argo Document · {doc_id}", {"document_id": doc_id})] if doc_id else []

    async def capture(self, source: QuestSource, record_ref: RecordRef, job: Any = None) -> CapturedArtifact:
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == record_ref.raw["document_id"], Document.owner == source.captain_username).first()
            if not doc:
                raise PermissionError("access_revoked")
            version = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.version_number.desc()).first()
            version_no = version.version_number if version else doc.version_count
            content = version.content if version else doc.current_content
            return CapturedArtifact(
                artifact_kind="document",
                external_locator=f"Argo Document · version {version_no} · {doc.title}",
                title=doc.title,
                raw_reference={"document_id": doc.id, "version_number": version_no},
                content=content or "",
            )
        finally:
            db.close()

    async def extract(self, source: QuestSource, artifact: CapturedArtifact, source_version_id: str, artifact_id: str, job: Any = None) -> list[NormalizedTextUnit]:
        text = (artifact.content or "").strip()
        if not text:
            raise ValueError("no_readable_text")
        return [NormalizedTextUnit(
            content=text,
            title=artifact.title,
            locator=artifact.external_locator,
            source_id=source.id,
            source_version_id=source_version_id,
            artifact_id=artifact_id,
            content_hash=content_hash(text),
            captured_at=artifact.captured_at,
            visibility_lane=self.visibility_lane(source),
            extraction_method="database_document",
            artifact_kind="document",
            raw_reference=artifact.raw_reference,
        )]


def _canonical_url(url: str) -> str:
    cleaned = url.strip()
    if "://" not in cleaned:
        cleaned = "https://" + cleaned
    parsed = urlparse(cleaned)
    parsed = parsed._replace(fragment="", scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower())
    return urlunparse(parsed)


class WebsiteQuestSourceAdapter(QuestSourceAdapter):
    async def discover(self, source: QuestSource, checkpoint: Any = None, job: Any = None) -> list[RecordRef]:
        cfg = _json_loads(source.configuration_json, {})
        urls = cfg.get("seed_urls") or cfg.get("urls") or ([cfg.get("url")] if cfg.get("url") else [])
        seen = set()
        refs = []
        for url in urls[: int(cfg.get("max_pages") or 10)]:
            canonical = _canonical_url(str(url))
            validate_public_http_url(canonical)
            if canonical not in seen:
                seen.add(canonical)
                refs.append(RecordRef(canonical, canonical, {"url": canonical, "capture_mode": cfg.get("capture_mode") or "url_capture"}))
        return refs

    async def capture(self, source: QuestSource, record_ref: RecordRef, job: Any = None) -> CapturedArtifact:
        from src.search.content import fetch_webpage_content
        url = validate_public_http_url(record_ref.raw["url"])
        result = await asyncio.get_running_loop().run_in_executor(
            None, lambda: fetch_webpage_content(url, timeout=10, max_bytes=1_500_000)
        )
        text = (result.get("content") or "").strip()
        status = "captured" if text else "needs_browser"
        return CapturedArtifact(
            artifact_kind="website",
            external_locator=url,
            title=result.get("title") or url,
            raw_reference={"url": url, "final_url": result.get("url") or url, "fetched_at": utcnow_naive().isoformat(), "capture_mode": record_ref.raw.get("capture_mode")},
            content=text,
            status=status,
        )

    async def extract(self, source: QuestSource, artifact: CapturedArtifact, source_version_id: str, artifact_id: str, job: Any = None) -> list[NormalizedTextUnit]:
        text = (artifact.content or "").strip()
        if artifact.status == "needs_browser":
            raise ValueError("needs_browser")
        if not text:
            raise ValueError("no_readable_text")
        return [NormalizedTextUnit(
            content=text,
            title=artifact.title,
            locator=artifact.external_locator,
            source_id=source.id,
            source_version_id=source_version_id,
            artifact_id=artifact_id,
            content_hash=content_hash(text),
            captured_at=artifact.captured_at,
            visibility_lane=self.visibility_lane(source),
            extraction_method="readable_html",
            artifact_kind="website",
            raw_reference=artifact.raw_reference,
        )]


class EmailQuestSourceAdapter(QuestSourceAdapter):
    def _validate_scope(self, cfg: dict[str, Any]) -> dict[str, Any]:
        scope = cfg.get("scope") if isinstance(cfg, dict) else {}
        if not isinstance(scope, dict) or not any(scope.get(k) for k in ("mailbox", "label", "sender", "subject", "query", "uids", "since")):
            raise ValueError("unconstrained_email_scope")
        return scope

    async def discover(self, source: QuestSource, checkpoint: Any = None, job: Any = None) -> list[RecordRef]:
        cfg = _json_loads(source.configuration_json, {})
        self._validate_scope(cfg)
        seeded = cfg.get("messages") if isinstance(cfg.get("messages"), list) else []
        refs = []
        for msg in seeded[:200]:
            uid = str(msg.get("uid") or msg.get("message_id") or uuid.uuid4().hex)
            refs.append(RecordRef(uid, f"Email · {msg.get('subject') or uid}", {"message": msg}))
        # Live IMAP fetch is intentionally not broad-scanned here. Existing
        # venture_email polling can create source-version signals; indexing
        # consumes selected messages when provided by constrained connectors/tests.
        return refs

    async def capture(self, source: QuestSource, record_ref: RecordRef, job: Any = None) -> CapturedArtifact:
        msg = record_ref.raw.get("message") or {}
        title = msg.get("subject") or f"Email {record_ref.key}"
        locator = f"Email: {title}"
        raw = {k: msg.get(k) for k in ("account_id", "folder", "uid", "thread_id", "sender", "subject", "received_at") if msg.get(k)}
        return CapturedArtifact("email", locator, title, raw, content=(msg.get("text") or msg.get("body") or ""))

    async def extract(self, source: QuestSource, artifact: CapturedArtifact, source_version_id: str, artifact_id: str, job: Any = None) -> list[NormalizedTextUnit]:
        text = (artifact.content or "").strip()
        if not text:
            raise ValueError("no_readable_text")
        return [NormalizedTextUnit(
            content=text,
            title=artifact.title,
            locator=artifact.external_locator,
            source_id=source.id,
            source_version_id=source_version_id,
            artifact_id=artifact_id,
            content_hash=content_hash(text),
            captured_at=artifact.captured_at,
            visibility_lane=self.visibility_lane(source),
            extraction_method="text_plain",
            artifact_kind="email",
            raw_reference=artifact.raw_reference,
        )]


class YoutubeQuestSourceAdapter(QuestSourceAdapter):
    async def discover(self, source: QuestSource, checkpoint: Any = None, job: Any = None) -> list[RecordRef]:
        cfg = _json_loads(source.configuration_json, {})
        urls = cfg.get("video_urls") or ([cfg.get("video_url")] if cfg.get("video_url") else [])
        from src.youtube_handler import extract_youtube_id
        refs = []
        for url in urls:
            vid = extract_youtube_id(str(url))
            if vid:
                refs.append(RecordRef(vid, f"YouTube · {vid}", {"video_id": vid, "url": str(url)}))
        return refs

    async def capture(self, source: QuestSource, record_ref: RecordRef, job: Any = None) -> CapturedArtifact:
        cfg = _json_loads(source.configuration_json, {})
        video_id = record_ref.raw["video_id"]
        transcript_text = ""
        title = f"YouTube {video_id}"
        try:
            from src.youtube_handler import extract_transcript_async, init_youtube
            init_youtube()
            url = record_ref.raw.get("url") or f"https://www.youtube.com/watch?v={video_id}"
            result = await extract_transcript_async(url, video_id)
            if isinstance(result, dict):
                transcript_text = result.get("transcript") or result.get("text") or ""
                title = result.get("title") or title
            elif isinstance(result, str):
                transcript_text = result
        except Exception as exc:
            logger.info("YouTube transcript unavailable for %s: %s", video_id, exc)
        status = "captured" if transcript_text.strip() else "transcript_unavailable"
        return CapturedArtifact(
            "youtube",
            f"YouTube · {video_id}",
            title,
            {"video_id": video_id, "url": record_ref.raw.get("url"), "allow_generated_captions": bool(cfg.get("allow_generated_captions"))},
            content=transcript_text,
            status=status,
        )

    async def extract(self, source: QuestSource, artifact: CapturedArtifact, source_version_id: str, artifact_id: str, job: Any = None) -> list[NormalizedTextUnit]:
        text = (artifact.content or "").strip()
        if artifact.status == "transcript_unavailable" or not text:
            raise ValueError("transcript_unavailable")
        return [NormalizedTextUnit(
            content=text,
            title=artifact.title,
            locator=f"{artifact.external_locator} · transcript",
            source_id=source.id,
            source_version_id=source_version_id,
            artifact_id=artifact_id,
            content_hash=content_hash(text),
            captured_at=artifact.captured_at,
            visibility_lane=self.visibility_lane(source),
            extraction_method="youtube_transcript",
            artifact_kind="youtube",
            raw_reference=artifact.raw_reference,
        )]


class BibleQuestSourceAdapter(QuestSourceAdapter):
    def _scope(self, source: QuestSource, job: Any) -> dict[str, Any]:
        cfg = _json_loads(source.configuration_json, {})
        scope = _json_loads(getattr(job, "scope_json", None), {}) if job else {}
        if scope.get("kind") != "bible_book_import":
            raise ValueError("bible_scope_invalid")
        testament = str(scope.get("testament") or cfg.get("testament") or "").lower()
        book_id = str(scope.get("book_id") or "").upper()
        translation = str(scope.get("translation") or cfg.get("default_translation") or "web").lower()
        from src.bible_catalog import validate_book_in_testament
        book = validate_book_in_testament(book_id, testament)
        return {"translation": translation, "testament": testament, "book": book, "selection_id": scope.get("selection_id")}

    async def discover(self, source: QuestSource, checkpoint: Any = None, job: Any = None) -> list[RecordRef]:
        scoped = self._scope(source, job)
        book = scoped["book"]
        db = SessionLocal()
        try:
            selection = db.query(QuestBibleBookSelection).filter(QuestBibleBookSelection.id == scoped.get("selection_id")).first()
            if selection:
                selection.state = "indexing"
                selection.total_chapters = book.chapters
                selection.completed_chapters = 0
                selection.last_error = None
                db.commit()
        finally:
            db.close()
        return [
            RecordRef(
                f"BIBLE|{scoped['translation'].upper()}|{book.book_id}|{chapter}",
                f"{book.name} {chapter} ({scoped['translation'].upper()})",
                {"translation": scoped["translation"], "testament": scoped["testament"], "book_id": book.book_id, "book_name": book.name, "chapter": chapter, "selection_id": scoped.get("selection_id")},
            )
            for chapter in range(1, book.chapters + 1)
        ]

    async def capture(self, source: QuestSource, record_ref: RecordRef, job: Any = None) -> CapturedArtifact:
        from services.bible_api_client import BibleApiClient
        raw = record_ref.raw
        chapter = await BibleApiClient().fetch_chapter(str(raw["translation"]), str(raw["book_id"]), int(raw["chapter"]))
        content = "\n".join(f"{v.verse_number}. {v.text}" for v in chapter.verses)
        return CapturedArtifact(
            artifact_kind="bible_chapter",
            external_locator=record_ref.key,
            title=record_ref.locator,
            raw_reference=raw | {"verse_count": len(chapter.verses)},
            content=content,
        )

    async def extract(self, source: QuestSource, artifact: CapturedArtifact, source_version_id: str, artifact_id: str, job: Any = None) -> list[NormalizedTextUnit]:
        raw = artifact.raw_reference
        lines = [line.strip() for line in (artifact.content or "").splitlines() if line.strip()]
        verses: list[tuple[int, str]] = []
        for line in lines:
            m = re.match(r"^(\d+)\.\s*(.*)$", line)
            if m:
                verses.append((int(m.group(1)), m.group(2).strip()))
        if not verses:
            raise ValueError("bible_invalid_response")
        units = []
        translation = str(raw["translation"]).lower()
        book_name = str(raw["book_name"])
        chapter = int(raw["chapter"])
        for start in range(0, len(verses), 3):
            window = verses[start:start + 5]
            if not window:
                continue
            start_verse = window[0][0]
            end_verse = window[-1][0]
            ref = f"{book_name} {chapter}:{start_verse}-{end_verse} ({translation.upper()})" if start_verse != end_verse else f"{book_name} {chapter}:{start_verse} ({translation.upper()})"
            content = "\n".join(f"{num}. {text}" for num, text in window)
            units.append(NormalizedTextUnit(
                content=content,
                title=ref,
                locator=ref,
                source_id=source.id,
                source_version_id=source_version_id,
                artifact_id=artifact_id,
                content_hash=content_hash(content),
                captured_at=artifact.captured_at,
                visibility_lane=self.visibility_lane(source),
                extraction_method="bible_api",
                extraction_confidence="high",
                artifact_kind="bible_passage",
                preserve_boundaries=True,
                raw_reference={
                    "artifact_kind": "bible_passage",
                    "translation": translation,
                    "testament": raw["testament"],
                    "book_id": raw["book_id"],
                    "book_name": book_name,
                    "chapter": chapter,
                    "start_verse": start_verse,
                    "end_verse": end_verse,
                    "reference": ref,
                    "is_current": True,
                },
            ))
        db = SessionLocal()
        try:
            selection = db.query(QuestBibleBookSelection).filter(QuestBibleBookSelection.id == raw.get("selection_id")).first()
            if selection:
                selection.completed_chapters = min((selection.completed_chapters or 0) + 1, selection.total_chapters or 1)
                db.commit()
        finally:
            db.close()
        return units


class DatabaseQuestSourceAdapter(QuestSourceAdapter):
    async def discover(self, source: QuestSource, checkpoint: Any = None, job: Any = None) -> list[RecordRef]:
        raise ValueError("database_source_unsupported")

    async def capture(self, source: QuestSource, record_ref: RecordRef, job: Any = None) -> CapturedArtifact:
        raise ValueError("database_source_unsupported")

    async def extract(self, source: QuestSource, artifact: CapturedArtifact, source_version_id: str, artifact_id: str, job: Any = None) -> list[NormalizedTextUnit]:
        raise ValueError("database_source_unsupported")


def adapter_for_source(source: QuestSource) -> QuestSourceAdapter:
    adapters = {
        "file": FileQuestSourceAdapter,
        "document": DocumentQuestSourceAdapter,
        "website": WebsiteQuestSourceAdapter,
        "email": EmailQuestSourceAdapter,
        "youtube": YoutubeQuestSourceAdapter,
        "bible": BibleQuestSourceAdapter,
        "database": DatabaseQuestSourceAdapter,
    }
    cls = adapters.get(source.source_type)
    if not cls:
        raise ValueError("unsupported_source_type")
    return cls()
