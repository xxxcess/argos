import pytest

from services import bible_api_client
from services.bible_api_client import BibleApiClient, BibleInvalidResponse


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class _FakeAsyncClient:
    def __init__(self, response, calls):
        self.response = response
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def get(self, url):
        self.calls.append(url)
        return self.response


@pytest.mark.asyncio
async def test_bible_api_client_uses_parameterized_data_endpoint(monkeypatch):
    calls = []
    payload = {
        "translation": {"identifier": "web", "name": "World English Bible"},
        "verses": [
            {"book_id": "JHN", "book": "John", "chapter": 3, "verse": 16, "text": "For God so loved the world.\n"},
        ],
    }
    monkeypatch.setattr(bible_api_client.httpx, "AsyncClient", lambda timeout: _FakeAsyncClient(_FakeResponse(payload=payload), calls))
    bible_api_client._request_timestamps.clear()
    bible_api_client._last_request_at = 0.0

    chapter = await BibleApiClient()._fetch_provider("web", "JHN", 3)

    assert calls == ["https://bible-api.com/data/web/JHN/3"]
    assert chapter.book_id == "JHN"
    assert chapter.chapter_number == 3
    assert chapter.verses[0].verse_number == 16
    assert chapter.verses[0].text == "For God so loved the world."


@pytest.mark.asyncio
async def test_bible_api_client_rejects_wrong_chapter_payload(monkeypatch):
    payload = {
        "translation": {"identifier": "web", "name": "World English Bible"},
        "verses": [
            {"book_id": "JHN", "book": "John", "chapter": 4, "verse": 1, "text": "Wrong chapter"},
        ],
    }
    monkeypatch.setattr(bible_api_client.httpx, "AsyncClient", lambda timeout: _FakeAsyncClient(_FakeResponse(payload=payload), []))
    bible_api_client._request_timestamps.clear()
    bible_api_client._last_request_at = 0.0

    with pytest.raises(BibleInvalidResponse):
        await BibleApiClient()._fetch_provider("web", "JHN", 3)
