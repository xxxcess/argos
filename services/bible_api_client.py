"""Bible API client with Quest-local cache persistence."""

from __future__ import annotations

import asyncio
import hashlib
import os
import random
import re
import time
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy.exc import IntegrityError

from core.database import BibleChapterCache, BibleVerse, SessionLocal, utcnow_naive
from src.bible_catalog import validate_book_in_testament, get_book


class BibleApiError(RuntimeError):
    code = "bible_unavailable"


class BibleRateLimited(BibleApiError):
    code = "bible_rate_limited"


class BibleUnavailable(BibleApiError):
    code = "bible_unavailable"


class BibleInvalidResponse(BibleApiError):
    code = "bible_invalid_response"


class BibleChapterNotFound(BibleApiError):
    code = "bible_chapter_not_found"


class BibleScopeInvalid(BibleApiError):
    code = "bible_scope_invalid"


class BulkImportDisabled(BibleApiError):
    code = "bulk_import_disabled"


@dataclass(frozen=True)
class BibleVersePayload:
    verse_number: int
    text: str


@dataclass(frozen=True)
class BibleChapterPayload:
    translation: str
    testament: str
    book_id: str
    book_name: str
    chapter_number: int
    verses: tuple[BibleVersePayload, ...]


_provider_lock = asyncio.Lock()
_last_request_at = 0.0


def min_request_interval_seconds() -> float:
    try:
        return max(0.0, float(os.getenv("BIBLE_API_MIN_REQUEST_INTERVAL_SECONDS", "1.5")))
    except Exception:
        return 1.5


def allow_full_testament_import() -> bool:
    return str(os.getenv("BIBLE_API_ALLOW_FULL_TESTAMENT_IMPORT", "false")).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _testament_for_book(book_id: str) -> str:
    return get_book(book_id).testament


class BibleApiClient:
    async def fetch_chapter(self, translation: str, book_id: str, chapter_number: int) -> BibleChapterPayload:
        translation = str(translation or "web").lower()
        book_id = str(book_id or "").upper()
        try:
            book = get_book(book_id)
        except ValueError as exc:
            raise BibleScopeInvalid("bible_scope_invalid") from exc
        if chapter_number < 1 or chapter_number > book.chapters:
            raise BibleChapterNotFound("bible_chapter_not_found")
        cached = self._read_cache(translation, book_id, chapter_number)
        if cached:
            return cached
        payload = await self._fetch_provider(translation, book_id, chapter_number)
        self._write_cache(payload)
        return payload

    def _read_cache(self, translation: str, book_id: str, chapter_number: int) -> BibleChapterPayload | None:
        db = SessionLocal()
        try:
            row = db.query(BibleChapterCache).filter(
                BibleChapterCache.translation == translation,
                BibleChapterCache.book_id == book_id,
                BibleChapterCache.chapter_number == chapter_number,
            ).first()
            if not row:
                return None
            verses = db.query(BibleVerse).filter(BibleVerse.bible_chapter_id == row.id).order_by(BibleVerse.verse_number.asc()).all()
            if not verses:
                return None
            return BibleChapterPayload(row.translation, row.testament, row.book_id, row.book_name, row.chapter_number, tuple(BibleVersePayload(v.verse_number, v.text) for v in verses))
        finally:
            db.close()

    def _write_cache(self, payload: BibleChapterPayload) -> None:
        db = SessionLocal()
        try:
            source = "\n".join(f"{v.verse_number}:{v.text}" for v in payload.verses)
            row = db.query(BibleChapterCache).filter(
                BibleChapterCache.translation == payload.translation,
                BibleChapterCache.book_id == payload.book_id,
                BibleChapterCache.chapter_number == payload.chapter_number,
            ).first()
            if row is None:
                row = BibleChapterCache(
                    id=uuid.uuid4().hex,
                    translation=payload.translation,
                    testament=payload.testament,
                    book_id=payload.book_id,
                    book_name=payload.book_name,
                    chapter_number=payload.chapter_number,
                    source_hash=hashlib.sha256(source.encode()).hexdigest(),
                    fetched_at=utcnow_naive(),
                )
                db.add(row)
                db.flush()
            else:
                row.source_hash = hashlib.sha256(source.encode()).hexdigest()
                row.fetched_at = utcnow_naive()
                db.query(BibleVerse).filter(BibleVerse.bible_chapter_id == row.id).delete(synchronize_session=False)
            for verse in payload.verses:
                db.add(BibleVerse(id=uuid.uuid4().hex, bible_chapter_id=row.id, verse_number=verse.verse_number, text=verse.text))
            db.commit()
        except IntegrityError:
            db.rollback()
        finally:
            db.close()

    async def _fetch_provider(self, translation: str, book_id: str, chapter_number: int) -> BibleChapterPayload:
        global _last_request_at
        book = get_book(book_id)
        url = f"https://bible-api.com/{book.name.replace(' ', '%20')}%20{chapter_number}"
        params = {"translation": translation}
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                async with _provider_lock:
                    wait = min_request_interval_seconds() - (time.monotonic() - _last_request_at)
                    if wait > 0:
                        await asyncio.sleep(wait)
                    async with httpx.AsyncClient(timeout=20.0) as client:
                        resp = await client.get(url, params=params)
                    _last_request_at = time.monotonic()
                if resp.status_code == 404:
                    raise BibleChapterNotFound("bible_chapter_not_found")
                if resp.status_code == 429:
                    raise BibleRateLimited("bible_rate_limited")
                if 500 <= resp.status_code < 600:
                    raise BibleUnavailable("bible_unavailable")
                if resp.status_code >= 400:
                    raise BibleChapterNotFound("bible_chapter_not_found")
                data = resp.json()
                verses = data.get("verses")
                if not isinstance(verses, list) or not verses:
                    raise BibleInvalidResponse("bible_invalid_response")
                out = []
                for item in verses:
                    number = int(item.get("verse") or 0)
                    text = _normalize_text(str(item.get("text") or ""))
                    if number < 1 or not text:
                        raise BibleInvalidResponse("bible_invalid_response")
                    out.append(BibleVersePayload(number, text))
                return BibleChapterPayload(translation, book.testament, book.book_id, book.name, chapter_number, tuple(out))
            except BibleInvalidResponse:
                raise
            except BibleChapterNotFound:
                raise
            except BibleRateLimited as exc:
                last_error = exc
            except (BibleUnavailable, httpx.NetworkError, httpx.TimeoutException) as exc:
                last_error = exc
            await asyncio.sleep(min(12.0, 0.75 * (2 ** attempt)) + random.uniform(0, 0.5))
        if isinstance(last_error, BibleRateLimited):
            raise last_error
        raise BibleUnavailable("bible_unavailable") from last_error
