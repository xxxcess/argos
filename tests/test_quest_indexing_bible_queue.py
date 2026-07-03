from types import SimpleNamespace

from src import quest_indexing


class _FlushOnlyDb:
    def __init__(self, calls):
        self.calls = calls

    def flush(self):
        self.calls.append("flush")


def test_next_bible_book_flushes_terminal_state_before_active_job_lookup(monkeypatch):
    """A just-completed job must not block the next queued book in the same session."""
    calls = []
    db = _FlushOnlyDb(calls)
    source = SimpleNamespace(
        source_type="bible",
        status="active",
        id="source-1",
        captain_username="captain",
    )

    def _has_active_job(_db, source_id):
        calls.append(f"active:{source_id}")
        return True

    monkeypatch.setattr(quest_indexing, "has_active_bible_book_job", _has_active_job)

    assert quest_indexing.enqueue_next_bible_book_job_if_idle(db, source) is None
    assert calls == ["flush", "active:source-1"]


def test_next_bible_book_does_not_queue_while_source_is_paused(monkeypatch):
    calls = []
    db = _FlushOnlyDb(calls)
    source = SimpleNamespace(
        source_type="bible",
        status="paused",
        id="source-1",
        captain_username="captain",
    )

    monkeypatch.setattr(
        quest_indexing,
        "has_active_bible_book_job",
        lambda *_args: (_ for _ in ()).throw(AssertionError("paused source must not query active jobs")),
    )

    assert quest_indexing.enqueue_next_bible_book_job_if_idle(db, source) is None
    assert calls == []
